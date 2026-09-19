#!/usr/bin/env python
"""
Interactive CLI tool to extract Company Names, Domains, and Websites from Freshsales
with rate-limit pacing and bulk storage into MySQL.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from typing import Any

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from app.clients.freshsales import FreshsalesClient
from app.config import settings
from app.database import init_db
from app.database.db import engine, get_db
from app.database.company_model import FreshsalesCompany
from app.services.company_extractor import (
    extract_domain,
    resolve_company_name,
    save_companies_batch,
    save_checkpoint,
    load_checkpoint,
    clear_checkpoint,
)


def format_duration(seconds: float) -> str:
    """Formats seconds into HH:MM:SS."""
    return str(timedelta(seconds=int(seconds)))


def print_banner() -> None:
    print("\n" + "=" * 65)
    print("      FRESHSALES COMPANY & DOMAIN EXTRACTION ENGINE")
    print("=" * 65)
    db_target = engine.url.render_as_string(hide_password=True)
    print(f" Database Target : {db_target}")
    print(f" Target Table    : freshsales_companies")
    print(f" Rate Limiter    : Safe Pacing (max 5,000 requests/hour)")
    print("=" * 65 + "\n")


def safe_input(prompt: str, default: str = "") -> str:
    """Reads input safely, handling Ctrl+C and EOF gracefully."""
    try:
        val = input(prompt).strip()
        return val if val else default
    except (KeyboardInterrupt, EOFError):
        print("\n\n[INFO] Operation cancelled by user.")
        sys.exit(0)


def prompt_account_count() -> int | None:
    """Prompts user for target extraction count."""
    while True:
        raw = safe_input("How many accounts do you want to proceed with? (e.g. 500, 10000, or press Enter for 'all'): ")
        if not raw or raw.lower() in ("all", "0"):
            return None  # All accounts
        try:
            val = int(raw.replace(",", ""))
            if val <= 0:
                print("Please enter a positive number or 'all'.")
                continue
            return val
        except ValueError:
            print("Invalid input. Please enter an integer number (e.g. 1000) or 'all'.")


def prompt_filters(client: FreshsalesClient) -> tuple[dict[str, Any], int | str | None]:
    """Prompts user to review default filters and optionally add more."""
    filters = {
        "company_name_not_empty": True,
        "strict_company_name_column": True,
        "unique_companies_only": True,
        "require_domain": False,
    }
    # Default to Freshsales dedicated company view: 'companynaes' (ID: 12001487155)
    selected_view_id: int | str | None = 12001487155
    view_display_name = "companynaes (ID: 12001487155)"

    print("\n" + "-" * 60)
    print(" Applied Filters & Settings for this run:")
    print("   [1] Column 'Company Name' (cf_company_name) is NOT empty")
    print("   [2] Unique Companies ONLY: Skips duplicate company names")
    print(f"   [3] Target View: {view_display_name} [Only accounts with company names]")
    print("   [4] Batch Speed: 100 accounts / request [4x Turbo Mode]")
    print("-" * 60)

    add_more = safe_input("Do you want to adjust filters or change view? [y/N]: ").lower()
    if add_more in ("y", "yes"):
        # Option 1: Strict column vs Flexible
        print("\n  1. Company Name Column Filtering:")
        print("     [A] Strict (Default): ONLY extract accounts where 'Company Name' (cf_company_name) is NOT empty")
        print("     [B] Flexible: Check 'cf_company_name' first; fallback to clean Account Name if non-domain")
        col_choice = safe_input("     Select option [A/b]: ", default="A").strip().upper()
        if col_choice == "B":
            filters["strict_company_name_column"] = False
            print("     -> Mode: Flexible (cf_company_name or clean Account Name)")
        else:
            filters["strict_company_name_column"] = True
            print("     -> Mode: Strict (Freshsales 'Company Name' column MUST NOT be empty)")

        # Option 2: Unique companies only toggle
        uniq_choice = safe_input("\n  2. Extract UNIQUE company names only (skip duplicate company names)? [Y/n]: ", default="y").lower()
        if uniq_choice in ("n", "no"):
            filters["unique_companies_only"] = False
            print("     -> Unique filter: OFF (save all accounts, even if company name repeats)")
        else:
            filters["unique_companies_only"] = True
            print("     -> Unique filter: ON (skip duplicate company names)")

        # Option 3: Require domain
        dom = safe_input("\n  3. Require Domain/Website to also be NOT empty? [y/N]: ").lower()
        if dom in ("y", "yes"):
            filters["require_domain"] = True
            print("     -> Added filter: Domain/Website is NOT empty")

        # Option 4: Select a Freshsales View
        print(f"\n  4. Freshsales View (Current: {view_display_name}):")
        view_opt = safe_input("     Change view? [enter 'all' for All Accounts, View ID, or press Enter to keep]: ").strip()
        if view_opt.lower() in ("all", "default", "none"):
            selected_view_id = None
            print("     -> View changed to: All Accounts (default)")
        elif view_opt.isdigit():
            selected_view_id = int(view_opt)
            print(f"     -> View changed to ID: {selected_view_id}")
        elif view_opt and view_opt.lower() not in ("n", "no"):
            try:
                print("     Fetching available Freshsales views...")
                api_filters = client.get_account_filters()
                if api_filters:
                    matched = None
                    for f in api_filters:
                        if view_opt.lower() in str(f.get("name", "")).lower():
                            matched = f
                            break
                    if matched:
                        selected_view_id = matched.get("id")
                        print(f"     -> Matched and selected: '{matched.get('name')}' (ID: {selected_view_id})")
                    else:
                        print(f"     -> View '{view_opt}' not recognized. Keeping {view_display_name}.")
            except Exception as e:
                print(f"     (Could not fetch views list: {e})")

    return filters, selected_view_id


def extract_accounts_loop(
    client: FreshsalesClient,
    target_count: int | None,
    filters: dict[str, Any],
    view_id: int | str | None = None,
    resume_checkpoint: dict[str, Any] | None = None,
) -> None:
    """Main extraction loop with live progress logging."""
    start_time = time.time()
    total_saved = 0
    total_fetched = 0
    total_requests = 0

    last_fetched_id = None
    page = 1
    per_page = 100

    if resume_checkpoint:
        last_fetched_id = resume_checkpoint.get("last_fetched_id")
        page = resume_checkpoint.get("last_page", 1)
        total_saved = resume_checkpoint.get("total_extracted", 0)
        print(f"\n[RESUME] Resuming from checkpoint: Batch {page} | Last ID: {last_fetched_id} | Previous Saved: {total_saved:,}")

    print("\n" + "=" * 65)
    print(" Starting extraction stream... Press Ctrl+C at any time to pause.")
    print("=" * 65 + "\n")

    strict_col = filters.get("strict_company_name_column", True)
    unique_companies_only = filters.get("unique_companies_only", True)
    seen_companies: set[str] = set()

    if unique_companies_only:
        try:
            db = get_db()
            seen_companies = set(
                r[0].strip().lower()
                for r in db.query(FreshsalesCompany.company_name).all()
                if r[0]
            )
            db.close()
            print(f"[CACHE] Loaded {len(seen_companies):,} existing unique company names from DB.")
            print("        Any accounts matching existing company names will be skipped automatically.\n")
        except Exception as e:
            print(f"[WARN] Could not pre-load existing companies: {e}")

    try:
        while True:
            total_requests += 1
            batch_start = time.time()

            # Attempt scroll API if no custom view or scroll supported, else page-based
            accounts_data: list[dict[str, Any]] = []
            has_next = True

            try:
                res = client.fetch_sales_accounts_scroll(
                    view_id=view_id,
                    last_fetched_id=last_fetched_id,
                    limit=per_page,
                )
                accounts_data = res.get("sales_accounts", []) or res.get("accounts", [])
                meta = res.get("meta", {})
                has_next = meta.get("has_next_page", bool(accounts_data))

                # Freshsales anomaly check: if results are returned descending
                if accounts_data and len(accounts_data) > 1 and int(accounts_data[0].get("id", 0)) > int(accounts_data[-1].get("id", 0)):
                    if last_fetched_id and str(last_fetched_id).isdigit():
                        last_fetched_id = str(int(last_fetched_id) + per_page)
                        page += 1
                        continue

                # Filter out any accounts at or below current cursor
                if last_fetched_id and str(last_fetched_id).isdigit():
                    cur_id_int = int(last_fetched_id)
                    accounts_data = [a for a in accounts_data if a.get("id") and int(a["id"]) > cur_id_int]
                    if not accounts_data:
                        # Non-advancing batch or gap in CRM IDs: leap forward past gap
                        last_fetched_id = str(cur_id_int + per_page)
                        page += 1
                        continue

                # Advance cursor monotonically to the latest account in this batch
                if accounts_data:
                    last_fetched_id = str(accounts_data[-1]["id"])
            except Exception as exc:
                print(f"\n[WARN] API request error on batch {page}: {exc}. Retrying in 5 seconds...")
                time.sleep(5)
                continue

            if not accounts_data:
                if not has_next:
                    print("\n[INFO] No more accounts returned by Freshsales. Extraction complete!")
                    clear_checkpoint()
                    break
                else:
                    page += 1
                    continue

            total_fetched += len(accounts_data)

            # Filter and prepare records
            records_to_save: list[dict[str, Any]] = []
            for acc in accounts_data:
                try:
                    account_id = str(acc.get("id") or "").strip()
                    company_name = resolve_company_name(acc, strict_column_only=strict_col)
                    website = str(acc.get("website") or acc.get("company_website") or "").strip()
                    raw_name = str(acc.get("name") or "").strip()
                    domain = extract_domain(website or raw_name)

                    # Check filters
                    if filters.get("company_name_not_empty") and not company_name:
                        continue
                    if filters.get("require_domain") and not domain and not website:
                        continue

                    # Unique company name deduplication
                    if unique_companies_only:
                        norm_cname = company_name.strip().lower()
                        if norm_cname in seen_companies:
                            continue
                        seen_companies.add(norm_cname)

                    records_to_save.append({
                        "account_id": account_id,
                        "company_name": company_name,
                        "domain": domain,
                        "website": website or None,
                    })
                except Exception:
                    continue

            # Save batch to DB
            saved_in_batch = save_companies_batch(records_to_save)
            total_saved += saved_in_batch

            # If cursor was not in meta, use last account ID
            if not last_fetched_id and accounts_data:
                last_fetched_id = str(accounts_data[-1].get("id", ""))

            # Save progress checkpoint
            save_checkpoint(
                last_fetched_id=last_fetched_id,
                last_page=page,
                total_extracted=total_saved,
                view_id=view_id,
            )

            # Calculate stats for CLI log
            elapsed = time.time() - start_time
            req_per_minute = (total_requests / (elapsed / 60.0)) if elapsed > 0 else 0.0
            acc_per_minute = (total_fetched / (elapsed / 60.0)) if elapsed > 0 else 0.0
            target_str = f"{target_count:,}" if target_count else "All"
            now_str = datetime.now().strftime("%H:%M:%S")

            print(
                f"[{now_str}] [Batch {page:4d}] "
                f"Fetched: {len(accounts_data):3d} | "
                f"Saved to DB: {total_saved:,} / {target_str} | "
                f"Speed: {acc_per_minute:,.0f} acc/min ({req_per_minute:4.1f} req/min) | "
                f"Elapsed: {format_duration(elapsed)}"
            )

            # Check if target count reached
            if target_count and total_saved >= target_count:
                print(f"\n[SUCCESS] Reached target goal of {target_count:,} accounts!")
                break

            # Only terminate if Freshsales scroll API explicitly indicates has_next_page is False
            if not has_next:
                print("\n[INFO] End of available records reached (has_next_page is False).")
                clear_checkpoint()
                break

            page += 1

    except KeyboardInterrupt:
        print("\n\n" + "!" * 65)
        print(" [PAUSED] Extraction interrupted by user (Ctrl+C).")
        print(f" Progress safely saved! Total accounts saved so far: {total_saved:,}")
        print(" When you run the script again, you can resume from this exact point.")
        print("!" * 65)
        return

    # Final summary
    total_elapsed = time.time() - start_time
    print("\n" + "=" * 65)
    print(" EXTRACTION COMPLETED SUCCESSFULLY")
    print("=" * 65)
    print(f" Total Accounts Saved to DB : {total_saved:,}")
    print(f" Total API Requests Made    : {total_requests:,}")
    print(f" Total Time Elapsed         : {format_duration(total_elapsed)}")
    print(f" Target Table               : freshsales_companies")
    print("=" * 65 + "\n")


def main() -> None:
    print_banner()

    # 1. Initialize database schema
    try:
        init_db()
        print("[OK] Database connection and table `freshsales_companies` verified.\n")
    except Exception as e:
        print(f"[ERROR] Could not connect to database: {e}")
        print("Please verify your DATABASE_URL in the .env file.")
        sys.exit(1)

    # 2. Check Freshsales client credentials
    client = FreshsalesClient()
    try:
        settings.require_api_key()
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        print("Please add your FRESHSALES_API_KEY to the .env file.")
        sys.exit(1)

    # 3. Check for previous checkpoint or existing DB progress
    checkpoint = load_checkpoint()
    resume = False
    if checkpoint:
        prev_saved = checkpoint.get("total_extracted", 0)
        prev_page = checkpoint.get("last_page", 1)
        prev_cursor = checkpoint.get("last_fetched_id")
        res_prompt = safe_input(
            f"A previous checkpoint was found ({prev_saved:,} accounts saved at batch {prev_page}, cursor ID: {prev_cursor}). Resume? [Y/n]: ",
            default="y",
        ).lower()
        if res_prompt in ("y", "yes"):
            resume = True
        else:
            clear_checkpoint()
            checkpoint = None
    else:
        # Check if database already has previously extracted accounts
        try:
            db = get_db()
            total_in_db = db.query(FreshsalesCompany).count()
            frontier = db.execute(
                text("SELECT MAX(CAST(account_id AS UNSIGNED)) FROM freshsales_companies WHERE CAST(account_id AS UNSIGNED) < 12006871357")
            ).scalar()
            db.close()
            if frontier and total_in_db > 0:
                print(f"[INFO] Found {total_in_db:,} accounts already saved in `freshsales_companies` table.")
                res_prompt = safe_input(
                    f"Resume extraction from current frontier account (ID: {frontier})? [Y/n]: ",
                    default="y",
                ).lower()
                if res_prompt in ("y", "yes"):
                    resume = True
                    checkpoint = {
                        "last_fetched_id": str(frontier),
                        "last_page": (total_in_db // 100) + 1,
                        "total_extracted": 0,
                        "view_id": 12001487155,
                    }
        except Exception:
            pass

    # 4. Account limit prompt (if not resuming or optional target)
    target_count: int | None = prompt_account_count() if not resume else None

    # 5. Filters prompt
    filters, view_id = prompt_filters(client)
    if resume and checkpoint and checkpoint.get("view_id") and not view_id:
        view_id = checkpoint.get("view_id")

    # 6. Preview & Final Confirmation
    print("\n" + "=" * 50)
    print(" Summary of Extraction Job:")
    print(f"   Target Accounts : {'All matching accounts' if not target_count else f'{target_count:,}'}")
    print("   Active Filters  : Company Name is NOT empty" + (", Domain is NOT empty" if filters.get("require_domain") else ""))
    print(f"   Target View ID  : {view_id or 'All Accounts (default)'}")
    print("   Batch Size      : 100 accounts / request (4x Turbo)")
    db_name = engine.url.render_as_string(hide_password=True)
    print(f"   Database Target : {db_name} -> freshsales_companies")
    print("=" * 50)

    proceed = safe_input("\nProceed to extract and save to database? [Y/n]: ", default="y").lower()
    if proceed not in ("y", "yes"):
        print("\nOperation cancelled by user. No data was extracted.")
        return

    # 7. Execute extraction
    extract_accounts_loop(
        client=client,
        target_count=target_count,
        filters=filters,
        view_id=view_id,
        resume_checkpoint=checkpoint if resume else None,
    )


if __name__ == "__main__":
    main()
