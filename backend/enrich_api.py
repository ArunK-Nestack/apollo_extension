import os
import re
import sys
import threading
import time
from typing import Any
from fastapi import APIRouter
from pydantic import BaseModel

enrich_router = APIRouter()

# In-memory batch company election cache to enforce 1 lead per company domain across pagination
_enrich_batch_seen_companies: dict[str, dict[str, list[dict]]] = {}
_enrich_batch_seen_lock = threading.Lock()


class EnrichContact(BaseModel):
    key: str
    name: str = ""
    first_name: str | None = ""
    last_name: str | None = ""
    job_title: str = ""
    company: str = ""
    domain: str = ""
    location: str | None = ""
    linkedin_url: str | None = None
    website_link: str | None = None
    employee_count: int | None = None


class EnrichCompanyItem(BaseModel):
    company: str = ""
    domain: str = ""
    website_link: str | None = None


class EnrichMatchRequest(BaseModel):
    contacts: list[EnrichContact] = []
    companies: list[EnrichCompanyItem] = []
    batch: str = "enrich_batch_1"
    collect_mode: str = "companies"
    title_guardrail_enabled: bool = True
    indian_name_guardrail_enabled: bool = True
    auto_save: bool = True


def ensure_enrich_saved_leads_table(conn):
    """Ensure the dedicated enrich_saved_leads table exists for storing scraped target leads with batch tracking."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS `enrich_saved_leads` (
                    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                    `batch` VARCHAR(64) NOT NULL DEFAULT 'enrich_batch_1',
                    `apollo_id` VARCHAR(128) DEFAULT '',
                    `name` VARCHAR(255) NOT NULL DEFAULT '',
                    `first_name` VARCHAR(128) DEFAULT '',
                    `last_name` VARCHAR(128) DEFAULT '',
                    `job_title` VARCHAR(255) DEFAULT '',
                    `email` VARCHAR(255) DEFAULT '',
                    `email_status` VARCHAR(64) DEFAULT '',
                    `company` VARCHAR(255) DEFAULT '',
                    `company_domain` VARCHAR(255) DEFAULT '',
                    `website_link` VARCHAR(512) DEFAULT '',
                    `annual_revenue` VARCHAR(64) DEFAULT '',
                    `employee_count` INT UNSIGNED DEFAULT NULL,
                    `industry` VARCHAR(128) DEFAULT '',
                    `tech_stack` JSON DEFAULT NULL,
                    `keywords` JSON DEFAULT NULL,
                    `company_phone` VARCHAR(64) DEFAULT '',
                    `hq_address` VARCHAR(512) DEFAULT '',
                    `location` VARCHAR(255) DEFAULT '',
                    `linkedin_url` VARCHAR(512) DEFAULT '',
                    `company_linkedin_url` VARCHAR(512) DEFAULT '',
                    `apollo_profile_url` VARCHAR(512) DEFAULT '',
                    `segment` VARCHAR(128) DEFAULT 'Target_Lead',
                    `account_used` VARCHAR(128) DEFAULT '',
                    `credits_charged` TINYINT UNSIGNED DEFAULT 0,
                    `enriched_at` TIMESTAMP NULL DEFAULT NULL,
                    `raw_enrichment_data` JSON DEFAULT NULL,
                    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX `idx_batch` (`batch`),
                    INDEX `idx_company_domain` (`company_domain`),
                    UNIQUE KEY `unique_batch_lead` (`batch`, `name`(128), `company_domain`(128))
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """)
    except Exception as e:
        print(f"[EnrichMatcher] Notice: ensure enrich_saved_leads table: {e}", flush=True)


def ensure_enrich_companies_table(conn):
    """Ensure dedicated enrich_companies table exists for storing unique company names and domains."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS `enrich_companies` (
                    `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                    `batch` VARCHAR(64) NOT NULL DEFAULT 'enrich_companies_1',
                    `company_name` VARCHAR(255) NOT NULL DEFAULT '',
                    `domain` VARCHAR(255) NOT NULL DEFAULT '',
                    `website_link` VARCHAR(512) DEFAULT '',
                    `crm_status` VARCHAR(64) DEFAULT 'net_new',
                    `source` VARCHAR(64) DEFAULT 'enrich_extension',
                    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX `idx_batch` (`batch`),
                    INDEX `idx_company_name` (`company_name`),
                    INDEX `idx_domain` (`domain`),
                    UNIQUE KEY `unique_batch_domain` (`batch`, `domain`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
            """)
    except Exception as e:
        print(f"[EnrichMatcher] Notice: ensure enrich_companies table: {e}", flush=True)


def _get_api_dependencies():
    """Dynamically import helper functions from backend.api / api."""
    try:
        import backend.api as api_mod
    except ModuleNotFoundError:
        import api as api_mod
    return api_mod


def _extract_canonical_domain(contact: EnrichContact, api_mod) -> str:
    """Extract canonical root domain from lead card domain, website link, or company name candidates."""
    raw_dom = (contact.domain or "").strip()
    if raw_dom:
        root_dom = api_mod.extract_root_domain(raw_dom)
        if root_dom:
            return root_dom

    web = (contact.website_link or "").strip()
    if web:
        root_dom = api_mod.extract_root_domain(web)
        if root_dom:
            return root_dom

    # Candidate domain fallback from company name
    comp = (contact.company or "").strip()
    if comp:
        cands = api_mod.generate_candidate_domains(comp)
        if cands:
            return api_mod.extract_root_domain(cands[0]) or cands[0]

    return ""


def check_enrich_contacts_in_crm_4_layers(
    contacts: list[EnrichContact],
    contact_primary_domain: dict[str, str],
    connection,
    api_mod,
    active_batch: str | None = None,
    extra_domains: list[str] | set[str] | None = None
) -> dict[str, dict]:
    """
    Exact Apollo 4-Layer Deduplication Engine for Enrich contacts:
      Layer 1 - Exact domain & full_name match in CRM `emails`, `apollo_saved_leads`, `enrich_saved_leads`, and `enrich_companies`.
      Layer 2 - Person-name anchor: computes LCS ratio between DB email domain and displayed domain.
      Layer 3 - Database prefix trie: checks if domain slug is a branch extension of a known DB domain.
      Layer 4 - DNS MX mail routing: resolves MX server and checks DB.
    """
    if not contacts and not extra_domains:
        return {}

    candidate_domains_set: set[str] = set()
    if extra_domains:
        for ed in extra_domains:
            if ed and str(ed).strip():
                candidate_domains_set.add(str(ed).strip().lower())

    contact_candidates: dict[str, list[tuple[str, str]]] = {}

    for c in contacts:
        cands = []
        prim_d = contact_primary_domain.get(c.key, "")
        if prim_d:
            cands.append(("card_domain", prim_d))
            candidate_domains_set.add(prim_d)

        web_d = api_mod.extract_root_domain(c.website_link or "")
        if web_d and web_d != prim_d:
            cands.append(("website", web_d))
            candidate_domains_set.add(web_d)

        comp = (c.company or "").strip()
        if comp:
            seen_doms = {d for _, d in cands}
            for cdom in api_mod.generate_candidate_domains(comp):
                root_cdom = api_mod.extract_root_domain(cdom)
                if root_cdom and root_cdom not in seen_doms:
                    cands.append(("company_candidate", root_cdom))
                    seen_doms.add(root_cdom)
                    candidate_domains_set.add(root_cdom)

        contact_candidates[c.key] = cands

    candidate_domains = list(candidate_domains_set)
    if not candidate_domains:
        return {}

    # Layer 1: Query CRM master emails table, apollo_saved_leads, and enrich_saved_leads
    schema = api_mod.get_target_table_schema(connection)
    tbl_name = schema["table_name"]
    domain_col = schema["email_domain"]
    name_col = schema["name"] or "full_name"

    matched_records: dict[tuple[str, str], str] = {}  # (norm_name, domain) -> raw_full_name
    matched_domains: set[str] = set()

    try:
        with connection.cursor() as cur:
            chunk_size = 1000
            for i in range(0, len(candidate_domains), chunk_size):
                chunk = candidate_domains[i:i + chunk_size]
                format_strings = ",".join(["%s"] * len(chunk))

                # 1. Check master CRM emails table
                query1 = f"SELECT `{domain_col}`, `{name_col}` FROM `{tbl_name}` WHERE `{domain_col}` IN ({format_strings});"
                cur.execute(query1, tuple(chunk))
                for r in cur.fetchall():
                    dom = str(r[0] or "").strip().lower()
                    raw_nm = str(r[1] or "").strip()
                    norm_nm = api_mod.normalize_text(raw_nm)
                    if dom:
                        matched_domains.add(dom)
                        if norm_nm:
                            matched_records[(norm_nm, dom)] = raw_nm

                # 2. Check apollo_saved_leads table
                query2 = f"SELECT `company_domain`, `name` FROM `apollo_saved_leads` WHERE `company_domain` IN ({format_strings});"
                try:
                    cur.execute(query2, tuple(chunk))
                    for r in cur.fetchall():
                        dom = str(r[0] or "").strip().lower()
                        raw_nm = str(r[1] or "").strip()
                        norm_nm = api_mod.normalize_text(raw_nm)
                        if dom:
                            matched_domains.add(dom)
                            if norm_nm:
                                matched_records[(norm_nm, dom)] = raw_nm
                except Exception:
                    pass

                # 3. Check enrich_saved_leads table
                query3 = f"SELECT `company_domain`, `name` FROM `enrich_saved_leads` WHERE `company_domain` IN ({format_strings});"
                try:
                    cur.execute(query3, tuple(chunk))
                    for r in cur.fetchall():
                        dom = str(r[0] or "").strip().lower()
                        raw_nm = str(r[1] or "").strip()
                        norm_nm = api_mod.normalize_text(raw_nm)
                        if dom:
                            matched_domains.add(dom)
                            if norm_nm:
                                matched_records[(norm_nm, dom)] = raw_nm
                except Exception:
                    pass

                # 4. Check enrich_companies table
                query4 = f"SELECT `domain`, `company_name` FROM `enrich_companies` WHERE `domain` IN ({format_strings});"
                try:
                    cur.execute(query4, tuple(chunk))
                    for r in cur.fetchall():
                        dom = str(r[0] or "").strip().lower()
                        raw_nm = str(r[1] or "").strip()
                        norm_nm = api_mod.normalize_text(raw_nm)
                        if dom:
                            matched_domains.add(dom)
                            if norm_nm:
                                matched_records[(norm_nm, dom)] = raw_nm
                except Exception:
                    pass
    except Exception as e:
        print(f"[EnrichMatcher] CRM query error: {e}", flush=True)

    # Layer 2 Prep: Lookup DB domains for each contact's full name
    unique_names_needed = set()
    for c in contacts:
        cands = contact_candidates.get(c.key, [])
        if not c.name or not cands:
            continue
        norm_nm = api_mod.normalize_text(c.name)
        any_l1_hit = any(
            (norm_nm, dom) in matched_records or dom in matched_domains
            for _, dom in cands
        )
        if not any_l1_hit:
            with api_mod._person_domain_cache_lock:
                if norm_nm not in api_mod._person_domain_cache:
                    unique_names_needed.add(c.name.strip())

    if unique_names_needed:
        try:
            with connection.cursor() as cur:
                fmt_nm = ",".join(["%s"] * len(unique_names_needed))
                # 1. Master CRM
                cur.execute(
                    f"SELECT `{name_col}`, `{domain_col}` FROM `{tbl_name}` WHERE `{name_col}` IN ({fmt_nm})",
                    tuple(unique_names_needed)
                )
                with api_mod._person_domain_cache_lock:
                    for r in cur.fetchall():
                        raw_nm = str(r[0] or "").strip()
                        dom = str(r[1] or "").strip().lower()
                        norm_nm = api_mod.normalize_text(raw_nm)
                        if norm_nm and dom:
                            if norm_nm not in api_mod._person_domain_cache:
                                api_mod._person_domain_cache[norm_nm] = []
                            if dom not in api_mod._person_domain_cache[norm_nm]:
                                api_mod._person_domain_cache[norm_nm].append(dom)

                # 2. apollo_saved_leads
                try:
                    cur.execute(
                        f"SELECT `name`, `company_domain` FROM `apollo_saved_leads` WHERE `name` IN ({fmt_nm})",
                        tuple(unique_names_needed)
                    )
                    with api_mod._person_domain_cache_lock:
                        for r in cur.fetchall():
                            raw_nm = str(r[0] or "").strip()
                            dom = str(r[1] or "").strip().lower()
                            norm_nm = api_mod.normalize_text(raw_nm)
                            if norm_nm and dom:
                                if norm_nm not in api_mod._person_domain_cache:
                                    api_mod._person_domain_cache[norm_nm] = []
                                if dom not in api_mod._person_domain_cache[norm_nm]:
                                    api_mod._person_domain_cache[norm_nm].append(dom)
                except Exception:
                    pass

                # 3. enrich_saved_leads
                try:
                    cur.execute(
                        f"SELECT `name`, `company_domain` FROM `enrich_saved_leads` WHERE `name` IN ({fmt_nm})",
                        tuple(unique_names_needed)
                    )
                    with api_mod._person_domain_cache_lock:
                        for r in cur.fetchall():
                            raw_nm = str(r[0] or "").strip()
                            dom = str(r[1] or "").strip().lower()
                            norm_nm = api_mod.normalize_text(raw_nm)
                            if norm_nm and dom:
                                if norm_nm not in api_mod._person_domain_cache:
                                    api_mod._person_domain_cache[norm_nm] = []
                                if dom not in api_mod._person_domain_cache[norm_nm]:
                                    api_mod._person_domain_cache[norm_nm].append(dom)
                except Exception:
                    pass

                # Negative cache
                with api_mod._person_domain_cache_lock:
                    for nm in unique_names_needed:
                        norm_nm = api_mod.normalize_text(nm)
                        if norm_nm not in api_mod._person_domain_cache:
                            api_mod._person_domain_cache[norm_nm] = []
        except Exception as e:
            print(f"[EnrichMatcher] Person-domain lookup error: {e}", flush=True)

    # Evaluate each contact through all 4 layers
    results = {}
    for c in contacts:
        norm_c_name = api_mod.normalize_text(c.name)
        for source, prim_d in contact_candidates.get(c.key, []):
            hit = api_mod._try_domain_layers(
                c, prim_d, norm_c_name, matched_records, matched_domains,
                connection=connection, domain_source=source
            )
            if hit:
                # Mark as existing in CRM
                results[c.key] = {
                    "exists": True,
                    "required": False,
                    "ignored": True,
                    "status": "existing_in_db",
                    "guardrail_status": hit.get("guardrail_status", "domain_already_in_db"),
                    "reason": hit.get("guardrail_reason") or f"Domain '{prim_d}' or contact '{c.name}' already exists in CRM.",
                    "matched_domain": prim_d,
                }
                break

    return results


@enrich_router.post("/match-enrich")
def match_enrich(request: EnrichMatchRequest):
    """
    Dedicated Enrich.so Lead Finder direct domain matcher:
    1. Extract canonical company domains from Enrich contact cards.
    2. Exact Apollo 4-Layer CRM Deduplication:
       - Checks master CRM `emails`, `apollo_saved_leads`, and `enrich_saved_leads`.
       - If company domain or person exists in CRM -> EXISTING IN CRM (Ignored: True, Required: False).
    3. Net-New Domain Evaluation (domains NOT in CRM):
       - Demographic Indian Name Filter (`is_unambiguous_pure_indian_name`).
       - 4-Layer Job Title Hierarchy Guardrails (64K+ DB rules, substrings, AI heuristics).
       - Domain Uniqueness: strictly enforces max 1 lead per company domain across the batch via seniority election (`get_seniority_score`).
    4. Auto-Persist: Automatically saves winning target leads into `enrich_saved_leads` under the active batch name.
    """
    api_mod = _get_api_dependencies()
    t0 = time.perf_counter()

    contacts = request.contacts
    total_received = len(contacts)
    batch_tag = str(request.batch or "enrich_batch_1").strip()[:64]

    results: dict[str, dict] = {}
    required_count = 0
    ignored_count = 0
    existing_count = 0
    saved_to_db_count = 0
    saved_companies_count = 0
    batch_total_companies = 0

    if not contacts and not getattr(request, "companies", []):
        return {
            "batch": batch_tag,
            "total": 0,
            "required_count": 0,
            "ignored_count": 0,
            "existing_count": 0,
            "saved_to_db_count": 0,
            "saved_companies_count": 0,
            "batch_total_companies": 0,
            "results": {},
            "execution_time_ms": 0.0,
        }

    # Step 1: Resolve canonical domain per contact
    contact_domains: dict[str, str] = {}
    for c in contacts:
        dom = _extract_canonical_domain(c, api_mod)
        contact_domains[c.key] = dom

    # Connect to MySQL RDS with retry safety
    def _get_safe_conn(retries=2, delay=0.8):
        last_err = None
        for _ in range(retries):
            try:
                return api_mod.get_connection()
            except Exception as e:
                last_err = e
                time.sleep(delay)
        raise last_err

    try:
        with _get_safe_conn() as conn:
            ensure_enrich_saved_leads_table(conn)

            # Preload existing batch saved leads into memory for domain uniqueness across pagination
            with _enrich_batch_seen_lock:
                if batch_tag not in _enrich_batch_seen_companies:
                    _enrich_batch_seen_companies[batch_tag] = {}
                    try:
                        with conn.cursor() as pre_cur:
                            pre_cur.execute(
                                "SELECT name, job_title, company, company_domain FROM enrich_saved_leads WHERE batch = %s",
                                (batch_tag,)
                            )
                            for r_name, r_title, r_comp, r_dom in pre_cur.fetchall():
                                ckey = api_mod.normalize_text(r_dom or "") or api_mod.normalize_text(r_comp or "")
                                if ckey:
                                    if ckey not in _enrich_batch_seen_companies[batch_tag]:
                                        _enrich_batch_seen_companies[batch_tag][ckey] = []
                                    _enrich_batch_seen_companies[batch_tag][ckey].append({
                                        "key": r_name,
                                        "name": r_name,
                                        "title": r_title,
                                        "score": api_mod.get_seniority_score(r_title or ""),
                                        "company": r_comp,
                                        "domain": r_dom,
                                    })
                    except Exception as ex_preload:
                        print(f"[EnrichMatcher] Notice: could not preload batch_seen for {batch_tag}: {ex_preload}", flush=True)

                seen_batch_companies = _enrich_batch_seen_companies[batch_tag]

            # Step 2: 4-Layer Deduplication Engine against CRM (`emails`, `apollo_saved_leads`, `enrich_saved_leads`, `enrich_companies`)
            extra_company_domains = [
                api_mod.extract_root_domain(comp.domain or "") or str(comp.domain or "").strip().lower()
                for comp in getattr(request, "companies", [])
                if comp.domain
            ]
            crm_matches = check_enrich_contacts_in_crm_4_layers(
                contacts, contact_domains, connection=conn, api_mod=api_mod, active_batch=batch_tag,
                extra_domains=extra_company_domains
            )

            net_new_contacts: list[EnrichContact] = []

            for c in contacts:
                d = contact_domains.get(c.key, "")
                if c.key in crm_matches:
                    existing_count += 1
                    ignored_count += 1
                    results[c.key] = crm_matches[c.key]
                elif not d:
                    # Contact has no identifiable company domain source
                    ignored_count += 1
                    results[c.key] = {
                        "exists": False,
                        "required": False,
                        "ignored": True,
                        "status": "no_domain",
                        "guardrail_status": "no_domain_source",
                        "reason": "No company domain found in Enrich lead card.",
                        "matched_domain": "",
                    }
                else:
                    # Net-new company domain candidate!
                    net_new_contacts.append(c)

            collect_mode = str(getattr(request, "collect_mode", "companies") or "companies").strip().lower()

            if collect_mode == "companies":
                # === COMPANIES COLLECTION MODE ===
                # In this mode, we target the company domain rather than person seniority.
                # Every contact whose company domain is NOT in CRM is accepted as a target company.
                for c in net_new_contacts:
                    d = contact_domains.get(c.key, "")
                    comp_name = (c.company or "").strip()
                    required_count += 1
                    results[c.key] = {
                        "exists": False,
                        "required": True,
                        "ignored": False,
                        "status": "new_company_saved",
                        "guardrail_status": "qualified_company",
                        "reason": f"Net-New Company '{comp_name}' ({d})",
                        "matched_domain": d,
                        "segment": "Target_Company",
                    }
                # Step 5 (enrich_saved_leads) is bypassed in companies mode
            else:
                # === CONTACTS COLLECTION MODE ===
                # Step 3: Batch Job Title Lookup for Net-New Candidates
                titles_to_check = [c.job_title for c in net_new_contacts if c.job_title.strip()]
                batch_title_results = api_mod.lookup_job_titles_batch(titles_to_check, connection=conn) if titles_to_check else {}

                # Prefetch Indian names for demographic filter
                prefetched_indian_db = {}
                if request.indian_name_guardrail_enabled and net_new_contacts:
                    all_names = [c.name.strip() for c in net_new_contacts if c.name and c.name.strip()]
                    if all_names:
                        prefetched_indian_db = api_mod.lookup_indian_names_batch(all_names, connection=conn)

                # Step 4: Evaluate Net-New Contacts through Guardrails & Enforce Domain Uniqueness
                for c in net_new_contacts:
                    d = contact_domains.get(c.key, "")
                    comp_name = (c.company or "").strip()
                    comp_key = api_mod.normalize_text(d) or api_mod.normalize_text(comp_name)

                    # 4.1 Demographic Filter (Option 2 Pure Indian Name Origin)
                    if request.indian_name_guardrail_enabled:
                        is_ind, ind_reason = api_mod.is_unambiguous_pure_indian_name(
                            c.name or "", connection=conn, prefetched_db_hits=prefetched_indian_db
                        )
                        if is_ind:
                            ignored_count += 1
                            results[c.key] = {
                                "exists": False,
                                "required": False,
                                "ignored": True,
                                "status": "indian_name_disqualified",
                                "guardrail_status": "indian_name_disqualified",
                                "reason": ind_reason,
                                "matched_domain": d,
                            }
                            continue

                    # 4.2 Job Title Hierarchy Check
                    t_eval = batch_title_results.get(c.job_title)
                    if not t_eval and request.title_guardrail_enabled:
                        t_eval = api_mod.evaluate_job_title_with_ai(c.job_title, connection=conn)
                    if not t_eval:
                        t_eval = {"required": True, "segment": "Target_Lead", "reason": "Approved Title", "status": "qualified"}

                    title_ok = bool(t_eval.get("required", False)) if request.title_guardrail_enabled else True
                    title_seg = t_eval.get("segment", "Target_Lead")
                    title_reason = t_eval.get("reason", "Approved decision maker title")

                    if not title_ok:
                        ignored_count += 1
                        results[c.key] = {
                            "exists": False,
                            "required": False,
                            "ignored": True,
                            "status": "disqualified_title",
                            "guardrail_status": "disqualified_title",
                            "reason": title_reason,
                            "matched_domain": d,
                            "segment": title_seg,
                        }
                        continue

                    # 4.3 Domain Uniqueness & Seniority Election (1 Contact Per Company Domain)
                    with _enrich_batch_seen_lock:
                        already_selected = seen_batch_companies.get(comp_key, [])
                        incoming_score = api_mod.get_seniority_score(c.job_title)

                        if len(already_selected) == 0:
                            required_count += 1
                            entry = {
                                "key": c.key,
                                "name": c.name,
                                "title": c.job_title,
                                "score": incoming_score,
                                "company": comp_name,
                                "domain": d,
                                "contact": c,
                                "segment": title_seg
                            }
                            seen_batch_companies[comp_key] = [entry]
                            results[c.key] = {
                                "exists": False,
                                "required": True,
                                "ignored": False,
                                "status": "qualified",
                                "guardrail_status": "qualified",
                                "reason": f"Target Lead: Net-new domain '{d}' + {title_reason}",
                                "matched_domain": d,
                                "segment": title_seg,
                            }
                        else:
                            prev_entry = already_selected[0]
                            prev_score = prev_entry.get("score", 0)

                            if incoming_score > prev_score:
                                prev_key = prev_entry.get("key")
                                if prev_key and prev_key in results:
                                    results[prev_key] = {
                                        "exists": False,
                                        "required": False,
                                        "ignored": True,
                                        "status": "company_limit_reached",
                                        "guardrail_status": "company_limit_reached",
                                        "reason": f"Replaced by higher-ranking decision maker ({c.name} - {c.job_title})",
                                        "matched_domain": d,
                                    }
                                    required_count = max(0, required_count - 1)
                                    ignored_count += 1

                                already_selected[0] = {
                                    "key": c.key,
                                    "name": c.name,
                                    "title": c.job_title,
                                    "score": incoming_score,
                                    "company": comp_name,
                                    "domain": d,
                                    "contact": c,
                                    "segment": title_seg
                                }
                                required_count += 1
                                results[c.key] = {
                                    "exists": False,
                                    "required": True,
                                    "ignored": False,
                                    "status": "qualified",
                                    "guardrail_status": "qualified",
                                    "reason": f"Target Lead: Highest-ranking decision maker for '{comp_name}'",
                                    "matched_domain": d,
                                    "segment": title_seg,
                                }
                            else:
                                ignored_count += 1
                                elected_name = prev_entry.get("name", "prior lead")
                                results[c.key] = {
                                    "exists": False,
                                    "required": False,
                                    "ignored": True,
                                    "status": "company_limit_reached",
                                    "guardrail_status": "company_limit_reached",
                                    "reason": f"Company '{comp_name}' already has lead selected ({elected_name}). Max 1 per company domain allowed.",
                                    "matched_domain": d,
                                    "segment": title_seg,
                                }

                # Step 5: Auto-Persist Winning Target Leads to MySQL table `enrich_saved_leads`
                if request.auto_save:
                    leads_to_save = []
                    for c in net_new_contacts:
                        res = results.get(c.key)
                        if res and res.get("required") is True:
                            name_parts = c.name.strip().split(" ", 1)
                            first_nm = c.first_name or (name_parts[0] if name_parts else "")
                            last_nm = c.last_name or (name_parts[1] if len(name_parts) > 1 else "")
                            d_val = contact_domains.get(c.key, "")
                            w_link = c.website_link or (f"https://{d_val}" if d_val else "")

                            leads_to_save.append((
                                batch_tag,
                                api_mod._s(c.name, 250),
                                api_mod._s(first_nm, 128),
                                api_mod._s(last_nm, 128),
                                api_mod._s(c.job_title, 250),
                                api_mod._s(c.company, 250),
                                api_mod._s(d_val, 250),
                                api_mod._s(w_link, 512),
                                api_mod._s(c.location or "", 250),
                                api_mod._s(c.linkedin_url or "", 512),
                                api_mod._s(res.get("segment") or "Target_Lead", 128)
                            ))

                    if leads_to_save:
                        try:
                            with conn.cursor() as cur:
                                cur.executemany("""
                                    INSERT INTO `enrich_saved_leads` (
                                        `batch`, `name`, `first_name`, `last_name`, `job_title`,
                                        `company`, `company_domain`, `website_link`, `location`,
                                        `linkedin_url`, `segment`
                                    )
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON DUPLICATE KEY UPDATE
                                        `job_title` = VALUES(`job_title`),
                                        `company` = VALUES(`company`),
                                        `website_link` = VALUES(`website_link`),
                                        `location` = VALUES(`location`),
                                        `segment` = VALUES(`segment`);
                                """, leads_to_save)
                                saved_to_db_count = len(leads_to_save)
                            print(f"\n[EnrichMatcher] Auto-saved {saved_to_db_count} target lead(s) into MySQL table `enrich_saved_leads` under batch '{batch_tag}'.", flush=True)
                        except Exception as ex_save:
                            print(f"[EnrichMatcher] Notice: Error auto-saving leads: {ex_save}", flush=True)

            # Step 6: In Companies Mode, Auto-Persist Net-New Company Names and Domains to `enrich_companies`
            if collect_mode == "companies" and request.auto_save:
                ensure_enrich_companies_table(conn)
                unique_companies_to_save: dict[str, tuple] = {}
                matched_crm_domains = {
                    str(m.get("matched_domain") or "").strip().lower()
                    for m in crm_matches.values()
                    if m.get("matched_domain")
                }

                def _check_domain_crm(dom: str, c_key: str | None = None) -> tuple[bool, str]:
                    if not dom:
                        return False, ""
                    if c_key and c_key in crm_matches:
                        return True, crm_matches[c_key].get("reason", "Existing in CRM (4-Layer)")
                    if dom in matched_crm_domains:
                        return True, "L1_exact_domain"
                    # L3 Trie prefix branch check
                    if getattr(api_mod, "_domain_trie", None) and api_mod._domain_trie.loaded:
                        incoming_slug = api_mod._clean_slug(dom)
                        if incoming_slug:
                            trie_match, prefix = api_mod._domain_trie.find_prefix_match(incoming_slug, min_prefix_len=4)
                            if trie_match and trie_match != dom and api_mod._is_valid_branch_match(trie_match, prefix, incoming_slug):
                                return True, f"L3_branch_of_{trie_match}"
                    # L4 DNS MX mail exchange check
                    if getattr(api_mod, "_DNS_AVAILABLE", False):
                        try:
                            with api_mod._mx_cache_lock:
                                mx_root = api_mod._mx_cache.get(dom)
                            if mx_root is None:
                                mx_root = api_mod._resolve_mx_root_domain(dom, timeout=0.2)
                            if mx_root and mx_root != dom:
                                with api_mod._crm_domain_cache_lock:
                                    if mx_root in api_mod._crm_domain_cache:
                                        return True, f"L4_mx_shared_{mx_root}"
                        except Exception:
                            pass
                    return False, ""

                # 6.1 Gather from explicit request.companies if passed
                for comp_item in getattr(request, "companies", []):
                    raw_dom = (comp_item.domain or "").strip()
                    c_dom = api_mod.extract_root_domain(raw_dom) or raw_dom.lower()
                    c_name = (comp_item.company or "").strip()
                    w_link = comp_item.website_link or (f"https://{c_dom}" if c_dom else "")
                    if c_dom:
                        is_crm, _ = _check_domain_crm(c_dom)
                        if not is_crm:
                            unique_companies_to_save[c_dom] = (
                                batch_tag,
                                api_mod._s(c_name, 250),
                                api_mod._s(c_dom, 250),
                                api_mod._s(w_link, 512),
                                "net_new",
                                "enrich_extension"
                            )

                # 6.2 Gather from contact cards on page
                for c in contacts:
                    c_dom = contact_domains.get(c.key, "").strip().lower()
                    c_name = (c.company or "").strip()
                    w_link = c.website_link or (f"https://{c_dom}" if c_dom else "")
                    if not c_dom and c_name:
                        cands = api_mod.generate_candidate_domains(c_name)
                        if cands:
                            c_dom = api_mod.extract_root_domain(cands[0]) or cands[0]

                    if c_dom:
                        is_crm, _ = _check_domain_crm(c_dom, c.key)
                        # STRICT: ONLY STORE IF NOT IN CRM!
                        if not is_crm:
                            if c_dom not in unique_companies_to_save:
                                unique_companies_to_save[c_dom] = (
                                    batch_tag,
                                    api_mod._s(c_name, 250),
                                    api_mod._s(c_dom, 250),
                                    api_mod._s(w_link, 512),
                                    "net_new",
                                    "enrich_extension"
                                )
                            elif c_name and not unique_companies_to_save[c_dom][1]:
                                prev = unique_companies_to_save[c_dom]
                                unique_companies_to_save[c_dom] = (prev[0], api_mod._s(c_name, 250), prev[2], prev[3], prev[4], prev[5])

                if unique_companies_to_save:
                    try:
                        with conn.cursor() as cur:
                            cur.executemany("""
                                INSERT INTO `enrich_companies` (
                                    `batch`, `company_name`, `domain`, `website_link`, `crm_status`, `source`
                                )
                                VALUES (%s, %s, %s, %s, %s, %s)
                                ON DUPLICATE KEY UPDATE
                                    `company_name` = IF(VALUES(`company_name`) != '' AND `company_name` = '', VALUES(`company_name`), `company_name`),
                                    `website_link` = IF(VALUES(`website_link`) != '' AND `website_link` = '', VALUES(`website_link`), `website_link`),
                                    `crm_status` = 'net_new';
                            """, list(unique_companies_to_save.values()))
                            saved_companies_count = len(unique_companies_to_save)
                        print(f"\n[EnrichMatcher] Stored {saved_companies_count} NET-NEW company/domain(s) in `enrich_companies` under batch '{batch_tag}'.", flush=True)
                    except Exception as ex_comp:
                        print(f"[EnrichMatcher] Notice: Error auto-saving net-new companies: {ex_comp}", flush=True)

                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT COUNT(*) FROM `enrich_companies` WHERE `batch` = %s AND `crm_status` = 'net_new'", (batch_tag,))
                        r_cnt = cur.fetchone()
                        batch_total_companies = int(r_cnt[0]) if r_cnt else 0
                except Exception:
                    batch_total_companies = 0
    except Exception as ex_db:
        print(f"[EnrichMatcher] Database query error: {ex_db}", flush=True)
        return {
            "batch": batch_tag,
            "total": total_received,
            "required_count": 0,
            "ignored_count": total_received,
            "existing_count": 0,
            "saved_to_db_count": 0,
            "saved_companies_count": 0,
            "batch_total_companies": 0,
            "results": {
                c.key: {
                    "exists": False,
                    "required": False,
                    "ignored": True,
                    "status": "db_error",
                    "reason": f"Database temporarily unavailable: {str(ex_db)[:90]}",
                    "matched_domain": contact_domains.get(c.key, ""),
                }
                for c in contacts
            },
            "error": f"Database unavailable: {str(ex_db)}",
            "execution_time_ms": round((time.perf_counter() - t0) * 1000, 2),
        }

    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"[EnrichMatcher] Page Processed: {total_received} Contacts | 🟢 Target: {required_count} | 🏢 Companies Saved: {saved_companies_count} (Total: {batch_total_companies}) | 🔵 Existing CRM: {existing_count} ({elapsed_ms:.1f}ms)", flush=True)

    return {
        "batch": batch_tag,
        "total": total_received,
        "required_count": required_count,
        "ignored_count": ignored_count,
        "existing_count": existing_count,
        "saved_to_db_count": saved_to_db_count,
        "saved_companies_count": saved_companies_count,
        "batch_total_companies": batch_total_companies,
        "results": results,
        "execution_time_ms": round(elapsed_ms, 2),
    }


@enrich_router.get("/enrich-batches")
def get_enrich_batches():
    """Retrieve summary of all saved Enrich lead batches from the database."""
    api_mod = _get_api_dependencies()
    try:
        with api_mod.get_connection() as conn:
            ensure_enrich_saved_leads_table(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT `batch`, COUNT(*) as total_leads, MIN(`created_at`) as started_at, MAX(`created_at`) as last_added
                    FROM `enrich_saved_leads`
                    GROUP BY `batch`
                    ORDER BY `id` DESC;
                """)
                rows = cur.fetchall()
                return {
                    "total_batches": len(rows),
                    "batches": [
                        {
                            "batch": r[0],
                            "total_leads": r[1],
                            "started_at": str(r[2]),
                            "last_added": str(r[3]),
                        }
                        for r in rows
                    ]
                }
    except Exception as e:
        return {"total_batches": 0, "batches": [], "error": str(e)}


@enrich_router.get("/enrich-companies-batches")
def get_enrich_companies_batches():
    """Retrieve summary of all saved Enrich unique companies by batch from the database."""
    api_mod = _get_api_dependencies()
    try:
        with api_mod.get_connection() as conn:
            ensure_enrich_companies_table(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT `batch`, 
                           COUNT(*) as total_companies,
                           SUM(CASE WHEN `crm_status` = 'net_new' THEN 1 ELSE 0 END) as net_new_count,
                           SUM(CASE WHEN `crm_status` = 'existing_in_crm' THEN 1 ELSE 0 END) as existing_count,
                           MIN(`created_at`) as started_at, 
                           MAX(`created_at`) as last_added
                    FROM `enrich_companies`
                    GROUP BY `batch`
                    ORDER BY `id` DESC;
                """)
                rows = cur.fetchall()
                return {
                    "total_batches": len(rows),
                    "batches": [
                        {
                            "batch": r[0],
                            "total_companies": r[1],
                            "net_new_count": int(r[2] or 0),
                            "existing_count": int(r[3] or 0),
                            "started_at": str(r[4]),
                            "last_added": str(r[5]),
                        }
                        for r in rows
                    ]
                }
    except Exception as e:
        return {"total_batches": 0, "batches": [], "error": str(e)}


class DirectCompanySaveRequest(BaseModel):
    batch: str = "enrich_batch_1"
    companies: list[EnrichCompanyItem]


@enrich_router.post("/save-enrich-companies")
def save_enrich_companies(request: DirectCompanySaveRequest):
    """Directly save unique companies and domains into enrich_companies table."""
    api_mod = _get_api_dependencies()
    batch_tag = str(request.batch or "enrich_batch_1").strip()[:64]
    if not request.companies:
        return {"batch": batch_tag, "saved": 0, "total_batch_companies": 0}

    try:
        with api_mod.get_connection() as conn:
            ensure_enrich_companies_table(conn)
            items = []
            for c in request.companies:
                dom = api_mod.extract_root_domain(c.domain or "") or c.domain.strip().lower()
                name = c.company.strip()
                link = c.website_link or (f"https://{dom}" if dom else "")
                if dom:
                    items.append((batch_tag, api_mod._s(name, 250), api_mod._s(dom, 250), api_mod._s(link, 512), "net_new", "direct_save"))
            if items:
                with conn.cursor() as cur:
                    cur.executemany("""
                        INSERT INTO `enrich_companies` (`batch`, `company_name`, `domain`, `website_link`, `crm_status`, `source`)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            `company_name` = IF(VALUES(`company_name`) != '', VALUES(`company_name`), `company_name`),
                            `website_link` = IF(VALUES(`website_link`) != '', VALUES(`website_link`), `website_link`);
                    """, items)
                    cur.execute("SELECT COUNT(*) FROM `enrich_companies` WHERE `batch` = %s", (batch_tag,))
                    total = cur.fetchone()[0]
                    return {"batch": batch_tag, "saved": len(items), "total_batch_companies": total}
            return {"batch": batch_tag, "saved": 0, "total_batch_companies": 0}
    except Exception as e:
        return {"batch": batch_tag, "error": str(e)}


@enrich_router.post("/invalidate-enrich-batch-cache")
def invalidate_enrich_batch_cache(batch: str | None = None):
    """Clear in-memory company election cache for a batch (or all batches)."""
    with _enrich_batch_seen_lock:
        if batch:
            b_tag = str(batch).strip()
            _enrich_batch_seen_companies.pop(b_tag, None)
            return {"status": "ok", "invalidated_batch": b_tag}
        else:
            _enrich_batch_seen_companies.clear()
            return {"status": "ok", "invalidated_all": True}
