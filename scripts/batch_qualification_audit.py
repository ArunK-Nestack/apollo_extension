#!/usr/bin/env python3
"""
Batch qualification audit: job titles (DB + optional LLM) and Indian names (local + optional LLM).
Used by manage_batches.py menu [5]/[6] and CLI.
"""

from __future__ import annotations

import math
import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.api import (
    OPENAI_API_KEY,
    OPENAI_DOMAIN_MODEL,
    classify_name_local,
    classify_names_compact_llm,
    classify_novel_titles_compact_llm,
    ensure_indian_surnames_seeded,
    get_connection,
    lookup_indian_names_batch,
    lookup_job_titles_batch,
    normalize_text,
)
from scripts.batch_domain_audit import fetch_batch_leads, resolve_batch_name

LLM_BATCH_SIZE = 25


def _chunked(items: list[str], size: int = LLM_BATCH_SIZE) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _sum_token_stats(stats_list: list[dict[str, Any]]) -> dict[str, int]:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for s in stats_list:
        for k in total:
            total[k] += int(s.get(k, 0))
    return total


def estimate_llm_cost_usd(stats: dict[str, int]) -> float:
    """ponytail: rough gpt-4o-mini list price; upgrade path: model-specific rate table."""
    inp = int(stats.get("prompt_tokens", 0))
    out = int(stats.get("completion_tokens", 0))
    return (inp * 0.15 + out * 0.60) / 1_000_000


def estimate_preflight_llm_cost(item_count: int, batch_size: int = LLM_BATCH_SIZE) -> dict[str, Any]:
    """Rough token/cost estimate before opt-in LLM (ponytail: heuristic, not API quote)."""
    if item_count <= 0:
        return {"batches": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "usd": 0.0}
    batches = math.ceil(item_count / batch_size)
    prompt_tokens = 0
    completion_tokens = 0
    remaining = item_count
    for _ in range(batches):
        n = min(batch_size, remaining)
        prompt_tokens += 280 + n * 14
        completion_tokens += n * 10
        remaining -= n
    stats = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    return {**stats, "batches": batches, "usd": estimate_llm_cost_usd(stats)}


def print_preflight_estimate(label: str, item_count: int) -> None:
    est = estimate_preflight_llm_cost(item_count)
    if est["batches"] <= 0:
        return
    print(
        f"\n Pre-flight LLM estimate ({label}): "
        f"{est['batches']} batch(es), ~{est['total_tokens']:,d} tokens, ~${est['usd']:.4f} USD"
    )


def print_llm_cost_summary(report: dict[str, Any], audit_label: str) -> None:
    """Print AI spend after a successful LLM audit step."""
    if not report.get("llm_ran"):
        return
    stats = report.get("token_stats") or {}
    total = int(stats.get("total_tokens", 0))
    if total <= 0:
        print("\n[AI COST] LLM finished but API returned no token usage.")
        return
    inp = int(stats.get("prompt_tokens", 0))
    out = int(stats.get("completion_tokens", 0))
    usd = estimate_llm_cost_usd(stats)
    batches = int(report.get("llm_batch_count", 0))
    batch_line = f"  LLM batches run:     {batches}\n" if batches else ""
    print("\n" + "=" * 60)
    print(f" AI COST — {audit_label}")
    print("=" * 60)
    print(f"  Model:               {OPENAI_DOMAIN_MODEL}")
    if batch_line:
        print(batch_line.rstrip())
    print(f"  Input tokens:        {inp:,d}")
    print(f"  Output tokens:       {out:,d}")
    print(f"  Total tokens:        {total:,d}")
    print(f"  Estimated cost:      ${usd:.4f} USD")
    print("=" * 60 + "\n")


def title_is_disqualified(info: dict[str, Any]) -> bool:
    if info.get("status") == "not_recognized_title":
        return False
    return not info.get("required", False)


def analyze_batch_titles(leads: list[dict[str, Any]], title_lookup: dict[str, dict] | None = None) -> dict[str, Any]:
    unique_titles = sorted({(L.get("job_title") or "").strip() for L in leads if (L.get("job_title") or "").strip()})
    lookup = title_lookup or {}

    by_title: dict[str, dict[str, Any]] = {}
    qualified: list[str] = []
    disqualified: list[str] = []
    not_recognized: list[str] = []

    for title in unique_titles:
        info = lookup.get(title) or {
            "required": False,
            "status": "not_recognized_title",
            "segment": "Not_Recognized",
            "reason": "Not evaluated",
        }
        by_title[title] = info
        st = info.get("status")
        if st == "not_recognized_title":
            not_recognized.append(title)
        elif title_is_disqualified(info):
            disqualified.append(title)
        else:
            qualified.append(title)

    def leads_for_titles(titles: set[str]) -> list[dict[str, Any]]:
        out = []
        for L in leads:
            t = (L.get("job_title") or "").strip()
            if t in titles:
                info = by_title.get(t, {})
                out.append(
                    {
                        "id": L.get("id"),
                        "name": L.get("name"),
                        "job_title": t,
                        "company_domain": L.get("company_domain"),
                        "reason": info.get("reason", ""),
                    }
                )
        return out

    return {
        "unique_titles": len(unique_titles),
        "qualified_titles": qualified,
        "disqualified_titles": disqualified,
        "not_recognized_titles": not_recognized,
        "title_lookup": by_title,
        "qualified_lead_count": len(leads_for_titles(set(qualified))),
        "disqualified_leads": leads_for_titles(set(disqualified)),
        "not_recognized_leads": leads_for_titles(set(not_recognized)),
    }


def run_title_llm_batches(
    conn,
    titles: list[str],
) -> tuple[dict[str, dict], dict[str, int]]:
    merged: dict[str, dict] = {}
    stats_list: list[dict] = []
    chunks = _chunked(titles)
    for i, chunk in enumerate(chunks, 1):
        print(f"  LLM batch {i}/{len(chunks)}: {len(chunk)} title(s)...")
        llm_res, t_stats = classify_novel_titles_compact_llm(chunk, connection=conn)
        conn.commit()
        stats_list.append(t_stats)
        if t_stats.get("total_tokens"):
            print(f"    batch cost: ~${estimate_llm_cost_usd(t_stats):.4f} ({t_stats['total_tokens']:,d} tokens)")
        for raw in chunk:
            norm = normalize_text(raw)
            if norm in llm_res:
                merged[raw] = llm_res[norm]
            else:
                for k, v in llm_res.items():
                    if normalize_text(k) == norm:
                        merged[raw] = v
                        break
    return merged, _sum_token_stats(stats_list)


def audit_batch_titles(conn, batch_name: str, *, run_llm: bool = False) -> dict[str, Any]:
    leads = fetch_batch_leads(conn, batch_name)
    unique_titles = sorted({(L.get("job_title") or "").strip() for L in leads if (L.get("job_title") or "").strip()})
    db_lookup = lookup_job_titles_batch(unique_titles, connection=conn) if unique_titles else {}
    analysis = analyze_batch_titles(leads, db_lookup)
    analysis["batch"] = batch_name
    analysis["total_leads"] = len(leads)
    analysis["llm_ran"] = False
    analysis["token_stats"] = {}

    if run_llm and analysis["not_recognized_titles"]:
        if not OPENAI_API_KEY:
            analysis["llm_error"] = "OPENAI_API_KEY not set"
        else:
            titles_sent = analysis["not_recognized_titles"]
            llm_lookup, token_stats = run_title_llm_batches(conn, titles_sent)
            merged = dict(analysis["title_lookup"])
            for t, info in llm_lookup.items():
                merged[t] = info
            analysis = analyze_batch_titles(leads, merged)
            analysis["batch"] = batch_name
            analysis["total_leads"] = len(leads)
            analysis["llm_ran"] = True
            analysis["token_stats"] = token_stats
            analysis["llm_batch_count"] = math.ceil(len(titles_sent) / LLM_BATCH_SIZE)

    return analysis


def print_title_audit_report(report: dict[str, Any]) -> None:
    batch = report.get("batch", "")
    print("\n" + "=" * 92)
    print(f" BATCH JOB TITLE AUDIT: {batch}")
    print("=" * 92)
    print(f" Total leads:              {report.get('total_leads', 0):,d}")
    print(f" Unique job titles:        {report.get('unique_titles', 0):,d}")
    print(f"   Qualified (wanted):     {len(report.get('qualified_titles') or []):,d} titles → {report.get('qualified_lead_count', 0):,d} leads")
    print(f"   Disqualified (unwanted):  {len(report.get('disqualified_titles') or []):,d} titles → {len(report.get('disqualified_leads') or []):,d} leads")
    print(f"   Not recognized:           {len(report.get('not_recognized_titles') or []):,d} titles → {len(report.get('not_recognized_leads') or []):,d} leads")

    if report.get("llm_error"):
        print(f"\n LLM skipped: {report['llm_error']}")

    nr = report.get("not_recognized_titles") or []
    if nr:
        print("\n NOT RECOGNIZED TITLES (run LLM to classify)")
        for t in nr[:30]:
            print(f"  - {t}")
        if len(nr) > 30:
            print(f"  ... +{len(nr) - 30} more")

    bad = report.get("disqualified_leads") or []
    if bad:
        print("\n UNWANTED LEADS (disqualified titles)")
        for row in bad[:25]:
            print(f"  id={row['id']} | {row.get('name')} | {row.get('job_title')} | {row.get('reason', '')[:60]}")
        if len(bad) > 25:
            print(f"  ... +{len(bad) - 25} more")
    print("=" * 92 + "\n")


def analyze_batch_names(
    leads: list[dict[str, Any]],
    name_lookup: dict[str, dict[str, Any]] | None = None,
    conn=None,
) -> dict[str, Any]:
    unique_names = sorted({(L.get("name") or "").strip() for L in leads if (L.get("name") or "").strip()})
    lookup = name_lookup or {}
    db_lookup = lookup_indian_names_batch(unique_names, conn) if conn else {}

    definite_indian: list[str] = []
    definite_foreign: list[str] = []
    unresolved: list[str] = []

    for name in unique_names:
        if name in lookup:
            if lookup[name].get("is_indian"):
                definite_indian.append(name)
            else:
                definite_foreign.append(name)
            continue
        if name in db_lookup:
            info = db_lookup[name]
            lookup[name] = {**info, "source": info.get("source", "db")}
            if info.get("is_indian"):
                definite_indian.append(name)
            else:
                definite_foreign.append(name)
            continue
        verdict, reason = classify_name_local(name, connection=conn)
        if verdict == "indian":
            definite_indian.append(name)
            lookup[name] = {"is_indian": True, "reason": reason, "source": "local"}
        elif verdict == "foreign":
            definite_foreign.append(name)
            lookup[name] = {"is_indian": False, "reason": reason, "source": "local"}
        else:
            unresolved.append(name)
            lookup[name] = {"is_indian": None, "reason": reason, "source": "pending_llm"}

    def leads_for_names(names: set[str]) -> list[dict[str, Any]]:
        out = []
        for L in leads:
            n = (L.get("name") or "").strip()
            if n in names:
                info = lookup.get(n, {})
                out.append(
                    {
                        "id": L.get("id"),
                        "name": n,
                        "job_title": L.get("job_title"),
                        "company_domain": L.get("company_domain"),
                        "reason": info.get("reason", ""),
                    }
                )
        return out

    indian_names = set(definite_indian)
    return {
        "unique_names": len(unique_names),
        "definite_indian_names": definite_indian,
        "definite_foreign_names": definite_foreign,
        "unresolved_names": unresolved,
        "name_lookup": lookup,
        "indian_leads": leads_for_names(indian_names),
    }


def run_name_llm_batches(conn, names: list[str]) -> tuple[dict[str, dict], dict[str, int]]:
    merged: dict[str, dict] = {}
    stats_list: list[dict] = []
    chunks = _chunked(names)
    for i, chunk in enumerate(chunks, 1):
        print(f"  LLM batch {i}/{len(chunks)}: {len(chunk)} name(s)...")
        llm_res, n_stats = classify_names_compact_llm(chunk, connection=conn)
        conn.commit()
        stats_list.append(n_stats)
        if n_stats.get("total_tokens"):
            print(f"    batch cost: ~${estimate_llm_cost_usd(n_stats):.4f} ({n_stats['total_tokens']:,d} tokens)")
        merged.update(llm_res)
    return merged, _sum_token_stats(stats_list)


def audit_batch_names(conn, batch_name: str, *, run_llm: bool = False) -> dict[str, Any]:
    ensure_indian_surnames_seeded(conn)
    leads = fetch_batch_leads(conn, batch_name)
    analysis = analyze_batch_names(leads, conn=conn)
    analysis["batch"] = batch_name
    analysis["total_leads"] = len(leads)
    analysis["llm_ran"] = False
    analysis["token_stats"] = {}

    if run_llm and analysis["unresolved_names"]:
        if not OPENAI_API_KEY:
            analysis["llm_error"] = "OPENAI_API_KEY not set"
        else:
            names_sent = analysis["unresolved_names"]
            llm_lookup, token_stats = run_name_llm_batches(conn, names_sent)
            combined_lookup = dict(analysis["name_lookup"])
            for n, info in llm_lookup.items():
                combined_lookup[n] = {**info, "source": "llm"}
            analysis = analyze_batch_names(leads, combined_lookup, conn=conn)
            analysis["batch"] = batch_name
            analysis["total_leads"] = len(leads)
            analysis["llm_ran"] = True
            analysis["token_stats"] = token_stats
            analysis["llm_batch_count"] = math.ceil(len(names_sent) / LLM_BATCH_SIZE)

    return analysis


def print_name_audit_report(report: dict[str, Any]) -> None:
    batch = report.get("batch", "")
    print("\n" + "=" * 92)
    print(f" BATCH INDIAN NAME AUDIT: {batch}")
    print("=" * 92)
    print(f" Total leads:              {report.get('total_leads', 0):,d}")
    print(f" Unique names:             {report.get('unique_names', 0):,d}")
    print(f"   Definite Indian (local):  {len(report.get('definite_indian_names') or []):,d}")
    print(f"   Foreign / safe (local):   {len(report.get('definite_foreign_names') or []):,d}")
    print(f"   Unresolved → LLM:         {len(report.get('unresolved_names') or []):,d}")
    print(f"   Pure Indian leads:        {len(report.get('indian_leads') or []):,d}")

    if report.get("llm_error"):
        print(f"\n LLM skipped: {report['llm_error']}")

    unresolved = report.get("unresolved_names") or []
    if unresolved and not report.get("llm_ran"):
        batches_n = math.ceil(len(unresolved) / LLM_BATCH_SIZE)
        print(f"\n Unresolved names need LLM ({batches_n} batch(es) of up to {LLM_BATCH_SIZE})")

    indian = report.get("indian_leads") or []
    if indian:
        print("\n PURE INDIAN LEADS")
        for row in indian[:25]:
            print(f"  id={row['id']} | {row.get('name')} | {row.get('job_title') or ''} | {row.get('reason', '')[:50]}")
        if len(indian) > 25:
            print(f"  ... +{len(indian) - 25} more")
    print("=" * 92 + "\n")


def delete_leads_by_ids(conn, batch_name: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    ids = [int(r["id"]) for r in rows if r.get("id") is not None]
    if not ids:
        return 0
    with conn.cursor() as cur:
        placeholders = ", ".join(["%s"] * len(ids))
        cur.execute(
            f"DELETE FROM `apollo_saved_leads` WHERE `batch` = %s AND `id` IN ({placeholders})",
            [batch_name] + ids,
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def prompt_delete_leads(conn, batch_name: str, rows: list[dict[str, Any]], header: str) -> int:
    if not rows:
        return 0
    print(header)
    for row in rows[:40]:
        print(
            f"  id={row['id']} | {row.get('name')} | {row.get('job_title') or ''} "
            f"| {row.get('company_domain') or ''}"
        )
    if len(rows) > 40:
        print(f"  ... +{len(rows) - 40} more")
    ans = input(f"\nDelete {len(rows)} lead(s) from '{batch_name}'? [y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        print("Skipped deletion.")
        return 0
    deleted = delete_leads_by_ids(conn, batch_name, rows)
    print(f"Deleted {deleted} row(s).")
    return deleted


def prompt_title_llm_and_delete(conn, report: dict[str, Any]) -> dict[str, Any]:
    batch = report["batch"]
    nr = report.get("not_recognized_titles") or []
    if nr and not report.get("llm_ran"):
        batches_n = math.ceil(len(nr) / LLM_BATCH_SIZE)
        print_preflight_estimate("job titles", len(nr))
        ans = input(
            f"\nRun LLM on {len(nr)} unrecognized title(s) ({batches_n} batch(es))? [y/N]: "
        ).strip().lower()
        if ans in ("y", "yes"):
            report = audit_batch_titles(conn, batch, run_llm=True)
            print_title_audit_report(report)

    bad = report.get("disqualified_leads") or []
    if bad:
        prompt_delete_leads(conn, batch, bad, "\n--- DELETE LEADS WITH UNWANTED JOB TITLES ---")
    print_llm_cost_summary(report, "Job title audit")
    return report


def prompt_name_llm_and_delete(conn, report: dict[str, Any]) -> dict[str, Any]:
    batch = report["batch"]
    unresolved = report.get("unresolved_names") or []
    if unresolved and not report.get("llm_ran"):
        batches_n = math.ceil(len(unresolved) / LLM_BATCH_SIZE)
        print_preflight_estimate("Indian names", len(unresolved))
        ans = input(
            f"\nRun LLM on {len(unresolved)} unresolved name(s) ({batches_n} batch(es))? [y/N]: "
        ).strip().lower()
        if ans in ("y", "yes"):
            report = audit_batch_names(conn, batch, run_llm=True)
            print_name_audit_report(report)

    indian = report.get("indian_leads") or []
    if indian:
        prompt_delete_leads(conn, batch, indian, "\n--- DELETE LEADS WITH PURE INDIAN NAMES ---")
    print_llm_cost_summary(report, "Indian name audit")
    return report


def _self_check() -> None:
    leads = [
        {"id": 1, "name": "Alice CEO", "job_title": "CEO", "company_domain": "acme.com"},
        {"id": 2, "name": "Bob Intern", "job_title": "Intern", "company_domain": "acme.com"},
        {"id": 3, "name": "Rajesh Kumar", "job_title": "Director", "company_domain": "beta.com"},
        {"id": 4, "name": "John Smith", "job_title": "Director", "company_domain": "gamma.com"},
    ]
    title_lookup = {
        "CEO": {"required": True, "status": "qualified", "reason": "ok"},
        "Intern": {"required": False, "status": "disqualified_title", "reason": "bad"},
        "Director": {"required": False, "status": "not_recognized_title", "reason": "novel"},
    }
    tr = analyze_batch_titles(leads, title_lookup)
    assert len(tr["disqualified_leads"]) == 1
    assert tr["disqualified_leads"][0]["id"] == 2
    assert len(tr["not_recognized_titles"]) == 1
    assert not title_is_disqualified(title_lookup["Director"])

    nr = analyze_batch_names(leads)
    assert len(nr["definite_indian_names"]) == 1
    assert nr["definite_indian_names"][0] == "Rajesh Kumar"
    assert "John Smith" in nr["definite_foreign_names"]

    nr2 = analyze_batch_names(
        leads,
        {"John Smith": {"is_indian": False, "reason": "Global", "source": "db"}},
    )
    assert "John Smith" in nr2["definite_foreign_names"]
    assert classify_name_local("John Smith")[0] == "foreign"
    assert classify_name_local("Mystery Person")[0] == "ambiguous"
    print("OK: batch_qualification_audit self-check passed")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        _self_check()
        raise SystemExit(0)

    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    batch_arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if mode not in ("titles", "names") or not batch_arg:
        print("Usage: python scripts/batch_qualification_audit.py titles <batch>")
        print("       python scripts/batch_qualification_audit.py names <batch>")
        print("       python scripts/batch_qualification_audit.py --self-check")
        raise SystemExit(1)

    with get_connection() as conn:
        resolved = resolve_batch_name(conn, batch_arg, [])
        if not resolved:
            print(f"[ERROR] Batch not found: '{batch_arg}'")
            raise SystemExit(1)
        if mode == "titles":
            report = audit_batch_titles(conn, resolved)
            print_title_audit_report(report)
            prompt_title_llm_and_delete(conn, report)
        else:
            report = audit_batch_names(conn, resolved)
            print_name_audit_report(report)
            prompt_name_llm_and_delete(conn, report)
