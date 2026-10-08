from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.database.db import get_db
from app.database.models import ContactAuditTrail, FileRunMetric
from app.models import FileProcessingResult


def save_run_metrics_to_db(result: FileProcessingResult, audit_rows: list[dict[str, Any]]) -> int:
    """
    Saves file run metrics and row-by-row audit trails into the database.
    Returns the generated run_id.
    """
    db = get_db()
    try:
        run_record = FileRunMetric(
            source_file=result.source_filename,
            owner=result.apollo_login_owner,
            tag=result.tag_applied,
            input=result.total_input_rows,
            tld_block=result.tld_filtered_count,
            created=result.freshly_created_count,
            data=result.updated_in_crm_count,
            failed=result.failed_errors_count,
            error=result.error_message,
            success_percentage=result.overall_success_percentage,
            wastage_percentage=result.wastage_percentage,
            status=result.status,
            timestamp=result.completed_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            report_file_path=result.report_file_path,
        )
        db.add(run_record)
        db.commit()
        db.refresh(run_record)

        run_id = run_record.id

        # Insert audit trail rows
        audit_objs = [
            ContactAuditTrail(
                run_id=run_id,
                email=r.get("email", ""),
                action=r.get("action", "unknown"),
                fields_filled_count=r.get("fields_filled_count", 0),
                fields_filled_names=r.get("fields_filled_names", ""),
                error_reason=r.get("error_reason", ""),
            )
            for r in audit_rows
        ]
        if audit_objs:
            db.bulk_save_objects(audit_objs)
            db.commit()

        return run_id
    finally:
        db.close()


def generate_audit_csv_report(
    source_filename: str,
    audit_rows: list[dict[str, Any]],
    reports_dir: Path,
) -> Path:
    """
    Generates a per-file audit CSV report in reports_dir.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(source_filename).stem
    report_file = reports_dir / f"{stem}_crm_audit.csv"

    fieldnames = [
        "email",
        "action",
        "fields_filled_count",
        "fields_filled_names",
        "error_reason",
    ]

    with report_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in audit_rows:
            writer.writerow(r)

    return report_file
