import os
import re
import unicodedata

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Contact Database Checker API",
    version="1.1.0",
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

def get_domain_brand(domain: str) -> str:
    """
    Examples:

    simplon.com
        -> simplon

    wso-security.com
        -> wso-security

    fitoag.com.br
        -> fitoag

    company.co.uk
        -> company

    person@ve-group.com
        -> ve-group
    """

    if not domain:
        return ""

    domain = domain.strip().lower()

    # If an email gets passed instead of just a domain
    if "@" in domain:
        domain = domain.split("@", 1)[1]

    # Remove protocol if somehow present
    domain = re.sub(
        r"^https?://",
        "",
        domain,
    )

    # Remove www.
    if domain.startswith("www."):
        domain = domain[4:]

    # Remove port
    domain = domain.split(":")[0]

    # Remove path
    domain = domain.split("/")[0]

    parts = [
        part
        for part in domain.split(".")
        if part
    ]

    if not parts:
        return ""

    if len(parts) == 1:
        return parts[0]

    # Handle domains such as:
    #
    # fitoag.com.br
    # company.co.uk
    # company.com.au

    second_level_suffixes = {
        "co",
        "com",
        "org",
        "net",
        "gov",
        "edu",
        "ac",
    }

    if (
        len(parts) >= 3
        and len(parts[-1]) == 2
        and parts[-2] in second_level_suffixes
    ):
        return parts[-3]

    # Normal:
    # simplon.com -> simplon
    # wso-security.com -> wso-security

    return parts[-2]


def company_tokens(value: str) -> set[str]:
    """
    Convert company/domain text into individual words.

    Example:

    "WSO Worldwide Security Options"

    becomes:

    {
        "wso",
        "worldwide",
        "security",
        "options"
    }

    "wso-security"

    becomes:

    {
        "wso",
        "security"
    }
    """

    if not value:
        return set()

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

    # Convert punctuation to spaces
    # rather than completely deleting it.

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return {
        token
        for token in value.split()
        if token
    }


# ============================================================
# COMPANY MATCHING
# ============================================================

def company_matches(
    apollo_company: str,
    crm_domain: str,
) -> bool:
    """
    Examples that should match:

    Apollo:
        Simplon Fahrrad GmbH

    CRM:
        simplon.com


    Apollo:
        VE GROUP

    CRM:
        ve-group.com


    Apollo:
        WSO Worldwide Security Options

    CRM:
        wso-security.com


    Apollo:
        Fito Ag.

    CRM:
        fitoag.com.br
    """

    if not apollo_company or not crm_domain:
        return False

    brand = get_domain_brand(
        crm_domain
    )

    if not brand:
        return False


    # --------------------------------------------------------
    # METHOD 1:
    # Compact normalized comparison
    # --------------------------------------------------------

    company_compact = normalize_text(
        apollo_company
    )

    brand_compact = normalize_text(
        brand
    )

    if not company_compact or not brand_compact:
        return False


    # Exact:
    #
    # VE GROUP
    # ve-group.com
    #
    # vegroup == vegroup

    if company_compact == brand_compact:
        return True


    # Brand contained inside company:
    #
    # Simplon Fahrrad GmbH
    # simplon.com

    if (
        len(brand_compact) >= 4
        and brand_compact in company_compact
    ):
        return True


    # Company contained inside brand

    if (
        len(company_compact) >= 4
        and company_compact in brand_compact
    ):
        return True


    # --------------------------------------------------------
    # METHOD 2:
    # Token comparison
    #
    # WSO Worldwide Security Options
    #
    # domain:
    # wso-security
    #
    # domain tokens:
    # {"wso", "security"}
    #
    # company tokens:
    # {"wso", "worldwide", "security", "options"}
    # --------------------------------------------------------

    apollo_tokens = company_tokens(
        apollo_company
    )

    domain_tokens = company_tokens(
        brand
    )

    if (
        domain_tokens
        and domain_tokens.issubset(
            apollo_tokens
        )
    ):
        return True


    # --------------------------------------------------------
    # METHOD 3:
    # Acronym matching
    #
    # Worldwide Security Options
    # -> WSO
    #
    # Domain:
    # wso-security.com
    # --------------------------------------------------------

    meaningful_tokens = [
        token
        for token in apollo_tokens
        if len(token) > 1
    ]

    if len(meaningful_tokens) >= 2:

        acronym = "".join(
            token[0]
            for token in meaningful_tokens
        )

        if (
            acronym
            and (
                acronym in domain_tokens
                or acronym == brand_compact
            )
        ):
            return True


    return False


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

    if not request.contacts:

        return {
            "results": {}
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

                "normalized_name":
                    normalized_name,

                "normalized_title":
                    normalized_title,
            }
        )


    if not prepared_contacts:

        return {
            "results": {}
        }


    # ========================================================
    # BUILD UNIQUE NAME + TITLE PAIRS
    # ========================================================

    pairs = list(
        {
            (
                item[
                    "normalized_name"
                ],

                item[
                    "normalized_title"
                ],
            )

            for item
            in prepared_contacts
        }
    )


    normalized_names = [
        pair[0]
        for pair in pairs
    ]


    normalized_titles = [
        pair[1]
        for pair in pairs
    ]


    # ========================================================
    # GET POSSIBLE MATCHES FROM POSTGRESQL
    #
    # PostgreSQL first filters using:
    #
    # Name + Job Title
    #
    # Company matching happens afterward.
    # ========================================================

    with get_connection() as connection:

        with connection.cursor() as cursor:

            cursor.execute(
                """
                WITH requested_contacts AS (

                    SELECT *

                    FROM unnest(
                        %s::text[],
                        %s::text[]
                    )

                    AS requested(
                        normalized_name,
                        normalized_title
                    )
                )

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

                INNER JOIN requested_contacts r

                    ON
                        c.normalized_name =
                        r.normalized_name

                    AND

                        c.normalized_title =
                        r.normalized_title;
                """,

                (
                    normalized_names,
                    normalized_titles,
                ),
            )

            database_rows = (
                cursor.fetchall()
            )


    # ========================================================
    # GROUP DATABASE CANDIDATES
    # ========================================================

    candidates = {}


    for row in database_rows:

        pair = (
            row[4],
            row[5],
        )


        if pair not in candidates:

            candidates[
                pair
            ] = []


        candidates[
            pair
        ].append(
            row
        )


    # ========================================================
    # COMPARE EACH APOLLO CONTACT
    # ========================================================

    results = {}


    for contact in prepared_contacts:

        pair = (
            contact[
                "normalized_name"
            ],

            contact[
                "normalized_title"
            ],
        )


        possible_matches = (
            candidates.get(
                pair,
                [],
            )
        )


        matched_row = None


        # ====================================================
        # COMPANY CHECK
        # ====================================================

        for row in possible_matches:

            # IMPORTANT:
            #
            # Use ORIGINAL email domain.
            #
            # Example:
            #
            # wso-security.com
            #
            # NOT:
            #
            # wsosecurity

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
                break


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
            }


        # ====================================================
        # NO MATCH
        # ====================================================

        else:

            results[
                contact["key"]
            ] = {

                "exists":
                    False
            }


    return {
        "results":
            results
    }