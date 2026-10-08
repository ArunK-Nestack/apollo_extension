from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.clients.freshsales import FreshsalesClient
from app.config import settings
from app.database.db import init_db
from app.logger import logger
from app.models import FileProcessingResult
from app.services.field_mapper import map_row_to_contact_payload
from app.services.input_processor import parse_and_dedupe_file
from app.services.reporter import generate_audit_csv_report, save_run_metrics_to_db
from app.services.tld_filter import filter_by_tld
from app.services.upsert_engine import resolve_delta_update


class BatchProcessor:
    def __init__(
        self,
        client: FreshsalesClient | None = None,
        auto_delete_input: bool | None = None,
    ) -> None:
        init_db()
        self.client = client or FreshsalesClient()
        self.auto_delete_input = (
            auto_delete_input if auto_delete_input is not None else settings.auto_delete_input
        )

    def find_input_files(self, input_dir: Path) -> list[Path]:
        if input_dir.is_file():
            return [input_dir] if input_dir.suffix.lower() in (".csv", ".xlsx", ".xls", ".xlsm") else []
        if not input_dir.exists() or not input_dir.is_dir():
            return []
        return [
            p
            for p in sorted(input_dir.iterdir(), key=lambda f: f.stat().st_mtime if f.is_file() else 0)
            if p.is_file()
            and p.suffix.lower() in (".csv", ".xlsx", ".xls", ".xlsm")
            and not p.name.startswith((".", "~"))
        ]


    def process_file(
        self,
        file_path: Path,
        reports_dir: Path,
        custom_tag: str | None = None,
        default_owner_id: str | None = None,
        audit_cache_path: Path | None = None,
    ) -> FileProcessingResult:
        """
        Executes the end-to-end sync pipeline for a single file.
        1. Parsing and In-File Deduplication.
        2. 33-TLD Country Code Exclusion.
        3. Pre-lookup & Non-overwrite Delta Resolution (uses audit_cache_path if provided to skip lookups).
        4. Sliding-Window Bulk Upsert Dispatch (max 6 concurrent in Freshsales).
        5. Completion Monitoring, Metrics & Audit CSV Generation.
        """
        logger.info("=" * 70)
        logger.info(f"PROCESSING INPUT FILE: {file_path.name}")
        logger.info("=" * 70)

        # 1. Parse and Deduplicate
        parsed = parse_and_dedupe_file(file_path)
        tag_to_use = (custom_tag.strip() if custom_tag and custom_tag.strip() else None) or parsed.tag_applied
        owner_id_to_use = default_owner_id or settings.default_owner_id

        logger.info(
            f"Step 1/5: Parsed {parsed.total_raw_rows} raw rows -> "
            f"{len(parsed.unique_records)} unique contacts (Dropped {parsed.duplicates_dropped} duplicate emails)."
        )
        logger.info(f"  -> Apollo Login Owner: {parsed.apollo_login_owner or 'None'}")
        logger.info(f"  -> Run Tag Applied:    {tag_to_use}")

        audit_rows: list[dict[str, Any]] = []

        # 2. Layer 1: 33 TLD Domain Filter
        valid_records, excluded_tld_records = filter_by_tld(
            parsed.unique_records,
            email_col=parsed.email_column,
        )
        tld_filtered_count = len(excluded_tld_records)
        logger.info(
            f"Step 2/5: 33-TLD Filter evaluated -> {len(valid_records)} allowed domains, "
            f"{tld_filtered_count} blocked domains excluded."
        )

        for exc_row in excluded_tld_records:
            audit_rows.append({
                "email": exc_row.get(parsed.email_column, ""),
                "action": "excluded_tld",
                "fields_filled_count": 0,
                "fields_filled_names": "",
                "error_reason": "Matched DELETE_TLDS blocklist",
            })

        result = FileProcessingResult(
            source_filename=file_path.name,
            tag_applied=tag_to_use,
            apollo_login_owner=parsed.apollo_login_owner,
            total_input_rows=parsed.total_raw_rows,
            file_duplicates_skipped=parsed.duplicates_dropped,
            tld_filtered_count=tld_filtered_count,
            contacts_sent_to_crm=len(valid_records),
        )

        if not valid_records:
            logger.warning("No valid records left to process after TLD filter.")
            result.status = "completed"
            result.completed_at = datetime.now().isoformat()
            report_file = generate_audit_csv_report(file_path.name, audit_rows, reports_dir)
            result.report_file_path = str(report_file)
            save_run_metrics_to_db(result, audit_rows)
            if self.auto_delete_input:
                self._cleanup_input_file(file_path)
            return result

        # 3. CRM Lookup & Delta Resolution
        all_emails = [r.get(parsed.email_column, "").lower().strip() for r in valid_records if r.get(parsed.email_column)]
        total_emails = len(all_emails)

        # Load precomputed classifications from audit cache if provided
        precomputed_actions: dict[str, str] = {}
        if audit_cache_path and Path(audit_cache_path).is_file():
            try:
                import pandas as pd
                df_cache = pd.read_csv(audit_cache_path)
                for _, r in df_cache.iterrows():
                    em = str(r.get("email", "")).strip().lower()
                    act = str(r.get("action", "")).strip().lower()
                    if em and act in ("created", "updated"):
                        precomputed_actions[em] = act
                logger.info(f"Loaded {len(precomputed_actions):,d} precomputed contact statuses from audit cache: {Path(audit_cache_path).name}")
            except Exception as exc:
                logger.warning(f"Could not load audit cache: {exc}")

        # Check which emails still require Freshsales API search
        emails_to_lookup = [em for em in all_emails if em not in precomputed_actions]
        existing_crm_map: dict[str, dict[str, Any]] = {}

        if emails_to_lookup:
            total_lookup = len(emails_to_lookup)
            logger.info(f"Step 3/5: Batch looking up {total_lookup:,d} emails in Freshsales CRM...")
            t_lookup_start = time.time()
            for i in range(0, total_lookup, 50):
                chunk = emails_to_lookup[i : i + 50]
                found = self.client.batch_lookup_emails(chunk)
                existing_crm_map.update(found)
                checked = min(i + len(chunk), total_lookup)
                pct = (checked / max(1, total_lookup)) * 100
                elapsed = time.time() - t_lookup_start
                rate = checked / max(elapsed, 0.001)
                eta_sec = int((total_lookup - checked) / max(rate, 0.001))
                eta_str = f"{eta_sec // 60}m {eta_sec % 60}s" if eta_sec > 60 else f"{eta_sec}s"
                logger.info(f"  -> [Lookup Progress] Checked {checked:,d} / {total_lookup:,d} ({pct:.1f}%) | Found in CRM: {len(existing_crm_map):,d} | ETA: {eta_str}")
            logger.info(f"  -> Lookup complete: {len(existing_crm_map):,d} already exist in CRM, {total_lookup - len(existing_crm_map):,d} brand new.")
        else:
            cached_updates = sum(1 for em in all_emails if precomputed_actions.get(em) == "updated")
            cached_creates = sum(1 for em in all_emails if precomputed_actions.get(em) == "created")
            logger.info(f"Step 3/5: All {total_emails:,d} contacts resolved from audit cache ({cached_creates:,d} new creates, {cached_updates:,d} delta updates). Zero API lookups needed.")

        # Build payloads formatted for Freshsales Bulk Upsert API
        upsert_payloads: list[dict[str, Any]] = []
        batch_meta: list[dict[str, Any]] = []

        for row in valid_records:
            incoming = map_row_to_contact_payload(
                row=row,
                tag=tag_to_use,
                default_owner_id=owner_id_to_use,
            )
            email_lower = incoming.email.lower().strip()

            cached_act = precomputed_actions.get(email_lower)
            if cached_act == "updated" or email_lower in existing_crm_map:
                # Existing contact -> resolve delta without overwrite
                existing_record = existing_crm_map.get(email_lower, {})
                contact_id = existing_record.get("id")
                delta = resolve_delta_update(
                    incoming=incoming,
                    existing_crm_record=existing_record,
                    tag=tag_to_use,
                )
                
                if contact_id:
                    upsert_payloads.append({
                        "id": str(contact_id),
                        "data": delta,
                    })
                else:
                    upsert_payloads.append({
                        "emails": incoming.email,
                        "data": delta,
                    })

                filled_keys = [k for k in delta if k not in ("emails", "tags")]
                audit_rows.append({
                    "email": incoming.email,
                    "action": "updated",
                    "fields_filled_count": len(filled_keys),
                    "fields_filled_names": ", ".join(filled_keys),
                    "error_reason": "",
                })
                batch_meta.append({"is_created": False, "email": incoming.email})
            else:
                # Brand new contact -> full payload
                full_data = incoming.to_contact_data_dict()
                upsert_payloads.append({
                    "emails": incoming.email,
                    "data": full_data,
                })

                audit_rows.append({
                    "email": incoming.email,
                    "action": "created",
                    "fields_filled_count": len(full_data),
                    "fields_filled_names": "All",
                    "error_reason": "",
                })
                batch_meta.append({"is_created": True, "email": incoming.email})

        # 4. Sliding-Window Bulk Upsert Dispatch & Monitoring (Max 6 concurrent jobs to respect Freshsales limit of 10)
        MAX_CONCURRENT_JOBS = 6
        batch_size = settings.batch_size
        poll_delay = min(settings.poll_interval_seconds, 5)
        total_batches = (len(upsert_payloads) + batch_size - 1) // max(1, batch_size)
        logger.info(f"Step 4/5: Dispatching {len(upsert_payloads):,d} records in {total_batches} batches (Max {MAX_CONCURRENT_JOBS} concurrent in Freshsales)...")

        in_flight_jobs: list[tuple[str, list[dict[str, Any]]]] = []

        def drain_finished_jobs(block_for_at_least_one: bool = False):
            nonlocal in_flight_jobs
            while in_flight_jobs:
                still_running = []
                completed_in_pass = 0
                for jid, meta_slice in in_flight_jobs:
                    try:
                        status_data = self.client.get_job_status(jid)
                        status_str = str(status_data.get("status", "")).lower()
                        if status_str in ("completed", "finished", "success", "done"):
                            c_count = sum(1 for m in meta_slice if m["is_created"])
                            u_count = sum(1 for m in meta_slice if not m["is_created"])
                            result.freshly_created_count += c_count
                            result.updated_in_crm_count += u_count
                            completed_in_pass += 1
                            logger.info(f"  [OK] Job {jid}: COMPLETED (+{c_count} created, +{u_count} updated). Total synced: {result.freshly_created_count + result.updated_in_crm_count:,d}")
                        elif status_str in ("failed", "error"):
                            err_msg = status_data.get("error", "Job failure")
                            logger.error(f"  [FAIL] Job {jid}: FAILED ({err_msg})")
                            result.failed_errors_count += len(meta_slice)
                            completed_in_pass += 1
                            for item_meta in meta_slice:
                                for ar in audit_rows:
                                    if ar.get("email") == item_meta["email"]:
                                        ar["error_reason"] = f"Bulk job failed: {err_msg}"
                        else:
                            still_running.append((jid, meta_slice))
                    except Exception as exc:
                        logger.warning(f"  [~ Retry] Error checking job {jid}: {exc}")
                        still_running.append((jid, meta_slice))

                in_flight_jobs = still_running
                if not block_for_at_least_one or completed_in_pass > 0 or not in_flight_jobs:
                    break
                time.sleep(poll_delay)

        batch_idx = 0
        for i in range(0, len(upsert_payloads), batch_size):
            batch_idx += 1
            batch_slice = upsert_payloads[i : i + batch_size]
            meta_slice = batch_meta[i : i + batch_size]

            # If at concurrency limit, wait until at least one finishes
            while len(in_flight_jobs) >= MAX_CONCURRENT_JOBS:
                drain_finished_jobs(block_for_at_least_one=True)

            # Dispatch with automatic retry on 405 (transactions limit)
            submitted = False
            for attempt in range(1, 10):
                try:
                    job_id = self.client.bulk_upsert_contacts(batch_slice)
                    in_flight_jobs.append((job_id, meta_slice))
                    logger.info(f"  [Dispatch] Batch {batch_idx}/{total_batches}: Job {job_id} queued ({len(batch_slice)} records) | Active in CRM: {len(in_flight_jobs)}/{MAX_CONCURRENT_JOBS}")
                    submitted = True
                    break
                except Exception as exc:
                    exc_str = str(exc)
                    if "405" in exc_str or "Max of 10 transactions" in exc_str:
                        logger.warning(f"  [Queue Full] Freshsales 10-transaction limit active (attempt {attempt}/9). Waiting for in-flight jobs to finish...")
                        drain_finished_jobs(block_for_at_least_one=True)
                        time.sleep(5)
                    else:
                        logger.warning(f"  [Retry] Dispatch error: {exc}. Retrying in 5s (attempt {attempt}/9)...")
                        time.sleep(5)

            if not submitted:
                logger.error(f"  [Error] Batch {batch_idx} could not be dispatched after retries.")
                result.failed_errors_count += len(batch_slice)
                for item_meta in meta_slice:
                    for ar in audit_rows:
                        if ar.get("email") == item_meta["email"]:
                            ar["error_reason"] = "Batch dispatch permanently failed"

        # Final drain: wait for all remaining in-flight jobs to complete
        logger.info(f"Step 5/5: Monitoring remaining {len(in_flight_jobs)} bulk jobs until completion...")
        while in_flight_jobs:
            drain_finished_jobs(block_for_at_least_one=True)

        result.status = "completed"
        result.completed_at = datetime.now().isoformat()


        # Generate report and save to SQLite
        report_file = generate_audit_csv_report(file_path.name, audit_rows, reports_dir)
        result.report_file_path = str(report_file)
        run_id = save_run_metrics_to_db(result, audit_rows)

        logger.info("=" * 70)
        logger.info(" RUN SUMMARY (Saved to SQLite DB)")
        logger.info("=" * 70)
        logger.info(f"Run ID:                 #{run_id}")
        logger.info(f"Source File:            {result.source_filename}")
        logger.info(f"Apollo Owner:           {result.apollo_login_owner or 'N/A'}")
        logger.info(f"Run Tag:                {result.tag_applied}")
        logger.info(f"Raw Input Rows:         {result.total_input_rows}")
        logger.info(f"Duplicates Dropped:     {result.file_duplicates_skipped}")
        logger.info(f"TLD Filtered (33 cc):   {result.tld_filtered_count}")
        logger.info(f"Sent to Freshsales:     {result.contacts_sent_to_crm}")
        logger.info(f"  - Freshly Created:    {result.freshly_created_count}")
        logger.info(f"  - Existing & Updated: {result.updated_in_crm_count}")
        logger.info(f"  - Failed Errors:      {result.failed_errors_count}")
        logger.info(f"Overall Success Rate:   {result.overall_success_percentage}%")
        logger.info(f"Detailed Audit CSV:     {report_file}")
        logger.info("=" * 70)

        # 6. Auto-delete input file
        if self.auto_delete_input:
            self._cleanup_input_file(file_path)

        return result

    def _cleanup_input_file(self, file_path: Path) -> None:
        try:
            if file_path.exists() and file_path.is_file():
                file_path.unlink()
                logger.info(f"[Cleanup] Deleted source file from input queue: {file_path.name}")
        except Exception as exc:
            logger.warning(f"[Cleanup Warning] Could not delete {file_path.name}: {exc}")

    def watch_and_process(self, input_dir: Path, reports_dir: Path) -> None:
        """
        Continuously watches input_dir for new files and processes them one by one.
        """
        input_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)

        logger.info("=" * 70)
        logger.info(" Freshsales CRM Continuous Upsert Watcher Active")
        logger.info("=" * 70)
        logger.info(f"Watching Input:   {input_dir}")
        logger.info(f"Writing Reports:  {reports_dir}")
        logger.info(f"SQLite DB:        {settings.data_dir / 'crm_automation.db'}")
        logger.info(f"Logs File:        {settings.data_dir.parent / 'logs' / 'agent.log'}")
        logger.info(f"Auto-delete:      {'Enabled' if self.auto_delete_input else 'Disabled'}")
        logger.info("=" * 70)
        logger.info("Waiting for Good email files to arrive... (Press Ctrl+C to stop)\n")

        try:
            while True:
                files = self.find_input_files(input_dir)
                if files:
                    current_file = files[0]
                    logger.info(f"[Queue] Found '{current_file.name}' ({len(files) - 1} more in queue). Starting processing...")
                    self.process_file(current_file, reports_dir)
                    continue

                time.sleep(settings.watch_interval_seconds)
        except KeyboardInterrupt:
            logger.info("[Watcher] Automation agent stopped by user.")
