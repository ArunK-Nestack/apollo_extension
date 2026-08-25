import json
import os
import re
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

# Ensure Windows stdout/stderr never crashes on non-ASCII international names
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import pymysql
import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# ============================================================
# ENVIRONMENT & CONFIGURATION
# ============================================================

load_dotenv()

_batch_counter = 0

DB_HOST = os.getenv("DB_HOST", "localhost").strip()
DB_PORT_STR = os.getenv("DB_PORT", "").strip()
if DB_PORT_STR:
    DB_PORT = int(DB_PORT_STR)
elif "rds.amazonaws.com" in DB_HOST or "mysql" in DB_HOST:
    DB_PORT = 3306
else:
    DB_PORT = 5432

DB_NAME = os.getenv("DB_NAME", "apollo_scrapers").strip()
DB_USER = os.getenv("DB_USER", "nestack").strip()
DB_PASSWORD = os.getenv("DB_PASSWORD", "").strip()
DB_TABLE = os.getenv("DB_TABLE", "emails").strip()

GUARDRAILS_ENABLED = os.getenv("GUARDRAILS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}

# Fast in-memory caches to avoid duplicate SQL queries within the same session
_crm_domain_cache: dict[str, bool] = {}  # domain -> exists_in_crm (bool)
_crm_domain_cache_lock = threading.Lock()

_title_cache: dict[str, dict] = {}  # normalized_title -> {is_required, tier, role_type}
_title_cache_lock = threading.Lock()

_schema_cache: dict[str, Any] = {}
_schema_cache_lock = threading.Lock()


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="Apollo Contact Database Checker (Deterministic)",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# DATABASE CONNECTION & SCHEMA DETECTION
# ============================================================

def is_mysql_conn(conn) -> bool:
    return isinstance(conn, pymysql.Connection) if "pymysql" in globals() else False


def get_connection():
    if DB_PORT == 3306 or "rds.amazonaws.com" in DB_HOST:
        return pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            autocommit=True,
            connect_timeout=15,
        )

    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def get_target_table_schema(conn) -> dict[str, Any]:
    global _schema_cache
    if _schema_cache:
        return _schema_cache

    with _schema_cache_lock:
        if _schema_cache:
            return _schema_cache

        target_table = DB_TABLE
        with conn.cursor() as cur:
            if is_mysql_conn(conn):
                cur.execute("""
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s;
                """, (DB_NAME, target_table))
                cols = [row[0] for row in cur.fetchall()]
            else:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = %s;
                """, (target_table,))
                cols = [row[0] for row in cur.fetchall()]

            _schema_cache = {
                "table_name": target_table,
                "columns": cols,
                "email": "email" if "email" in cols else cols[0] if cols else "email",
                "name": "full_name" if "full_name" in cols else "name" if "name" in cols else None,
                "email_domain": "domain" if "domain" in cols else "email_domain" if "email_domain" in cols else "domain",
            }
            print(f"[ContactChecker] DB Schema initialized for '{target_table}': {_schema_cache}", flush=True)
            return _schema_cache


# ============================================================
# DETERMINISTIC TEXT & DOMAIN NORMALIZATION
# ============================================================

LEGAL_SUFFIXES = [
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "technologies", "technology", "tech", "services", "solutions", "group",
    "holdings", "holding", "pvt", "private", "gmbh", "co", "company",
    "international", "global", "consulting", "enterprises", "media", "labs"
]

COMMON_TLDS = [
    ".com", ".io", ".ai", ".co", ".net", ".org", ".app", ".dev",
    ".tech", ".co.uk", ".com.au", ".ca", ".de", ".fr", ".in", ".us"
]


def normalize_text(value: str) -> str:
    """Strip accents, punctuation, and non-alphanumeric chars."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value).strip().lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", value)


def normalize_domain(domain: str) -> str:
    """Normalize a domain string (e.g. 'https://www.Liquid.AI/' -> 'liquid.ai')."""
    if not domain:
        return ""
    dom = domain.strip().lower()
    if dom.startswith("http://") or dom.startswith("https://"):
        try:
            parsed = urlparse(dom)
            dom = parsed.netloc or parsed.path
        except Exception:
            pass
    dom = re.sub(r"^www\.", "", dom)
    dom = dom.split("/")[0].split(":")[0].strip()
    return dom


def clean_company_name(company: str) -> str:
    """Clean company name for heuristic matching (e.g. 'Datadog, Inc.' -> 'Datadog')."""
    if not company:
        return ""
    text = re.sub(r"[^\w\s.-]", " ", str(company)).strip()
    words = text.split()
    while words and words[-1].lower().rstrip(".") in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words).strip() or text


def generate_candidate_domains(company_name: str) -> list[str]:
    """
    Deterministically generate candidate domains from a company name.
    Examples:
      - 'warnerpacific.com' -> ['warnerpacific.com']
      - 'Liquid AI' -> ['liquid.ai', 'liquidai.com', 'liquid.com']
      - 'Datadog, Inc.' -> ['datadog.com', 'datadoghq.com', 'datadog.io']
    """
    if not company_name:
        return []

    comp_raw = company_name.strip().lower()
    candidates = []

    # 1. Check if the company text itself is an explicit domain
    for tld in COMMON_TLDS:
        if comp_raw.endswith(tld) or f"{tld}/" in comp_raw:
            norm_dom = normalize_domain(comp_raw)
            if norm_dom:
                candidates.append(norm_dom)
                return candidates

    cleaned = clean_company_name(comp_raw)
    norm_comp = normalize_text(cleaned)
    if not norm_comp:
        return []

    # 2. Check for embedded TLD words in the company name (e.g. 'Liquid AI' -> 'liquid.ai')
    if comp_raw.endswith(" ai") or comp_raw.endswith(" tech") or comp_raw.endswith(" io") or comp_raw.endswith(" app"):
        prefix = clean_company_name(comp_raw.rsplit(" ", 1)[0])
        tld_part = comp_raw.rsplit(" ", 1)[1]
        norm_prefix = normalize_text(prefix)
        if norm_prefix:
            candidates.append(f"{norm_prefix}.{tld_part}")

    # 3. Standard candidate permutations
    candidates.append(f"{norm_comp}.com")
    candidates.append(f"{norm_comp}.io")
    candidates.append(f"{norm_comp}.ai")
    candidates.append(f"{norm_comp}hq.com")
    candidates.append(f"{norm_comp}tech.com")

    # Remove duplicates while preserving order
    seen = set()
    result = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)

    return result


# ============================================================
# DETERMINISTIC CRM DOMAIN CHECKER (7.28M Database)
# ============================================================

def check_domains_in_crm_batch(candidate_domains: list[str], connection=None) -> tuple[bool, str]:
    """
    Check if any of the candidate domains exist in the CRM 'emails' table.
    Uses in-memory cache + fast indexed SQL query.
    Returns: (exists_in_crm: bool, matched_domain: str)
    """
    if not candidate_domains:
        return False, ""

    # 1. Check memory cache first
    with _crm_domain_cache_lock:
        for dom in candidate_domains:
            if dom in _crm_domain_cache:
                if _crm_domain_cache[dom] is True:
                    return True, dom

    # 2. Query MySQL / Postgres
    def do_query(conn):
        schema = get_target_table_schema(conn)
        tbl_name = schema["table_name"]
        domain_col = schema["email_domain"]

        with conn.cursor() as cur:
            if is_mysql_conn(conn):
                format_strings = ",".join(["%s"] * len(candidate_domains))
                query = f"SELECT `{domain_col}` FROM `{tbl_name}` WHERE `{domain_col}` IN ({format_strings}) LIMIT 1;"
                cur.execute(query, tuple(candidate_domains))
            else:
                query = f"SELECT {domain_col} FROM \"{tbl_name}\" WHERE {domain_col} = ANY(%s) LIMIT 1;"
                cur.execute(query, (candidate_domains,))

            row = cur.fetchone()
            if row and row[0]:
                found_domain = str(row[0]).strip().lower()
                with _crm_domain_cache_lock:
                    _crm_domain_cache[found_domain] = True
                return True, found_domain

            # Cache negatives
            with _crm_domain_cache_lock:
                for dom in candidate_domains:
                    _crm_domain_cache[dom] = False
            return False, ""

    if connection:
        return do_query(connection)
    else:
        with get_connection() as conn:
            return do_query(conn)


# ============================================================
# LAYER 1: TOP-TIER EXECUTIVE SUBSTRING PATTERNS
# ============================================================

# Order matters: check more specific multi-word patterns first!
VERY_HIGH_PRIORITY_SUBSTRINGS = [
    r"\bchief\b",
    r"\bceo\b",
    r"\bowner\b",
    r"\bfounder\b",
    r"\bco-founder\b",
    r"\bpartner\b",
    r"\bmanaging director\b",
    r"\bexecutive director\b",
    # Vice President variations
    r"\bexecutive vice president\b",
    r"\bsenior vice president\b",
    r"\bvice president\b",
    r"\bvice-president\b",
    r"\bassistant vice president\b",
    # VP acronym variations
    r"\bevp\b",
    r"\be\.v\.p\.\b",
    r"\bsvp\b",
    r"\bs\.v\.p\.\b",
    r"\bavp\b",
    r"\ba\.v\.p\.\b",
    r"\bvp\b",
    r"\bv\.p\.\b",
    # President (checked after Vice President so VP isn't mislabeled)
    r"\bpresident\b",
]

HIGH_PRIORITY_SUBSTRINGS = [
    r"\bdirector\b",
    r"\bhead of\b",
    r"\bglobal head\b",
    r"\bregional head\b",
    r"\bcountry head\b",
    r"\bgeneral manager\b",
    r"\bgm\b",
    r"\bg\.m\.\b",
]


def match_priority_substrings(job_title: str) -> tuple[bool, str, str] | None:
    """
    Layer 1: Fast regex/substring match for top-tier executive titles.
    Returns: (is_required: bool, priority_level: str, matched_substring: str) or None
    """
    if not job_title:
        return None
    t_clean = job_title.strip().lower()
    # Normalize dotted acronyms (e.g. "V.P." -> "vp", "S.V.P." -> "svp", "G.M." -> "gm")
    t_nodots = re.sub(r"\.", "", t_clean)

    # 1. Very High Priority Substrings (Chief, CEO, VP, Vice President, President, Founder, etc.)
    for pat in VERY_HIGH_PRIORITY_SUBSTRINGS:
        if re.search(pat, t_clean) or re.search(pat, t_nodots):
            sub = pat.replace(r"\b", "").replace(r"\.", "").replace(r"\-", "-").strip()
            return True, "Very High", sub

    # 2. High Priority Substrings (Director, Head of, GM, etc.)
    for pat in HIGH_PRIORITY_SUBSTRINGS:
        if re.search(pat, t_clean) or re.search(pat, t_nodots):
            sub = pat.replace(r"\b", "").replace(r"\.", "").replace(r"\-", "-").strip()
            return True, "High", sub

    return None


# ============================================================
# 2-LAYER JOB TITLE EVALUATOR (SUBSTRINGS + DATABASE LOOKUP)
# ============================================================

def lookup_job_title_in_db(job_title: str, connection=None) -> dict:
    """
    2-Layer Job Title Evaluation (Zero AI):
    Layer 1: Top-tier executive substring check (Chief, CEO, President, Owner, Founder, Partner, Managing Director, VP, Director, Head of, GM).
    Layer 2: Database table lookup against 'job_title_guardrails' (31,385 titles across Segments).
    """
    if not job_title:
        return {"required": False, "segment": "Unspecified", "reason": "No title specified"}

    norm_title = normalize_text(job_title)

    with _title_cache_lock:
        if norm_title in _title_cache:
            return _title_cache[norm_title]

    # --- LAYER 1: PRIORITY SUBSTRING CHECK ---
    layer1_match = match_priority_substrings(job_title)
    if layer1_match:
        is_req, prio, sub = layer1_match
        res = {
            "required": True,
            "segment": f"Prio_{prio.replace(' ', '_')}_{sub.title()}",
            "reason": f"Layer 1 Substring Match: '{sub}' ({prio} Priority)",
        }
        with _title_cache_lock:
            _title_cache[norm_title] = res
        return res

    # --- LAYER 2: DATABASE TABLE LOOKUP ---
    def do_query(conn):
        try:
            with conn.cursor() as cur:
                if is_mysql_conn(conn):
                    cur.execute("""
                        SELECT is_required, segment
                        FROM job_title_guardrails
                        WHERE normalized_title = %s
                        LIMIT 1;
                    """, (norm_title,))
                else:
                    cur.execute("""
                        SELECT is_required, segment
                        FROM job_title_guardrails
                        WHERE normalized_title = %s
                        LIMIT 1;
                    """, (norm_title,))

                row = cur.fetchone()
                if row:
                    is_req = bool(row[0])
                    seg_name = row[1] or ""
                    res = {
                        "required": is_req,
                        "segment": seg_name,
                        "reason": f"Layer 2 DB Segment: {seg_name} ({'Required Prio 1/2' if is_req else 'Disqualified Prio 3/4'})",
                    }
                    with _title_cache_lock:
                        _title_cache[norm_title] = res
                    return res
        except Exception:
            pass

        # Strict Table Whitelist: Not matched in Layer 1 or Layer 2
        fallback = {
            "required": False,
            "segment": "Not_In_Approved_List",
            "reason": f"Title '{job_title}' is not in Layer 1 priority keywords or approved database segments.",
        }

        with _title_cache_lock:
            _title_cache[norm_title] = fallback
        return fallback

    if connection:
        return do_query(connection)
    else:
        with get_connection() as conn:
            return do_query(conn)


# ============================================================
# API MODELS & MATCHING ENDPOINT
# ============================================================

class ApolloContact(BaseModel):
    key: str
    name: str = ""
    first_name: str | None = None
    last_name: str | None = None
    job_title: str = ""
    company: str = ""
    location: str | None = ""
    company_domain: str | None = None
    linkedin_url: str | None = None
    apollo_profile_url: str | None = None


class ApolloMatchRequest(BaseModel):
    contacts: list[ApolloContact]
    title_guardrail_enabled: bool = False
    indian_name_guardrail_enabled: bool = False


@app.get("/health")
def health_check():
    return {"status": "ok", "engine": "deterministic_v2"}


@app.post("/match-apollo")
def match_apollo(request: ApolloMatchRequest):
    global _batch_counter
    _batch_counter += 1
    batch_num = _batch_counter
    t0 = time.perf_counter()

    contacts = request.contacts
    total_received = len(contacts)

    results = {}
    existing_count = 0
    required_count = 0
    ignored_count = 0

    print("\n" + "=" * 80, flush=True)
    print(f">>> [BATCH #{batch_num}] INGESTED {total_received} APOLLO CONTACT(S)", flush=True)
    print(f"    Engine: 100% Deterministic (Zero AI) | Title Filter: {'ON' if request.title_guardrail_enabled else 'OFF'}", flush=True)
    print("=" * 80, flush=True)

    seen_required_companies: dict[str, list[str]] = {}  # comp_key -> list of selected contact names
    max_contacts_per_comp = int(os.getenv("MAX_CONTACTS_PER_COMPANY", "1"))

    with get_connection() as conn:
        # Pre-initialize table schema
        get_target_table_schema(conn)

        for idx, contact in enumerate(contacts, 1):
            comp_name = contact.company.strip()
            title_name = contact.job_title.strip()

            # 1. Deterministic Domain Extraction & Generation
            candidate_domains = []
            if contact.company_domain:
                norm_d = normalize_domain(contact.company_domain)
                if norm_d:
                    candidate_domains.append(norm_d)

            for d in generate_candidate_domains(comp_name):
                if d not in candidate_domains:
                    candidate_domains.append(d)

            primary_domain = candidate_domains[0] if candidate_domains else (normalize_text(comp_name) + ".com")
            comp_key = normalize_text(primary_domain) or normalize_text(comp_name)

            # ----------------------------------------------------
            # STEP 1: JOB TITLE CHECK (If Title Filter Enabled)
            # ----------------------------------------------------
            title_ok = True
            title_reason = "Net-new company domain"
            title_segment = "Approved"

            if request.title_guardrail_enabled and title_name:
                title_info = lookup_job_title_in_db(title_name, connection=conn)
                title_ok = title_info.get("required", False)
                title_reason = title_info.get("reason", "")
                title_segment = title_info.get("segment", "")

                if not title_ok:
                    ignored_count += 1
                    results[contact.key] = {
                        "exists": False,
                        "required": False,
                        "ignored": True,
                        "guardrail_status": "disqualified_title",
                        "segment": title_segment,
                        "guardrail_reason": title_reason,
                        "matched_domain": primary_domain,
                    }
                    print(f"[{idx:02d}/{total_received:02d}] {contact.name} @ {comp_name} ({title_name}) -> [DISQUALIFIED TITLE: {title_segment}]", flush=True)
                    continue

            # ----------------------------------------------------
            # STEP 2: CRM DOMAIN LOOKUP (Against 7.28M Database)
            # ----------------------------------------------------
            domain_in_crm, matched_crm_domain = check_domains_in_crm_batch(candidate_domains, connection=conn)

            if domain_in_crm:
                ignored_count += 1
                results[contact.key] = {
                    "exists": True,
                    "required": False,
                    "ignored": True,
                    "guardrail_status": "domain_already_in_db",
                    "guardrail_reason": f"Company domain '{matched_crm_domain}' already exists in CRM database.",
                    "matched_domain": matched_crm_domain,
                    "matched_db_domain": matched_crm_domain,
                }
                print(f"[{idx:02d}/{total_received:02d}] {contact.name} @ {comp_name} -> [EXISTING DOMAIN: {matched_crm_domain}]", flush=True)
                continue

            # ----------------------------------------------------
            # STEP 3: 1 CONTACT PER COMPANY DEDUPLICATION
            # ----------------------------------------------------
            already_selected = seen_required_companies.get(comp_key, [])
            if len(already_selected) >= max_contacts_per_comp:
                ignored_count += 1
                results[contact.key] = {
                    "exists": False,
                    "required": False,
                    "ignored": True,
                    "guardrail_status": "company_limit_reached",
                    "guardrail_reason": f"Company '{comp_name}' already has {len(already_selected)} contact(s) selected ({', '.join(already_selected)}). Max {max_contacts_per_comp} per company allowed.",
                    "matched_domain": primary_domain,
                    "segment": title_segment,
                }
                print(f"[{idx:02d}/{total_received:02d}] {contact.name} @ {comp_name} -> [IGNORED: 1 Contact/Company Limit (already selected {already_selected[0]})]", flush=True)
            else:
                seen_required_companies.setdefault(comp_key, []).append(contact.name or f"Contact #{idx}")
                required_count += 1
                results[contact.key] = {
                    "exists": False,
                    "required": True,
                    "ignored": False,
                    "guardrail_status": "qualified",
                    "segment": title_segment,
                    "guardrail_reason": title_reason,
                    "matched_domain": primary_domain,
                }
                print(f"[{idx:02d}/{total_received:02d}] {contact.name} @ {comp_name} ({title_name}) -> [REQUIRED LEAD: {primary_domain} ({title_segment})]", flush=True)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    print("-" * 80, flush=True)
    print(f"[SUMMARY] Batch #{batch_num} completed in {elapsed_ms:.1f}ms! (Processed: {total_received}, Required: {required_count}, Existing/Ignored: {ignored_count})", flush=True)
    print("=" * 80 + "\n", flush=True)

    return {
        "status": "success",
        "batch_id": batch_num,
        "results": results,
        "summary": {
            "contacts_received": total_received,
            "contacts_processed": total_received,
            "existing": existing_count,
            "required": required_count,
            "ignored": ignored_count,
            "execution_time_ms": round(elapsed_ms, 2),
        },
        "activity": [],
    }


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print(">>> [ContactChecker API] Starting Pure Deterministic Engine (Port 8000)")
    print("=" * 70 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
