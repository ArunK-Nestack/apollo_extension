import json
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Literal, cast
from urllib.parse import urlparse

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field

_batch_counter = 0

# ============================================================
# PERSISTENT DOMAIN CACHE (Decoupled & In-Memory + JSON)
# ============================================================

_DOMAIN_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "domain_cache.json",
)
_domain_cache_lock = threading.Lock()
_global_domain_cache: dict[str, dict] = {}


def load_domain_cache() -> dict[str, dict]:
    global _global_domain_cache
    if not _global_domain_cache:
        with _domain_cache_lock:
            if not _global_domain_cache and os.path.exists(_DOMAIN_CACHE_FILE):
                try:
                    with open(_DOMAIN_CACHE_FILE, "r", encoding="utf-8") as f:
                        _global_domain_cache = json.load(f)
                except Exception as err:
                    print(f"[ContactChecker] Warning: Failed to load domain cache: {err}")
                    _global_domain_cache = {}
    return _global_domain_cache


def get_cached_domain_resolution(company: str, location: str = "") -> dict | None:
    cache = load_domain_cache()
    norm_comp = normalize_text(company)
    norm_loc = normalize_text(location)
    key_with_loc = f"{norm_comp}::{norm_loc}"
    key_comp_only = f"{norm_comp}::"

    if key_with_loc in cache:
        return cache[key_with_loc]
    if key_comp_only in cache:
        return cache[key_comp_only]
    return None


def save_domain_cache_entry(company: str, location: str, resolution_data: dict):
    global _global_domain_cache
    norm_comp = normalize_text(company)
    norm_loc = normalize_text(location)
    key = f"{norm_comp}::{norm_loc}"

    with _domain_cache_lock:
        _global_domain_cache[key] = resolution_data
        try:
            os.makedirs(os.path.dirname(_DOMAIN_CACHE_FILE), exist_ok=True)
            with open(_DOMAIN_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(_global_domain_cache, f, indent=2)
        except Exception as err:
            print(f"[ContactChecker] Warning: Failed to save domain cache: {err}")


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# DOMAIN RESOLVER CONFIGURATION
# ============================================================

DOMAIN_RESOLVER_ENABLED = (
    os.getenv("DOMAIN_RESOLVER_ENABLED", "true")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

OPENAI_DOMAIN_MODEL = os.getenv(
    "OPENAI_DOMAIN_MODEL",
    "gpt-4o-mini",
).strip()

DOMAIN_KNOWLEDGE_MIN_CONFIDENCE = float(
    os.getenv(
        "DOMAIN_KNOWLEDGE_MIN_CONFIDENCE",
        "0.95",
    )
)

DOMAIN_WEB_MIN_CONFIDENCE = float(
    os.getenv(
        "DOMAIN_WEB_MIN_CONFIDENCE",
        "0.90",
    )
)

# ============================================================
# GUARDRAILS CONFIGURATION
# ============================================================

GUARDRAILS_ENABLED = (
    os.getenv("GUARDRAILS_ENABLED", "true")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

MAX_CONTACTS_PER_COMPANY = int(
    os.getenv("MAX_CONTACTS_PER_COMPANY", "5")
)

OPENAI_TITLE_GUARDRAIL_MODEL = os.getenv(
    "OPENAI_TITLE_GUARDRAIL_MODEL",
    "gpt-4o-mini",
).strip()

_openai_client = None


def get_openai_client():
    """
    Create the OpenAI client lazily.

    This keeps the API server usable for deterministic matching even
    when the resolver is disabled.
    """

    global _openai_client

    if _openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()

        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is missing. "
                "Add it to .env or disable DOMAIN_RESOLVER_ENABLED."
            )

        _openai_client = OpenAI(
            api_key=api_key
        )

    return _openai_client



# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Contact Database Checker API",
    version="1.5.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


# ============================================================
# STRUCTURED ACTIVITY LOGGING
#
# Logs are:
# - printed to the FastAPI terminal
# - returned to the extension for the live Activity panel
# - NOT written to PostgreSQL or any file
# ============================================================

def activity_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def append_activity(
    activity: list[dict],
    event: str,
    message: str,
    *,
    contact_key: str = "",
    level: str = "info",
    details: dict | None = None,
):
    entry = {
        "timestamp": activity_timestamp(),
        "event": event,
        "message": message,
        "level": level,
        "contact_key": contact_key,
        "details": details or {},
    }

    activity.append(entry)

    detail_text = ""

    if entry["details"]:
        safe_parts = []

        for key, value in entry["details"].items():
            if isinstance(
                value,
                (str, int, float, bool),
            ):
                safe_parts.append(
                    f"{key}={value}"
                )
            elif isinstance(value, list):
                safe_parts.append(
                    f"{key}={','.join(str(item) for item in value)}"
                )

        if safe_parts:
            detail_text = (
                " | "
                + " ".join(safe_parts)
            )

    contact_text = (
        f" [{contact_key}]"
        if contact_key
        else ""
    )

    print(
        f"[ContactChecker]{contact_text} "
        f"{event}: {message}"
        f"{detail_text}",
        flush=True,
    )


# ============================================================
# GENERAL TEXT NORMALIZATION
# ============================================================

def normalize_text(value: str) -> str:
    """
    Examples:

    "VE GROUP"
        -> "vegroup"

    "Chief Operations Officer (COO)"
        -> "chiefoperationsofficercoo"

    "Franz Milicevic"
        -> "franzmilicevic"

    "Fito Ag."
        -> "fitoag"
    """

    if not value:
        return ""

    value = value.strip().lower()

    # Remove accents
    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )

    # Keep letters and numbers only
    value = re.sub(
        r"[^a-z0-9]",
        "",
        value,
    )

    return value


# ============================================================
# DOMAIN / COMPANY NORMALIZATION
# ============================================================

# Common legal/company suffixes. These are removed only when
# building an additional comparison variant. The original company
# text is still kept as another variant, so names such as
# "VE Group" can still match "ve-group.com" exactly.
LEGAL_COMPANY_SUFFIXES = {
    "ag",
    "bv",
    "co",
    "company",
    "corp",
    "corporation",
    "gmbh",
    "group",
    "inc",
    "incorporated",
    "llc",
    "llp",
    "limited",
    "ltd",
    "nv",
    "oy",
    "plc",
    "pllc",
    "pte",
    "pty",
    "sa",
    "sas",
    "sarl",
    "spa",
}

# If the TLD itself is commonly used as part of a technology brand,
# include brand + TLD as a comparison variant.
# Example:
#   Liquid AI       -> liquidai
#   liquid.ai       -> liquid + ai -> liquidai
BRANDABLE_TLDS = {
    "ai",
    "app",
    "cloud",
    "dev",
    "digital",
    "io",
    "software",
    "tech",
}

# Common second-level public suffix labels used with country TLDs.
# This keeps company.co.uk -> company, fitoag.com.br -> fitoag, etc.
SECOND_LEVEL_SUFFIXES = {
    "ac",
    "co",
    "com",
    "edu",
    "gov",
    "net",
    "org",
}

# Avoid accepting a weak one-word containment match for generic
# company/domain words.
GENERIC_COMPANY_WORDS = {
    "ai",
    "business",
    "company",
    "corp",
    "corporation",
    "data",
    "digital",
    "enterprise",
    "enterprises",
    "global",
    "group",
    "holding",
    "holdings",
    "industries",
    "international",
    "services",
    "solutions",
    "systems",
    "tech",
    "technology",
    "technologies",
    "web",
}


def clean_domain_host(domain: str) -> str:
    """
    Convert an email/domain/URL into a clean hostname.

    Examples:
        person@ve-group.com
            -> ve-group.com

        https://www.liquid.liquid.ai/about
            -> liquid.liquid.ai
    """

    if not domain:
        return ""

    value = domain.strip().lower()

    # If an email gets passed instead of just a domain.
    if "@" in value:
        value = value.split("@", 1)[1]

    # Remove protocol.
    value = re.sub(
        r"^[a-z][a-z0-9+.-]*://",
        "",
        value,
    )

    # Remove credentials if a URL somehow contains them.
    if "@" in value:
        value = value.rsplit("@", 1)[1]

    # Remove path/query/fragment.
    value = re.split(r"[/?#]", value, maxsplit=1)[0]

    # Remove port.
    value = value.split(":", 1)[0]

    # Remove leading/trailing dots.
    value = value.strip(".")

    if value.startswith("www."):
        value = value[4:]

    return value


def get_domain_parts(domain: str) -> tuple[str, str, str]:
    """
    Return:
        (clean_host, registrable_brand, suffix)

    Examples:
        simplon.com
            -> ("simplon.com", "simplon", "com")

        fitoag.com.br
            -> ("fitoag.com.br", "fitoag", "com.br")

        liquid.liquid.ai
            -> ("liquid.liquid.ai", "liquid", "ai")

        company.co.uk
            -> ("company.co.uk", "company", "co.uk")
    """

    host = clean_domain_host(domain)

    if not host:
        return "", "", ""

    parts = [
        part
        for part in host.split(".")
        if part
    ]

    if not parts:
        return host, "", ""

    if len(parts) == 1:
        return host, parts[0], ""

    # Country-code domains such as company.co.uk / fitoag.com.br.
    if (
        len(parts) >= 3
        and len(parts[-1]) == 2
        and parts[-2] in SECOND_LEVEL_SUFFIXES
    ):
        return (
            host,
            parts[-3],
            f"{parts[-2]}.{parts[-1]}",
        )

    return host, parts[-2], parts[-1]


def get_domain_brand(domain: str) -> str:
    """
    Return the registrable brand label from a domain.

    Examples:
        simplon.com            -> simplon
        wso-security.com       -> wso-security
        fitoag.com.br          -> fitoag
        company.co.uk          -> company
        liquid.liquid.ai       -> liquid
        person@ve-group.com    -> ve-group
    """

    _, brand, _ = get_domain_parts(domain)
    return brand


def company_token_list(value: str) -> list[str]:
    """
    Convert company/domain text into ordered lowercase tokens.

    Example:
        "WSO Worldwide Security Options"
        -> ["wso", "worldwide", "security", "options"]
    """

    if not value:
        return []

    value = value.strip().lower()

    value = unicodedata.normalize(
        "NFKD",
        value,
    )

    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )

    # Convert punctuation to spaces instead of deleting it so that
    # "wso-security" becomes two useful tokens.
    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return [
        token
        for token in value.split()
        if token
    ]


def company_tokens(value: str) -> set[str]:
    return set(company_token_list(value))


def strip_legal_suffix_tokens(tokens: list[str]) -> list[str]:
    """
    Remove trailing legal suffixes only.

    Examples:
        ["liquid", "ai", "inc"]
            -> ["liquid", "ai"]

        ["simplon", "fahrrad", "gmbh"]
            -> ["simplon", "fahrrad"]

    The raw company variant is still kept separately, so this does
    not destroy exact matches such as VE Group <-> ve-group.com.
    """

    result = list(tokens)

    while (
        len(result) > 1
        and result[-1] in LEGAL_COMPANY_SUFFIXES
    ):
        result.pop()

    return result


def company_variants(company: str) -> set[str]:
    """
    Build strong normalized variants for an Apollo company name.

    Examples:
        "Liquid AI, Inc."
        -> {"liquidaiinc", "liquidai", "lai"}

        "Worldwide Security Options"
        -> {"worldwidesecurityoptions", "wso"}
    """

    tokens = company_token_list(company)

    if not tokens:
        return set()

    core_tokens = strip_legal_suffix_tokens(tokens)

    variants = {
        normalize_text(company),
        "".join(core_tokens),
    }

    # Acronym is useful for names such as:
    # Worldwide Security Options -> WSO
    if len(core_tokens) >= 2:
        acronym = "".join(
            token[0]
            for token in core_tokens
            if token
        )

        if len(acronym) >= 2:
            variants.add(acronym)

    return {
        value
        for value in variants
        if value
    }


def domain_variants(domain: str) -> set[str]:
    """
    Build normalized variants from the stored email domain.

    Examples:
        ve-group.com
            -> {"vegroup"}

        liquid.ai
            -> {"liquid", "liquidai"}

        liquid.liquid.ai
            -> {"liquid", "liquidai"}

        fitoag.com.br
            -> {"fitoag"}
    """

    host, brand, suffix = get_domain_parts(domain)

    if not host or not brand:
        return set()

    brand_compact = normalize_text(brand)

    variants = {
        brand_compact,
    }

    # Treat semantic/brandable TLDs as a possible part of the brand.
    # This specifically supports cases such as Liquid AI <-> liquid.ai.
    suffix_last = suffix.split(".")[-1] if suffix else ""

    if suffix_last in BRANDABLE_TLDS:
        variants.add(
            normalize_text(
                f"{brand}{suffix_last}"
            )
        )

    return {
        value
        for value in variants
        if value
    }


# ============================================================
# COMPANY MATCHING
# ============================================================

def company_matches(
    apollo_company: str,
    crm_domain: str,
) -> bool:
    """
    Compare an Apollo company name against an existing CRM/email domain.

    This function DOES NOT store any company information. It only creates
    temporary normalized variants and returns True/False.

    Examples that should match:
        Simplon Fahrrad GmbH  <-> simplon.com
        VE GROUP              <-> ve-group.com
        WSO Worldwide Security Options <-> wso-security.com
        Fito Ag.              <-> fitoag.com.br
        Liquid AI             <-> liquid.ai
        Liquid AI             <-> liquid.liquid.ai

    Example that should NOT match:
        Global Technology     <-> globalconstruction.com
    """

    if not apollo_company or not crm_domain:
        return False

    _, brand, _ = get_domain_parts(
        crm_domain
    )

    if not brand:
        return False

    company_variant_set = company_variants(
        apollo_company
    )

    domain_variant_set = domain_variants(
        crm_domain
    )

    if not company_variant_set or not domain_variant_set:
        return False

    # --------------------------------------------------------
    # METHOD 1 — Strong exact variant intersection
    # --------------------------------------------------------
    # Examples:
    #   VE GROUP    -> vegroup
    #   ve-group.com -> vegroup
    #
    #   Liquid AI   -> liquidai
    #   liquid.ai   -> liquidai
    # --------------------------------------------------------

    if company_variant_set & domain_variant_set:
        return True

    # --------------------------------------------------------
    # METHOD 2 — Multi-token domain brand contained in company
    # --------------------------------------------------------
    # Example:
    #   WSO Worldwide Security Options
    #   wso-security.com
    #
    # Domain tokens {wso, security} are both in the company.
    # Requiring 2+ tokens makes this much safer than accepting any
    # random shared word.
    # --------------------------------------------------------

    apollo_token_list = company_token_list(
        apollo_company
    )

    apollo_token_set = set(
        strip_legal_suffix_tokens(
            apollo_token_list
        )
    )

    domain_token_list = company_token_list(
        brand
    )

    domain_token_set = set(
        domain_token_list
    )

    if (
        len(domain_token_set) >= 2
        and domain_token_set.issubset(
            apollo_token_set
        )
    ):
        return True

    # --------------------------------------------------------
    # METHOD 3 — Distinctive single brand as the first company token
    # --------------------------------------------------------
    # Example:
    #   Simplon Fahrrad GmbH <-> simplon.com
    #
    # We intentionally reject generic single words such as "global",
    # "technology", "solutions", etc. for this weaker rule.
    # --------------------------------------------------------

    brand_compact = normalize_text(
        brand
    )

    core_company_tokens = strip_legal_suffix_tokens(
        apollo_token_list
    )

    first_company_token = (
        normalize_text(core_company_tokens[0])
        if core_company_tokens
        else ""
    )

    if (
        len(domain_token_set) == 1
        and len(brand_compact) >= 5
        and brand_compact not in GENERIC_COMPANY_WORDS
        and first_company_token == brand_compact
    ):
        return True

    # --------------------------------------------------------
    # METHOD 4 — Strong compact prefix/containment
    # --------------------------------------------------------
    # Example:
    #   ABC Technologies <-> abctech.com
    #
    # Only use non-generic strings with enough characters.
    # This avoids loose one-word matches such as:
    #   Global Technology <-> globalconstruction.com
    # --------------------------------------------------------

    core_company_compact = normalize_text(
        "".join(core_company_tokens)
    )

    for domain_variant in domain_variant_set:
        if (
            len(domain_variant) >= 6
            and domain_variant not in GENERIC_COMPANY_WORDS
            and (
                core_company_compact.startswith(
                    domain_variant
                )
                or domain_variant.startswith(
                    core_company_compact
                )
            )
        ):
            return True

    # --------------------------------------------------------
    # METHOD 5 — Acronym match
    # --------------------------------------------------------
    # Example:
    #   Worldwide Security Options -> WSO
    #   wso-security.com -> token "wso"
    # --------------------------------------------------------

    if len(core_company_tokens) >= 2:
        acronym = "".join(
            token[0]
            for token in core_company_tokens
            if token
        )

        if (
            len(acronym) >= 2
            and (
                acronym in domain_token_set
                or acronym in domain_variant_set
            )
        ):
            return True

    return False



# ============================================================
# DOMAIN RESOLUTION FALLBACK
#
# Only these three domain types are allowed:
#
# 1. company_official
# 2. company_brand
# 3. person_professional
#
# Nothing resolved here is written to PostgreSQL.
# ============================================================

DomainType = Literal[
    "company_official",
    "company_brand",
    "person_professional",
]


class ResolvedDomainCandidate(BaseModel):
    domain: str

    type: DomainType

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )


class KnowledgeDomainResolution(BaseModel):
    status: Literal[
        "known",
        "uncertain",
        "unknown",
    ]

    # True only when the model is highly confident that its answer
    # covers every confidently known/applicable domain among the
    # three allowed categories.
    coverage_complete: bool

    domains: list[
        ResolvedDomainCandidate
    ]

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )


class WebDomainResolution(BaseModel):
    status: Literal[
        "verified",
        "unresolved",
    ]

    domains: list[
        ResolvedDomainCandidate
    ]

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    evidence_urls: list[str]


def canonicalize_domain(value: str) -> str:
    """
    Convert a URL/email/domain into a plain lowercase hostname.

    Examples:
        https://www.example.com/about
            -> example.com

        person@example.com
            -> example.com

        portal.example.com
            -> portal.example.com
    """

    if not value:
        return ""

    value = value.strip().lower()

    if "@" in value and "://" not in value:
        value = value.rsplit("@", 1)[1]

    if "://" not in value:
        value = f"https://{value}"

    try:
        parsed = urlparse(value)
        host = parsed.hostname or ""
    except Exception:
        host = ""

    host = host.strip(".").lower()

    if host.startswith("www."):
        host = host[4:]

    if not re.fullmatch(
        r"[a-z0-9.-]+",
        host,
    ):
        return ""

    if "." not in host:
        return ""

    return host


def domains_equivalent(
    left: str,
    right: str,
) -> bool:
    """
    Treat exact domains and parent/subdomain relationships as the
    same lookup domain.

    Examples:
        liquid.ai
        liquid.liquid.ai
            -> True

        example.com
        portal.example.com
            -> True
    """

    left_host = canonicalize_domain(left)
    right_host = canonicalize_domain(right)

    if not left_host or not right_host:
        return False

    if left_host == right_host:
        return True

    return (
        left_host.endswith(
            f".{right_host}"
        )
        or
        right_host.endswith(
            f".{left_host}"
        )
    )


def clean_resolved_domains(
    domains: list[
        ResolvedDomainCandidate
    ],
) -> list[dict]:
    """
    Canonicalize and deduplicate temporary resolver output while
    preserving domain type and confidence.

    Multiple domain types can point to different legitimate domains.
    We intentionally do NOT keep regional, legacy, product-only,
    social, directory, or generic mailbox domains.
    """

    allowed_types = {
        "company_official",
        "company_brand",
        "person_professional",
    }

    blocked_hosts = {
        "gmail.com",
        "outlook.com",
        "hotmail.com",
        "yahoo.com",
        "icloud.com",
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "x.com",
        "twitter.com",
        "crunchbase.com",
        "apollo.io",
    }

    cleaned = []
    seen = set()

    for item in domains:
        host = canonicalize_domain(
            item.domain
        )

        domain_type = (
            item.type
        )

        confidence = float(
            item.confidence
        )

        if (
            not host
            or host in blocked_hosts
            or domain_type not in allowed_types
        ):
            continue

        key = (
            host,
            domain_type,
        )

        if key in seen:
            continue

        seen.add(key)

        cleaned.append(
            {
                "domain": host,
                "type": domain_type,
                "confidence": confidence,
            }
        )

    # This is a safety ceiling, not an instruction to invent domains.
    # The resolver is expected to return only confidently attributable
    # domains from the three allowed categories.
    return cleaned[:20]


def knowledge_candidate_text(
    domains: list[dict],
) -> str:
    """
    Format knowledge-only candidates so the web-search pass can verify
    them rather than ignoring useful model knowledge.
    """

    if not domains:
        return "None"

    lines = []

    for item in domains:
        lines.append(
            (
                f"- {item['domain']} "
                f"({item['type']}, "
                f"confidence={item['confidence']:.2f})"
            )
        )

    return "\n".join(lines)


def resolve_domains_from_model_knowledge(
    name: str,
    job_title: str,
    company: str,
    location: str,
) -> dict:
    """
    First fallback:
    use ONLY existing model knowledge.

    If the model is not nearly certain AND complete about the three
    permitted domain categories, the caller performs a web search.
    """

    client = get_openai_client()

    name_text = (
        name.strip()
        if name
        else "Name not available"
    )

    title_text = (
        job_title.strip()
        if job_title
        else "Job title not available"
    )

    location_text = (
        location.strip()
        if location
        else "Location not available"
    )

    parse_kwargs = {}
    if any(OPENAI_DOMAIN_MODEL.lower().startswith(p) for p in ["o1", "o3", "gpt-5"]):
        parse_kwargs["reasoning"] = {"effort": "none"}

    response = client.responses.parse(
        model=OPENAI_DOMAIN_MODEL,
        **parse_kwargs,
        input=[
            {
                "role": "system",
                "content": (
                    "You are a conservative contact-domain resolver. "
                    "Use ONLY knowledge already present in the model. "
                    "Do not browse the web in this step. "
                    "Do not guess or construct domains from company/person "
                    "names. Resolve domains only when you reliably know "
                    "they belong to the exact company or exact person. "
                    "You may return ONLY three types: "
                    "company_official = the company's primary official "
                    "website domain; "
                    "company_brand = another official brand domain operated "
                    "by the same company; "
                    "person_professional = a domain clearly owned/used by "
                    "the exact named person as their professional/business "
                    "website. "
                    "Do NOT include regional domains, legacy domains, "
                    "product-only domains, social networks, directories, "
                    "lead databases, or generic email providers. "
                    "A person_professional domain must be tied to the exact "
                    "person; never infer it merely because the domain looks "
                    "like their name. "
                    "Set coverage_complete=true only when you are highly "
                    "confident you know the complete applicable set among "
                    "these three categories. If anything may be missing or "
                    "ambiguous, set coverage_complete=false so a later web "
                    "verification can run."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Person: {name_text}\n"
                    f"Job title: {title_text}\n"
                    f"Company: {company}\n"
                    f"Location: {location_text}\n\n"
                    "Return every confidently known applicable domain "
                    "within ONLY these categories: company_official, "
                    "company_brand, person_professional. "
                    "If you do not reliably know them, return uncertain "
                    "or unknown instead of guessing."
                ),
            },
        ],

        text_format=
            KnowledgeDomainResolution,
    )

    parsed = response.output_parsed

    if parsed is None:
        return {
            "status": "unknown",
            "coverage_complete": False,
            "domains": [],
            "confidence": 0.0,
            "method": "knowledge",
        }

    domains = clean_resolved_domains(
        parsed.domains
    )

    return {
        "status": parsed.status,
        "coverage_complete": bool(
            parsed.coverage_complete
        ),
        "domains": domains,
        "confidence": float(
            parsed.confidence
        ),
        "method": "knowledge",
    }


def resolve_domains_with_web(
    name: str,
    job_title: str,
    company: str,
    location: str,
    knowledge_domains: list[dict],
) -> dict:
    """
    Second fallback:
    force web search and verify ALL confidently attributable domains
    within the three permitted categories.

    The knowledge-only candidates are supplied as candidates to verify,
    not as evidence.
    """

    client = get_openai_client()

    name_text = (
        name.strip()
        if name
        else "Name not available"
    )

    title_text = (
        job_title.strip()
        if job_title
        else "Job title not available"
    )

    location_text = (
        location.strip()
        if location
        else "Location not available"
    )

    candidate_text = (
        knowledge_candidate_text(
            knowledge_domains
        )
    )

    # Some OpenAI Python SDK/Pylance versions expose older TypedDict
    # definitions. Cast only the tool declaration; the runtime request
    # still sends the current Responses API web_search shape.
    web_search_tool = cast(
        Any,
        {
            "type": "web_search",
            "search_context_size": "low",
        },
    )

    web_kwargs = {}
    if any(OPENAI_DOMAIN_MODEL.lower().startswith(p) for p in ["o1", "o3", "gpt-5"]):
        web_kwargs["reasoning"] = {"effort": "low"}

    response = client.responses.parse(
        model=OPENAI_DOMAIN_MODEL,
        **web_kwargs,
        tools=[
            web_search_tool
        ],

        # This function runs only when internal knowledge was not
        # sufficiently complete/confident. Web search is therefore
        # mandatory here.
        tool_choice="required",

        input=[
            {
                "role": "system",
                "content": (
                    "You are verifying domains for one exact Apollo "
                    "contact. You MUST use web search. "
                    "Search enough to check all three permitted categories: "
                    "company_official, company_brand, and "
                    "person_professional. "
                    "Use company name + location to distinguish similarly "
                    "named businesses. Use the exact person's name + title "
                    "+ company when checking a person_professional domain. "
                    "Return ONLY domains that are confidently attributable "
                    "to the exact company or exact person. "
                    "company_official means the company's primary official "
                    "website. "
                    "company_brand means another official brand domain "
                    "operated by that same company. "
                    "person_professional means a professional/business "
                    "website domain clearly belonging to the exact person. "
                    "Do NOT include regional-only domains, legacy domains, "
                    "product-only domains, social networks, directories, "
                    "lead databases, or generic email providers. "
                    "Do not treat a domain that merely resembles a person's "
                    "name as evidence. "
                    "The supplied model-knowledge candidates are only "
                    "candidates to verify; they are not evidence. "
                    "Prefer the company's/person's own official websites "
                    "as evidence, with reputable independent sources only "
                    "as supporting evidence. "
                    "Before finalizing, check whether any additional "
                    "confidently attributable domain exists within the "
                    "three allowed categories. "
                    "If none can be verified, return unresolved."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Person: {name_text}\n"
                    f"Job title: {title_text}\n"
                    f"Company: {company}\n"
                    f"Location: {location_text}\n\n"
                    "Model-knowledge candidates to verify:\n"
                    f"{candidate_text}\n\n"
                    "Find and verify every confidently attributable domain "
                    "within ONLY: company_official, company_brand, "
                    "person_professional."
                ),
            },
        ],

        text_format=
            WebDomainResolution,
    )

    parsed = response.output_parsed

    if parsed is None:
        return {
            "status": "unresolved",
            "domains": [],
            "confidence": 0.0,
            "method": "web",
            "evidence_urls": [],
        }

    domains = clean_resolved_domains(
        parsed.domains
    )

    evidence_urls = [
        url.strip()
        for url in parsed.evidence_urls
        if url and url.strip()
    ][:20]

    return {
        "status": parsed.status,
        "domains": domains,
        "confidence": float(
            parsed.confidence
        ),
        "method": "web",
        "evidence_urls":
            evidence_urls,
    }


def resolve_contact_domains(
    name: str,
    job_title: str,
    company: str,
    location: str = "",
    activity_logger=None,
) -> dict:
    """
    Complete temporary resolver:

    1. deterministic company/domain matching happens before this function
    2. model knowledge only
    3. if knowledge is not both high-confidence AND coverage-complete,
       force web search
    4. web search verifies all confidently attributable domains among:
         - company_official
         - company_brand
         - person_professional
    5. nothing is written to PostgreSQL
    """

    def log(
        event: str,
        message: str,
        *,
        level: str = "info",
        details: dict | None = None,
    ):
        if activity_logger is not None:
            activity_logger(
                event,
                message,
                level=level,
                details=details or {},
            )

    if not DOMAIN_RESOLVER_ENABLED:
        log(
            "DOMAIN_RESOLVER_DISABLED",
            "LLM/domain resolver is disabled.",
            level="warning",
        )
        return {
            "status": "disabled",
            "domains": [],
            "confidence": 0.0,
            "method": "disabled",
        }

    name = (
        name or ""
    ).strip()

    job_title = (
        job_title or ""
    ).strip()

    company = (
        company or ""
    ).strip()

    location = (
        location or ""
    ).strip()

    if not company:
        return {
            "status": "unresolved",
            "domains": [],
            "confidence": 0.0,
            "method": "none",
        }

    # Check persistent cache (Memory / JSON)
    cached_res = get_cached_domain_resolution(company, location)
    if cached_res:
        log(
            "RESOLVER_CACHE_HIT",
            "Reused persistent cached domain resolution.",
            details={
                "domains": [d.get("domain", "") for d in cached_res.get("domains", [])],
                "company": company,
            },
        )
        return cached_res

    log(
        "LLM_KNOWLEDGE_START",
        "Checking model knowledge for official/brand/person domains.",
        details={
            "company": company,
            "location": location or "not_available",
        },
    )

    try:
        knowledge = (
            resolve_domains_from_model_knowledge(
                name,
                job_title,
                company,
                location,
            )
        )
    except Exception as error:
        print(
            "Domain resolver knowledge error:",
            repr(error),
        )

        log(
            "LLM_KNOWLEDGE_ERROR",
            "Model-knowledge lookup failed.",
            level="error",
            details={
                "error": str(error),
            },
        )

        knowledge = {
            "status": "unknown",
            "coverage_complete": False,
            "domains": [],
            "confidence": 0.0,
            "method": "knowledge_error",
        }

    log(
        "LLM_KNOWLEDGE_RESULT",
        "Model-knowledge lookup completed.",
        details={
            "status": knowledge.get(
                "status",
                "unknown",
            ),
            "confidence": round(
                float(
                    knowledge.get(
                        "confidence",
                        0.0,
                    )
                ),
                4,
            ),
            "coverage_complete": bool(
                knowledge.get(
                    "coverage_complete",
                    False,
                )
            ),
            "domains": [
                (
                    f"{item.get('type', '')}:"
                    f"{item.get('domain', '')}"
                )
                for item in knowledge.get(
                    "domains",
                    [],
                )
            ],
        },
    )

    # Skip web only when the model claims near-certain, complete
    # knowledge for the permitted categories.
    if (
        knowledge["status"] == "known"
        and knowledge["coverage_complete"] is True
        and knowledge["domains"]
        and knowledge["confidence"]
        >= DOMAIN_KNOWLEDGE_MIN_CONFIDENCE
    ):
        log(
            "LLM_KNOWLEDGE_ACCEPTED",
            "High-confidence model knowledge accepted; web search skipped.",
            details={
                "domains": [
                    item.get(
                        "domain",
                        "",
                    )
                    for item in knowledge.get(
                        "domains",
                        [],
                    )
                ],
            },
        )

        save_domain_cache_entry(company, location, knowledge)
        return knowledge

    log(
        "WEB_SEARCH_START",
        "Model knowledge was incomplete/uncertain; starting mandatory web verification.",
        details={
            "knowledge_status": knowledge.get(
                "status",
                "unknown",
            ),
            "knowledge_confidence": round(
                float(
                    knowledge.get(
                        "confidence",
                        0.0,
                    )
                ),
                4,
            ),
        },
    )

    try:
        web_result = (
            resolve_domains_with_web(
                name,
                job_title,
                company,
                location,
                knowledge.get(
                    "domains",
                    [],
                ),
            )
        )
    except Exception as error:
        print(
            "Domain resolver web error:",
            repr(error),
        )

        log(
            "WEB_SEARCH_ERROR",
            "Web domain verification failed.",
            level="error",
            details={
                "error": str(error),
            },
        )

        unresolved_res = {
            "status": "unresolved",
            "domains": [],
            "confidence": 0.0,
            "method": "web_error",
        }
        save_domain_cache_entry(company, location, unresolved_res)
        return unresolved_res

    log(
        "WEB_SEARCH_RESULT",
        "Web domain verification completed.",
        details={
            "status": web_result.get(
                "status",
                "unresolved",
            ),
            "confidence": round(
                float(
                    web_result.get(
                        "confidence",
                        0.0,
                    )
                ),
                4,
            ),
            "domains": [
                (
                    f"{item.get('type', '')}:"
                    f"{item.get('domain', '')}"
                )
                for item in web_result.get(
                    "domains",
                    [],
                )
            ],
        },
    )

    if (
        web_result["status"]
        == "verified"
        and web_result["domains"]
        and web_result["confidence"]
        >= DOMAIN_WEB_MIN_CONFIDENCE
    ):
        log(
            "WEB_SEARCH_ACCEPTED",
            "Verified web domains accepted for DB comparison.",
            details={
                "domains": [
                    item.get(
                        "domain",
                        "",
                    )
                    for item in web_result.get(
                        "domains",
                        [],
                    )
                ],
            },
        )

        save_domain_cache_entry(company, location, web_result)
        return web_result

    log(
        "DOMAIN_UNRESOLVED",
        "No sufficiently verified domain was found.",
        level="warning",
    )

    unresolved_final = {
        **web_result,
        "status": "unresolved",
        "domains": [],
    }
    save_domain_cache_entry(company, location, unresolved_final)
    return unresolved_final


def match_resolved_domains_to_rows(
    resolved_domains: list[dict],
    possible_matches: list,
    normalized_name: str,
):
    """
    Compare temporary AI-resolved domains against database domains.

    Rules:
    - company_official/company_brand can match any row already selected
      for this contact's Name + Job Title candidate group.
    - person_professional is allowed ONLY when the database row belongs
      to the exact normalized person name.

    Returns:
        (matched_row, matched_domain_info)
        or
        (None, None)
    """

    for item in resolved_domains:
        resolved_domain = (
            item.get(
                "domain",
                "",
            )
        )

        domain_type = (
            item.get(
                "type",
                "",
            )
        )

        if not resolved_domain:
            continue

        for row in possible_matches:
            stored_domain = (
                row[6] or ""
            )

            stored_normalized_name = (
                row[4] or ""
            )

            if not stored_domain:
                continue

            if (
                domain_type
                == "person_professional"
                and stored_normalized_name
                != normalized_name
            ):
                continue

            if domains_equivalent(
                resolved_domain,
                stored_domain,
            ):
                return (
                    row,
                    item,
                )

    return (
        None,
        None,
    )

# ============================================================
# JOB TITLE HIERARCHY & FUNCTIONAL RELEVANCE GUARDRAIL (AI)
#
# Implements the tiered decision-maker reasoning structure from
# job-title-hierarchy-guardrails (1).md using OpenAI (gpt-4o-mini).
# ============================================================

class TitleGuardrailEvaluation(BaseModel):
    tier: int = Field(
        ge=1,
        le=7,
        description="Seniority tier from 1 (Founder/Owner) to 7 (Individual Contributor)",
    )
    function_relevant: bool = Field(
        description="True if functional domain is relevant to software, AI/ML, product, data, innovation, IT/digital transformation, operations (with tech/AI/digital/systems), or executive leadership",
    )
    regional_synonym_applied: str | None = Field(
        default=None,
        description="Regional title mapping applied (e.g. 'Managing Director -> CEO equivalent for AU/NZ/SG', 'Head of X -> Tier 3/4 equivalent for AU/NZ/SG'), or null",
    )
    role_type: Literal["decision_maker", "evaluator"] = Field(
        description="Tagged role type: 'decision_maker' (budget/sign-off authority) or 'evaluator' (evaluator/champion)",
    )
    required: bool = Field(
        description="True if contact should be included in outreach list (Tiers 1-6 with function_relevant=true are required; Tier 7 or function irrelevant are false)",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence level of the evaluation",
    )
    reason: str = Field(
        description="One-line explanation of the qualification decision including tier, function, and role_type reasoning",
    )


_title_guardrail_cache: dict[tuple[str, str, str, str, str], dict] = {}


def infer_region_from_location(location: str) -> str:
    """Infer region code (US, AU, NZ, SG) from location string if possible."""
    if not location:
        return ""
    loc_lower = location.lower()
    if any(k in loc_lower for k in ["australia", "sydney", "melbourne", "brisbane", "perth", " adelaide", ", au", " au "]):
        return "AU"
    if any(k in loc_lower for k in ["new zealand", "auckland", "wellington", "christchurch", ", nz", " nz "]):
        return "NZ"
    if any(k in loc_lower for k in ["singapore", ", sg", " sg "]):
        return "SG"
    if any(k in loc_lower for k in ["united states", "usa", ", us", " us ", "san francisco", "new york", "austin", "seattle", "boston", "chicago", "california", "texas", "washington"]):
        return "US"
    return ""


def check_company_domain_exists_in_db(
    company_name: str,
    resolved_domains: list[dict] | None = None,
    connection = None,
) -> tuple[bool, str]:
    """
    Guardrail 1 (Target New Domains):
    Checks if any contact in the CRM database already belongs to this company's domain.
    Returns (True, matched_db_domain) if found, else (False, "").
    """
    comp_norm = normalize_text(company_name)
    if not comp_norm:
        return False, ""

    comp_vars = list(company_variants(company_name))
    candidate_domains = set()
    candidate_normalized = set(comp_vars)

    for v in comp_vars:
        if v:
            candidate_domains.add(f"{v}.com")
            candidate_domains.add(f"{v}.io")
            candidate_domains.add(f"{v}.co")
            candidate_domains.add(f"{v}.ai")
            candidate_domains.add(f"{v}.net")
            candidate_domains.add(f"{v}.org")

    if resolved_domains:
        for item in resolved_domains:
            dom = item.get("domain", "").strip().lower()
            if dom:
                candidate_domains.add(dom)
                brand = dom.split(".")[0]
                norm_brand = normalize_text(brand)
                if norm_brand:
                    candidate_normalized.add(norm_brand)

    query = """
        SELECT email_domain, normalized_domain
        FROM contacts
        WHERE normalized_domain = ANY(%s)
           OR email_domain = ANY(%s)
        LIMIT 1;
    """

    if connection:
        with connection.cursor() as cursor:
            cursor.execute(
                query,
                (list(candidate_normalized), list(candidate_domains)),
            )
            row = cursor.fetchone()
            if row:
                return True, row[0] or row[1] or ""
    else:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    query,
                    (list(candidate_normalized), list(candidate_domains)),
                )
                row = cursor.fetchone()
                if row:
                    return True, row[0] or row[1] or ""

    return False, ""


def evaluate_job_title_with_ai(
    name: str,
    job_title: str,
    company: str,
    location: str = "",
    employee_count: int | None = None,
    region: str = "",
    apollo_id: str = "",
) -> dict:
    """
    AI-powered job title hierarchy & functional relevance evaluation.
    Evaluates contacts using the 7-tier decision-maker framework, regional mapping,
    company-size adjustments, and function-override keywords.
    """
    if not GUARDRAILS_ENABLED:
        return {
            "tier": 1,
            "function_relevant": True,
            "regional_synonym_applied": None,
            "role_type": "decision_maker",
            "required": True,
            "confidence": "high",
            "reason": "Guardrails disabled",
        }

    job_title_clean = (job_title or "").strip()
    company_clean = (company or "").strip()
    location_clean = (location or "").strip()
    region_clean = (region or "").strip() or infer_region_from_location(location_clean)
    apollo_id_clean = (apollo_id or "").strip()

    cache_key = (
        normalize_text(job_title_clean),
        normalize_text(company_clean),
        normalize_text(location_clean),
        str(employee_count) if employee_count is not None else "",
        normalize_text(region_clean),
    )

    if cache_key in _title_guardrail_cache:
        return _title_guardrail_cache[cache_key]

    client = get_openai_client()

    emp_str = (
        f"employee_count: {employee_count}\n"
        if employee_count is not None
        else "employee_count: Unknown\n"
    )

    region_str = (
        f"region: {region_clean}\n"
        if region_clean
        else "region: Not specified (infer from location if possible)\n"
    )

    try:
        response = client.responses.parse(
            model=OPENAI_TITLE_GUARDRAIL_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert B2B SaaS decision-maker evaluator for AI/agent-solutions products "
                        "selling into US, AU, NZ, and SG markets. Evaluate the contact's job title using the "
                        "following tiered decision-maker reasoning structure:\n\n"
                        "1. Seniority Tiers (Tiers 1-7):\n"
                        "- Tier 1: Owner / Founder (Founder, Co-Founder, Managing Partner, Owner)\n"
                        "- Tier 2: C-Suite (CEO, President, CTO, CIO, CPO, COO, Chief AI Officer, Chief Data Officer, Chief Digital Officer)\n"
                        "- Tier 3: VP / Senior Leadership (VP Engineering, VP Product, VP AI/Data, VP Operations, SVP, EVP, Regional VP, Country Manager, Regional Director, GM of X, Head of X in AU/NZ/SG)\n"
                        "- Tier 4: Director (Director of Engineering, Director of Product, Director of AI/Data, Director of Innovation, Director of Technology, Head of X in smaller orgs)\n"
                        "- Tier 5: Senior Manager / Principal IC (Engineering Manager, Senior Product Manager, Principal Engineer, Lead Engineer, Head of Engineering in US/non-exec)\n"
                        "- Tier 6: Manager (Product Manager, Program Manager, Project Manager)\n"
                        "- Tier 7: Individual Contributor (Analyst, Associate, Specialist, Coordinator, Engineer non-lead, Intern)\n\n"
                        "2. Default Inclusion Rule:\n"
                        "- Tiers 1–6 = required: true WHENEVER function_relevant is true.\n"
                        "- If function_relevant is false (e.g. HR, Legal, pure Sales/Marketing), required MUST BE false regardless of seniority.\n"
                        "- Tier 7 = required: false (always excluded by default).\n"
                        "- CRITICAL: The presence of senior contacts at a company is NEVER a reason to exclude Manager-tier contacts (Tiers 5 and 6). "
                        "Both senior leaders (budget owners) and managers (hands-on evaluators / champions) must be captured and tagged with their role_type.\n\n"
                        "3. Functional Relevance Filter:\n"
                        "- Relevant functions (include / function_relevant=true): Engineering / Software / AI / ML, Product, Data, Innovation / R&D, "
                        "IT / Digital Transformation, Operations (when paired with Technology, Digital, AI, or Systems), General Management / Founder / C-level.\n"
                        "- Excluded functions by default (function_relevant=false, required=false even at senior tiers like VP or Director): Sales (unless RevOps or Sales Engineering), "
                        "Marketing (unless MarTech or Growth-AI), HR / People / Talent Acquisition, Legal / Compliance, pure Finance (unless CFO or Director of Finance with explicit sign-off), non-managerial Customer Support / Success.\n"
                        "- Edge case: Founder / CEO / Managing Director is ALWAYS included (function_relevant=true, required=true) regardless of stated function.\n\n"
                        "4. Regional Title Mapping (US vs AU / NZ / SG):\n"
                        "- AU / NZ / SG: 'Managing Director (MD)' = Tier 2 (CEO equivalent).\n"
                        "- AU / NZ / SG: 'Head of X' = functional head with real budget authority (Tier 3-4).\n"
                        "- AU / NZ / SG: 'Country Manager' / 'Regional Director' = Tier 3.\n"
                        "- 'General Manager (GM)' = Tier 2-3 depending on scope.\n\n"
                        "5. Company-Size Adjustment Logic (Adjusts role_type and confidence, NOT required):\n"
                        "- <= 50 employees: Flat structure. Tiers 1–6 tagged required: true, role_type: 'decision_maker'.\n"
                        "- 51–150 employees: Tiers 1–4 tagged role_type: 'decision_maker'. Tiers 5–6 tagged role_type: 'evaluator'. Both are required: true.\n"
                        "- 150+ employees: Tiers 1–4 tagged role_type: 'decision_maker'. Tiers 5–6 tagged role_type: 'evaluator'. Both are required: true.\n"
                        "- If employee count is unknown, default to standard: Tiers 1–4 -> 'decision_maker', Tiers 5–6 -> 'evaluator'. Both required: true.\n"
                        "- Net rule: NO company-size band should ever cause a Manager-tier contact (Tiers 5-6) to be dropped.\n\n"
                        "6. Function-Override Keywords (Confidence Booster & Role-Type Upgrader):\n"
                        "- Keywords: AI, Agent, Agentic, Automation, ML, Machine Learning, Innovation, Applied AI, AI/ML, Intelligent Automation, Digital Transformation.\n"
                        "- When present in a Tier 5 or Tier 6 title: boost confidence to 'high' and upgrade role_type to 'decision_maker' (e.g. 'Engineering Manager, Applied AI' at a 200-person company -> required: true, role_type: 'decision_maker', confidence: 'high').\n\n"
                        "7. Explicit Exclude List (Tier 7 - always required: false regardless of size/region):\n"
                        "- Analyst, Associate, Specialist, Coordinator, Intern, Graduate/Junior *, Executive Assistant, Recruiter, Talent Acquisition *, "
                        "Sales Development Rep (SDR), Account Executive (AE) (unless Sales Engineering/RevOps), HR Business Partner, Marketing Coordinator, non-managerial Customer Support Rep.\n"
                        "- Always set tier: 7, required: false.\n\n"
                        "Provide your reasoned structured evaluation matching the output schema."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"job_title: {job_title_clean}\n"
                        f"company_name: {company_clean}\n"
                        f"apollo_id: {apollo_id_clean or 'N/A'}\n"
                        f"{emp_str}"
                        f"{region_str}"
                        f"location: {location_clean or 'Not specified'}\n"
                        f"person_name: {name}\n"
                    ),
                },
            ],
            text_format=TitleGuardrailEvaluation,
        )

        parsed = response.output_parsed
        if parsed is None:
            res = {
                "tier": 4,
                "function_relevant": True,
                "regional_synonym_applied": None,
                "role_type": "decision_maker",
                "required": True,
                "confidence": "low",
                "reason": "AI evaluation parse fallback",
            }
        else:
            tier = parsed.tier
            function_relevant = parsed.function_relevant
            role_type = parsed.role_type
            confidence = parsed.confidence
            required = parsed.required
            regional_synonym = parsed.regional_synonym_applied
            reason = parsed.reason

            # Guardrail Rule 1: Founder / CEO / Managing Director is ALWAYS included & function_relevant
            title_lower = job_title_clean.lower()
            is_founder_ceo = any(
                k in title_lower
                for k in ["founder", "co-founder", "ceo", "chief executive", "managing director"]
            )

            # Functional exclude check: HR, Legal, pure Sales/Marketing
            is_excluded_function = any(
                re.search(rf"\b{re.escape(k)}\b", title_lower)
                for k in [
                    "human resources", "hr director", "hr manager", "hr business partner", "head of hr",
                    "legal counsel", "legal", "counsel", "compliance",
                    "recruiter", "talent acquisition", "talent",
                    "marketing coordinator", "customer support"
                ]
            ) and not any(
                re.search(rf"\b{re.escape(k)}\b", title_lower)
                for k in ["revops", "sales engineering", "sales engineer", "martech", "ai", "ml", "automation"]
            )

            is_pure_sales = (
                re.search(r"\bsales\b", title_lower) is not None
                and not any(
                    k in title_lower
                    for k in ["revops", "sales engineering", "sales engineer", "solutions", "ai", "ml", "automation", "tech"]
                )
            )

            is_tier7_role = any(
                re.search(rf"\b{re.escape(k)}\b", title_lower)
                for k in ["intern", "internship", "recruiter", "executive assistant", "sales development rep", "sdr", "bdr", "marketing coordinator"]
            ) and not any(
                re.search(rf"\b{re.escape(k)}\b", title_lower)
                for k in ["director", "lead", "principal", "head of", "manager", "vp", "chief"]
            )

            if is_founder_ceo:
                function_relevant = True
                required = True
                if tier > 2:
                    tier = 2
            elif is_excluded_function or is_pure_sales:
                function_relevant = False
                required = False
            elif is_tier7_role or tier >= 7:
                tier = 7
                required = False
            else:
                if not function_relevant:
                    required = False
                elif tier in range(1, 7):
                    required = True

            # Guardrail Rule 2: Company size <= 50 -> role_type is decision_maker for Tiers 1-6
            if employee_count is not None and employee_count <= 50 and required:
                role_type = "decision_maker"

            # Guardrail Rule 3: Function override keywords upgrade role_type to decision_maker & confidence to high
            has_override_keyword = any(
                re.search(rf"\b{re.escape(kw)}\b", title_lower)
                for kw in [
                    "ai", "agent", "agentic", "automation", "ml", "machine learning",
                    "innovation", "applied ai", "ai/ml", "intelligent automation", "digital transformation"
                ]
            )
            if has_override_keyword and required:
                role_type = "decision_maker"
                confidence = "high"

            res = {
                "tier": tier,
                "function_relevant": function_relevant,
                "regional_synonym_applied": regional_synonym,
                "role_type": role_type,
                "required": required,
                "confidence": confidence,
                "reason": reason,
            }

    except Exception as error:
        print("AI Title guardrail evaluation error:", repr(error))
        res = {
            "tier": 4,
            "function_relevant": True,
            "regional_synonym_applied": None,
            "role_type": "decision_maker",
            "required": True,
            "confidence": "low",
            "reason": f"Fallback due to evaluation error: {error}",
        }

    _title_guardrail_cache[cache_key] = res
    return res


# ============================================================
# GUARDRAIL 3: PURE INDIAN NAME FILTER (Demographic Classifier)
# ============================================================

_indian_name_cache: dict[str, dict] = {}


class SingleNameClassification(BaseModel):
    id: str
    name: str
    is_pure_indian: bool = Field(
        description=(
            "True ONLY if 100% certain unambiguous pure Indian name "
            "(e.g. Sharma, Patel, Rao, Iyer, Mukherjee, Gupta, Reddy, Nair, Singh, Mehta, Agarwal). "
            "False for any edge case, Goan/Portuguese Christian name (D'Souza, Fernandes, Lobo, Pinto), "
            "Arab/Middle Eastern name (Khan, Sheikh, Qureshi), Western/foreign name, or ambiguous name."
        )
    )
    confidence: Literal["high", "medium", "low"]
    reason: str


class BatchNameClassificationResponse(BaseModel):
    results: list[SingleNameClassification]


def classify_names_origin_batch(contacts: list[dict]) -> dict[str, dict]:
    """
    Classifies a batch of names as UNAMBIGUOUS PURE INDIAN vs FOREIGN/EDGE CASE.
    Strict Rule: If there is ANY ambiguity or edge case:
      - Goan/Kerala/Portuguese Christian names (e.g., Peter D'Souza, Kevin Fernandes, Keith Lobo, Ryan Pinto)
      - Middle Eastern / Arab names (e.g., Zaid Khan, Tariq Sheikh, Imran Al-Mansoor)
      - Western / Global ambiguous names (e.g., Maya Lin, Neil Armstrong, Anita Berg, Sonya Meyer)
    It MUST be classified as is_pure_indian: False.
    Only 100% unambiguous pure Indian linguistic names (Sharma, Patel, Iyer, Rao, Mukherjee,
    Reddy, Nair, Singh, Mehta, Gupta, Agarwal, etc.) are is_pure_indian: True.
    """
    global _indian_name_cache

    if not contacts:
        return {}

    output: dict[str, dict] = {}
    missing_tasks = []

    for c in contacts:
        cid = c["key"]
        norm_name = c["normalized_name"]
        if norm_name in _indian_name_cache:
            output[cid] = _indian_name_cache[norm_name]
        else:
            missing_tasks.append({"id": cid, "name": c["name"]})

    if not missing_tasks:
        return output

    try:
        client = get_openai_client()
        input_payload = json.dumps(missing_tasks)

        response = client.responses.parse(
            model=OPENAI_DOMAIN_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a conservative demographic name origin classifier. "
                        "Your task is to classify whether each person's full name is of "
                        "UNAMBIGUOUS, PURE INDIAN ORIGIN (e.g. Sharma, Patel, Iyer, Rao, Mukherjee, "
                        "Reddy, Nair, Singh, Gupta, Mehta, Agarwal, Chatterjee, Banerjee, Kulkarni, Hegde).\n\n"
                        "CRITICAL CONSERVATIVE POLICY — WHEN IN DOUBT, DO NOT CLASSIFY AS INDIAN:\n"
                        "1. Goan, Mangalorean & Kerala Portuguese/Christian names (e.g. D'Souza, Fernandes, "
                        "Lobo, Pinto, Mathew, Kurian, Dias, Perera, Rodrigues, Alvares, Gomez) -> is_pure_indian = FALSE.\n"
                        "2. Middle Eastern, Arab & Central Asian names (e.g. Khan, Sheikh, Qureshi, "
                        "Ansari, Al-Mansoor, Siddiqui, Tariq, Farhan, Syed) -> is_pure_indian = FALSE.\n"
                        "3. Western, Hispanic, East Asian, or Global ambiguous names (e.g. Maya, Neil, "
                        "Anita, Sonya, John, David, Jean, Goh, Chen, Lee) -> is_pure_indian = FALSE.\n"
                        "4. ONLY return is_pure_indian = true when you are 100% certain the name belongs "
                        "to a pure, unmistakable Indian heritage/surname."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Classify the following contacts:\n{input_payload}",
                },
            ],
            text_format=BatchNameClassificationResponse,
        )

        parsed = response.output_parsed
        if parsed and parsed.results:
            for item in parsed.results:
                res_dict = {
                    "is_pure_indian": item.is_pure_indian,
                    "confidence": item.confidence,
                    "reason": item.reason,
                }
                output[item.id] = res_dict
                matching_contact = next((c for c in contacts if c["key"] == item.id), None)
                if matching_contact:
                    _indian_name_cache[matching_contact["normalized_name"]] = res_dict
    except Exception as err:
        print(f"[ContactChecker] Warning: Indian name batch classification error: {err}")
        for task in missing_tasks:
            output[task["id"]] = {
                "is_pure_indian": False,
                "confidence": "low",
                "reason": f"Fallback error: {err}",
            }

    return output


# ============================================================
# REQUEST MODELS
# ============================================================

class CheckRequest(BaseModel):
    emails: list[str]


class ApolloContact(BaseModel):
    key: str
    name: str
    job_title: str
    company: str
    location: str = ""
    employee_count: int | None = None
    region: str = ""


class ApolloMatchRequest(BaseModel):
    contacts: list[ApolloContact]
    title_guardrail_enabled: bool = False
    indian_name_guardrail_enabled: bool = False


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok"
    }


# ============================================================
# NORMAL EMAIL CHECK
# ============================================================

@app.post("/check")
def check_emails(
    request: CheckRequest
):

    emails = {
        email.strip().lower()
        for email in request.emails
        if email.strip()
    }

    if not emails:
        return {
            "results": {}
        }

    email_list = list(
        emails
    )

    with get_connection() as connection:

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT DISTINCT LOWER(email)
                FROM contacts
                WHERE LOWER(email) = ANY(%s);
                """,
                (
                    email_list,
                ),
            )

            rows = cursor.fetchall()


    found = {
        row[0]
        for row in rows
    }


    results = {
        email: email in found
        for email in email_list
    }


    return {
        "results": results
    }


# ============================================================
# APOLLO MATCHING
#
# MATCHES USING:
#
# 1. NAME
# 2. JOB TITLE
# 3. COMPANY vs EMAIL DOMAIN
#
# ============================================================

@app.post("/match-apollo")
def match_apollo(
    request: ApolloMatchRequest
):
    activity: list[dict] = []

    append_activity(
        activity,
        "BATCH_STARTED",
        "Apollo contact batch received.",
        details={
            "contacts_received":
                len(request.contacts),
        },
    )

    if not request.contacts:
        summary = {
            "contacts_received": 0,
            "contacts_processed": 0,
            "existing": 0,
            "required": 0,
            "deterministic_matches": 0,
            "knowledge_lookups": 0,
            "web_searches": 0,
            "resolver_unresolved": 0,
        }

        return {
            "results": {},
            "activity": activity,
            "summary": summary,
        }


    # ========================================================
    # NORMALIZE APOLLO CONTACTS
    # ========================================================

    prepared_contacts = []


    for contact in request.contacts:

        normalized_name = normalize_text(
            contact.name
        )

        normalized_title = normalize_text(
            contact.job_title
        )


        if (
            not normalized_name
            or not normalized_title
            or not contact.company.strip()
        ):
            append_activity(
                activity,
                "CONTACT_SKIPPED",
                "Contact is missing name, title, or company.",
                contact_key=contact.key,
                level="warning",
                details={
                    "name": contact.name,
                    "job_title": contact.job_title,
                    "company": contact.company,
                },
            )

            continue


        prepared_contacts.append(
            {
                "key":
                    contact.key,

                "name":
                    contact.name.strip(),

                "job_title":
                    contact.job_title.strip(),

                "company":
                    contact.company.strip(),

                "location":
                    contact.location.strip(),

                "employee_count":
                    contact.employee_count,

                "region":
                    contact.region.strip(),

                "normalized_name":
                    normalized_name,

                "normalized_title":
                    normalized_title,
            }
        )


    if not prepared_contacts:
        summary = {
            "contacts_received":
                len(request.contacts),
            "contacts_processed": 0,
            "existing": 0,
            "required": 0,
            "deterministic_matches": 0,
            "knowledge_lookups": 0,
            "web_searches": 0,
            "resolver_unresolved": 0,
        }

        append_activity(
            activity,
            "BATCH_COMPLETE",
            "No valid contacts were available to process.",
            level="warning",
            details=summary,
        )

        return {
            "results": {},
            "activity": activity,
            "summary": summary,
        }


    # ========================================================
    # COUNT CONTACTS PER COMPANY IN BATCH
    # ========================================================

    company_counts = {}
    for item in prepared_contacts:
        comp_key = normalize_text(item["company"]) or "unknown"
        company_counts[comp_key] = company_counts.get(comp_key, 0) + 1

    # ========================================================
    # BUILD UNIQUE NORMALIZED NAMES
    # ========================================================

    unique_names = list(
        {
            item["normalized_name"]
            for item in prepared_contacts
        }
    )

    append_activity(
        activity,
        "DB_QUERY_START",
        "Looking up matching normalized Name candidates.",
        details={
            "unique_names":
                len(unique_names),
        },
    )


    # ========================================================
    # GET POSSIBLE MATCHES FROM POSTGRESQL & COMPARE
    # ========================================================

    global _batch_counter
    _batch_counter += 1
    batch_num = _batch_counter
    start_time = time.perf_counter()

    title_mode = "ON (7-Tier Seniority & Function Relevance)" if request.title_guardrail_enabled else "OFF (New Domains Auto-Qualified)"
    indian_mode = "ON (Pure Indian Names Excluded)" if request.indian_name_guardrail_enabled else "OFF"

    print("\n" + "=" * 90, flush=True)
    print(f">>> [BATCH #{batch_num}] INGESTED {len(request.contacts)} APOLLO CONTACT(S)", flush=True)
    print(f"    Mode: Guardrail 1 (Target New Domains): ACTIVE | Guardrail 2 (AI Title): {title_mode} | Guardrail 3 (Indian Name): {indian_mode}", flush=True)
    print("=" * 90, flush=True)

    with get_connection() as connection:

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    c.email,
                    c.first_name,
                    c.last_name,
                    c.job_title,

                    c.normalized_name,
                    c.normalized_title,

                    c.email_domain,
                    c.normalized_domain,

                    c.source_login,
                    c.source_file

                FROM contacts c
                WHERE c.normalized_name = ANY(%s);
                """,
                (
                    unique_names,
                ),
            )

            database_rows = (
                cursor.fetchall()
            )


        append_activity(
            activity,
            "DB_QUERY_COMPLETE",
            "Database candidate lookup completed.",
            details={
                "database_rows":
                    len(database_rows),
            },
        )


        # ========================================================
        # GROUP DATABASE CANDIDATES BY NORMALIZED NAME
        # ========================================================

        candidates = {}

        for row in database_rows:
            norm_name = row[4]
            if norm_name not in candidates:
                candidates[norm_name] = []
            candidates[norm_name].append(row)


        # ========================================================
        # CONCURRENT DOMAIN RESOLUTION PRE-FETCH (PARALLEL WORKERS)
        # ========================================================

        results = {}
        domain_resolution_cache = {}

        if DOMAIN_RESOLVER_ENABLED:
            unique_company_tasks = {}
            for contact in prepared_contacts:
                comp = contact["company"].strip()
                if not comp:
                    continue
                loc = contact.get("location", "").strip()
                norm_key = (
                    contact["normalized_name"],
                    contact["normalized_title"],
                    normalize_text(comp),
                    normalize_text(loc),
                )

                cached_res = get_cached_domain_resolution(comp, loc)
                if cached_res:
                    domain_resolution_cache[norm_key] = cached_res
                elif norm_key not in unique_company_tasks:
                    unique_company_tasks[norm_key] = (
                        contact["name"],
                        contact["job_title"],
                        comp,
                        loc,
                    )

            if unique_company_tasks:
                print(
                    f"   [PARALLEL RESOLVER] Concurrently resolving {len(unique_company_tasks)} unique company domain(s)...",
                    flush=True,
                )
                with ThreadPoolExecutor(max_workers=15) as executor:
                    future_to_key = {
                        executor.submit(
                            resolve_contact_domains,
                            name,
                            title,
                            comp,
                            loc,
                        ): k
                        for k, (name, title, comp, loc) in unique_company_tasks.items()
                    }
                    for future in as_completed(future_to_key):
                        k = future_to_key[future]
                        try:
                            res = future.result()
                            domain_resolution_cache[k] = res
                        except Exception as err:
                            print(f"[ContactChecker] Error resolving domain for {k}: {err}")

        # ========================================================
        # GUARDRAIL 3: BATCH INDIAN NAME ORIGIN PRE-FETCH
        # ========================================================

        name_classifications = {}
        if GUARDRAILS_ENABLED and request.indian_name_guardrail_enabled:
            name_classifications = classify_names_origin_batch(prepared_contacts)

        for idx, contact in enumerate(prepared_contacts, 1):

            def contact_log(
                event: str,
                message: str,
                *,
                level: str = "info",
                details: dict | None = None,
            ):
                append_activity(
                    activity,
                    event,
                    message,
                    contact_key=contact[
                        "key"
                    ],
                    level=level,
                    details=details or {},
                )


            contact_log(
                "CONTACT_CHECK_STARTED",
                (
                    f"{contact['name']} | "
                    f"{contact['job_title']} | "
                    f"{contact['company']}"
                ),
                details={
                    "location":
                        contact.get(
                            "location",
                            "",
                        )
                        or "not_available",
                },
            )

            print(
                f"\n[{idx:02d}/{len(prepared_contacts):02d}] Contact: \"{contact['name']}\" | \"{contact['company']}\" | \"{contact['job_title']}\" (Loc: {contact.get('location') or 'N/A'})",
                flush=True,
            )

            possible_matches = (
                candidates.get(
                    contact["normalized_name"],
                    [],
                )
            )


            contact_log(
                "DB_CANDIDATES",
                "Name candidate rows loaded from DB.",
                details={
                    "count":
                        len(
                            possible_matches
                        ),
                },
            )


            matched_row = None
            matched_domain_info = None
            resolution = None


            # ====================================================
            # COMPANY CHECK — STAGE 1 (DETERMINISTIC)
            # ====================================================

            for row in possible_matches:

                email_domain = row[6]

                if not email_domain:
                    continue

                if company_matches(
                    contact[
                        "company"
                    ],
                    email_domain,
                ):

                    matched_row = row

                    contact_log(
                        "DETERMINISTIC_MATCH",
                        "Company name matched an existing DB domain without AI.",
                        details={
                            "db_domain":
                                email_domain,
                        },
                    )

                    break


            # ====================================================
            # COMPANY CHECK — STAGE 2 (AI DOMAIN RESOLUTION)
            # ====================================================

            if (
                matched_row is None
                and possible_matches
                and DOMAIN_RESOLVER_ENABLED
            ):

                contact_log(
                    "DETERMINISTIC_NO_MATCH",
                    "Free company/domain normalization did not find a match.",
                )

                cache_key = (
                    contact[
                        "normalized_name"
                    ],
                    contact[
                        "normalized_title"
                    ],
                    normalize_text(
                        contact["company"]
                    ),
                    normalize_text(
                        contact.get(
                            "location",
                            "",
                        )
                    ),
                )

                if (
                    cache_key
                    not in domain_resolution_cache
                ):
                    domain_resolution_cache[
                        cache_key
                    ] = resolve_contact_domains(
                        contact["name"],
                        contact["job_title"],
                        contact["company"],
                        contact.get(
                            "location",
                            "",
                        ),
                        activity_logger=
                            contact_log,
                    )
                else:
                    contact_log(
                        "RESOLVER_CACHE_HIT",
                        "Reused temporary domain resolution from this API batch.",
                    )

                resolution = (
                    domain_resolution_cache[
                        cache_key
                    ]
                )

                if resolution.get("domains"):
                    (
                        matched_row,
                        matched_domain_info,
                    ) = match_resolved_domains_to_rows(
                        resolution["domains"],
                        possible_matches,
                        contact[
                            "normalized_name"
                        ],
                    )

                    if matched_row:
                        contact_log(
                            "DOMAIN_DB_MATCH",
                            "Resolved domain matched an existing DB domain.",
                            details={
                                "resolved_domain":
                                    matched_domain_info.get(
                                        "domain",
                                        "",
                                    ),
                                "domain_type":
                                    matched_domain_info.get(
                                        "type",
                                        "",
                                    ),
                                "db_domain":
                                    matched_row[6]
                                    or "",
                                "method":
                                    resolution.get(
                                        "method",
                                        "",
                                    ),
                            },
                        )
                    else:
                        contact_log(
                            "DOMAIN_DB_NO_MATCH",
                            "Resolved domains did not match the DB candidate domains.",
                            details={
                                "resolved_domains": [
                                    item.get(
                                        "domain",
                                        "",
                                    )
                                    for item
                                    in resolution.get(
                                        "domains",
                                        [],
                                    )
                                ],
                            },
                        )

            elif (
                matched_row is None
                and not possible_matches
            ):
                contact_log(
                    "NO_DB_CANDIDATES",
                    (
                        "No existing Name candidate was found in CRM. "
                        "Evaluating company domain against database."
                    ),
                )

            elif (
                matched_row is None
                and possible_matches
                and not DOMAIN_RESOLVER_ENABLED
            ):
                contact_log(
                    "RESOLVER_SKIPPED",
                    "Domain resolver is disabled.",
                    level="warning",
                )


            # ====================================================
            # MATCH FOUND (PERSON ALREADY IN CRM)
            # ====================================================

            if matched_row:

                email = (
                    matched_row[0]
                    or ""
                )

                first_name = (
                    matched_row[1]
                    or ""
                )

                last_name = (
                    matched_row[2]
                    or ""
                )

                stored_job_title = (
                    matched_row[3]
                    or ""
                )

                email_domain = (
                    matched_row[6]
                    or ""
                )

                source_login = (
                    matched_row[8]
                    or ""
                )

                source_file = (
                    matched_row[9]
                    or ""
                )


                results[
                    contact["key"]
                ] = {

                    "exists":
                        True,

                    "required":
                        False,

                    "ignored":
                        False,

                    "email":
                        email,

                    "name":
                        (
                            f"{first_name} "
                            f"{last_name}"
                        ).strip(),

                    "job_title":
                        stored_job_title,

                    "apollo_company":
                        contact[
                            "company"
                        ],

                    "email_domain":
                        email_domain,

                    "domain_brand":
                        get_domain_brand(
                            email_domain
                        ),

                    "source_login":
                        source_login,

                    "source_file":
                        source_file,

                    "domain_match_method":
                        (
                            resolution.get(
                                "method"
                            )
                            if resolution
                            else "deterministic"
                        ),

                    "resolved_domain":
                        (
                            matched_domain_info.get(
                                "domain",
                                "",
                            )
                            if matched_domain_info
                            else ""
                        ),

                    "resolved_domain_type":
                        (
                            matched_domain_info.get(
                                "type",
                                "",
                            )
                            if matched_domain_info
                            else ""
                        ),
                }


                contact_log(
                    "FINAL_EXISTING",
                    "Contact marked Existing.",
                    details={
                        "db_domain":
                            email_domain,
                        "match_method":
                            (
                                resolution.get(
                                    "method"
                                )
                                if resolution
                                else "deterministic"
                            ),
                    },
                )

                print(
                    f"   |-- Step 1 (CRM Person Match): Found {len(possible_matches)} candidate(s) in CRM",
                    flush=True,
                )
                print(
                    f"   |-- Step 2 (Domain Match): MATCHED CRM Domain '{email_domain}'",
                    flush=True,
                )
                print(
                    f"   \\--> FINAL RESULT: [EXISTING] ({email})",
                    flush=True,
                )


            # ====================================================
            # NO CRM PERSON MATCH -> APPLY GUARDRAILS
            # ====================================================

            else:
                domain_in_db = False
                db_domain_match = ""

                if GUARDRAILS_ENABLED:
                    # Guardrail 1: Target New Domains
                    # Check if company domain already exists in CRM database with any contact
                    resolved_doms = resolution.get("domains") if resolution else []
                    domain_in_db, db_domain_match = check_company_domain_exists_in_db(
                        contact["company"],
                        resolved_domains=resolved_doms,
                        connection=connection,
                    )

                    # If not found deterministically and DOMAIN_RESOLVER_ENABLED is on and resolution wasn't done:
                    if not domain_in_db and DOMAIN_RESOLVER_ENABLED and not resolution:
                        cache_key = (
                            contact["normalized_name"],
                            contact["normalized_title"],
                            normalize_text(contact["company"]),
                            normalize_text(contact.get("location", "")),
                        )
                        if cache_key not in domain_resolution_cache:
                            domain_resolution_cache[cache_key] = resolve_contact_domains(
                                contact["name"],
                                contact["job_title"],
                                contact["company"],
                                contact.get("location", ""),
                                activity_logger=contact_log,
                            )
                        resolution = domain_resolution_cache[cache_key]
                        if resolution.get("domains"):
                            domain_in_db, db_domain_match = check_company_domain_exists_in_db(
                                contact["company"],
                                resolved_domains=resolution.get("domains"),
                                connection=connection,
                            )

                if domain_in_db:
                    contact_log(
                        "DOMAIN_ALREADY_IN_DB",
                        f"Company domain '{db_domain_match}' already exists in CRM database with existing contacts. Contact ignored to target new domains.",
                        level="warning",
                        details={
                            "company": contact["company"],
                            "matched_db_domain": db_domain_match,
                        },
                    )
                    results[contact["key"]] = {
                        "exists": False,
                        "required": False,
                        "ignored": True,
                        "guardrail_status": "domain_already_in_db",
                        "guardrail_reason": f"Company domain '{db_domain_match}' already exists in CRM database with existing contacts.",
                        "matched_db_domain": db_domain_match,
                        "domain_resolution_status": (
                            resolution.get("status") if resolution else "not_needed"
                        ),
                        "domain_match_method": (
                            resolution.get("method") if resolution else "deterministic"
                        ),
                    }

                    print(
                        f"   |-- Step 1 (CRM Person Match): No duplicate person found",
                        flush=True,
                    )
                    print(
                        f"   |-- Step 2 (Guardrail 1 - New Domain): Domain '{db_domain_match}' ALREADY EXISTS in CRM",
                        flush=True,
                    )
                    print(
                        f"   \\--> FINAL RESULT: [IGNORED: Existing Domain] (Not net-new)",
                        flush=True,
                    )

                elif GUARDRAILS_ENABLED and request.indian_name_guardrail_enabled and name_classifications.get(contact["key"], {}).get("is_pure_indian") is True:
                    name_origin = name_classifications[contact["key"]]
                    contact_log(
                        "INDIAN_NAME_DISQUALIFIED",
                        f"Contact excluded by Indian Name Filter: {name_origin.get('reason')}",
                        level="warning",
                        details=name_origin,
                    )
                    results[contact["key"]] = {
                        "exists": False,
                        "required": False,
                        "ignored": True,
                        "guardrail_status": "indian_name_disqualified",
                        "guardrail_reason": f"Excluded by Indian Name Filter: {name_origin.get('reason')}",
                        "name_origin_details": name_origin,
                        "domain_resolution_status": (
                            resolution.get("status") if resolution else "not_needed"
                        ),
                        "domain_match_method": (
                            resolution.get("method") if resolution else "deterministic"
                        ),
                    }

                    print(
                        f"   |-- Step 1 (CRM Person Match): No duplicate person found",
                        flush=True,
                    )
                    print(
                        f"   |-- Step 2 (Guardrail 1 - New Domain): Domain is NET-NEW",
                        flush=True,
                    )
                    print(
                        f"   |-- Step 3 (Guardrail 3 - Indian Name): Pure Indian Origin -> DISQUALIFIED ({name_origin.get('reason')})",
                        flush=True,
                    )
                    print(
                        f"   \\--> FINAL RESULT: [IGNORED: Indian Origin] (Non-target demographic)",
                        flush=True,
                    )

                elif GUARDRAILS_ENABLED and request.title_guardrail_enabled:
                    contact_log(
                        "TITLE_GUARDRAIL_START",
                        f"Evaluating job title '{contact['job_title']}' via AI hierarchy guardrails.",
                        details={
                            "job_title": contact["job_title"],
                            "company": contact["company"],
                            "location": contact.get("location", ""),
                            "employee_count": contact.get("employee_count"),
                            "region": contact.get("region", ""),
                        },
                    )

                    guardrail_res = evaluate_job_title_with_ai(
                        name=contact["name"],
                        job_title=contact["job_title"],
                        company=contact["company"],
                        location=contact.get("location", ""),
                        employee_count=contact.get("employee_count"),
                        region=contact.get("region", ""),
                        apollo_id=contact.get("key", ""),
                    )

                    if guardrail_res.get("required") is True:
                        contact_log(
                            "TITLE_QUALIFIED",
                            f"Contact qualified as REQUIRED (Tier {guardrail_res.get('tier')}, role_type={guardrail_res.get('role_type')}): {guardrail_res.get('reason')}",
                            details=guardrail_res,
                        )
                        results[contact["key"]] = {
                            "exists": False,
                            "required": True,
                            "ignored": False,
                            "guardrail_status": "qualified",
                            "tier": guardrail_res.get("tier"),
                            "role_type": guardrail_res.get("role_type"),
                            "function_relevant": guardrail_res.get("function_relevant"),
                            "regional_synonym_applied": guardrail_res.get("regional_synonym_applied"),
                            "confidence": guardrail_res.get("confidence"),
                            "guardrail_reason": guardrail_res.get("reason"),
                            "domain_resolution_status": (
                                resolution.get("status") if resolution else "not_needed"
                            ),
                            "domain_match_method": (
                                resolution.get("method") if resolution else "deterministic"
                            ),
                        }

                        print(
                            f"   |-- Step 1 (CRM Person Match): No duplicate person found",
                            flush=True,
                        )
                        print(
                            f"   |-- Step 2 (Guardrail 1 - New Domain): Domain is NET-NEW",
                            flush=True,
                        )
                        print(
                            f"   |-- Step 3 (Guardrail 2 - AI Title): Tier {guardrail_res.get('tier')} [{guardrail_res.get('role_type')}] -> QUALIFIED ({guardrail_res.get('reason')})",
                            flush=True,
                        )
                        print(
                            f"   \\--> FINAL RESULT: [REQUIRED LEAD] (Saved for CSV export)",
                            flush=True,
                        )

                    else:
                        contact_log(
                            "TITLE_DISQUALIFIED",
                            f"Contact excluded by Title Guardrail (Tier {guardrail_res.get('tier')}): {guardrail_res.get('reason')}",
                            level="warning",
                            details=guardrail_res,
                        )
                        results[contact["key"]] = {
                            "exists": False,
                            "required": False,
                            "ignored": True,
                            "guardrail_status": "disqualified_title",
                            "tier": guardrail_res.get("tier"),
                            "role_type": guardrail_res.get("role_type"),
                            "function_relevant": guardrail_res.get("function_relevant"),
                            "regional_synonym_applied": guardrail_res.get("regional_synonym_applied"),
                            "confidence": guardrail_res.get("confidence"),
                            "guardrail_reason": guardrail_res.get("reason"),
                            "domain_resolution_status": (
                                resolution.get("status") if resolution else "not_needed"
                            ),
                            "domain_match_method": (
                                resolution.get("method") if resolution else "deterministic"
                            ),
                        }

                        print(
                            f"   |-- Step 1 (CRM Person Match): No duplicate person found",
                            flush=True,
                        )
                        print(
                            f"   |-- Step 2 (Guardrail 1 - New Domain): Domain is NET-NEW",
                            flush=True,
                        )
                        print(
                            f"   |-- Step 3 (Guardrail 2 - AI Title): Tier {guardrail_res.get('tier')} -> DISQUALIFIED ({guardrail_res.get('reason')})",
                            flush=True,
                        )
                        print(
                            f"   \\--> FINAL RESULT: [IGNORED: Disqualified Title] (Non-target persona)",
                            flush=True,
                        )

                else:
                    # Guardrails 2 & 3 are OFF (or passed): Qualified directly based on Guardrail 1
                    results[contact["key"]] = {
                        "exists": False,
                        "required": True,
                        "ignored": False,
                        "guardrail_status": "qualified",
                        "guardrail_reason": "New target domain qualified.",
                        "domain_resolution_status": (
                            resolution.get("status") if resolution else "not_needed"
                        ),
                        "domain_match_method": (
                            resolution.get("method") if resolution else "deterministic"
                        ),
                    }

                    contact_log(
                        "FINAL_REQUIRED",
                        "Contact marked Required (New Target Domain).",
                    )

                    print(
                        f"   |-- Step 1 (CRM Person Match): No duplicate person found",
                        flush=True,
                    )
                    print(
                        f"   |-- Step 2 (Guardrail 1 - New Domain): Domain is NET-NEW",
                        flush=True,
                    )
                    print(
                        f"   |-- Step 3 (Auto-Qualified): Qualified as Required Lead",
                        flush=True,
                    )
                    print(
                        f"   \\--> FINAL RESULT: [REQUIRED LEAD] (Saved for CSV export)",
                        flush=True,
                    )


    # ========================================================
    # SUMMARY
    # ========================================================

    existing_count = sum(
        1
        for result in results.values()
        if result.get("exists") is True
    )

    required_count = sum(
        1
        for result in results.values()
        if result.get("exists") is False and result.get("required") is True and not result.get("ignored")
    )

    ignored_count = sum(
        1
        for result in results.values()
        if result.get("ignored") is True or (result.get("exists") is False and result.get("required") is False)
    )

    domain_already_in_db_count = sum(
        1
        for result in results.values()
        if result.get("guardrail_status") == "domain_already_in_db"
    )

    title_disqualified_count = sum(
        1
        for result in results.values()
        if result.get("guardrail_status") == "disqualified_title"
    )

    indian_name_disqualified_count = sum(
        1
        for result in results.values()
        if result.get("guardrail_status") == "indian_name_disqualified"
    )

    event_names = [
        entry["event"]
        for entry in activity
    ]

    summary = {
        "contacts_received":
            len(request.contacts),

        "contacts_processed":
            len(results),

        "existing":
            existing_count,

        "required":
            required_count,

        "ignored":
            ignored_count,

        "domain_already_in_db":
            domain_already_in_db_count,

        "title_disqualified":
            title_disqualified_count,

        "indian_name_disqualified":
            indian_name_disqualified_count,

        "title_guardrail_enabled":
            request.title_guardrail_enabled,

        "indian_name_guardrail_enabled":
            request.indian_name_guardrail_enabled,

        "deterministic_matches":
            event_names.count(
                "DETERMINISTIC_MATCH"
            ),

        "knowledge_lookups":
            event_names.count(
                "LLM_KNOWLEDGE_START"
            ),

        "web_searches":
            event_names.count(
                "WEB_SEARCH_START"
            ),

        "resolver_unresolved":
            event_names.count(
                "DOMAIN_UNRESOLVED"
            ),

        "no_db_candidates":
            event_names.count(
                "NO_DB_CANDIDATES"
            ),
    }


    append_activity(
        activity,
        "BATCH_COMPLETE",
        "Apollo batch processing completed.",
        details=summary,
    )

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    print("\n" + "-" * 90, flush=True)
    print(f"[SUMMARY] [BATCH #{batch_num} COMPLETED in {elapsed_ms:.1f}ms]", flush=True)
    print(f"   Processed: {len(results)} | Existing: {existing_count} | Required Leads: {required_count} | Ignored: {ignored_count} (Existing Domain: {domain_already_in_db_count}, Title Disqualified: {title_disqualified_count}, Indian Name Excluded: {indian_name_disqualified_count})", flush=True)
    print("=" * 90 + "\n", flush=True)


    return {
        "results":
            results,

        "activity":
            activity,

        "summary":
            summary,
    }
