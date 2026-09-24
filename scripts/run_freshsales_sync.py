#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Isolated Freshsales Sync Runner
===============================
Runs the Freshsales agent BatchProcessor in an isolated process to prevent
namespace collisions with millionverifier_agent.
"""

import os
import sys
import json
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FS_AGENT_DIR = PROJECT_ROOT / "freshsales_agent"

# Ensure FS_AGENT_DIR is first in sys.path
if str(FS_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(FS_AGENT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()
fs_env = FS_AGENT_DIR / ".env"
if fs_env.exists():
    load_dotenv(fs_env)

from app.config import settings as fs_settings
from app.services.batch_processor import BatchProcessor


def main():
    parser = argparse.ArgumentParser(description="Run Freshsales CRM batch sync on a CSV file")
    parser.add_argument("--file", required=True, help="Path to verified Good CSV file")
    parser.add_argument("--tag", required=False, default="", help="Custom tag to apply in Freshsales")
    parser.add_argument("--reports-dir", required=False, default="", help="Directory to save audit reports")
    parser.add_argument("--owner-id", required=False, default="", help="Default owner ID")
    parser.add_argument("--audit-cache", required=False, default="", help="Path to previous audit CSV for instant delta lookup cache")
    parser.add_argument("--json-output", action="store_true", help="Print result summary as JSON")

    args = parser.parse_args()
    file_path = Path(args.file).resolve()
    if not file_path.is_file():
        print(f"[ERROR] File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    # Configure stdout to flush immediately
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:
            pass

    fs_settings.require_api_key()

    reports_dir = Path(args.reports_dir).resolve() if args.reports_dir else (PROJECT_ROOT / "exports" / "freshsales_reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    audit_cache_p = Path(args.audit_cache).resolve() if args.audit_cache else None

    processor = BatchProcessor(auto_delete_input=False)
    result = processor.process_file(
        file_path=file_path,
        reports_dir=reports_dir,
        custom_tag=args.tag if args.tag else None,
        default_owner_id=args.owner_id if args.owner_id else None,
        audit_cache_path=audit_cache_p,
    )

    summary = {
        "status": result.status,
        "tag_applied": result.tag_applied,
        "total_input_rows": result.total_input_rows,
        "freshly_created_count": result.freshly_created_count,
        "updated_in_crm_count": result.updated_in_crm_count,
        "tld_filtered_count": result.tld_filtered_count,
        "failed_errors_count": result.failed_errors_count,
        "contacts_sent_to_crm": result.contacts_sent_to_crm,
        "report_file_path": result.report_file_path or "",
    }

    if args.json_output:
        print(f"__RESULT_JSON_START__{json.dumps(summary)}__RESULT_JSON_END__")

    sys.exit(0 if result.status != "failed" else 1)


if __name__ == "__main__":
    main()
