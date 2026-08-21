v1.7 DESIGN SUMMARY
===================

Browser side
------------
User opens Apollo results page.
User activates extension.
User clicks Analyze Current Page.

content.js:
    reads rendered contact rows
    extracts name/company/title/location
    extracts existing Apollo profile reference from rendered href/data-to
    sends only matching fields to localhost
    never sends Apollo ID/reference to the backend
    receives Existing/Required
    decorates rows
    accumulates Required records locally
    exports/copies Required records when user asks

Backend side
------------
One PostgreSQL query for all unique normalized names.

Per contact:
    no same-name candidate
        -> Required immediately

    same-name candidate
        -> deterministic company/domain matching

    deterministic fails
        -> temporary company-domain cache

    still unresolved
        -> LLM knowledge

    knowledge incomplete/uncertain
        -> web verification

No PostgreSQL storage of resolver results.

Temporary company cache
-----------------------
Only:
    company_official
    company_brand

Never caches:
    person_professional

Default TTL:
    6 hours

Optional .env:
    DOMAIN_CACHE_TTL_SECONDS=21600
    DOMAIN_CACHE_MAX_ENTRIES=1000
