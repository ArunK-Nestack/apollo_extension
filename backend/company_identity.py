"""Conservative company identity decisions for Apollo People Search results."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable, Protocol

EXISTING_CONFIRMED = "EXISTING_CONFIRMED"
NEW_CONFIRMED = "NEW_CONFIRMED"
AMBIGUOUS = "AMBIGUOUS"
UNRESOLVED = "UNRESOLVED"
NO_COMPANY = "NO_COMPANY"

LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "company", "co", "plc", "lp", "llp", "gmbh", "ag", "sa", "pte", "pvt",
}
GENERIC_TOKENS = {
    "the", "group", "global", "international", "services", "solutions", "company",
    "technology", "technologies", "systems", "consulting", "holdings", "partners",
}
VERIFIED_DOMAIN_SOURCES = {"clearbit_brand", "dns_candidate", "verified_cache", "mysql_verified"}


def conservative_company_key(value: str) -> str:
    """Normalize spelling and legal suffixes without removing business identity words."""
    text = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def conservative_variants(value: str) -> set[str]:
    raw = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    key = conservative_company_key(value)
    variants = {item for item in (raw, key) if item}
    if key:
        variants.update({f"{key} {suffix}" for suffix in ("inc", "llc", "ltd", "corp")})
        variants.update({f"{key}, {suffix}" for suffix in ("inc", "llc", "ltd", "corp")})
        variants.update({f"{key} {suffix}." for suffix in ("inc", "llc", "ltd", "corp")})
        variants.update({f"{key}, {suffix}." for suffix in ("inc", "llc", "ltd", "corp")})
    return variants


def clean_domain(value: str) -> str:
    domain = str(value or "").strip().casefold()
    if "@" in domain:
        domain = domain.rsplit("@", 1)[-1]
    domain = re.sub(r"^https?://", "", domain)
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.split("/", 1)[0].split(":", 1)[0].strip(".")


@dataclass
class CompanyDecision:
    company_name: str
    normalized_name: str
    status: str
    confidence: float
    canonical_name: str = ""
    canonical_domain: str = ""
    source: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CompanyRepository(Protocol):
    def exact_matches(self, names: Iterable[str]) -> dict[str, dict[str, Any]]: ...
    def existing_domains(self, domains: Iterable[str]) -> set[str]: ...
    def candidate_matches(self, name: str, limit: int = 5) -> list[dict[str, Any]]: ...


class JevDecider(Protocol):
    def decide(self, cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]: ...


def _resolved_for(name: str, resolved: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return (
        resolved.get(name)
        or resolved.get(name.casefold())
        or resolved.get(conservative_company_key(name))
        or {}
    )


def classify_companies(
    company_names: Iterable[str],
    repository: CompanyRepository,
    resolver: Callable[[list[str]], dict[str, dict[str, Any]]],
    jev_decider: JevDecider | None = None,
    company_context: dict[str, dict[str, Any]] | None = None,
    mode: str = "conservative",
) -> dict[str, CompanyDecision]:
    """Classify unique company names with selectable Jev AI review modes:
       - 'conservative': Review strictly ambiguous candidates (>=90% match threshold).
       - 'aggressive'  : Review candidates and auto-unlock confirmed distinct new entities (>=80% match, >=85% diff).
       - 'non_exact'   : Review ALL non-exact entities (>=75% match, >=80% diff).
    """
    mode = str(mode or "conservative").strip().lower().replace("-", "_")
    names = list(dict.fromkeys(str(name or "").strip() for name in company_names))
    results: dict[str, CompanyDecision] = {}
    non_empty = [name for name in names if name]
    exact = repository.exact_matches(non_empty)

    unresolved_names: list[str] = []
    for name in names:
        normalized = conservative_company_key(name)
        if not name:
            results[name] = CompanyDecision(name, normalized, NO_COMPANY, 1.0, source="missing_company")
        elif name in exact:
            match = exact[name]
            results[name] = CompanyDecision(
                name,
                normalized,
                EXISTING_CONFIRMED,
                0.98,
                canonical_name=str(match.get("canonical_name") or name),
                canonical_domain=clean_domain(match.get("domain") or ""),
                source="exact_company_name",
                evidence={"matched_name": match.get("matched_name") or match.get("canonical_name") or name},
            )
        else:
            unresolved_names.append(name)

    resolved = resolver(unresolved_names) if unresolved_names else {}
    verified_by_name: dict[str, tuple[str, dict[str, Any]]] = {}
    verified_domains: list[str] = []
    for name in unresolved_names:
        info = _resolved_for(name, resolved)
        domain = clean_domain(info.get("company_domain") or info.get("domain") or "")
        source = str(info.get("source") or "")
        verified = bool(info.get("verified")) or source in VERIFIED_DOMAIN_SOURCES
        if domain and verified and source != "deterministic_candidate":
            verified_by_name[name] = (domain, info)
            verified_domains.append(domain)

    existing_domains = repository.existing_domains(verified_domains)
    unresolved_without_domain = [name for name in unresolved_names if name not in verified_by_name]
    batch_candidate_lookup = getattr(repository, "candidate_matches_batch", None)
    if callable(batch_candidate_lookup):
        candidates_by_name = batch_candidate_lookup(unresolved_without_domain, limit=5)
    else:
        candidates_by_name = {
            name: repository.candidate_matches(name, limit=5)
            for name in unresolved_without_domain
        }

    ambiguous_cases: list[dict[str, Any]] = []
    for name in unresolved_names:
        normalized = conservative_company_key(name)
        if name in verified_by_name:
            domain, info = verified_by_name[name]
            if domain in existing_domains:
                status, confidence, source = EXISTING_CONFIRMED, 0.99, "exact_domain"
            else:
                status = NEW_CONFIRMED
                confidence = 0.93 if info.get("source") == "clearbit_brand" else 0.90
                source = str(info.get("source") or "verified_domain")
            results[name] = CompanyDecision(
                name,
                normalized,
                status,
                confidence,
                canonical_name=name,
                canonical_domain=domain,
                source=source,
                evidence={"domain_verified": True, "resolver_source": info.get("source")},
            )
            continue

        candidates = candidates_by_name.get(name, [])
        if candidates:
            results[name] = CompanyDecision(
                name,
                normalized,
                AMBIGUOUS,
                0.60,
                source="name_candidates",
                candidates=candidates,
                evidence={"reason": "Similar names require a constrained decision"},
            )
            ambiguous_cases.append({
                "company_name": name,
                "context": (company_context or {}).get(name, {}),
                "candidates": candidates,
            })
        else:
            info = _resolved_for(name, resolved)
            results[name] = CompanyDecision(
                name,
                normalized,
                UNRESOLVED,
                0.0,
                source=str(info.get("source") or "insufficient_evidence"),
                evidence={"reason": "No exact company match or verified domain"},
            )
            if mode == "non_exact":
                # In non-exact mode, submit all unresolved entities to Jev for verification
                ambiguous_cases.append({
                    "company_name": name,
                    "context": (company_context or {}).get(name, {}),
                    "candidates": [],
                })

    if jev_decider and ambiguous_cases:
        try:
            answers = jev_decider.decide(ambiguous_cases)
        except Exception as exc:
            answers = {}
            for case in ambiguous_cases:
                results[case["company_name"]].evidence["jev_error"] = str(exc)[:300]
        for case in ambiguous_cases:
            name = case["company_name"]
            answer = answers.get(name) or {}
            choice = str(answer.get("choice") or "insufficient_evidence")
            confidence = float(answer.get("confidence") or 0.0)
            probability = float(answer.get("probability") or confidence)
            decision_confidence = min(confidence, probability)

            # Determine match and distinct entity thresholds based on selected mode
            if mode == "conservative":
                match_threshold = 0.90
                diff_threshold = 1.01  # Conservative never auto-unlocks purely on "different_company"
            elif mode == "aggressive":
                match_threshold = 0.80
                diff_threshold = 0.85  # Aggressive unlocks distinct entities with >=85% confidence
            else:  # non_exact
                match_threshold = 0.75
                diff_threshold = 0.80  # Non-exact unlocks distinct entities with >=80% confidence

            if choice.startswith("candidate_") and decision_confidence >= match_threshold:
                try:
                    selected = case["candidates"][int(choice.split("_", 1)[1])]
                except (ValueError, IndexError):
                    continue
                results[name] = CompanyDecision(
                    name,
                    conservative_company_key(name),
                    EXISTING_CONFIRMED,
                    decision_confidence,
                    canonical_name=str(selected.get("canonical_name") or ""),
                    canonical_domain=clean_domain(selected.get("domain") or ""),
                    source="jev_candidate",
                    candidates=case["candidates"],
                    evidence={"jev_choice": choice, "jev_mode": mode},
                )
            elif (choice in ("different_company", "is_independent_company")) and decision_confidence >= diff_threshold:
                ctx = case.get("context") or {}
                canonical_domain = clean_domain(ctx.get("company_domain") or ctx.get("domain") or "")
                results[name] = CompanyDecision(
                    name,
                    conservative_company_key(name),
                    NEW_CONFIRMED,
                    decision_confidence,
                    canonical_name=name,
                    canonical_domain=canonical_domain,
                    source="jev_different_company",
                    candidates=case.get("candidates", []),
                    evidence={"jev_choice": choice, "jev_mode": mode},
                )
            else:
                results[name].evidence["jev_choice"] = choice
                results[name].evidence["jev_confidence"] = confidence
                results[name].evidence["jev_probability"] = probability
                results[name].evidence["jev_mode"] = mode

    return results


def partition_people_by_company_decision(
    people: Iterable[dict[str, Any]],
    decisions: dict[str, CompanyDecision],
) -> tuple[list[tuple[dict[str, Any], CompanyDecision]], dict[str, int]]:
    """Keep only confirmed-new people and count every held/rejected lead explicitly."""
    accepted: list[tuple[dict[str, Any], CompanyDecision]] = []
    counts = {
        EXISTING_CONFIRMED: 0,
        AMBIGUOUS: 0,
        UNRESOLVED: 0,
        NO_COMPANY: 0,
    }
    for person in people:
        organization = person.get("organization") or {}
        company_name = str(person.get("organization_name") or organization.get("name") or "").strip()
        decision = decisions[company_name]
        if decision.status == NEW_CONFIRMED:
            accepted.append((person, decision))
        else:
            counts[decision.status] = counts.get(decision.status, 0) + 1
    return accepted, counts


class MySQLCompanyRepository:
    """Read existing company evidence and persist auditable identity decisions."""

    def __init__(self, connection: Any, active_batch: str = "") -> None:
        self.connection = connection
        self.active_batch = active_batch

    def ensure_schema(self) -> None:
        with self.connection.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS company_identity (
                    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                    canonical_name VARCHAR(255) NOT NULL,
                    normalized_name VARCHAR(255) NOT NULL UNIQUE,
                    canonical_domain VARCHAR(255) NULL,
                    industry VARCHAR(255) NULL,
                    resolution_status VARCHAR(32) NOT NULL,
                    confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
                    resolution_source VARCHAR(64) NULL,
                    source_batch VARCHAR(255) NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_company_identity_domain (canonical_domain)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS company_alias (
                    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                    company_identity_id BIGINT UNSIGNED NOT NULL,
                    alias_name VARCHAR(255) NOT NULL,
                    normalized_alias VARCHAR(255) NOT NULL UNIQUE,
                    source VARCHAR(64) NULL,
                    confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_company_alias_identity (company_identity_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS company_match_decision (
                    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                    batch VARCHAR(255) NULL,
                    input_company VARCHAR(255) NOT NULL,
                    normalized_input VARCHAR(255) NOT NULL,
                    decision VARCHAR(32) NOT NULL,
                    matched_company VARCHAR(255) NULL,
                    canonical_domain VARCHAR(255) NULL,
                    confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
                    decision_source VARCHAR(64) NULL,
                    evidence_json JSON NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_company_decision_batch (batch),
                    INDEX idx_company_decision_name (normalized_input)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS jev_decision_cache (
                    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                    company_name VARCHAR(255) NOT NULL,
                    normalized_name VARCHAR(255) NOT NULL,
                    choice VARCHAR(64) NOT NULL,
                    confidence DECIMAL(5,4) NOT NULL DEFAULT 0,
                    probability DECIMAL(5,4) NOT NULL DEFAULT 0,
                    jev_mode VARCHAR(32) NOT NULL DEFAULT 'conservative',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_jev_cache_name_mode (normalized_name, jev_mode)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'company_identity'
                  AND COLUMN_NAME = 'source_batch'
            """)
            if not int((cur.fetchone() or [0])[0] or 0):
                cur.execute("ALTER TABLE company_identity ADD COLUMN source_batch VARCHAR(255) NULL")
        self.connection.commit()

    @staticmethod
    def _chunks(values: list[str], size: int = 500) -> Iterable[list[str]]:
        for index in range(0, len(values), size):
            yield values[index:index + size]

    def exact_matches(self, names: Iterable[str]) -> dict[str, dict[str, Any]]:
        names = list(names)
        variant_to_names: dict[str, set[str]] = {}
        for name in names:
            for variant in conservative_variants(name):
                variant_to_names.setdefault(variant, set()).add(name)
        matches: dict[str, dict[str, Any]] = {}
        variants = list(variant_to_names)
        if not variants:
            return matches

        def absorb(rows: Iterable[tuple[Any, Any]]) -> None:
            for raw_name, raw_domain in rows:
                db_name = str(raw_name or "").strip()
                db_key = conservative_company_key(db_name)
                for input_name in names:
                    if db_key and db_key == conservative_company_key(input_name):
                        matches[input_name] = {
                            "canonical_name": db_name,
                            "matched_name": db_name,
                            "domain": clean_domain(raw_domain or ""),
                        }

        with self.connection.cursor() as cur:
            for chunk in self._chunks(variants):
                placeholders = ",".join(["%s"] * len(chunk))
                params = tuple(chunk)
                try:
                    cur.execute(
                        f"SELECT canonical_name, canonical_domain FROM company_identity "
                        f"WHERE normalized_name IN ({placeholders}) "
                        f"AND (source_batch IS NULL OR source_batch != %s)",
                        params + (self.active_batch,),
                    )
                    absorb(cur.fetchall())
                except Exception:
                    pass
                try:
                    cur.execute(
                        f"SELECT ca.normalized_alias, ci.canonical_name, ci.canonical_domain "
                        f"FROM company_alias ca JOIN company_identity ci ON ci.id = ca.company_identity_id "
                        f"WHERE ca.normalized_alias IN ({placeholders}) "
                        f"AND (ci.source_batch IS NULL OR ci.source_batch != %s)",
                        params + (self.active_batch,),
                    )
                    alias_rows = cur.fetchall()
                    for alias_key, canonical_name, domain in alias_rows:
                        canonical = str(canonical_name or "").strip()
                        for input_name in variant_to_names.get(str(alias_key or "").casefold(), set()):
                            matches[input_name] = {
                                "canonical_name": canonical,
                                "matched_name": str(alias_key or ""),
                                "domain": clean_domain(domain or ""),
                            }
                except Exception:
                    pass
                for table, name_col, domain_col in (
                    ("emails", "company_name", "domain"),
                    ("apollo_saved_leads", "company", "company_domain"),
                    ("enrich_saved_leads", "company", "company_domain"),
                ):
                    try:
                        batch_clause = ""
                        query_params = params
                        if table in {"apollo_saved_leads", "enrich_saved_leads"} and self.active_batch:
                            batch_clause = " AND `batch` != %s"
                            query_params = params + (self.active_batch,)
                        cur.execute(
                            f"SELECT DISTINCT `{name_col}`, `{domain_col}` FROM `{table}` "
                            f"WHERE `{name_col}` IN ({placeholders}){batch_clause}",
                            query_params,
                        )
                        absorb(cur.fetchall())
                    except Exception:
                        pass
        return matches

    def existing_domains(self, domains: Iterable[str]) -> set[str]:
        domains = list(dict.fromkeys(clean_domain(domain) for domain in domains if clean_domain(domain)))
        found: set[str] = set()
        if not domains:
            return found
        with self.connection.cursor() as cur:
            for chunk in self._chunks(domains, 1000):
                placeholders = ",".join(["%s"] * len(chunk))
                for table, column in (
                    ("emails", "domain"),
                    ("apollo_saved_leads", "company_domain"),
                    ("enrich_saved_leads", "company_domain"),
                    ("company_identity", "canonical_domain"),
                ):
                    try:
                        batch_clause = ""
                        query_params = tuple(chunk)
                        if table in {"apollo_saved_leads", "enrich_saved_leads"} and self.active_batch:
                            batch_clause = " AND `batch` != %s"
                            query_params += (self.active_batch,)
                        elif table == "company_identity" and self.active_batch:
                            batch_clause = " AND (source_batch IS NULL OR source_batch != %s)"
                            query_params += (self.active_batch,)
                        cur.execute(
                            f"SELECT DISTINCT `{column}` FROM `{table}` "
                            f"WHERE `{column}` IN ({placeholders}){batch_clause}",
                            query_params,
                        )
                        found.update(clean_domain(row[0]) for row in cur.fetchall() if row and row[0])
                    except Exception:
                        pass
        return found

    def candidate_matches(self, name: str, limit: int = 5) -> list[dict[str, Any]]:
        return self.candidate_matches_batch([name], limit=limit).get(name, [])

    def candidate_matches_batch(
        self,
        names: Iterable[str],
        limit: int = 5,
    ) -> dict[str, list[dict[str, Any]]]:
        """Find fuzzy candidates with indexed prefix reads instead of full-table scans."""
        names = list(dict.fromkeys(str(name or "").strip() for name in names if str(name or "").strip()))
        name_keys = {name: conservative_company_key(name) for name in names}
        prefixes = []
        for key in name_keys.values():
            distinctive = [
                token for token in key.split()
                if len(token) >= 4 and token not in GENERIC_TOKENS
            ]
            if distinctive:
                prefixes.append(distinctive[0])
        prefixes = sorted(set(prefixes))
        if not prefixes:
            return {name: [] for name in names}

        rows: list[tuple[str, str]] = []
        with self.connection.cursor() as cur:
            for prefix_chunk in self._chunks(prefixes, 25):
                predicates = " OR ".join(["normalized_name LIKE %s"] * len(prefix_chunk))
                params: tuple[Any, ...] = tuple(f"{prefix}%" for prefix in prefix_chunk)
                batch_clause = ""
                if self.active_batch:
                    batch_clause = " AND (source_batch IS NULL OR source_batch != %s)"
                    params += (self.active_batch,)
                try:
                    cur.execute(
                        "SELECT canonical_name, canonical_domain FROM company_identity "
                        f"WHERE ({predicates}){batch_clause} LIMIT 250",
                        params,
                    )
                    rows.extend((str(row[0] or ""), clean_domain(row[1] or "")) for row in cur.fetchall())
                except Exception:
                    pass

                alias_predicates = " OR ".join(["ca.normalized_alias LIKE %s"] * len(prefix_chunk))
                alias_params: tuple[Any, ...] = tuple(f"{prefix}%" for prefix in prefix_chunk)
                alias_batch_clause = ""
                if self.active_batch:
                    alias_batch_clause = " AND (ci.source_batch IS NULL OR ci.source_batch != %s)"
                    alias_params += (self.active_batch,)
                try:
                    cur.execute(
                        "SELECT ci.canonical_name, ci.canonical_domain "
                        "FROM company_alias ca JOIN company_identity ci ON ci.id = ca.company_identity_id "
                        f"WHERE ({alias_predicates}){alias_batch_clause} LIMIT 250",
                        alias_params,
                    )
                    rows.extend((str(row[0] or ""), clean_domain(row[1] or "")) for row in cur.fetchall())
                except Exception:
                    pass

                # emails.company_name has a B-tree index. Keep the column bare and
                # the pattern prefix-anchored so MySQL can use an index range scan.
                email_predicates = " OR ".join(["company_name LIKE %s"] * len(prefix_chunk))
                try:
                    cur.execute(
                        "SELECT DISTINCT company_name, domain FROM emails "
                        f"WHERE ({email_predicates}) LIMIT 250",
                        tuple(f"{prefix}%" for prefix in prefix_chunk),
                    )
                    rows.extend((str(row[0] or ""), clean_domain(row[1] or "")) for row in cur.fetchall())
                except Exception:
                    pass

        results: dict[str, list[dict[str, Any]]] = {}
        for name, key in name_keys.items():
            candidates: dict[tuple[str, str], dict[str, Any]] = {}
            for candidate_name, domain in rows:
                candidate_key = conservative_company_key(candidate_name)
                score = SequenceMatcher(None, key, candidate_key).ratio()
                if score < 0.55 or candidate_key == key:
                    continue
                identity = (candidate_key, domain)
                candidates[identity] = {
                    "canonical_name": candidate_name,
                    "domain": domain,
                    "name_similarity": round(score, 4),
                }
            results[name] = sorted(
                candidates.values(),
                key=lambda item: item["name_similarity"],
                reverse=True,
            )[:limit]
        return results

    def record_decisions(self, decisions: Iterable[CompanyDecision]) -> None:
        rows = []
        identities = []
        aliases = []
        for decision in decisions:
            rows.append((
                self.active_batch,
                decision.company_name,
                decision.normalized_name,
                decision.status,
                decision.canonical_name,
                decision.canonical_domain,
                decision.confidence,
                decision.source,
                json.dumps(decision.evidence, ensure_ascii=False),
            ))
            if decision.status in {EXISTING_CONFIRMED, NEW_CONFIRMED} and decision.canonical_name:
                canonical_key = conservative_company_key(decision.canonical_name)
                identities.append((
                    decision.canonical_name,
                    canonical_key,
                    decision.canonical_domain or None,
                    decision.status,
                    decision.confidence,
                    decision.source,
                    self.active_batch if decision.status == NEW_CONFIRMED else None,
                ))
                if decision.normalized_name and decision.normalized_name != canonical_key:
                    aliases.append((
                        decision.company_name,
                        decision.normalized_name,
                        decision.source,
                        decision.confidence,
                        canonical_key,
                    ))
        with self.connection.cursor() as cur:
            if rows:
                cur.executemany("""
                    INSERT INTO company_match_decision
                        (batch, input_company, normalized_input, decision, matched_company,
                         canonical_domain, confidence, decision_source, evidence_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, rows)
            if identities:
                cur.executemany("""
                    INSERT INTO company_identity
                        (canonical_name, normalized_name, canonical_domain, resolution_status,
                        confidence, resolution_source, source_batch)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        canonical_domain = COALESCE(VALUES(canonical_domain), canonical_domain),
                        resolution_status = VALUES(resolution_status),
                        confidence = GREATEST(confidence, VALUES(confidence)),
                        resolution_source = VALUES(resolution_source)
                """, identities)
            if aliases:
                cur.executemany("""
                    INSERT INTO company_alias
                        (company_identity_id, alias_name, normalized_alias, source, confidence)
                    SELECT id, %s, %s, %s, %s
                    FROM company_identity
                    WHERE normalized_name = %s
                    ON DUPLICATE KEY UPDATE
                        company_identity_id = VALUES(company_identity_id),
                        source = VALUES(source),
                        confidence = GREATEST(company_alias.confidence, VALUES(confidence))
                """, aliases)
        self.connection.commit()


class TypeSafeJevCompanyDecider:
    """Constrained System One requests with persistent MySQL/memory caching & rich context enrichment."""

    _memory_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def __init__(
        self,
        api_key: str,
        timeout: float = 30.0,
        max_cases: int = 250,
        connection: Any = None,
        mode: str = "conservative",
    ) -> None:
        self.api_key = str(api_key or "").strip()
        if not self.api_key:
            raise ValueError("TYPESAFE_API_KEY is required when Jev review is enabled")
        self.timeout = timeout
        self.max_cases = max_cases
        self.used_cases = 0
        self.connection = connection
        self.mode = str(mode or "conservative").strip().lower().replace("-", "_")

    def _fetch_from_cache(self, cases: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        cached: dict[str, dict[str, Any]] = {}
        missing: list[dict[str, Any]] = []

        # 1. Check in-memory session cache first
        for case in cases:
            name = case["company_name"]
            key = conservative_company_key(name)
            if (key, self.mode) in self._memory_cache:
                cached[name] = self._memory_cache[(key, self.mode)]
            else:
                missing.append(case)

        if not missing or not self.connection:
            return cached, missing

        # 2. Check persistent MySQL jev_decision_cache
        try:
            name_map = {conservative_company_key(c["company_name"]): c["company_name"] for c in missing}
            keys = list(name_map.keys())
            if keys:
                placeholders = ",".join(["%s"] * len(keys))
                with self.connection.cursor() as cur:
                    cur.execute(
                        f"SELECT normalized_name, choice, confidence, probability "
                        f"FROM jev_decision_cache "
                        f"WHERE normalized_name IN ({placeholders}) AND jev_mode = %s",
                        tuple(keys) + (self.mode,),
                    )
                    for norm_name, choice, conf, prob in cur.fetchall():
                        orig_name = name_map.get(norm_name)
                        if orig_name:
                            ans = {
                                "choice": str(choice),
                                "confidence": float(conf or 0.0),
                                "probability": float(prob or conf or 0.0),
                                "cached": True,
                            }
                            cached[orig_name] = ans
                            self._memory_cache[(norm_name, self.mode)] = ans
        except Exception:
            pass

        uncached = [c for c in missing if c["company_name"] not in cached]
        return cached, uncached

    def _save_to_cache(self, cases: list[dict[str, Any]], results: dict[str, dict[str, Any]]) -> None:
        db_rows = []
        for case in cases:
            name = case["company_name"]
            ans = results.get(name)
            if not ans:
                continue
            norm_name = conservative_company_key(name)
            self._memory_cache[(norm_name, self.mode)] = ans
            db_rows.append((
                name,
                norm_name,
                ans.get("choice", "insufficient_evidence"),
                float(ans.get("confidence", 0.0)),
                float(ans.get("probability", 0.0)),
                self.mode,
            ))

        if db_rows and self.connection:
            try:
                with self.connection.cursor() as cur:
                    cur.executemany("""
                        INSERT INTO jev_decision_cache
                            (company_name, normalized_name, choice, confidence, probability, jev_mode)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            choice = VALUES(choice),
                            confidence = VALUES(confidence),
                            probability = VALUES(probability),
                            updated_at = CURRENT_TIMESTAMP
                    """, db_rows)
                self.connection.commit()
            except Exception:
                pass

    def decide(self, cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        import requests

        # Check local cache first
        cached_results, uncached_cases = self._fetch_from_cache(cases)
        if not uncached_cases:
            return cached_results

        remaining = max(0, self.max_cases - self.used_cases)
        selected = uncached_cases[:remaining]
        if not selected:
            return cached_results

        state = {"ambiguous_companies": selected, "rule": "Use only supplied candidates and evidence."}
        questions: dict[str, dict[str, Any]] = {}
        for index, case in enumerate(selected):
            candidates = case.get("candidates") or []
            context = case.get("context") or {}

            # Construct rich context snippet (Location, Industry, Domain, Headcount)
            ctx_items = []
            if context.get("location"):
                ctx_items.append(f"Location: {context['location']}")
            if context.get("industry"):
                ctx_items.append(f"Industry: {context['industry']}")
            if context.get("company_domain"):
                ctx_items.append(f"Domain: {context['company_domain']}")
            if context.get("num_employees"):
                ctx_items.append(f"Headcount: {context['num_employees']}")
            ctx_str = f" [Context: {'; '.join(ctx_items)}]" if ctx_items else ""

            if candidates:
                criteria = {
                    f"candidate_{candidate_index}": (
                        f"{c.get('canonical_name') or c.get('name')}"
                        + (f" [domain: {c.get('domain')}]" if c.get('domain') else "")
                        + (f" [location: {c.get('location')}]" if c.get('location') else "")
                    )
                    for candidate_index, c in enumerate(candidates)
                }
                criteria["different_company"] = "The incoming company is distinct from every candidate."
                criteria["insufficient_evidence"] = "The evidence is insufficient for a safe decision."
                question_text = f"Which candidate is the same operating company as '{case['company_name']}'{ctx_str}?"
            else:
                criteria = {
                    "is_independent_company": "The incoming entity is a distinct operating business (not in CRM).",
                    "insufficient_evidence": "The evidence is insufficient to verify this company.",
                }
                question_text = f"Is '{case['company_name']}'{ctx_str} a distinct operating business?"

            questions[f"company_{index}"] = {
                "type": "choice",
                "instructions": {
                    "company_name": case["company_name"],
                    "question": question_text,
                },
                "criteria": criteria,
            }

        response = requests.post(
            "https://api.typesafe.ai/v1/systemone",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            data=json.dumps({"state": state, "model": "jev-latest", "questions": questions}),
            timeout=self.timeout,
        )
        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(f"TypeSafe returned HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
        answers = payload.get("answers") or {}
        self.used_cases += len(selected)

        newly_decided: dict[str, dict[str, Any]] = {}
        for index, case in enumerate(selected):
            answer = answers.get(f"company_{index}") or {}
            choice = str(answer.get("choice") or "insufficient_evidence")
            newly_decided[case["company_name"]] = {
                "choice": choice,
                "confidence": float(answer.get("confidence") or 0.0),
                "probability": float((answer.get("probabilities") or {}).get(choice, 0.0)),
            }

        # Save new verdicts to cache
        self._save_to_cache(selected, newly_decided)

        # Merge with cached results
        return {**cached_results, **newly_decided}
