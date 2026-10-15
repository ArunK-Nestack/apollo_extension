"""Persistent orchestration primitives for the operator-controlled CyclePilot agent."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS_PATH = PROJECT_ROOT / "config" / "apollo_accounts.json"
SEARCHES_PATH = PROJECT_ROOT / "config" / "saved_searches.json"
ACTIVE_STATUSES = {"planned", "profiling", "ready_for_enrichment", "enriching", "awaiting_verification"}
_workers: dict[str, threading.Thread] = {}
_workers_guard = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_accounts() -> list[dict[str, Any]]:
    if not ACCOUNTS_PATH.exists():
        return []
    rows = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("accounts", [])
    return [row for row in rows if isinstance(row, dict) and row.get("email")]


def public_accounts() -> list[dict[str, Any]]:
    """Return account metadata without ever serializing an API key."""
    return [
        {"id": row.get("id"), "name": row.get("name", ""), "email": row["email"]}
        for row in load_accounts()
    ]


def find_account(login_email: str) -> dict[str, Any] | None:
    wanted = str(login_email or "").strip().lower()
    return next(
        (row for row in load_accounts() if str(row.get("email", "")).strip().lower() == wanted),
        None,
    )


def load_scoped_searches(login_email: str) -> list[dict[str, Any]]:
    """Load saved searches owned by exactly one login; never fall back to all logins."""
    if not SEARCHES_PATH.exists():
        return []
    data = json.loads(SEARCHES_PATH.read_text(encoding="utf-8"))
    wanted = str(login_email or "").strip().lower()
    if isinstance(data, dict):
        rows = next(
            (value for key, value in data.items() if str(key).strip().lower() == wanted),
            [],
        )
    else:
        owner_keys = ("source_account", "account_email", "login_email")
        rows = [
            row for row in data if isinstance(row, dict)
            and any(str(row.get(key, "")).strip().lower() == wanted for key in owner_keys)
        ]
    result = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("display_name") or f"Search {index + 1}").strip()
        result.append({
            "id": str(row.get("recommendation_config_id") or f"search-{index}"),
            "name": name,
            "display_name": str(row.get("display_name") or name),
            "filters": row.get("filters") or {},
            "is_live": bool(row.get("is_live", True)),
        })
    return result


def calculate_target(credits_remaining: int, reserve: int, existing_ready: int = 0) -> dict[str, int]:
    usable = max(0, int(credits_remaining or 0) - max(0, int(reserve or 0)))
    existing = max(0, min(int(existing_ready or 0), usable))
    return {"usable_credits": usable, "existing_ready": existing, "new_contacts_needed": max(0, usable - existing)}


def make_manifest_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def ensure_schema(conn) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS cyclepilot_runs (
            run_id VARCHAR(36) PRIMARY KEY,
            login_email VARCHAR(255) NOT NULL,
            operator_name VARCHAR(255) NOT NULL DEFAULT 'operator',
            cycle_tag VARCHAR(255) NOT NULL,
            batch_name VARCHAR(255) NOT NULL,
            selected_searches_json LONGTEXT NOT NULL,
            plan_json LONGTEXT NULL,
            status VARCHAR(64) NOT NULL,
            stage VARCHAR(64) NOT NULL,
            progress INT NOT NULL DEFAULT 0,
            credit_reserve INT NOT NULL DEFAULT 0,
            verifier_approved TINYINT(1) NOT NULL DEFAULT 0,
            manifest_hash VARCHAR(64) NULL,
            error_message TEXT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            INDEX idx_cyclepilot_login_status (login_email, status),
            INDEX idx_cyclepilot_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS cyclepilot_events (
            event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            run_id VARCHAR(36) NOT NULL,
            level VARCHAR(16) NOT NULL,
            stage VARCHAR(64) NOT NULL,
            message TEXT NOT NULL,
            details_json LONGTEXT NULL,
            created_at DATETIME NOT NULL,
            INDEX idx_cyclepilot_events_run (run_id, event_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS cyclepilot_login_locks (
            login_email VARCHAR(255) PRIMARY KEY,
            run_id VARCHAR(36) NOT NULL,
            operator_name VARCHAR(255) NOT NULL,
            acquired_at DATETIME NOT NULL,
            heartbeat_at DATETIME NOT NULL,
            expires_at DATETIME NOT NULL,
            INDEX idx_cyclepilot_lock_expiry (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    ]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()


def append_event(conn, run_id: str, level: str, stage: str, message: str, details: dict | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cyclepilot_events
               (run_id, level, stage, message, details_json, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (run_id, level, stage, message, json.dumps(details or {}, default=str), _utc_now()),
        )
    conn.commit()


def acquire_login_lock(conn, login_email: str, run_id: str, operator_name: str) -> bool:
    """Atomically claim a login unless another non-expired run owns it."""
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cyclepilot_login_locks
               (login_email, run_id, operator_name, acquired_at, heartbeat_at, expires_at)
               VALUES (%s, %s, %s, NOW(), NOW(), DATE_ADD(NOW(), INTERVAL 10 MINUTE))
               ON DUPLICATE KEY UPDATE
                 run_id = IF(expires_at < NOW() OR run_id = VALUES(run_id), VALUES(run_id), run_id),
                 operator_name = IF(expires_at < NOW() OR run_id = VALUES(run_id), VALUES(operator_name), operator_name),
                 acquired_at = IF(expires_at < NOW() OR run_id = VALUES(run_id), NOW(), acquired_at),
                 heartbeat_at = IF(expires_at < NOW() OR run_id = VALUES(run_id), NOW(), heartbeat_at),
                 expires_at = IF(expires_at < NOW() OR run_id = VALUES(run_id), DATE_ADD(NOW(), INTERVAL 10 MINUTE), expires_at)
            """,
            (login_email, run_id, operator_name),
        )
        cur.execute("SELECT run_id FROM cyclepilot_login_locks WHERE login_email = %s", (login_email,))
        row = cur.fetchone()
    conn.commit()
    owner = row[0] if not isinstance(row, dict) else row.get("run_id")
    return owner == run_id


def release_login_lock(conn, login_email: str, run_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM cyclepilot_login_locks WHERE login_email = %s AND run_id = %s", (login_email, run_id))
    conn.commit()


def create_run(conn, login_email: str, search_ids: list[str], reserve: int, operator_name: str = "operator") -> dict:
    account = find_account(login_email)
    if not account:
        raise ValueError("Unknown Apollo login")
    catalog = load_scoped_searches(login_email)
    selected = [row for row in catalog if row["id"] in set(search_ids)]
    if not selected:
        raise ValueError("Select at least one saved search owned by this login")

    from scripts.apollo_saved_search_inspector import get_account_cycle_window
    _, _, cycle_tag = get_account_cycle_window(login_email)
    batch_name = f"{login_email.upper()}({cycle_tag})"
    run_id = str(uuid.uuid4())
    now = _utc_now()
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cyclepilot_runs
               (run_id, login_email, operator_name, cycle_tag, batch_name, selected_searches_json,
                status, stage, progress, credit_reserve, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'planned', 'operator_review', 0, %s, %s, %s)""",
            (run_id, login_email, operator_name, cycle_tag, batch_name, json.dumps(selected), max(0, reserve), now, now),
        )
    conn.commit()
    append_event(conn, run_id, "info", "operator_review", "Run created from an operator request.", {
        "login": login_email, "batch": batch_name, "searches": [row["name"] for row in selected]
    })
    return get_run(conn, run_id)


def _decode_run(row: Any, columns: list[str] | None = None) -> dict[str, Any]:
    data = dict(row) if isinstance(row, dict) else dict(zip(columns or [], row))
    for key in ("selected_searches_json", "plan_json"):
        raw = data.pop(key, None)
        data[key.removesuffix("_json")] = json.loads(raw) if raw else ([] if key.startswith("selected") else {})
    for key in ("created_at", "updated_at"):
        if data.get(key) is not None:
            data[key] = str(data[key])
    data["verifier_approved"] = bool(data.get("verifier_approved"))
    return data


def get_run(conn, run_id: str) -> dict[str, Any] | None:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM cyclepilot_runs WHERE run_id = %s", (run_id,))
        columns = [item[0] for item in cur.description]
        row = cur.fetchone()
        if not row:
            return None
        result = _decode_run(row, columns)
        cur.execute(
            """SELECT event_id, level, stage, message, details_json, created_at
               FROM cyclepilot_events WHERE run_id = %s ORDER BY event_id DESC LIMIT 200""",
            (run_id,),
        )
        event_columns = [item[0] for item in cur.description]
        events = []
        for event in cur.fetchall():
            item = dict(event) if isinstance(event, dict) else dict(zip(event_columns, event))
            item["details"] = json.loads(item.pop("details_json") or "{}")
            item["created_at"] = str(item["created_at"])
            events.append(item)
    result["events"] = events
    return result


def list_runs(conn, limit: int = 30) -> list[dict[str, Any]]:
    ensure_schema(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM cyclepilot_runs ORDER BY created_at DESC LIMIT %s", (max(1, min(limit, 100)),))
        columns = [item[0] for item in cur.description]
        return [_decode_run(row, columns) for row in cur.fetchall()]


def update_run(conn, run_id: str, *, status: str, stage: str, progress: int, plan: dict | None = None,
               error: str | None = None, manifest_hash: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE cyclepilot_runs SET status=%s, stage=%s, progress=%s,
               plan_json=COALESCE(%s, plan_json), error_message=%s,
               manifest_hash=COALESCE(%s, manifest_hash), updated_at=%s WHERE run_id=%s""",
            (status, stage, max(0, min(progress, 100)), json.dumps(plan, default=str) if plan is not None else None,
             error, manifest_hash, _utc_now(), run_id),
        )
    conn.commit()


def _prepare_worker(run_id: str, connection_factory: Callable[[], Any]) -> None:
    conn = connection_factory()
    run = get_run(conn, run_id)
    if not run:
        conn.close()
        return
    login = run["login_email"]
    try:
        if not acquire_login_lock(conn, login, run_id, run["operator_name"]):
            raise RuntimeError("This login is already owned by another active CyclePilot run")
        update_run(conn, run_id, status="profiling", stage="credit_snapshot", progress=5)
        append_event(conn, run_id, "info", "credit_snapshot", "Login lock acquired; reading the free credit snapshot.")

        from scripts.enrich_batch_interactive import get_account_credit_snapshot
        from scripts.apollo_search_optimizer import (
            batch_probe_candidates,
            generate_title_and_functional_slicing_candidates,
            get_search_history,
        )
        account = find_account(login)
        credit = get_account_credit_snapshot(account or {}, dry_run=False)
        target = calculate_target(int(credit.get("credits_remaining", 0) or 0), run["credit_reserve"], 0)
        ranked_by_search = []
        update_run(conn, run_id, status="profiling", stage="slice_profiling", progress=15)

        for index, search in enumerate(run["selected_searches"]):
            titles, functions, _ = generate_title_and_functional_slicing_candidates(
                search.get("filters") or {}, title_mode="decision_makers_only"
            )
            history = get_search_history(login, search["name"]).get("keywords", {})
            candidates = []
            for value, category in [(x, "Job Title") for x in titles] + [(x, "Functional") for x in functions]:
                if value.lower() not in history:
                    candidates.append((value, category))
                if len(candidates) >= 30:
                    break
            probed = batch_probe_candidates(account.get("api_key", ""), search.get("filters") or {}, candidates, max_workers=5)
            probed.sort(key=lambda row: (row.get("total_leads", 0) > 2500, -min(row.get("total_leads", 0), 2500)))
            ranked_by_search.append({"search_id": search["id"], "search_name": search["name"], "slices": probed[:12]})
            append_event(conn, run_id, "info", "slice_profiling", f"Profiled {search['name']}.", {
                "candidates": len(candidates), "useful_slices": len(probed)
            })
            update_run(conn, run_id, status="profiling", stage="slice_profiling",
                       progress=15 + int(55 * (index + 1) / len(run["selected_searches"])))

        plan = {
            "credit_snapshot": {key: value for key, value in credit.items() if key not in {"api_key", "key", "token"}},
            "target": target,
            "batch_name": run["batch_name"],
            "cycle_tag": run["cycle_tag"],
            "ranked_searches": ranked_by_search,
            "cleaning": ["known job-title rules", "unknown-title review queue", "Indian-name review guardrail", "one contact per domain"],
            "next_stage": "Operator approval is required before any enrichment credits are used.",
        }
        update_run(conn, run_id, status="ready_for_enrichment", stage="approval_required", progress=70, plan=plan)
        append_event(conn, run_id, "success", "approval_required", "Zero-credit preparation finished. No enrichment credits were used.", target)
    except Exception as exc:
        update_run(conn, run_id, status="failed", stage="failed", progress=0, error=str(exc))
        append_event(conn, run_id, "error", "failed", str(exc))
    finally:
        release_login_lock(conn, login, run_id)
        conn.close()
        with _workers_guard:
            _workers.pop(run_id, None)


def start_preparation(run_id: str, connection_factory: Callable[[], Any]) -> bool:
    with _workers_guard:
        current = _workers.get(run_id)
        if current and current.is_alive():
            return False
        worker = threading.Thread(target=_prepare_worker, args=(run_id, connection_factory), daemon=True,
                                  name=f"cyclepilot-{run_id[:8]}")
        _workers[run_id] = worker
        worker.start()
        return True

