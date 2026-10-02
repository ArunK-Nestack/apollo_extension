from __future__ import annotations

import argparse
import sys
from pathlib import Path

from typing import Any

# Add freshsales_agent root to sys.path so app module resolves cleanly
agent_root = Path(__file__).resolve().parent.parent
if str(agent_root) not in sys.path:
    sys.path.insert(0, str(agent_root))

from app.config import settings
from app.database.db import get_db, init_db
from app.database.models import FileRunMetric
from app.services.batch_processor import BatchProcessor

def _format_table(headers: list[str], rows: list[list[Any]]) -> str:
    try:
        from tabulate import tabulate
        return tabulate(rows, headers=headers, tablefmt="github")
    except ImportError:
        # Built-in clean ASCII table
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, val in enumerate(row):
                col_widths[i] = max(col_widths[i], len(str(val)))

        header_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
        sep_line = "|-" + "-|-".join("-" * col_widths[i] for i in range(len(headers))) + "-|"
        row_lines = [
            "| " + " | ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row)) + " |"
            for row in rows
        ]
        return "\n".join([header_line, sep_line] + row_lines)


def check_config() -> None:
    print("=" * 70)
    print(" Freshsales CRM Automation Configuration")
    print("=" * 70)
    print(f"Freshsales Domain:       {settings.domain}")
    print(f"API Key Configured:      {'Yes' if settings.api_key and not settings.api_key.startswith('your_') else 'No (Missing in .env)'}")
    print(f"Default Owner ID:        {settings.default_owner_id or 'None (Unassigned)'}")
    print(f"Input Folder:            {settings.input_dir}")
    print(f"Reports Folder:          {settings.reports_dir}")
    print(f"Database URL:            {settings.database_url}")
    print(f"Poll Interval:           {settings.poll_interval_seconds}s")
    print(f"Batch Size:              {settings.batch_size}")
    print(f"Auto-delete Input:       {settings.auto_delete_input}")
    print("=" * 70)


def show_stats() -> None:
    init_db()
    db = get_db()
    try:
        records = db.query(FileRunMetric).order_by(FileRunMetric.id.desc()).limit(20).all()
        if not records:
            print("No file run metrics found in database yet.")
            return

        table_data = []
        for r in records:
            table_data.append([
                f"#{r.id}",
                (r.source_file or "")[:25],
                (r.owner or "-")[:25],
                (r.tag or "-")[:28],
                r.input,
                r.tld_block,
                r.created,
                r.data,
                r.failed,
                (r.error or "-")[:12],
                f"{r.success_percentage}%",
                f"{r.wastage_percentage}%",
                r.status,
                (r.timestamp or "")[:19].replace("T", " "),
            ])

        headers = [
            "ID", "Source File", "Owner", "Tag", "Input", "TLD Block",
            "Created", "Data", "Failed", "Error", "Success %", "Wastage %", "Status", "Timestamp"
        ]

        print("=" * 70)
        print(" FRESHSALES CRM AUTOMATION -- RECENT RUN METRICS (DATABASE)")
        print("=" * 70)
        print(_format_table(headers, table_data))
        print("=" * 70)
    finally:
        db.close()


def run_automation(
    input_dir: str | None = None,
    reports_dir: str | None = None,
    continuous: bool = True,
    delete_input: bool = True,
) -> None:
    settings.require_api_key()

    in_path = Path(input_dir) if input_dir else settings.input_dir
    rep_path = Path(reports_dir) if reports_dir else settings.reports_dir

    processor = BatchProcessor(auto_delete_input=delete_input)

    if continuous:
        processor.watch_and_process(input_dir=in_path, reports_dir=rep_path)
    else:
        files = processor.find_input_files(in_path)
        if not files:
            print(f"No CSV or Excel files found in {in_path}.")
            return
        for f in files:
            processor.process_file(f, rep_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freshsales CRM Continuous Ingestion, 33-TLD Filter & Upsert Agent"
    )

    subparsers = parser.add_subparsers(dest="command")

    # Command: config
    subparsers.add_parser(
        "config",
        help="Check Freshsales connection, domain, and folder paths",
    )

    # Command: stats
    subparsers.add_parser(
        "stats",
        help="Display SQLite run history and metrics in a clean table",
    )

    # Command: watch
    watch_parser = subparsers.add_parser(
        "watch",
        help="Run continuous watcher on Input Folder",
    )
    watch_parser.add_argument(
        "--input-dir",
        "-i",
        default=None,
        help=f"Path to input folder (default: {settings.input_dir})",
    )
    watch_parser.add_argument(
        "--reports-dir",
        "-r",
        default=None,
        help=f"Path to reports folder (default: {settings.reports_dir})",
    )
    watch_parser.add_argument(
        "--keep-input",
        action="store_true",
        help="Do not delete the input file after processing",
    )

    # Command: process
    process_parser = subparsers.add_parser(
        "process",
        help="Run verification (continuous by default, or --once)",
    )
    process_parser.add_argument(
        "--input-dir",
        "-i",
        default=None,
        help=f"Path to input folder (default: {settings.input_dir})",
    )
    process_parser.add_argument(
        "--reports-dir",
        "-r",
        default=None,
        help=f"Path to reports folder (default: {settings.reports_dir})",
    )
    process_parser.add_argument(
        "--once",
        action="store_true",
        help="Process current files once and exit instead of continuous watch",
    )
    process_parser.add_argument(
        "--keep-input",
        action="store_true",
        help="Do not delete the input file after processing",
    )

    args = parser.parse_args()

    if args.command == "config":
        check_config()
        return

    if args.command == "stats":
        show_stats()
        return

    if args.command == "watch":
        run_automation(
            input_dir=args.input_dir,
            reports_dir=args.reports_dir,
            continuous=True,
            delete_input=not args.keep_input,
        )
        return

    if args.command == "process":
        run_automation(
            input_dir=args.input_dir,
            reports_dir=args.reports_dir,
            continuous=not args.once,
            delete_input=not args.keep_input,
        )
        return

    # Default: continuous watch
    run_automation(continuous=True, delete_input=True)


if __name__ == "__main__":
    main()
