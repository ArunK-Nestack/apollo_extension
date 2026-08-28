import csv
import io
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
from openai import OpenAI
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

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_DOMAIN_MODEL = os.getenv("OPENAI_DOMAIN_MODEL", "gpt-4o-mini").strip()

# Fast in-memory caches to avoid duplicate SQL queries within the same session
_company_domain_cache: dict[str, str] = {}  # normalized_company -> domain
_company_domain_cache_lock = threading.Lock()

_crm_domain_cache: dict[str, bool] = {}  # domain -> exists_in_crm (bool)
_crm_domain_cache_lock = threading.Lock()

_title_cache: dict[str, dict] = {}  # normalized_title -> {is_required, segment, status, reason}
_title_cache_lock = threading.Lock()

_indian_name_cache: dict[str, tuple[bool, str]] = {}  # normalized_name -> (is_pure_indian, reason)
_indian_name_cache_lock = threading.Lock()

_schema_cache: dict[str, Any] = {}
_schema_cache_lock = threading.Lock()

_batch_seen_companies: dict[str, dict[str, list[str]]] = {}  # batch_tag -> (comp_key -> list of contact names)
_batch_seen_companies_lock = threading.Lock()

BLOCKER_KEYWORDS = ("compliance", "legal", "regulatory", "procurement", "privacy", "gdpr", "grc", "trade", "ethics", "audit")

LLM_SYSTEM_PROMPT = """Classify B2B job titles into sales segments.
Req (r=1): A1_Signer (C-suite/board), A2_Budget_Holder (VP/SVP/EVP), A3_Approver (Director), B1_Champion / B1_Champion_Technical (Manager/Lead/Architect), B2_Champion_Commercial (Commercial/Sales/RevOps Lead), B3_Technical_Evaluator (Sr Engineer/Dev), B4_Process_Owner (PMO/Prog Mgr), C1_User (Analyst/IC/Specialist), D1_Door_Opener (CoS/EA), D2_Regional_Leader (Regional/Country Dir).
NotReq (r=0): X1_Procurement, X2_Security_Privacy, X3_Compliance_Quality, C2_Entry (Intern/Junior/Assoc), A0_Board, X_Blocker.

Confidence & Routing:
- c=H (High) -> a=Auto-Accept
- c=M (Medium) -> a=Review-Queue
- c=L (Low) -> a=Unclassified-Exclude
- Blocker/compliance word without exact rule -> a=Hard-Stop-Manual-Review, r=0, c=L

Output plain CSV lines (no markdown):
index,segment,is_required(1|0),confidence(H|M|L),routing_action"""


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="Apollo Lead Processing & Resolution Engine",
    version="3.0.0",
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
    """Clean company name for heuristic matching (e.g. 'Datadog, Inc. · 50 employees' -> 'Datadog')."""
    if not company:
        return ""
    text = str(company).strip()
    # Strip employee count suffixes like "· 150 employees" or "• 50 employees"
    text = re.sub(r"[·•|].*?(?:employees|people|workers|emp).*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[-–—]\s*\d+[\d,]*\s*(?:employees|people|emp).*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*\((?:formerly|yc|acquired|seed|series\s+[a-z]).*?\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w\s.-]", " ", text).strip()
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
# REFINED DOMAIN LOOKUP CHAIN (detected_companies Table)
# ============================================================

def ensure_detected_companies_table(conn):
    """Ensure the detected_companies table exists for storing detected company names, website links, and domains."""
    try:
        with conn.cursor() as cur:
            if is_mysql_conn(conn):
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS `detected_companies` (
                        `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                        `company_name` VARCHAR(255) NOT NULL DEFAULT '',
                        `normalized_company` VARCHAR(255) NOT NULL DEFAULT '',
                        `website_link` VARCHAR(512) DEFAULT '',
                        `domain` VARCHAR(255) NOT NULL DEFAULT '',
                        `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX `idx_normalized_company` (`normalized_company`),
                        INDEX `idx_domain` (`domain`),
                        UNIQUE KEY `unique_company_domain` (`normalized_company`(128), `domain`(128))
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)
            else:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS detected_companies (
                        id SERIAL PRIMARY KEY,
                        company_name VARCHAR(255) NOT NULL DEFAULT '',
                        normalized_company VARCHAR(255) NOT NULL DEFAULT '',
                        website_link VARCHAR(512) DEFAULT '',
                        domain VARCHAR(255) NOT NULL DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT unique_company_domain UNIQUE (normalized_company, domain)
                    );
                """)
    except Exception as e:
        print(f"[ContactChecker] Notice: ensure detected_companies table error: {e}", flush=True)


def resolve_company_domains(contacts: list[Any], connection=None) -> tuple[dict[str, str], dict[str, str]]:
    """
    Refined Domain Lookup Chain:
      1. Query 'detected_companies' table / L1 cache using normalized company name.
      2. If found in 'detected_companies', reuse preserved domain.
      3. If NOT found in 'detected_companies', extract domain from Apollo website_link (or candidate generator)
         and insert (company_name, normalized_company, website_link, domain) into 'detected_companies'.
    Returns:
      - contact_primary_domain: dict[contact_key -> domain]
      - contact_website_link: dict[contact_key -> website_link]
    """
    if not contacts:
        return {}, {}

    contact_primary_domain: dict[str, str] = {}
    contact_website_link: dict[str, str] = {}

    missing_norm_comps = set()
    for c in contacts:
        comp_name = clean_company_name(c.company)
        norm_comp = normalize_text(comp_name)
        with _company_domain_cache_lock:
            if norm_comp in _company_domain_cache:
                contact_primary_domain[c.key] = _company_domain_cache[norm_comp]
            else:
                if norm_comp:
                    missing_norm_comps.add(norm_comp)

    # Batch query detected_companies DB table for cache misses
    if missing_norm_comps:
        def do_comp_query(conn):
            ensure_detected_companies_table(conn)
            try:
                with conn.cursor() as cur:
                    if is_mysql_conn(conn):
                        format_strings = ",".join(["%s"] * len(missing_norm_comps))
                        sql = f"SELECT `normalized_company`, `domain` FROM `detected_companies` WHERE `normalized_company` IN ({format_strings});"
                        cur.execute(sql, tuple(missing_norm_comps))
                    else:
                        sql = 'SELECT normalized_company, domain FROM "detected_companies" WHERE normalized_company = ANY(%s);'
                        cur.execute(sql, (list(missing_norm_comps),))

                    rows = cur.fetchall()
                    with _company_domain_cache_lock:
                        for row in rows:
                            if row and len(row) >= 2:
                                n_c = str(row[0]).strip().lower()
                                d_v = str(row[1]).strip().lower()
                                if n_c and d_v:
                                    _company_domain_cache[n_c] = d_v
            except Exception as e:
                print(f"[ContactChecker] Notice: detected_companies lookup: {e}", flush=True)

        if connection:
            do_comp_query(connection)
        else:
            with get_connection() as conn:
                do_comp_query(conn)

    # For contacts without domain, extract from website_link and persist to detected_companies
    new_records_to_insert = []
    for c in contacts:
        comp_name = clean_company_name(c.company)
        norm_comp = normalize_text(comp_name)

        resolved_domain = _company_domain_cache.get(norm_comp)
        web_link = (getattr(c, "website_link", None) or "").strip()

        if not resolved_domain:
            if web_link:
                resolved_domain = normalize_domain(web_link)
            elif getattr(c, "company_domain", None):
                resolved_domain = normalize_domain(c.company_domain)

            if not resolved_domain:
                cand_doms = generate_candidate_domains(comp_name)
                resolved_domain = cand_doms[0] if cand_doms else (norm_comp + ".com" if norm_comp else "")

            if resolved_domain and norm_comp:
                with _company_domain_cache_lock:
                    _company_domain_cache[norm_comp] = resolved_domain

                new_records_to_insert.append((
                    comp_name[:255],
                    norm_comp[:255],
                    web_link[:512] or (f"https://{resolved_domain}" if resolved_domain else ""),
                    resolved_domain[:255],
                ))

        contact_primary_domain[c.key] = resolved_domain or (norm_comp + ".com" if norm_comp else "")
        contact_website_link[c.key] = web_link or (f"https://{resolved_domain}" if resolved_domain else "")

    if new_records_to_insert:
        def do_insert_comps(conn):
            ensure_detected_companies_table(conn)
            try:
                with conn.cursor() as cur:
                    if is_mysql_conn(conn):
                        sql = """
                            INSERT INTO `detected_companies` (`company_name`, `normalized_company`, `website_link`, `domain`)
                            VALUES (%s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE
                                `website_link` = IF(VALUES(`website_link`) != '', VALUES(`website_link`), `website_link`),
                                `domain` = VALUES(`domain`),
                                `updated_at` = CURRENT_TIMESTAMP;
                        """
                    else:
                        sql = """
                            INSERT INTO detected_companies (company_name, normalized_company, website_link, domain)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (normalized_company, domain) DO UPDATE
                                SET website_link = CASE WHEN EXCLUDED.website_link != '' THEN EXCLUDED.website_link ELSE detected_companies.website_link END,
                                    updated_at = CURRENT_TIMESTAMP;
                        """
                    cur.executemany(sql, new_records_to_insert)
                    print(f"[ContactChecker] Auto-persisted {len(new_records_to_insert)} new record(s) into `detected_companies`.", flush=True)
            except Exception as e:
                print(f"[ContactChecker] Notice: insert detected_companies error: {e}", flush=True)

        if connection:
            do_insert_comps(connection)
        else:
            with get_connection() as conn:
                do_insert_comps(conn)

    return contact_primary_domain, contact_website_link


# ============================================================
# DETERMINISTIC CRM DOMAIN CHECKER (7.28M Database)
# ============================================================

def check_domains_in_crm_batch(candidate_domains: list[str], connection=None) -> tuple[bool, str]:
    """
    Check if any candidate domains exist in the CRM 'emails' table.
    Uses L1 memory cache + fast indexed SQL query.
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

VERY_HIGH_PRIORITY_SUBSTRINGS = [
    r"\bchief\b",
    r"\bceo\b",
    r"\bcto\b",
    r"\bcfo\b",
    r"\bcoo\b",
    r"\bcro\b",
    r"\bcmo\b",
    r"\bcpo\b",
    r"\bcio\b",
    r"\bciso\b",
    r"\bowner\b",
    r"\bfounder\b",
    r"\bco-founder\b",
    r"\bpartner\b",
    r"\bmanaging director\b",
    r"\bexecutive director\b",
    r"\bexecutive vice president\b",
    r"\bsenior vice president\b",
    r"\bvice president\b",
    r"\bvice-president\b",
    r"\bassistant vice president\b",
    r"\bevp\b",
    r"\be\.v\.p\.\b",
    r"\bsvp\b",
    r"\bs\.v\.p\.\b",
    r"\bavp\b",
    r"\ba\.v\.p\.\b",
    r"\bvp\b",
    r"\bv\.p\.\b",
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


EXCLUDED_ENTRY_SUBSTRINGS = [
    r"\bintern\b",
    r"\binternship\b",
    r"\btrainee\b",
    r"\bapprentice\b",
    r"\bstudent\b",
]


def match_priority_substrings(job_title: str) -> tuple[bool, str, str] | None:
    """
    Fast regex/substring match for executive and excluded titles.
    Returns: (is_required: bool, priority_level: str, matched_substring: str) or None
    """
    if not job_title:
        return None
    t_clean = job_title.strip().lower()
    t_nodots = re.sub(r"\.", "", t_clean)

    # 1. Immediate disqualification for explicit intern/entry-level keywords
    for pat in EXCLUDED_ENTRY_SUBSTRINGS:
        if re.search(pat, t_clean) or re.search(pat, t_nodots):
            sub = pat.replace(r"\b", "").replace(r"\.", "").replace(r"\-", "-").strip()
            return False, "Excluded", sub

    # 2. Very High Priority Substrings (C-suite, VP, Founder, etc.)
    for pat in VERY_HIGH_PRIORITY_SUBSTRINGS:
        if re.search(pat, t_clean) or re.search(pat, t_nodots):
            sub = pat.replace(r"\b", "").replace(r"\.", "").replace(r"\-", "-").strip()
            return True, "Very High", sub

    # 3. High Priority Substrings (Director, Head of, GM, etc.)
    for pat in HIGH_PRIORITY_SUBSTRINGS:
        if re.search(pat, t_clean) or re.search(pat, t_nodots):
            sub = pat.replace(r"\b", "").replace(r"\.", "").replace(r"\-", "-").strip()
            return True, "High", sub

    return None


# ============================================================
# OPTION 2: PURE INDIAN NAME ORIGIN FILTER & EDGE-CASE SAFETY
# ============================================================

PURE_INDIAN_SURNAMES = {
    "sharma", "patel", "rao", "mukherjee", "agarwal", "agrawal", "gupta", "singh",
    "verma", "reddy", "kumar", "iyer", "joshi", "mehta", "shah", "chatterjee",
    "banerjee", "nair", "menon", "bhattacharya", "mishra", "pandey", "yadav",
    "chowdhury", "choudhury", "choudhary", "bhatia", "kapoor", "khanna", "malhotra",
    "bose", "sen", "sengupta", "das", "dasgupta", "saxena", "tiwari", "tripathi",
    "shukla", "dubey", "chaubey", "narayan", "natarajan", "raman", "krishnan",
    "subramanian", "venkataraman", "swamy", "naidu", "shetty", "hegde", "pai",
    "kulkarni", "deshmukh", "patil", "jadhav", "pawar", "shinde", "gaikwad",
    "prasad", "sinha", "srivastava", "chawla", "arora", "sethi", "sood", "puri",
    "deshpande", "gokhale", "bhave", "apte", "gadgil", "kelkar", "chawla"
}

SAFE_EDGE_CASE_SURNAMES = {
    "dsouza", "d souza", "fernandes", "pinto", "pereira", "lobo", "albuquerque",
    "coutinho", "braganza", "rodrigues", "silva", "costa", "souza", "dias", "gonsalves",
    "sheikh", "shaikh", "mistry", "poonawalla", "wadia", "godrej", "tata", "contractor",
    "merchant", "engineer", "vakil", "al", "alsayed", "altamimi", "khan"
}

SAFE_GLOBAL_FIRST_NAMES = {
    "john", "david", "michael", "james", "robert", "william", "richard", "thomas",
    "charles", "daniel", "matthew", "anthony", "mark", "donald", "steven", "paul",
    "andrew", "joshua", "kenneth", "kevin", "brian", "george", "edward", "ronald",
    "timothy", "jason", "jeffrey", "ryan", "jacob", "gary", "nicholas", "eric",
    "stephen", "jonathan", "larry", "justin", "scott", "brandon", "frank", "benjamin",
    "gregory", "samuel", "raymond", "patrick", "alexander", "jack", "dennis", "jerry",
    "alice", "sarah", "emma", "olivia", "sophia", "isabella", "charlotte", "amelia",
    "mia", "harper", "evelyn", "abigail", "emily", "elizabeth", "mila", "ella",
    "avery", "sofia", "camila", "aria", "scarlett", "victoria", "madison", "luna",
    "grace", "chloe", "penelope", "layla", "riley", "zoey", "nora", "lily",
    "eleanor", "hannah", "lillian", "addison", "aubrey", "ellie", "stella", "natalie",
    "zoe", "leah", "hazel", "violet", "aurora", "savannah", "audrey", "brooklyn",
    "pierre", "hans", "jean", "lucas", "mateo", "chen", "lin", "wang", "zhang", "liu",
    "yang", "huang", "wu", "zhou", "xu", "sun", "ma", "zhu", "hu", "guo", "he", "gao"
}


def is_unambiguous_pure_indian_name(full_name: str, connection=None) -> tuple[bool, str]:
    """
    Option 2: Strict Pure Indian Name Origin Evaluation with Edge-Case Protection.
    Returns: (is_pure_indian: bool, reason: str)
      - True -> Pure Indian Name Origin (Excluded / Ignored)
      - False -> Foreign / Anglo / Goan Christian / Parsi / Global (Preserved as Required)
    """
    if not full_name:
        return False, "No name specified"

    norm_name = normalize_text(full_name)
    if not norm_name:
        return False, "Empty normalized name"

    with _indian_name_cache_lock:
        if norm_name in _indian_name_cache:
            return _indian_name_cache[norm_name]

    name_parts = full_name.strip().lower().split()
    if not name_parts:
        return False, "Invalid name"

    first_name = name_parts[0]
    last_name = name_parts[-1] if len(name_parts) > 1 else ""

    # 1. Check for Goan Christian / Mangalorean / Parsi / Arab / Global safe exceptions
    for safe_sur in SAFE_EDGE_CASE_SURNAMES:
        if safe_sur in last_name or safe_sur in full_name.lower():
            res = (False, f"Safe Edge-Case Origin: '{last_name.title()}' preserved (Goan/Parsi/Global)")
            with _indian_name_cache_lock:
                _indian_name_cache[norm_name] = res
            return res

    # 2. Check for unmistakable Western / Global first names
    if first_name in SAFE_GLOBAL_FIRST_NAMES and last_name not in PURE_INDIAN_SURNAMES:
        res = (False, f"Foreign / Global Name: '{full_name}' preserved")
        with _indian_name_cache_lock:
            _indian_name_cache[norm_name] = res
        return res

    # 3. Check for unmistakable Pure Indian surnames
    for ind_sur in PURE_INDIAN_SURNAMES:
        if ind_sur == last_name or (len(name_parts) > 1 and ind_sur in [p.lower() for p in name_parts]):
            res = (True, f"Demographic Filter: Pure Indian Name Origin ('{ind_sur.title()}')")
            with _indian_name_cache_lock:
                _indian_name_cache[norm_name] = res
            return res

    # Default conservative policy: Non-Indian or Ambiguous is PRESERVED
    res = (False, f"Global Name: '{full_name}' preserved")
    with _indian_name_cache_lock:
        _indian_name_cache[norm_name] = res
    return res


# ============================================================
# NOVEL TITLE CLASSIFIER (CAVEMAN + PONYTAIL COMPACT PROMPTING)
# ============================================================

def classify_novel_titles_compact_llm(novel_titles: list[str], connection=None) -> tuple[dict[str, dict], dict]:
    """Call gpt-4o-mini for unique unrecognized titles, auto-insert into MySQL job_title_guardrails table."""
    if not novel_titles or not OPENAI_API_KEY:
        return {}, {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "latency_ms": 0.0,
            "high_conf": 0,
            "med_conf": 0,
            "low_conf": 0,
        }

    client = OpenAI(api_key=OPENAI_API_KEY)
    user_prompt = "Classify these titles:\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(novel_titles))

    t0 = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=OPENAI_DOMAIN_MODEL,
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=1500,
            temperature=0.0
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        raw_text = response.choices[0].message.content or ""
        raw_text = raw_text.replace("```csv", "").replace("```", "").strip()
        usage = response.usage
        prompt_tokens = usage.prompt_tokens
        comp_tokens = usage.completion_tokens
        total_tokens = usage.total_tokens
    except Exception as e:
        print(f"[LLM Error] Failed to classify novel titles with {OPENAI_DOMAIN_MODEL}: {e}", flush=True)
        return {}, {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "latency_ms": 0.0,
            "high_conf": 0,
            "med_conf": 0,
            "low_conf": 0,
        }

    parsed_items = {}
    csv_reader = csv.reader(io.StringIO(raw_text))
    for row in csv_reader:
        if not row or len(row) < 3:
            continue
        try:
            idx_str = row[0].strip().rstrip(".")
            if not idx_str.isdigit():
                continue
            idx = int(idx_str)
            seg = row[1].strip()
            r_val = int(row[2].strip()) if row[2].strip().isdigit() else 0
            conf_val = row[3].strip().upper() if len(row) > 3 else "M"
            action_val = row[4].strip() if len(row) > 4 else ("Auto-Accept" if conf_val == "H" else "Review-Queue")
            parsed_items[idx] = {
                "segment": seg,
                "is_required": bool(r_val),
                "confidence": conf_val,
                "action": action_val,
            }
        except Exception:
            continue

    classified_dict = {}
    db_rows_to_insert = []
    high_c = 0
    med_c = 0
    low_c = 0

    for idx, raw_title in enumerate(novel_titles, 1):
        norm_title = normalize_text(raw_title)
        item = parsed_items.get(idx, {})

        seg = item.get("segment", "Unknown")
        is_req = bool(item.get("is_required", False))
        conf_code = item.get("confidence", "M")
        conf_str = "High" if conf_code == "H" else ("Medium" if conf_code == "M" else "Low")
        route = item.get("action", "Review-Queue")
        reason = f"LLM Classified: {seg} ({conf_str} Conf)"

        norm_key = raw_title.strip().lower()
        has_blocker_word = any(w in norm_key for w in BLOCKER_KEYWORDS)
        if has_blocker_word and not seg.startswith("X"):
            route = "Hard-Stop-Manual-Review"
            conf_str = "Low"
            is_req = False
            reason = "Hard Stop: Blocker/compliance function without explicit rule."

        if conf_str == "High":
            high_c += 1
        elif conf_str == "Medium":
            med_c += 1
        else:
            low_c += 1

        status = "qualified" if is_req else "disqualified_title"
        res_info = {
            "required": is_req,
            "status": status,
            "segment": seg,
            "confidence": conf_str,
            "routing_action": route,
            "reason": reason,
        }

        classified_dict[norm_title] = res_info
        with _title_cache_lock:
            _title_cache[norm_title] = res_info

        db_rows_to_insert.append((
            raw_title[:255],
            norm_title[:255],
            seg[:64],
            1 if is_req else 0,
        ))

    if db_rows_to_insert and connection:
        try:
            with connection.cursor() as cur:
                sql = """
                    INSERT INTO `job_title_guardrails` (`job_title`, `normalized_title`, `segment`, `is_required`)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        `segment` = VALUES(`segment`),
                        `is_required` = VALUES(`is_required`);
                """
                cur.executemany(sql, db_rows_to_insert)
        except Exception as e:
            print(f"[DB Error] Failed to persist LLM titles to DB: {e}", flush=True)

    token_stats = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": comp_tokens,
        "total_tokens": total_tokens,
        "latency_ms": round(latency_ms, 1),
        "high_conf": high_c,
        "med_conf": med_c,
        "low_conf": low_c,
    }

    return classified_dict, token_stats


# ============================================================
# ASYNCHRONOUS BACKGROUND MICRO-BATCH WORKER (50-ITEM FLUSH)
# ============================================================

class BackgroundTitleBatchWorker:
    """Background worker that micro-batches novel titles up to 50 items (or 15s timeout)."""
    def __init__(self, batch_size: int = 50, flush_interval_sec: float = 15.0):
        self.batch_size = batch_size
        self.flush_interval_sec = flush_interval_sec
        self._queue: list[str] = []
        self._queued_normalized: set[str] = set()
        self._lock = threading.Lock()
        self._last_flush_time = time.time()
        self._processed_batches = 0
        self._processed_titles = 0
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()

    def enqueue(self, job_title: str) -> bool:
        if not job_title or not OPENAI_API_KEY:
            return False
        raw_title = job_title.strip()
        norm_title = normalize_text(raw_title)
        if not norm_title:
            return False

        with self._lock:
            with _title_cache_lock:
                if norm_title in _title_cache and _title_cache[norm_title].get("status") != "not_recognized_title":
                    return False
            if norm_title in self._queued_normalized:
                return False

            self._queue.append(raw_title)
            self._queued_normalized.add(norm_title)
            q_len = len(self._queue)

        if q_len >= self.batch_size:
            threading.Thread(target=self.flush_batch, daemon=True).start()
        return True

    def flush_batch(self):
        titles_to_process = []
        with self._lock:
            if not self._queue:
                return
            titles_to_process = self._queue[:self.batch_size]
            self._queue = self._queue[self.batch_size:]
            for t in titles_to_process:
                self._queued_normalized.discard(normalize_text(t))
            self._last_flush_time = time.time()

        if not titles_to_process:
            return

        try:
            with get_connection() as conn:
                res_dict, token_stats = classify_novel_titles_compact_llm(titles_to_process, connection=conn)
                self._processed_batches += 1
                self._processed_titles += len(titles_to_process)
                est_cost = (token_stats['prompt_tokens'] * 0.00000015) + (token_stats['completion_tokens'] * 0.0000006)
                print(
                    f"\n[BackgroundBatchWorker] Evaluated {len(titles_to_process)} novel titles in {token_stats['latency_ms']}ms "
                    f"({token_stats['total_tokens']} tokens, ${est_cost:.6f} USD). Persisted to MySQL `job_title_guardrails`.",
                    flush=True
                )
        except Exception as e:
            print(f"[BackgroundBatchWorker Error] Failed to process batch of {len(titles_to_process)} titles: {e}", flush=True)

    def _worker_loop(self):
        while self._running:
            time.sleep(2.0)
            now = time.time()
            with self._lock:
                should_flush = len(self._queue) >= self.batch_size or (len(self._queue) > 0 and (now - self._last_flush_time >= self.flush_interval_sec))
            if should_flush:
                self.flush_batch()

    def get_status(self) -> dict:
        with self._lock:
            return {
                "queued_titles": len(self._queue),
                "processed_batches": self._processed_batches,
                "processed_titles": self._processed_titles,
                "batch_size_threshold": self.batch_size,
                "flush_interval_sec": self.flush_interval_sec,
                "seconds_since_last_flush": round(time.time() - self._last_flush_time, 1),
            }


background_batch_worker = BackgroundTitleBatchWorker(batch_size=50, flush_interval_sec=15.0)


# ============================================================
# 2-LAYER JOB TITLE EVALUATOR (DATABASE LOOKUP + SUBSTRINGS)
# ============================================================

def lookup_job_titles_batch(job_titles: list[str], connection=None) -> dict[str, dict]:
    """
    High-Performance 1-Shot Batch Job Title Evaluator:
    Bundles all distinct titles into 1 single SQL query instead of 25 sequential roundtrips.
    """
    if not job_titles:
        return {}

    results = {}
    missing_norm_titles = {}

    for t in job_titles:
        raw_t = (t or "").strip()
        if not raw_t:
            results[raw_t] = {"required": False, "status": "disqualified_title", "segment": "Unspecified", "reason": "No title specified"}
            continue

        norm_t = normalize_text(raw_t)
        with _title_cache_lock:
            if norm_t in _title_cache:
                results[raw_t] = _title_cache[norm_t]
                continue

        # Check Layer 2 Substrings before DB
        layer2_match = match_priority_substrings(raw_t)
        if layer2_match:
            is_req, prio, sub = layer2_match
            res = {
                "required": is_req,
                "status": "qualified" if is_req else "disqualified_title",
                "segment": f"Prio_{prio.replace(' ', '_')}_{sub.title()}",
                "reason": f"Layer 2 Substring Match: '{sub}' ({prio} Priority)",
            }
            with _title_cache_lock:
                _title_cache[norm_t] = res
            results[raw_t] = res
            continue

        missing_norm_titles[norm_t] = raw_t

    if missing_norm_titles:
        def do_batch_query(conn):
            try:
                with conn.cursor() as cur:
                    format_strings = ",".join(["%s"] * len(missing_norm_titles))
                    sql = f"SELECT `normalized_title`, `is_required`, `segment` FROM `job_title_guardrails` WHERE `normalized_title` IN ({format_strings});"
                    cur.execute(sql, tuple(missing_norm_titles.keys()))
                    rows = cur.fetchall()

                    found_norms = set()
                    for r in rows:
                        n_t = str(r[0]).strip().lower()
                        is_req = bool(r[1])
                        seg_name = r[2] or ""
                        found_norms.add(n_t)
                        res = {
                            "required": is_req,
                            "status": "qualified" if is_req else "disqualified_title",
                            "segment": seg_name,
                            "reason": f"Layer 1 DB Segment: {seg_name} ({'Required' if is_req else 'Excluded'})",
                        }
                        with _title_cache_lock:
                            _title_cache[n_t] = res
                        raw_title = missing_norm_titles.get(n_t)
                        if raw_title:
                            results[raw_title] = res

                    # For remaining unfound titles
                    for n_t, raw_title in missing_norm_titles.items():
                        if n_t not in found_norms:
                            fallback = {
                                "required": False,
                                "status": "not_recognized_title",
                                "segment": "Not_Recognized",
                                "reason": f"Title '{raw_title}' is not recognized in our database.",
                            }
                            with _title_cache_lock:
                                _title_cache[n_t] = fallback
                            results[raw_title] = fallback
            except Exception as e:
                print(f"[ContactChecker] Notice: batch title query: {e}", flush=True)

        if connection:
            do_batch_query(connection)
        else:
            with get_connection() as conn:
                do_batch_query(conn)

    return results


def lookup_job_title_in_db(job_title: str, connection=None) -> dict:
    """Single job title evaluation fallback."""
    batch_res = lookup_job_titles_batch([job_title], connection=connection)
    return batch_res.get(job_title.strip()) or {
        "required": False,
        "status": "disqualified_title",
        "segment": "Unspecified",
        "reason": "No title specified"
    }


# Backwards compatibility helper
def evaluate_job_title_with_ai(title: str = "", name: str = "", company: str = "", location: str = "", employee_count: int | None = None, apollo_id: str = "", connection=None, **kwargs) -> dict:
    job_t = title or kwargs.get("job_title", "")
    return lookup_job_title_in_db(job_t, connection=connection)


# ============================================================
# API MODELS & MATCHING ENDPOINT
# ============================================================

class ApolloContact(BaseModel):
    key: str
    apollo_id: str | None = ""
    name: str = ""
    first_name: str | None = None
    last_name: str | None = None
    job_title: str = ""
    company: str = ""
    location: str | None = ""
    company_domain: str | None = None
    website_link: str | None = None
    email: str | None = ""
    linkedin_url: str | None = None
    apollo_profile_url: str | None = None


class ApolloMatchRequest(BaseModel):
    contacts: list[ApolloContact]
    batch: str = "batch_1"
    title_guardrail_enabled: bool = False
    indian_name_guardrail_enabled: bool = False


class SyncSavedLeadItem(BaseModel):
    apollo_id: str | None = ""
    first_name: str | None = ""
    last_name: str | None = ""
    name: str | None = ""
    job_title: str | None = ""
    company: str | None = ""
    domain: str | None = ""
    website_link: str | None = ""
    email: str | None = ""
    location: str | None = ""
    linkedin_url: str | None = ""
    apollo_profile_url: str | None = ""
    segment: str | None = ""


class SyncSavedLeadsRequest(BaseModel):
    batch: str = "batch_1"
    contacts: list[SyncSavedLeadItem]


@app.get("/health")
def health_check():
    return {"status": "ok", "engine": "company_domains_lookup_v3"}


@app.post("/flush-pending-queues")
def flush_pending_queues():
    """Synchronously flush background title micro-batches before CSV export."""
    background_batch_worker.flush_batch()
    return {
        "status": "ok",
        "flushed_at": datetime.now(timezone.utc).isoformat(),
        "worker_status": background_batch_worker.get_status()
    }


@app.get("/detected-companies")
@app.get("/company-domains")
def get_detected_companies_list(limit: int = 100):
    """Retrieve the latest company domain resolution records from detected_companies table."""
    with get_connection() as conn:
        ensure_detected_companies_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT `id`, `company_name`, `normalized_company`, `website_link`, `domain`, `created_at`
                FROM `detected_companies`
                ORDER BY `id` DESC
                LIMIT %s;
            """, (limit,))
            rows = cur.fetchall()
            return {
                "total": len(rows),
                "companies": [
                    {
                        "id": r[0],
                        "company_name": r[1],
                        "normalized_company": r[2],
                        "website_link": r[3],
                        "domain": r[4],
                        "created_at": str(r[5]),
                    }
                    for r in rows
                ]
            }


@app.post("/sync-saved-leads")
def sync_saved_leads(request: SyncSavedLeadsRequest):
    """Direct sync endpoint: immediately persists all collected required leads into MySQL apollo_saved_leads."""
    if not request.contacts:
        return {"status": "ok", "synced": 0}

    batch_tag = str(request.batch or "batch_1").strip()[:64]
    rows_to_insert = []
    for c in request.contacts:
        rows_to_insert.append((
            batch_tag,
            (c.apollo_id or "")[:128],
            (c.name or "")[:255],
            (c.first_name or "")[:128],
            (c.last_name or "")[:128],
            (c.job_title or "")[:255],
            (c.company or "")[:255],
            (c.domain or "")[:255],
            (c.location or "")[:255],
            (c.linkedin_url or "")[:512],
            (c.apollo_profile_url or "")[:512],
            (c.segment or "Required_Lead")[:128]
        ))

    with get_connection() as conn:
        with conn.cursor() as cur:
            sql = """
                INSERT INTO `apollo_saved_leads` (
                    `batch`, `apollo_id`, `name`, `first_name`, `last_name`,
                    `job_title`, `company`, `company_domain`, `location`,
                    `linkedin_url`, `apollo_profile_url`, `segment`
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    `job_title` = VALUES(`job_title`),
                    `company_domain` = VALUES(`company_domain`),
                    `segment` = VALUES(`segment`);
            """
            cur.executemany(sql, rows_to_insert)

    print(f"\n[ContactChecker] Synced {len(rows_to_insert)} lead(s) into MySQL table `apollo_saved_leads` under '{batch_tag}'.", flush=True)
    return {"status": "ok", "synced": len(rows_to_insert)}


@app.get("/saved-leads-batches")
def get_saved_leads_batches():
    """Retrieve summary of all saved lead batches from the database."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT `batch`, COUNT(*) as total_leads, MIN(`created_at`) as started_at, MAX(`created_at`) as last_added
                FROM `apollo_saved_leads`
                GROUP BY `batch`
                ORDER BY `id` ASC;
            """)
            rows = cur.fetchall()
            return {
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

    batch_tag = str(request.batch or "batch_1").strip()
    with _batch_seen_companies_lock:
        if batch_tag not in _batch_seen_companies:
            _batch_seen_companies[batch_tag] = {}
        seen_required_companies = _batch_seen_companies[batch_tag]

    max_contacts_per_comp = int(os.getenv("MAX_CONTACTS_PER_COMPANY", "1"))

    unique_domains_seen = set()
    existing_domain_contacts = 0
    net_new_domain_contacts = 0
    db_title_hits = 0
    novel_titles_sent = 0
    token_stats = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0.0,
        "high_conf": 0,
        "med_conf": 0,
        "low_conf": 0,
    }

    with get_connection() as conn:
        # Pre-initialize table schema
        get_target_table_schema(conn)

        # --------------------------------------------------------
        # STEP 1: REFINED DOMAIN LOOKUP CHAIN (company_domains)
        # --------------------------------------------------------
        contact_primary_domain, contact_website_link = resolve_company_domains(contacts, connection=conn)

        for d in contact_primary_domain.values():
            if d:
                unique_domains_seen.add(d)

        # Check all unique candidate domains in CRM 'emails' table in 1 single fast indexed query
        check_domains_in_crm_batch(list(unique_domains_seen), connection=conn)

        # --------------------------------------------------------
        # STEP 2: GROUP CONTACTS BY EXISTING DOMAIN VS NET-NEW
        # --------------------------------------------------------
        net_new_contacts: list[ApolloContact] = []

        for idx, contact in enumerate(contacts, 1):
            prim_d = contact_primary_domain.get(contact.key, "")
            domain_in_crm, matched_crm_domain = check_domains_in_crm_batch([prim_d], connection=conn)

            if domain_in_crm:
                existing_domain_contacts += 1
                existing_count += 1
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
            else:
                net_new_domain_contacts += 1
                net_new_contacts.append(contact)

        # --------------------------------------------------------
        # STEP 3: DEMOGRAPHIC & JOB TITLE QUALIFICATION (1-SHOT BATCH)
        # --------------------------------------------------------
        titles_to_check = [c.job_title for c in net_new_contacts if c.job_title.strip()]
        batch_title_results = lookup_job_titles_batch(titles_to_check, connection=conn)

        contact_title_eval: dict[str, dict] = {}
        novel_titles_to_eval: dict[str, str] = {}

        for contact in net_new_contacts:
            title_name = contact.job_title.strip()
            if not title_name:
                contact_title_eval[contact.key] = {
                    "required": False,
                    "status": "disqualified_title",
                    "segment": "Unspecified",
                    "reason": "No title specified"
                }
                continue

            t_info = batch_title_results.get(title_name) or lookup_job_title_in_db(title_name, connection=conn)
            contact_title_eval[contact.key] = t_info

            if t_info.get("status") == "not_recognized_title":
                norm_t = normalize_text(title_name)
                novel_titles_to_eval[norm_t] = title_name
                background_batch_worker.enqueue(title_name)
                novel_titles_sent += 1
            else:
                db_title_hits += 1

        title_filter_active = request.title_guardrail_enabled
        indian_filter_active = request.indian_name_guardrail_enabled

        # --------------------------------------------------------
        # STEP 4: DECISION LOGIC, DEMOGRAPHIC & 1/COMPANY LIMIT
        # --------------------------------------------------------
        for idx, contact in enumerate(net_new_contacts, 1):
            comp_name = contact.company.strip()
            prim_d = contact_primary_domain.get(contact.key, "")
            comp_key = normalize_text(prim_d) or normalize_text(comp_name)

            # 4.1 Option 2 Pure Indian Name Demographic Filter
            if indian_filter_active:
                is_ind, ind_reason = is_unambiguous_pure_indian_name(contact.name or "", connection=conn)
                if is_ind:
                    ignored_count += 1
                    results[contact.key] = {
                        "exists": False,
                        "required": False,
                        "ignored": True,
                        "guardrail_status": "indian_name_disqualified",
                        "guardrail_reason": ind_reason,
                        "matched_domain": prim_d,
                    }
                    continue

            # 4.2 Job Title Hierarchy Check
            t_info = contact_title_eval.get(contact.key, {"required": False, "segment": "Unrecognized", "reason": "Not recognized", "status": "not_recognized_title"})
            title_ok = t_info.get("required", False) if title_filter_active else True
            title_status = t_info.get("status", "qualified" if title_ok else "not_recognized_title")
            title_seg = t_info.get("segment", "")
            title_reason = t_info.get("reason", "")

            if title_filter_active and not title_ok:
                ignored_count += 1
                results[contact.key] = {
                    "exists": False,
                    "required": False,
                    "ignored": True,
                    "guardrail_status": title_status,
                    "segment": title_seg,
                    "guardrail_reason": title_reason,
                    "matched_domain": prim_d,
                }
            else:
                # 4.3 Check 1 Contact per Company Limit
                already_selected = seen_required_companies.get(comp_key, [])
                is_same_contact = any(isinstance(item, dict) and item.get("key") == contact.key for item in already_selected)

                if is_same_contact:
                    # Contact is ALREADY the elected lead for this company across page sorts
                    required_count += 1
                    results[contact.key] = {
                        "exists": False,
                        "required": True,
                        "ignored": False,
                        "guardrail_status": "qualified",
                        "segment": title_seg,
                        "guardrail_reason": title_reason,
                        "matched_domain": prim_d,
                    }
                elif len(already_selected) >= max_contacts_per_comp:
                    elected_names = ", ".join(item.get("name", "") if isinstance(item, dict) else str(item) for item in already_selected)
                    ignored_count += 1
                    results[contact.key] = {
                        "exists": False,
                        "required": False,
                        "ignored": True,
                        "guardrail_status": "company_limit_reached",
                        "guardrail_reason": f"Company '{comp_name}' already has lead selected ({elected_names}). Max {max_contacts_per_comp} per company allowed.",
                        "matched_domain": prim_d,
                        "segment": title_seg,
                    }
                else:
                    seen_required_companies.setdefault(comp_key, []).append({
                        "key": contact.key,
                        "name": contact.name or f"Contact #{idx}"
                    })
                    required_count += 1
                    results[contact.key] = {
                        "exists": False,
                        "required": True,
                        "ignored": False,
                        "guardrail_status": "qualified",
                        "segment": title_seg,
                        "guardrail_reason": title_reason,
                        "matched_domain": prim_d,
                    }

        # --------------------------------------------------------
        # STEP 5: AUTO-PERSIST REQUIRED LEADS TO MYSQL BATCH TABLE
        # --------------------------------------------------------
        required_leads_to_save = []
        batch_tag = str(request.batch or "batch_1").strip()

        for contact in net_new_contacts:
            r = results.get(contact.key)
            if r and r.get("required") is True:
                apollo_id_val = (contact.apollo_id or (contact.key.replace("apollo-", "") if contact.key.startswith("apollo-") else contact.key) or "")
                name_parts = (contact.name or "").strip().split(None, 1)
                first_name_val = contact.first_name or (name_parts[0] if name_parts else "")
                last_name_val = contact.last_name or (name_parts[1] if len(name_parts) > 1 else "")
                domain_val = contact_primary_domain.get(contact.key) or contact.company_domain or ""
                apollo_url_val = contact.apollo_profile_url or (f"https://app.apollo.io/#/people/{apollo_id_val}" if apollo_id_val else "")

                required_leads_to_save.append((
                    batch_tag[:64],
                    apollo_id_val[:128],
                    (contact.name or "")[:255],
                    first_name_val[:128],
                    last_name_val[:128],
                    (contact.job_title or "")[:255],
                    (contact.company or "")[:255],
                    domain_val[:255],
                    (contact.location or "")[:255],
                    (contact.linkedin_url or "")[:512],
                    apollo_url_val[:512],
                    (r.get("segment") or "Required_Lead")[:128]
                ))

        if required_leads_to_save:
            try:
                with conn.cursor() as cur:
                    sql = """
                        INSERT INTO `apollo_saved_leads` (
                            `batch`, `apollo_id`, `name`, `first_name`, `last_name`,
                            `job_title`, `company`, `company_domain`, `location`,
                            `linkedin_url`, `apollo_profile_url`, `segment`
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            `job_title` = VALUES(`job_title`),
                            `company_domain` = VALUES(`company_domain`),
                            `segment` = VALUES(`segment`);
                    """
                    cur.executemany(sql, required_leads_to_save)
            except Exception as e:
                print(f"[ContactChecker] Notice: Auto-save leads batch error: {e}", flush=True)

    elapsed_ms = (time.perf_counter() - t0) * 1000

    pred_dom_ms = len(unique_domains_seen) * 0.5
    pred_title_ms = len(net_new_contacts) * 0.1
    pred_llm_ms = (400.0 + novel_titles_sent * 30.0) if novel_titles_sent > 0 else 0.0
    predicted_latency_ms = round(pred_dom_ms + pred_title_ms + pred_llm_ms + 10.0, 1)

    page_cost_usd = (token_stats["prompt_tokens"] * 0.00000015) + (token_stats["completion_tokens"] * 0.00000060)

    # --------------------------------------------------------
    # STEP 6: PER-PAGE DASHBOARD LOG
    # --------------------------------------------------------
    print("\n" + "=" * 80, flush=True)
    print(f">>> [APOLLO PAGE #{batch_num} DASHBOARD] Ingested {total_received} Contacts | Title Filter: {'ON' if request.title_guardrail_enabled else 'OFF'} | Indian Filter: {'ON' if request.indian_name_guardrail_enabled else 'OFF'}", flush=True)
    print("=" * 80, flush=True)
    print(f"Contacts Summary : Total: {total_received} | 🟢 Required: {required_count} | ⚪ Existing/Ignored: {ignored_count}", flush=True)
    print(f"Domain Breakdown : Unique Domains: {len(unique_domains_seen)} | In CRM: {existing_domain_contacts} | Net-New: {net_new_domain_contacts}", flush=True)
    print(f"Job Title Engine : DB Cache Hits: {db_title_hits} | Sent to {OPENAI_DOMAIN_MODEL}: {novel_titles_sent}", flush=True)
    if novel_titles_sent > 0:
        print(f"Confidence Stats : High (Auto-Accept): {token_stats['high_conf']} | Medium (Review Queue): {token_stats['med_conf']} | Low/Stop: {token_stats['low_conf']}", flush=True)
        print(f"Token Matrix     : Prompt: {token_stats['prompt_tokens']} | Completion: {token_stats['completion_tokens']} | Total Tokens: {token_stats['total_tokens']}", flush=True)
        print(f"Estimated Cost   : ${page_cost_usd:.6f} USD ({token_stats['total_tokens']/max(1, total_received):.1f} tokens/contact)", flush=True)
    else:
        print("Token Matrix     : 0 tokens consumed ($0.00 USD - 100% Resolved from Database Cache)", flush=True)

    print(f"Execution Latency: Actual: {elapsed_ms:.1f}ms | Predicted: ~{predicted_latency_ms:.1f}ms ({elapsed_ms/max(1, total_received):.1f} ms/contact)", flush=True)
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
            "predicted_time_ms": predicted_latency_ms,
            "novel_titles_sent_to_llm": novel_titles_sent,
            "tokens_consumed": token_stats["total_tokens"],
            "estimated_cost_usd": page_cost_usd,
        },
        "activity": [],
    }


@app.get("/batch-worker-status")
def get_batch_worker_status():
    """Retrieve status of the asynchronous background job title micro-batch worker."""
    return {
        "status": "active",
        "worker": background_batch_worker.get_status()
    }


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print(">>> [ContactChecker API] Starting Lead Processing Engine (Port 8000)")
    print("=" * 70 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)
