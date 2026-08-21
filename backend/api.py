import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Literal, cast
from urllib.parse import urlparse

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel, Field


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
    "gpt-5.6-luna",
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

    response = client.responses.parse(
        model=OPENAI_DOMAIN_MODEL,

        reasoning={
            "effort": "none"
        },

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

    response = client.responses.parse(
        model=OPENAI_DOMAIN_MODEL,

        reasoning={
            "effort": "low"
        },

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

        return {
            "status": "unresolved",
            "domains": [],
            "confidence": 0.0,
            "method": "web_error",
        }

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

        return web_result

    log(
        "DOMAIN_UNRESOLVED",
        "No sufficiently verified domain was found.",
        level="warning",
    )

    return {
        **web_result,
        "status": "unresolved",
        "domains": [],
    }


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


class ApolloMatchRequest(BaseModel):
    contacts: list[ApolloContact]


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
    # GET POSSIBLE MATCHES FROM POSTGRESQL
    # ========================================================

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
    # COMPARE EACH APOLLO CONTACT
    # ========================================================

    results = {}

    # Temporary per-request cache only.
    domain_resolution_cache = {}


    for contact in prepared_contacts:

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
        # COMPANY CHECK — STAGE 1
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
        # COMPANY CHECK — STAGE 2
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
                    "No existing Name + Job Title candidate was found, "
                    "so domain resolution is not needed under the current duplicate rule."
                ),
                level="warning",
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
        # MATCH FOUND
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


        # ====================================================
        # NO MATCH
        # ====================================================

        else:

            results[
                contact["key"]
            ] = {

                "exists":
                    False,

                "domain_resolution_status":
                    (
                        resolution.get(
                            "status"
                        )
                        if resolution
                        else "not_needed"
                    ),

                "domain_match_method":
                    (
                        resolution.get(
                            "method"
                        )
                        if resolution
                        else "deterministic"
                    ),
            }


            contact_log(
                "FINAL_REQUIRED",
                "Contact marked Required by the current duplicate logic.",
                details={
                    "resolver_status":
                        (
                            resolution.get(
                                "status"
                            )
                            if resolution
                            else "not_needed"
                        ),
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
        if result.get("exists") is False
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


    return {
        "results":
            results,

        "activity":
            activity,

        "summary":
            summary,
    }

