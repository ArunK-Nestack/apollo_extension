#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Login Credit & Pipeline Audit (Accurate & Rich UI)
=================================================
Full-pipeline audit for any Apollo login account showing exact,
mathematically consistent credit utilization and lead flow across all stages:

  Stage 1  - Apollo API Enrichment Ledger : credit usage (Found / No-Email / Wasted)
  Stage 2  - Apollo Saved Leads DB        : leads saved to DB (Enriched vs Pending)
  Stage 3  - Enrich.so Web Leads DB       : leads enriched via web (0 Apollo credits)
  Stage 4  - MillionVerifier Email Quality : sent / Good / Bad / Risky breakdown
  Stage 5  - Freshsales CRM Sync Ledger   : unique leads pushed, created, updated, TLD
  Stage 5b - Freshsales Run History       : execution metrics per file run
  Stage 6  - Freshsales Live CRM Activity : daily creation/update scan
"""

from __future__ import annotations

import os
import sys
import re
import json
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict

# Ensure project root is in path
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in [str(_ROOT), str(_ROOT / "freshsales_agent")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")
load_dotenv(_ROOT / "freshsales_agent" / ".env", override=False)

# Force UTF-8 on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Rich Terminal UI Imports
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.align import Align

console = Console(force_terminal=True, legacy_windows=False, highlight=False)

# -----------------------------------------------------------------
# PATH CONSTANTS
# -----------------------------------------------------------------
LEDGER_PATH    = _ROOT / "config" / "freshsales_synced_batches.json"
MV_JOBS_PATH   = _ROOT / "config" / "millionverifier_jobs.json"
MV_CACHE_DIR   = _ROOT / "scratch" / "millionverifier_cache"
FS_REPORTS_DIR = _ROOT / "exports" / "freshsales_reports"

# -----------------------------------------------------------------
# ACCURATE BATCH-TO-ACCOUNT RESOLVER
# -----------------------------------------------------------------

def get_account_for_batch(batch: str, account_used: str = "") -> str:
    """
    Deterministic resolver that maps a database batch name or account tag
    to the single true Apollo login email it belongs to.
    Prevents cross-account pollution (e.g. vijay_raghavan vs vraghavan).
    """
    b = (batch or "").lower().replace("-", "_").replace(".", "_").replace("@", "_")
    au = (account_used or "").lower().strip()

    # Abel Abraham
    if "abel" in b or "abel" in au:
        return "abel.abraham@nestacktechnologies.com"

    # Vijay Raghavan (4 different domain accounts)
    if "vijay_raghavan" in b or "vijay.raghavan" in b or "vijay raghavan" in au:
        if "nestacktechnology" in b or "nestacktechnologies" in b:
            return "vijay.raghavan@nestacktechnologies.com"
        elif "nestack_net" in b:
            return "vijay.raghavan@nestack.net"
        elif "nestacktech" in b:
            return "vijay.raghavan@nestacktech.com"
        else:
            return "vijay.raghavan@nestack.com"

    # Vijay (standalone)
    if "vijay" in b and "raghavan" not in b:
        return "vijay@nestacktech.com"

    # V Raghavan / V Raghav (distinct from Vijay Raghavan)
    if "vraghavan" in b or "varaghavan" in b or "vraghvan" in b or "vraghav" in b or "vraghav" in au:
        if "nestacktechnology" in b or "nestacktechnology" in au:
            return "vraghav@nestacktechnology.com"
        elif "nestacktech" in b or "nestacktech" in au:
            return "vraghavan@nestacktech.com"
        else:
            return "vraghavan@nestack.com"

    # Recruiting
    if "recruiting" in b or "recruiting" in au:
        return "recruiting@nestack.com"

    # R Chandran (biz vs info)
    if "rchandran" in b or "rchandran" in au:
        if "biz" in b:
            return "rchandran@nestack.biz"
        elif "info" in b:
            return "rchandran@nestack.info"

    # Rahul (co.in vs technology vs nestack-tech)
    if "rahul" in b or "rahul" in au:
        if "co_in" in b:
            return "rahul@nestack.co.in"
        elif "nestack_tech" in b:
            return "rahul@nestack-tech.com"
        elif "nestaktechnology" in b or "nestacktechnology" in b:
            return "rahul@nestaktechnology.com"

    # Madhava Reddy (nestack-tech vs nestacktech)
    if "madhava" in b or "machava" in b or "madhava" in au:
        if "nestack_tech" in b or "madhava_tech" in b or "madhava-tech" in (batch or "").lower():
            return "madhava.reddy@nestack-tech.com"
        elif "nestacktech" in b or "tech_nestack" in b or "madhava_reddy_sep" in b:
            return "madhava.reddy@nestacktech.com"

    return ""


def _load_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _normalise_email(s: str) -> str:
    return str(s or "").strip().lower()


def _login_matches(candidate: str, target: str) -> bool:
    """Exact or clean login match preventing cross-account bleed."""
    c = _normalise_email(candidate)
    t = _normalise_email(target)
    if not c or not t:
        return False
    if c == t:
        return True
    # Strip parameters like tags: 'vraghavan@nestack.com(aug 20 - sep 20)' -> 'vraghavan@nestack.com'
    c_clean = c.split("(")[0].strip()
    if c_clean == t:
        return True
    return False


def _pct(part: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{part / total * 100:.1f}%"


def _fmt_date(raw) -> str:
    if not raw:
        return "--"
    s = str(raw)
    return s[:16] if len(s) >= 16 else s


# -----------------------------------------------------------------
# STAGE 1: APOLLO API ENRICHMENT LEDGER (batch_enrichment_ledger)
# -----------------------------------------------------------------

def _stage1_apollo(conn, login_email: str) -> Dict[str, Any]:
    result = {
        "total_attempts": 0, "email_found": 0, "no_email": 0,
        "no_match": 0, "api_error": 0, "credits_charged": 0,
        "wasted": 0, "first_date": None, "last_date": None,
        "batches": [], "error": None,
    }
    try:
        target = login_email.lower().strip()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT outcome, SUM(credits_charged), COUNT(*),
                       MIN(attempted_at), MAX(attempted_at), batch
                FROM batch_enrichment_ledger
                WHERE LOWER(login_email) = %s
                GROUP BY outcome, batch
                ORDER BY MIN(attempted_at) ASC
            """, (target,))
            rows = cur.fetchall()

            batch_map = defaultdict(lambda: {
                "batch": "", "email_found": 0, "no_email": 0,
                "no_match": 0, "api_error": 0, "credits": 0,
                "first": None, "last": None
            })
            for (outcome, cred, cnt, first_at, last_at, batch) in rows:
                cnt = int(cnt or 0); cred = int(cred or 0)
                result["total_attempts"]   += cnt
                result["credits_charged"]  += cred
                outcome = str(outcome or "")
                result[outcome] = result.get(outcome, 0) + cnt
                if not result["first_date"] or (first_at and str(first_at) < str(result["first_date"])):
                    result["first_date"] = first_at
                if not result["last_date"] or (last_at and str(last_at) > str(result["last_date"])):
                    result["last_date"] = last_at
                bk = str(batch or "unknown")
                batch_map[bk]["batch"] = bk
                batch_map[bk][outcome] = batch_map[bk].get(outcome, 0) + cnt
                batch_map[bk]["credits"] += cred
                if not batch_map[bk]["first"] or (first_at and str(first_at) < str(batch_map[bk]["first"])):
                    batch_map[bk]["first"] = first_at
                if not batch_map[bk]["last"] or (last_at and str(last_at) > str(batch_map[bk]["last"])):
                    batch_map[bk]["last"] = last_at

            result["batches"] = sorted(batch_map.values(), key=lambda b: str(b.get("first") or ""))
            result["wasted"]  = result.get("no_email", 0) + result.get("no_match", 0)
    except Exception as exc:
        result["error"] = str(exc)
    return result


# -----------------------------------------------------------------
# STAGE 2: APOLLO SAVED LEADS DB (apollo_saved_leads)
# -----------------------------------------------------------------

def _stage1b_saved_leads(conn, login_email: str) -> Dict[str, Any]:
    result = {
        "total_saved": 0, "enriched": 0, "verified_emails": 0,
        "credits_charged": 0, "total_accounts": 0, "total_domains": 0,
        "first_saved": None, "last_saved": None,
        "batches": [], "error": None,
    }
    try:
        target = login_email.lower().strip()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT batch, account_used,
                       COUNT(*),
                       SUM(CASE WHEN enriched_at IS NOT NULL THEN 1 ELSE 0 END),
                       SUM(CASE WHEN email_status = 'verified' THEN 1 ELSE 0 END),
                       SUM(COALESCE(credits_charged, 0)),
                       COUNT(DISTINCT NULLIF(company, '')),
                       COUNT(DISTINCT NULLIF(company_domain, '')),
                       MIN(created_at), MAX(created_at)
                FROM apollo_saved_leads
                GROUP BY batch, account_used
                ORDER BY MIN(created_at) ASC
            """)
            all_rows = cur.fetchall()

        all_companies = set()
        all_domains = set()

        for (batch, acct, total, enriched, verified, credits, u_comps, u_doms, first_at, last_at) in all_rows:
            batch_str = str(batch or "")
            acct_str  = str(acct or "")
            # Verify batch strictly belongs to this target account
            if get_account_for_batch(batch_str, acct_str) != target:
                continue

            total    = int(total or 0); enriched  = int(enriched or 0)
            verified = int(verified or 0); credits = int(credits or 0)
            u_comps  = int(u_comps or 0); u_doms = int(u_doms or 0)
            result["total_saved"]      += total
            result["enriched"]         += enriched
            result["verified_emails"]  += verified
            result["credits_charged"]  += credits
            result["total_accounts"]   += u_comps
            result["total_domains"]    += u_doms
            if not result["first_saved"] or (first_at and str(first_at) < str(result["first_saved"])):
                result["first_saved"] = first_at
            if not result["last_saved"] or (last_at and str(last_at) > str(result["last_saved"])):
                result["last_saved"] = last_at
            result["batches"].append({
                "batch": batch_str, "account_used": acct_str,
                "total": total, "enriched": enriched, "verified": verified,
                "credits": credits, "accounts": u_comps, "domains": u_doms,
                "first": _fmt_date(first_at), "last": _fmt_date(last_at),
            })
    except Exception as exc:
        result["error"] = str(exc)
    return result


# -----------------------------------------------------------------
# STAGE 3: ENRICH.SO WEB ENRICHMENT (enrich_saved_leads)
# -----------------------------------------------------------------

def _stage3_enrich_so(conn, login_email: str) -> Dict[str, Any]:
    result = {"batches": [], "total_saved": 0, "enriched": 0, "verified_emails": 0, "credits": 0}
    try:
        target = login_email.lower().strip()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT batch, account_used,
                       COUNT(*),
                       SUM(CASE WHEN enriched_at IS NOT NULL THEN 1 ELSE 0 END),
                       SUM(CASE WHEN email_status = 'verified' THEN 1 ELSE 0 END),
                       SUM(COALESCE(credits_charged, 0)),
                       MIN(created_at), MAX(created_at)
                FROM enrich_saved_leads
                GROUP BY batch, account_used
                ORDER BY MIN(created_at) ASC
            """)
            erows = cur.fetchall()

        for (batch, acct, total, enriched, verified, credits, first_at, last_at) in erows:
            batch_str = str(batch or "")
            acct_str  = str(acct or "")
            if acct_str.lower() != target and get_account_for_batch(batch_str, acct_str) != target:
                continue
            t = int(total or 0); e = int(enriched or 0)
            v = int(verified or 0); c = int(credits or 0)
            result["total_saved"]     += t
            result["enriched"]        += e
            result["verified_emails"] += v
            result["credits"]         += c
            result["batches"].append({
                "batch": batch_str, "total": t, "enriched": e,
                "verified": v, "credits": c, "first": _fmt_date(first_at), "last": _fmt_date(last_at)
            })
    except Exception:
        pass
    return result


# -----------------------------------------------------------------
# STAGE 4: MILLIONVERIFIER BULK VERIFICATION
# -----------------------------------------------------------------

def _stage4_millionverifier(login_email: str) -> Dict[str, Any]:
    result = {"jobs": [], "total_sent": 0, "good": 0, "bad": 0, "risky": 0, "good_files": []}
    target = login_email.lower().strip()
    user_part = target.split("@")[0].replace(".", "_")

    # 1. From millionverifier_jobs.json
    jobs_data = _load_json(MV_JOBS_PATH) or []
    for job in jobs_data:
        login = str(job.get("login", "") or job.get("account_name", "")).strip().lower()
        file_id = str(job.get("file_id", "")).lower()

        # Prevent cross matching between vijay_raghavan and vraghavan
        if "vijay" in file_id and "vijay" not in target:
            continue
        if ("vraghavan" in target or "varaghavan" in target) and "vijay" in file_id:
            continue

        if _login_matches(login, target) or f"local_{user_part}_" in file_id:
            result["jobs"].append(job)
            result["total_sent"] += int(job.get("total_rows", 0) or 0)
            result["good"]       += int(job.get("good_count", 0) or 0)
            result["bad"]        += int(job.get("bad_count", 0) or 0)
            result["risky"]      += int(job.get("risky_count", 0) or 0)
            gcsv = job.get("good_csv_path", "")
            if gcsv and Path(gcsv).exists() and Path(gcsv).resolve() not in result["good_files"]:
                result["good_files"].append(Path(gcsv).resolve())

    # 2. Check cache directory
    if MV_CACHE_DIR.exists():
        dom_slug = (target.split("@")[1].replace(".", "_").replace("-", "_") if "@" in target else "")
        for cat_dir in MV_CACHE_DIR.iterdir():
            if not cat_dir.is_dir():
                continue
            dn = cat_dir.name.lower()
            if "vijay" in dn and "vijay" not in target:
                continue
            if ("vraghavan" in target or "varaghavan" in target) and "vijay" in dn:
                continue

            if user_part in dn and (dom_slug in dn or "nestack" in dn):
                good_dir = cat_dir / "good"
                if good_dir.exists():
                    for gf in good_dir.glob("*.csv"):
                        if gf.resolve() not in result["good_files"]:
                            result["good_files"].append(gf.resolve())
                        m_good  = re.search(r"(\d+)\s+good",  gf.name.lower())
                        m_total = re.search(r"(\d+)\s+total", gf.name.lower())
                        if m_good and result["good"] == 0:
                            result["good"] = int(m_good.group(1))
                        if m_total and result["total_sent"] == 0:
                            result["total_sent"] = int(m_total.group(1))

    return result


# -----------------------------------------------------------------
# STAGE 5: FRESHSALES CRM SYNC LEDGER (DEDUPLICATED + ACCOUNTS)
# -----------------------------------------------------------------

def _stage5_freshsales(login_email: str) -> Dict[str, Any]:
    """
    Parses freshsales_synced_batches.json and correctly de-duplicates retry files.
    Extracts Accounts Created (Companies) and Unique Corporate Domains from audit logs.
    """
    result = {
        "syncs": [],
        "total_pushed": 0,
        "created": 0,
        "updated": 0,
        "tld_blocked": 0,
        "accounts_created": 0,
        "domains_created": 0,
        "sample_accounts": [],
        "tags": []
    }
    ledger = _load_json(LEDGER_PATH) or {}
    target = login_email.lower().strip()

    all_created_companies: Dict[str, str] = {}
    all_created_domains = set()
    sample_accounts_list: List[Dict[str, str]] = []

    for key, entry in ledger.items():
        tag = entry.get("tag", "")
        stem = str(entry.get("file_stem", "")).lower()

        # Strict account matching
        tag_email = tag.split("(")[0].strip().lower()
        if tag_email != target:
            # Check stem
            user_part = target.split("@")[0].replace(".", "_")
            if f"{user_part}_nestack" not in stem:
                continue
            if "vijay" in stem and "vijay" not in target:
                continue
            if ("vraghavan" in target or "varaghavan" in target) and "vijay" in stem:
                continue

        # Extract accounts and domains for this sync entry
        src_path = Path(entry.get("file_path", ""))
        aud_path = Path(entry.get("audit_file", ""))
        entry_comps = set()
        entry_doms  = set()

        if src_path.exists() and aud_path.exists():
            actions = {}
            with open(aud_path, "r", encoding="utf-8", errors="replace") as fp:
                for r in csv.DictReader(fp):
                    actions[r.get("email", "").strip().lower()] = r.get("action", "")

            with open(src_path, "r", encoding="utf-8", errors="replace") as fp:
                for r in csv.DictReader(fp):
                    e = r.get("Email", "").strip().lower()
                    if actions.get(e) == "created":
                        comp = (r.get("Company Name") or r.get("Company Name for Emails") or r.get("Account") or "").strip()
                        dom = e.split("@")[1] if "@" in e else ""
                        if comp:
                            entry_comps.add(comp.lower())
                            if comp not in all_created_companies:
                                all_created_companies[comp] = dom
                            if len(sample_accounts_list) < 15 and not any(s["company"] == comp for s in sample_accounts_list):
                                sample_accounts_list.append({"company": comp, "domain": dom})
                        if dom:
                            entry_doms.add(dom.lower())
                            all_created_domains.add(dom.lower())

        entry_copy = dict(entry)
        entry_copy["accounts_created"] = len(entry_comps)
        entry_copy["domains_created"]  = len(entry_doms)
        result["syncs"].append(entry_copy)

        is_retry = "_unsynced_retry_" in key.lower() or "_retry_" in key.lower()
        # Only add total_leads from the primary base run to prevent double-counting input leads
        if not is_retry:
            result["total_pushed"] += int(entry.get("total_leads", 0) or 0)

        # Net outcomes (created/updated/tld) are distinct operations that add up across retries
        result["created"]     += int(entry.get("created", 0) or 0)
        result["updated"]     += int(entry.get("updated", 0) or 0)
        result["tld_blocked"] += int(entry.get("tld_blocked", 0) or 0)

        if tag and tag not in result["tags"]:
            result["tags"].append(tag)

    result["accounts_created"] = len(all_created_companies)
    result["domains_created"]  = len(all_created_domains)
    result["sample_accounts"]  = sample_accounts_list

    # Export full accounts CSV if accounts were created
    if all_created_companies:
        safe_login = re.sub(r'[^a-zA-Z0-9_]', '_', login_email)
        out_csv = FS_REPORTS_DIR / f"{safe_login}_created_accounts.csv"
        try:
            FS_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            with open(out_csv, "w", newline="", encoding="utf-8") as out_fp:
                writer = csv.writer(out_fp)
                writer.writerow(["Index", "Company Account Name", "Corporate Domain"])
                for idx, (comp, dom) in enumerate(sorted(all_created_companies.items()), 1):
                    writer.writerow([idx, comp, dom])
            result["accounts_csv_path"] = str(out_csv)
        except Exception:
            pass

    return result


# -----------------------------------------------------------------
# STAGE 5b: FILE RUN METRICS
# -----------------------------------------------------------------

def _stage5b_file_metrics(conn, login_email: str) -> List[Dict]:
    runs = []
    try:
        target = login_email.lower().strip()
        user_part = target.split("@")[0]
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, source_file, owner, tag, input, tld_block,
                       created, data, failed, success_percentage,
                       wastage_percentage, status, timestamp
                FROM file_run_metrics
                WHERE LOWER(owner) = %s OR LOWER(tag) LIKE %s OR LOWER(source_file) LIKE %s
                ORDER BY id ASC
            """, (target, f"%{target}%", f"%{user_part}%"))
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                r = dict(zip(cols, row))
                sf = str(r.get("source_file", "")).lower()
                # Exclude cross-pollination
                if "vijay" in sf and "vijay" not in target:
                    continue
                runs.append(r)
    except Exception:
        pass
    return runs


# -----------------------------------------------------------------
# STAGE 6: FRESHSALES LIVE CRM DAILY COUNTS
# -----------------------------------------------------------------

def _stage6_freshsales_live(login_email: str) -> Dict[str, Any]:
    result = {"by_date": {}, "total_created": 0, "total_updated": 0, "available": False}
    cache_path = _ROOT / "scratch" / "freshsales_daily_breakdown.json"
    if not cache_path.exists():
        return result
    try:
        data = _load_json(cache_path)
        by_login = data.get("by_login", {}) if isinstance(data, dict) else {}
        target = login_email.lower().strip()
        for key, daily in by_login.items():
            if _login_matches(key, target):
                result["by_date"]   = daily
                result["available"] = True
                for dt, counts in daily.items():
                    result["total_created"] += int(counts.get("created", 0))
                    result["total_updated"] += int(counts.get("updated", 0))
                break
    except Exception:
        pass
    return result


# -----------------------------------------------------------------
# LOGIN DISCOVERY
# -----------------------------------------------------------------

def _load_all_logins(conn) -> List[Dict[str, Any]]:
    logins: Dict[str, Dict] = {}

    def _add(email: str, name: str = "", source: str = ""):
        e = _normalise_email(email)
        if not e or "@" not in e:
            return
        if e not in logins:
            logins[e] = {"email": e, "name": name, "sources": []}
        if source and source not in logins[e]["sources"]:
            logins[e]["sources"].append(source)
        if name and not logins[e]["name"]:
            logins[e]["name"] = name

    # 1. Accounts config
    accts_data = _load_json(_ROOT / "config" / "apollo_accounts.json")
    if isinstance(accts_data, list):
        for a in accts_data:
            _add(a.get("email", ""), a.get("name", ""), "apollo_accounts")
    elif isinstance(accts_data, dict):
        for a in accts_data.values():
            if isinstance(a, dict):
                _add(a.get("email", ""), a.get("name", ""), "apollo_accounts")

    # 2. Enrichment ledger
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT login_email, account_name FROM batch_enrichment_ledger")
            for (le, an) in cur.fetchall():
                _add(le, an, "ledger")
    except Exception:
        pass

    # 3. MillionVerifier jobs
    for j in (_load_json(MV_JOBS_PATH) or []):
        _add(j.get("login", ""), j.get("account_name", ""), "millionverifier")

    # 4. Freshsales ledger
    for entry in (_load_json(LEDGER_PATH) or {}).values():
        tag = entry.get("tag", "")
        m = re.match(r"^([^(]+)", tag)
        if m:
            _add(m.group(1).strip(), "", "freshsales")

    return sorted(logins.values(), key=lambda x: x["email"])


# -----------------------------------------------------------------
# BEAUTIFUL RICH LOGIN SELECTION MENU
# -----------------------------------------------------------------

def prompt_login_selection(conn) -> Optional[str]:
    """Display all known logins in a polished Rich Table and prompt for selection."""
    logins = _load_all_logins(conn)
    if not logins:
        console.print("[bold red]No logins found.[/bold red] Run enrichment or sync at least one batch first.")
        return None

    table = Table(
        title="[bold bright_white]APOLLO ACCOUNTS DIRECTORY[/bold bright_white]\n[dim]Select an account to view full credit & pipeline audit[/dim]",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="bright_blue",
        show_lines=False
    )

    table.add_column("#", justify="right", style="bold yellow")
    table.add_column("Login Email", style="bold bright_white", no_wrap=False)
    table.add_column("Account Owner", style="white", no_wrap=False)
    table.add_column("Active Channels", justify="left")

    for idx, L in enumerate(logins, 1):
        badges = []
        for s in L["sources"]:
            if s == "apollo_accounts":
                badges.append("[cyan]Apollo[/cyan]")
            elif s == "ledger":
                badges.append("[yellow]DB[/yellow]")
            elif s == "millionverifier":
                badges.append("[magenta]MV[/magenta]")
            elif s == "freshsales":
                badges.append("[green]CRM[/green]")
            else:
                badges.append(f"[dim]{s}[/dim]")
        src_str = " • ".join(badges) if badges else "[dim]--[/dim]"
        name_str = L["name"] if L["name"] else "[dim]--[/dim]"
        table.add_row(str(idx), L["email"], name_str, src_str)

    console.print()
    console.print(table)

    while True:
        sel = console.input(f"\n  [bold cyan]Select account[/bold cyan] [1-{len(logins)}] or enter email (Enter = cancel): ").strip()
        if not sel:
            return None
        if sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(logins):
                return logins[idx]["email"]
            console.print("  [red]Invalid selection number.[/red]")
        elif "@" in sel:
            return sel.strip().lower()
        else:
            console.print("  [red]Please enter a valid number from the list or a full email address.[/red]")


# -----------------------------------------------------------------
# MAIN AUDIT PRINTER (RICH DASHBOARD)
# -----------------------------------------------------------------

def run_login_audit(conn, login_email: Optional[str] = None) -> None:
    """Full pipeline audit for a selected login rendered as an accurate, balanced Rich dashboard."""
    if not login_email:
        login_email = prompt_login_selection(conn)
    if not login_email:
        console.print("[dim]Audit cancelled.[/dim]")
        return

    login_email = login_email.lower().strip()

    with console.status(f"[bold cyan]Gathering telemetry for [bold yellow]{login_email}[/bold yellow]...[/bold cyan]", spinner="dots"):
        s1         = _stage1_apollo(conn, login_email)
        s1b        = _stage1b_saved_leads(conn, login_email)
        s1c_file   = _stage5b_file_metrics(conn, login_email)
        s3_enrich  = _stage3_enrich_so(conn, login_email)
        s4_mv      = _stage4_millionverifier(login_email)
        s5_fs      = _stage5_freshsales(login_email)
        s6_live    = _stage6_freshsales_live(login_email)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. HEADER BANNER
    header_text = Text()
    header_text.append("APOLLO LEAD & PIPELINE CREDIT AUDIT\n", style="bold bright_white")
    header_text.append("Account: ", style="dim")
    header_text.append(login_email, style="bold yellow")
    header_text.append("   •   Generated: ", style="dim")
    header_text.append(now_str, style="cyan")

    console.print()
    console.print(Panel(Align.center(header_text), box=box.ROUNDED, border_style="cyan", padding=(0, 2)))

    # Metrics computation
    db_saved_leads   = s1b["total_saved"]
    api_enriched     = s1b["enriched"]
    unenriched_count = max(0, db_saved_leads - api_enriched)
    db_accounts      = s1b["total_accounts"]
    db_domains       = s1b["total_domains"]
    web_enriched     = s3_enrich["total_saved"]

    credits_charged  = s1["credits_charged"]
    email_found      = s1["email_found"]
    credits_wasted   = s1["wasted"]

    total_mv_sent    = s4_mv["total_sent"]
    total_mv_good    = s4_mv["good"]
    total_mv_bad     = s4_mv["bad"]
    total_mv_risky   = s4_mv["risky"]

    total_fs_pushed   = s5_fs["total_pushed"]
    total_fs_created  = s5_fs["created"]
    total_fs_accounts = s5_fs["accounts_created"]
    total_fs_domains  = s5_fs["domains_created"]
    total_fs_updated  = s5_fs["updated"]
    total_fs_tld      = s5_fs["tld_blocked"]

    # 2. EXECUTIVE 2x2 KPI CARDS GRID
    kpi_table = Table(box=box.ROUNDED, border_style="bright_blue", expand=True, show_header=False)
    kpi_table.add_column("Left", ratio=1)
    kpi_table.add_column("Right", ratio=1)

    card1 = (
        f"[bold cyan]STAGE 1: APOLLO INGESTION & EXTRACTION[/bold cyan]\n"
        f"  Apollo DB Leads Saved: [bold white]{db_saved_leads:,}[/bold white]\n"
        f"  Target Accounts (Companies): [bold bright_white]{db_accounts:,}[/bold bright_white] ({db_domains:,} Domains)\n"
        f"  API Enriched: [bold green]{api_enriched:,}[/bold green] ({_pct(api_enriched, db_saved_leads)})\n"
        f"  Web Enriched (Enrich.so): [bold bright_green]{web_enriched:,}[/bold bright_green]\n"
        f"  Pending Unenriched: [bold yellow]{unenriched_count:,}[/bold yellow]"
    )
    card2 = (
        f"[bold yellow]STAGE 1b: CREDIT UTILIZATION & BURN[/bold yellow]\n"
        f"  Apollo Credits Charged: [bold yellow]{credits_charged:,}[/bold yellow]\n"
        f"  Email Reveals (Found): [bold green]{email_found:,}[/bold green]\n"
        f"  Wasted (No Email / Match): [bold red]{credits_wasted:,}[/bold red] ({_pct(credits_wasted, credits_charged or 1)})\n"
        f"  Burn Efficiency: [bold green]{_pct(email_found, credits_charged or 1)}[/bold green]"
    )
    card3 = (
        f"[bold magenta]STAGE 2: MILLIONVERIFIER QUALITY[/bold magenta]\n"
        f"  Submitted to Verifier: [bold white]{total_mv_sent:,}[/bold white]\n"
        f"  Deliverable (Good): [bold green]{total_mv_good:,}[/bold green] ({_pct(total_mv_good, total_mv_sent)})\n"
        f"  Risky (Catch-all / Unknown): [yellow]{total_mv_risky:,}[/yellow] ({_pct(total_mv_risky, total_mv_sent)})\n"
        f"  Bad (Bounced / Invalid): [red]{total_mv_bad:,}[/red] ({_pct(total_mv_bad, total_mv_sent)})"
    )
    card4 = (
        f"[bold green]STAGE 3: FRESHSALES CRM SYNC[/bold green]\n"
        f"  Clean Leads Pushed: [bold white]{total_fs_pushed:,}[/bold white]\n"
        f"  Contacts Created: [bold green]+{total_fs_created:,}[/bold green] ({_pct(total_fs_created, total_fs_pushed)})\n"
        f"  Accounts Created (Companies): [bold bright_green]{total_fs_accounts:,}[/bold bright_green]\n"
        f"  Corporate Domains: [bold white]{total_fs_domains:,}[/bold white]\n"
        f"  Existing Updated: [cyan]↺ {total_fs_updated:,}[/cyan] ({_pct(total_fs_updated, total_fs_pushed)})\n"
        f"  Foreign TLD Filtered: [dim red]{total_fs_tld:,}[/dim red]"
    )

    kpi_table.add_row(card1, card2)
    kpi_table.add_section()
    kpi_table.add_row(card3, card4)
    console.print(kpi_table)

    # 3. SLEEK VISUAL PIPELINE FLOW
    def _d(v): return f"{v:,}" if isinstance(v, int) and v else "--"

    flow_grid = Table.grid(expand=True)
    flow_grid.add_column(justify="center", ratio=2)
    flow_grid.add_column(justify="center", ratio=1)
    flow_grid.add_column(justify="center", ratio=2)
    flow_grid.add_column(justify="center", ratio=1)
    flow_grid.add_column(justify="center", ratio=2)
    flow_grid.add_column(justify="center", ratio=1)
    flow_grid.add_column(justify="center", ratio=2)

    total_pipeline_in = db_saved_leads + web_enriched
    s1_box = f"[bold cyan]1. EXTRACTION[/bold cyan]\n[white]{_d(db_saved_leads)} Apollo Leads[/white]\n[dim]{_d(web_enriched)} Web Leads[/dim]"
    s2_box = f"[bold yellow]2. ENRICHED[/bold yellow]\n[green]{_d(api_enriched + web_enriched)} Enriched[/green]\n[dim]{_d(unenriched_count)} Pending[/dim]"
    s3_box = f"[bold magenta]3. VERIFICATION[/bold magenta]\n[white]{_d(total_mv_sent)} Submitted[/white]\n[green]{_d(total_mv_good)} Good Deliverable[/green]"
    s4_box = (
        f"[bold green]4. CRM SYNC[/bold green]\n"
        f"[white]{_d(total_fs_pushed)} Leads Pushed[/white]\n"
        f"[green]+{_d(total_fs_created)} Contacts[/green]\n"
        f"[bright_green]{_d(total_fs_accounts)} Accounts[/bright_green]"
    )

    flow_grid.add_row(s1_box, "[bold cyan]────►[/bold cyan]", s2_box, "[bold cyan]────►[/bold cyan]", s3_box, "[bold cyan]────►[/bold cyan]", s4_box)
    console.print(Panel(flow_grid, title="[bold bright_white]BALANCED END-TO-END PIPELINE FLOW[/bold bright_white]", box=box.ROUNDED, border_style="cyan"))

    # 4. DETAILED STAGE TABLES

    # STAGE 1: APOLLO API ENRICHMENT LEDGER
    if s1["total_attempts"]:
        t1 = Table(title="[bold cyan]STAGE 1: APOLLO API ENRICHMENT LEDGER (batch_enrichment_ledger)[/bold cyan]", box=box.ROUNDED, border_style="cyan")
        t1.add_column("Batch Identifier", style="bold white", overflow="fold")
        t1.add_column("Email Found", justify="right", style="green")
        t1.add_column("No Email", justify="right", style="yellow")
        t1.add_column("No Match", justify="right", style="red")
        t1.add_column("Credits Charged", justify="right", style="bold yellow")
        t1.add_column("First Attempt", justify="center", style="dim")
        t1.add_column("Last Attempt", justify="center", style="dim")

        for b in s1["batches"]:
            t1.add_row(
                str(b["batch"]),
                f"{b.get('email_found',0):,}",
                f"{b.get('no_email',0):,}",
                f"{b.get('no_match',0):,}",
                f"{b.get('credits',0):,}",
                _fmt_date(b["first"]),
                _fmt_date(b["last"])
            )
        console.print(t1)
    else:
        console.print("[dim]  ℹ  Stage 1 (Enrichment Ledger): No direct API reveal calls logged for this account.[/dim]")

    # STAGE 2: APOLLO SAVED LEADS LEDGER
    if s1b["total_saved"]:
        t2 = Table(title="[bold cyan]STAGE 2: SAVED LEADS DATABASE (apollo_saved_leads)[/bold cyan]", box=box.ROUNDED, border_style="cyan")
        t2.add_column("Batch Identifier", style="bold white", overflow="fold")
        t2.add_column("Total Saved", justify="right", style="bold white")
        t2.add_column("Accounts / Domains", justify="center", style="cyan")
        t2.add_column("Enriched", justify="right", style="green")
        t2.add_column("Verified Status", justify="right", style="bright_green")
        t2.add_column("Credits", justify="right", style="bold yellow")
        t2.add_column("First Seen", justify="center", style="dim")
        t2.add_column("Last Seen", justify="center", style="dim")

        for b in s1b["batches"]:
            t2.add_row(
                str(b["batch"]),
                f"{b['total']:,}",
                f"{b.get('accounts',0):,} / {b.get('domains',0):,}",
                f"{b['enriched']:,}",
                f"{b['verified']:,}",
                f"{b['credits']:,}",
                str(b["first"]),
                str(b["last"])
            )
        console.print(t2)
    else:
        console.print("[dim]  ℹ  Stage 2 (Saved Leads DB): No saved leads records found for this account.[/dim]")

    # STAGE 3: ENRICH.SO WEB ENRICHMENT
    if s3_enrich["total_saved"]:
        t3 = Table(title="[bold cyan]STAGE 3: ENRICH.SO WEB ENRICHMENT (enrich_saved_leads)[/bold cyan]", box=box.ROUNDED, border_style="cyan")
        t3.add_column("Batch Identifier", style="bold white", overflow="fold")
        t3.add_column("Total Leads", justify="right", style="white")
        t3.add_column("Enriched", justify="right", style="green")
        t3.add_column("Verified Status", justify="right", style="bright_green")
        t3.add_column("Apollo Credits", justify="right", style="bold yellow")
        t3.add_column("First Seen", justify="center", style="dim")
        t3.add_column("Last Seen", justify="center", style="dim")

        for b in s3_enrich["batches"]:
            t3.add_row(
                str(b["batch"]),
                f"{b['total']:,}",
                f"{b['enriched']:,}",
                f"{b['verified']:,}",
                f"{b['credits']:,}",
                str(b["first"]),
                str(b["last"])
            )
        console.print(t3)
    else:
        console.print("[dim]  ℹ  Stage 3 (Enrich.so Web Saves): No Enrich.so web records found for this account.[/dim]")

    # STAGE 4: MILLIONVERIFIER
    if s4_mv["total_sent"] or s4_mv["jobs"] or s4_mv["good_files"]:
        t4 = Table(title="[bold cyan]STAGE 4: MILLIONVERIFIER VERIFICATION RUNS[/bold cyan]", box=box.ROUNDED, border_style="cyan")
        t4.add_column("Job File ID", style="bold white", overflow="fold")
        t4.add_column("Status", style="bold green")
        t4.add_column("Total Sent", justify="right", style="white")
        t4.add_column("Good (Deliverable)", justify="right", style="bold green")
        t4.add_column("Risky (Catch-All)", justify="right", style="yellow")
        t4.add_column("Bad (Bounce)", justify="right", style="red")
        t4.add_column("Created", justify="center", style="dim")

        for j in s4_mv["jobs"]:
            t4.add_row(
                str(j.get("file_id", "")),
                str(j.get("status", "ok")),
                f"{int(j.get('total_rows',0) or 0):,}",
                f"{int(j.get('good_count',0) or 0):,}",
                f"{int(j.get('risky_count',0) or 0):,}",
                f"{int(j.get('bad_count',0) or 0):,}",
                str(j.get("created_at", ""))[:16]
            )
        console.print(t4)

        if s4_mv["good_files"]:
            gf_table = Table(title="[dim]Exported Clean Deliverable Files on Disk[/dim]", box=box.SIMPLE, show_header=True, header_style="bold green")
            gf_table.add_column("Deliverable CSV Filename", style="green", overflow="fold")
            gf_table.add_column("File Size", justify="right", style="dim")
            for gf in s4_mv["good_files"]:
                size_kb = int(gf.stat().st_size / 1024) if gf.exists() else 0
                gf_table.add_row(gf.name, f"{size_kb:,} KB")
            console.print(gf_table)
    else:
        console.print("[dim]  ℹ  Stage 4 (MillionVerifier): No batch verification jobs logged for this account.[/dim]")

    # STAGE 5: FRESHSALES CRM SYNC LEDGER
    if s5_fs["syncs"]:
        t5 = Table(title="[bold cyan]STAGE 5: FRESHSALES CRM SYNC LEDGER (Deduplicated Net History)[/bold cyan]", box=box.ROUNDED, border_style="cyan")
        t5.add_column("Synced Source File / Pass", style="bold white", overflow="fold")
        t5.add_column("Batch Input", justify="right", style="white")
        t5.add_column("Contacts Created", justify="right", style="bold green")
        t5.add_column("Accounts Created", justify="right", style="bold bright_green")
        t5.add_column("Unique Domains", justify="right", style="cyan")
        t5.add_column("Updated", justify="right", style="bold cyan")
        t5.add_column("TLD Blocked", justify="right", style="dim red")
        t5.add_column("Synced At", justify="center", style="dim")

        for entry in s5_fs["syncs"]:
            is_retry = "_unsynced_retry_" in str(entry.get("file_stem", "")).lower()
            pass_label = " (Retry Pass)" if is_retry else " (Primary Pass)"
            stem_display = str(entry.get("file_stem", "")) + pass_label
            t5.add_row(
                stem_display,
                f"{int(entry.get('total_leads',0)):,}",
                f"{int(entry.get('created',0)):,}",
                f"{int(entry.get('accounts_created',0)):,}",
                f"{int(entry.get('domains_created',0)):,}",
                f"{int(entry.get('updated',0)):,}",
                f"{int(entry.get('tld_blocked',0)):,}",
                str(entry.get("synced_at", ""))[:16]
            )
        console.print(t5)

        if s5_fs["sample_accounts"]:
            sa_table = Table(title="[bold bright_white]Sample Company Accounts Created in Freshsales CRM (First 15)[/bold bright_white]", box=box.SIMPLE, show_header=True, header_style="bold green")
            sa_table.add_column("#", justify="right", style="dim yellow", width=4)
            sa_table.add_column("Company Account Name", style="white")
            sa_table.add_column("Corporate Domain", style="cyan")
            for idx, item in enumerate(s5_fs["sample_accounts"], 1):
                sa_table.add_row(str(idx), item.get("company", ""), item.get("domain", ""))
            console.print(sa_table)

            if s5_fs.get("accounts_csv_path"):
                console.print(f"[dim]  📁 Full list of [bold green]{s5_fs['accounts_created']:,}[/bold green] created company accounts saved to: [cyan]{s5_fs['accounts_csv_path']}[/cyan][/dim]\n")
    else:
        console.print("[dim]  ℹ  Stage 5 (Freshsales Sync Ledger): No synced batch entries recorded in config ledger.[/dim]")

    # STAGE 5b: FILE RUN METRICS
    if s1c_file:
        t5b = Table(title="[bold cyan]STAGE 5b: FRESHSALES FILE EXECUTION AUDIT (file_run_metrics)[/bold cyan]", box=box.ROUNDED, border_style="cyan")
        t5b.add_column("#", justify="right", style="yellow")
        t5b.add_column("Source File", style="white", overflow="fold")
        t5b.add_column("Input", justify="right", style="white")
        t5b.add_column("New", justify="right", style="bold green")
        t5b.add_column("Upd", justify="right", style="bold cyan")
        t5b.add_column("TLD", justify="right", style="dim red")
        t5b.add_column("Failed", justify="right", style="red")
        t5b.add_column("Success%", justify="right", style="bold green")
        t5b.add_column("Timestamp", justify="center", style="dim")

        for r in s1c_file:
            t5b.add_row(
                str(r.get("id", "")),
                str(r.get("source_file", "")),
                f"{int(r.get('input',0) or 0):,}",
                f"{int(r.get('created',0) or 0):,}",
                f"{int(r.get('data',0) or 0):,}",
                f"{int(r.get('tld_block',0) or 0):,}",
                f"{int(r.get('failed',0) or 0):,}",
                f"{float(r.get('success_percentage',0) or 0):.1f}%",
                str(r.get("timestamp", ""))[:16]
            )
        console.print(t5b)

    # STAGE 6: FRESHSALES LIVE ACTIVITY
    if s6_live["available"] and s6_live["by_date"]:
        t6 = Table(title="[bold cyan]STAGE 6: FRESHSALES CRM LIVE ACTIVITY (Daily Ingestion Scan)[/bold cyan]", box=box.ROUNDED, border_style="cyan")
        t6.add_column("Activity Date", style="white", justify="center")
        t6.add_column("New Contacts Created", justify="right", style="bold green")
        t6.add_column("Contacts Updated", justify="right", style="bold cyan")
        t6.add_column("Operation Pattern", style="bold yellow")

        for dt in sorted(s6_live["by_date"].keys()):
            c = int(s6_live["by_date"][dt].get("created", 0))
            u = int(s6_live["by_date"][dt].get("updated", 0))
            note = ""
            if c >= 1000:
                note = "[bold green]★ BULK INGESTION[/bold green]"
            elif u >= 1000:
                note = "[cyan]↺ DEDUP / UPDATE PASS[/cyan]"
            elif u >= 500:
                note = "[dim cyan]↺ BATCH REFRESH[/dim cyan]"
            elif c > 0 or u > 0:
                note = "[dim]Standard Sync[/dim]"
            else:
                continue

            t6.add_row(dt, f"{c:,}", f"{u:,}", note)
        console.print(t6)

    # 5. ACTIONABLE RECOMMENDATIONS PANEL
    steps = []
    if unenriched_count > 0:
        steps.append(f"[bold yellow]1. Enrich Saved Leads[/bold yellow]  ──►  [white]{unenriched_count:,}[/white] unenriched leads waiting in DB for this login. Run [bold cyan]Manage Batches > Enrich Batch[/bold cyan].")
    if (api_enriched + web_enriched) > total_mv_sent and total_mv_sent > 0:
        gap = (api_enriched + web_enriched) - total_mv_sent
        steps.append(f"[bold yellow]2. Verify Quality[/bold yellow]       ──►  [white]{gap:,}[/white] leads may need verification. Run [bold cyan]Option [8][/bold cyan].")
    if total_mv_good > total_fs_pushed and total_mv_good > 0:
        gap = total_mv_good - total_fs_pushed
        steps.append(f"[bold yellow]3. Push to Freshsales[/bold yellow]   ──►  [white]{gap:,}[/white] deliverable good leads ready for CRM. Run [bold cyan]Option [F][/bold cyan].")
    if not steps:
        steps.append("[bold green]✔ All pipeline stages are completely synchronized and consistent for this account.[/bold green]")

    rec_panel = Panel(
        "\n".join(f"  {s}" for s in steps),
        title="[bold green]RECOMMENDED NEXT PIPELINE ACTIONS[/bold green]",
        box=box.ROUNDED,
        border_style="green",
        padding=(1, 1)
    )
    console.print(rec_panel)
    console.print()


# -----------------------------------------------------------------
# STANDALONE ENTRY POINT
# -----------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Apollo Login Credit & Pipeline Audit")
    parser.add_argument("--login", "-l", help="Directly audit a specific login email", default=None)
    args = parser.parse_args()

    from backend.api import get_connection
    with get_connection() as _conn:
        run_login_audit(_conn, login_email=args.login)
