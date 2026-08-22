# Single Contact Processing Pipeline: End-to-End Execution Breakdown

This document provides an exhaustive, step-by-step technical breakdown of the complete lifecycle of a **single contact row**—from the moment it appears in the Apollo.io web interface to final database matching, domain deduplication (targeting net-new domains), optional AI title qualification, UI badge injection, and local session persistence.

---

## Architecture Overview

```
[ Apollo.io DOM Row ]
         │
         ▼
[ 1. Client-Side DOM Extraction & Settlement ]
         │ (content.js)
         ▼
[ 2. Extension Service Worker Relaying ]
         │ (background.js: POST /match-apollo)
         ▼
[ 3. Backend Ingestion & Text Normalization ]
         │ (backend/api.py: normalize_text)
         ▼
[ 4. PostgreSQL CRM Person Lookup ]
         │ (SQL: WHERE normalized_name = ANY(%s))
         ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 5. Duplicate Detection & Person Matching Engine             │
 │                                                             │
 │   [5.1] Deterministic 5-Method Matcher                      │
 │         ├── Method 1: Exact Variant Intersection            │
 │         ├── Method 2: Multi-Token Domain Brand Containment  │
 │         ├── Method 3: Distinctive First Token (Len >= 5)    │
 │         ├── Method 4: Compact Prefix / Containment (Len >=6)│
 │         └── Method 5: Acronym / Initialism Matching         │
 │                                                             │
 │   [5.2] 2-Tier AI Domain Resolver (Fallback if DB Candidates)│
 │         ├── Tier A: OpenAI Model Knowledge (gpt-5.6-luna)   │
 │         ├── Tier B: Verified Live Web Search (Fallback)     │
 │         └── Tier C: Domain Equivalence Verification         │
 └─────────────────────────────────────────────────────────────┘
         │
         ├───► [ PERSON MATCH FOUND ] ──► Status: EXISTING (✓ Existing Badge)
         │                                (Not Saved, Not Exported)
         │
         └───► [ NO CRM PERSON MATCH ] ──► Proceed to Guardrails
                                                   │
 ┌─────────────────────────────────────────────────┴───────────┐
 │ 6. Guardrail Qualification Pipeline                         │
 │                                                             │
 │   [6.1] Guardrail 1: Target New Domains (DB Domain Check)   │
 │         ├── Checks if ANY contact in CRM has this domain    │
 │         └── Domain in DB ──► Status: IGNORED                │
 │                              (⊘ Existing Domain Badge)      │
 │                                                             │
 │   [6.2] Guardrail 2: AI Job Title Hierarchy (OPTIONAL)      │
 │         ├── [Toggle OFF]: Auto-qualifies as REQUIRED ★      │
 │         └── [Toggle ON]: Evaluates 7-Tier AI Hierarchy      │
 │               ├── Tiers 1–6 + Tech/Prod/Ops ──► REQUIRED ★  │
 │               └── Tier 7 IC / Non-Tech      ──► IGNORED ⊘   │
 └─────────────────────────────────────────────────────────────┘
         │
         ▼
[ 7. UI Badge Rendering, Tooltip Binding & Local Storage ]
         │ (content.js: chrome.storage.local & DOM Injection)
         ▼
[ Ready for 1-Click CSV Export (Strictly Required Leads Only) ]
```

---

## Detailed Step-by-Step Lifecycle for a Single Contact

### Step 1: DOM Ingestion, Settlement & Extraction (`content.js`)

When an Apollo.io search grid loads or updates via virtual scroll:

1. **Row Discovery**: The content script locates the contact row via anchor tags matching `a[href*="/contacts/"]` or `a[data-to*="/contacts/"]`.
2. **Virtual DOM Settlement**: Apollo frequently mounts row skeletons before hydrating text. The script implements a settle retry engine:
   - Evaluates whether the row's name, title, and company nodes contain valid text.
   - If empty, pauses execution and retries up to 6 times (150ms intervals) until text nodes hydrate.
3. **Field Extraction**:
   - `apollo_id` (`key`): Extracted from the URL identifier (e.g., `/contacts/64f8a12bc90...`).
   - `name`: Cleaned text from the primary profile anchor.
   - `job_title`: Extracted from the adjacent title cell or title container.
   - `company`: Scraped from the company anchor or parent container text (stripping employee count suffixes like `· 150 employees`).
   - `location`: Scraped by locating header columns matching `Location` / `City` (e.g., `"Sydney, Australia"`).
   - `employee_count`: Extracted from company badge metadata if present (e.g., `50`).
4. **Batch Queue Debounce**: The single contact object is enqueued into a batch buffer. After a 200ms debounce, all pending contacts on the current view are dispatched together with the active `title_guardrail_enabled` toggle setting.

---

### Step 2: Message Passing via Service Worker (`background.js`)

1. `content.js` sends a runtime message to `background.js`:
   ```json
   {
     "type": "MATCH_APOLLO",
     "title_guardrail_enabled": false,
     "contacts": [
       {
         "key": "64f8a12bc90e",
         "name": "Alex Mitchell",
         "job_title": "Head of Engineering",
         "company": "Canva Pty Ltd",
         "location": "Sydney, New South Wales, Australia",
         "employee_count": 4000,
         "region": ""
       }
     ]
   }
   ```
2. `background.js` measures network round-trip latency, initiates an HTTP `POST` request to `http://127.0.0.1:8000/match-apollo`, and routes the structured JSON response back to the active tab.

---

### Step 3: Server-Side Normalization & Database Lookup (`backend/api.py`)

#### 3.1 Text Normalization
The backend receives the contact payload and applies standard sanitization via `normalize_text()`:
- **Unicode Decomposition**: Applies `unicodedata.normalize('NFKD', ...)` to strip accents (e.g., `é` $\rightarrow$ `e`).
- **Punctuation & Noise Removal**: Replaces special characters, hyphens, and whitespace with single spaces, followed by lowercase conversion.
- **Example**:
  - `Name`: `"Alex Mitchell"` $\rightarrow$ `"alex mitchell"`
  - `Job Title`: `"Head of Engineering"` $\rightarrow$ `"head of engineering"`
  - `Company`: `"Canva Pty Ltd"` $\rightarrow$ `"canva pty ltd"`

#### 3.2 SQL Batch Candidate Retrieval
The server queries the local PostgreSQL CRM database:
```sql
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
WHERE c.normalized_name = ANY(ARRAY['alex mitchell']);
```
- **Outcome A**: **Zero rows returned** $\rightarrow$ No person with this name exists in the CRM. The contact skips person duplicate resolution and jumps immediately to **Step 6 (Guardrail Qualification: New Domain Check)**.
- **Outcome B**: **1 or more candidate rows returned** $\rightarrow$ The contact enters **Step 4 & Step 5 (Person vs. Domain Duplicate Matching)**.

---

### Step 4: Layer 1 — Deterministic 5-Method Matching

If CRM database candidate rows exist for `"alex mitchell"`, the system tests the contact's company (`"Canva Pty Ltd"`) against each candidate row's `email_domain` (e.g., `"canva.com"`).

It evaluates 5 deterministic methods in sequence with zero AI cost:

```
[ Input Company: "Canva Pty Ltd" ]  vs.  [ Candidate DB Domain: "canva.com" ]
                             │
 ├── Method 1: Exact Variant Intersection ──────────────► [ MATCH ]
 ├── Method 2: Multi-Token Domain Brand Containment ────► (If M1 fails)
 ├── Method 3: Distinctive First Token (len >= 5) ──────► (If M2 fails)
 ├── Method 4: Compact Prefix / Substring (len >= 6) ───► (If M3 fails)
 └── Method 5: Acronym / Initialism Matching ───────────► (If M4 fails)
```

1. **Method 1 (Exact Variant Intersection)**: Strips legal extensions (`pty ltd`, `inc`, `llc`, etc.), generates brand permutations (`canva`), and tests intersection against domain brand.
2. **Method 2 (Multi-Token Domain Brand Containment)**: Verifies if 2+ non-generic domain tokens are a subset of company tokens.
3. **Method 3 (Distinctive First Token)**: Matches primary non-generic token with length $\ge 5$ (e.g., `"Simplon"` vs. `simplon.com`).
4. **Method 4 (Compact Prefix / Substring Containment)**: Substring matching with length $\ge 6$.
5. **Method 5 (Acronym Match)**: Extracts initialisms (e.g., `"WSO"` vs. `wso.com`).

> **Decision Point**:
> - If **matched** $\rightarrow$ Contact is flagged `exists: True`, `required: False`, `ignored: False`, `match_method: "deterministic"`. Proceeds to **Step 7**.
> - If **not matched** $\rightarrow$ Proceeds to **Step 5 (AI Domain Resolution)**.

---

### Step 5: Layer 2 — 2-Tier AI Domain Resolution (Fallback)

When deterministic matching fails for a contact with CRM name candidates:
### Step 3: Guardrail Evaluation Engine
When a contact has no duplicate person in the CRM database, they are evaluated through three modular guardrails:

```
[ NO CRM PERSON MATCH ]
           │
           ▼
[ Guardrail 1: Target New Domains ] ──► (Domain in DB? -> IGNORE: Existing Domain)
           │
           ▼ (Net-New Target Domain)
[ Guardrail 3: Pure Indian Name Filter ] (Toggleable: ON/OFF)
     │
     ├──► Unambiguous Pure Indian Name? -> IGNORE: Indian Origin (⊘ Excluded)
     │
     └──► Foreign or Edge Case (Goan/Arab/Global)? -> PASS
           │
           ▼
[ Guardrail 2: AI Title Hierarchy Filter ] (Toggleable: ON/OFF)
     │
     ├──► Setting: OFF -> Auto-qualify as ★ REQUIRED LEAD
     │
     └──► Setting: ON  -> Tiers 1-6 (Relevant Function) -> ★ REQUIRED LEAD
                        -> Tier 7 / Excluded Function   -> IGNORE: Disqualified Title
```

| Guardrail | Purpose | Toggleable | Default Setting | Disqualification Action |
| :--- | :--- | :---: | :---: | :--- |
| **Guardrail 1: Target New Domains** | Ensures we only target net-new domains by checking PostgreSQL `contacts` for any previous contact at that company domain. | No (Always Active) | **ACTIVE** | `exists: false, required: false, ignored: true, guardrail_status: "domain_already_in_db"` |
| **Guardrail 3: Pure Indian Name Filter** | Demographically classifies names via `gpt-4o-mini` in batch mode. Excludes pure Indian names while preserving tricky edge cases (Goan, Arab, Global). | **Yes** | **OFF** | `exists: false, required: false, ignored: true, guardrail_status: "indian_name_disqualified"` |
| **Guardrail 2: AI Title Hierarchy** | Evaluates seniority (Tiers 1–6) and functional relevance, excluding HR, Legal, and pure Sales. | **Yes** | **OFF** | `exists: false, required: false, ignored: true, guardrail_status: "disqualified_title"` |ins with confidence scores ($\ge 0.95$ gating).
2. **Tier B: Live Web Search Fallback**: Enforces live search if model knowledge is uncertain or company is obscure.
3. **Tier C: Domain Equivalence Check**: Compares resolved domains against the candidate's database domain.

> **Decision Point**:
> - If resolved domain matches DB candidate $\rightarrow$ Flagged `exists: True`, `required: False`, `ignored: False`.
> - If no match $\rightarrow$ Contact is confirmed as **NOT in CRM** and proceeds to **Step 6 (Guardrail Qualification)**.

---

### Step 6: Layer 3 — Guardrail Qualification Pipeline

When a contact is confirmed NOT to be in the CRM as an existing person, it undergoes guardrail evaluation.

```
                    [ Non-CRM Contact ]
                             │
                             ▼
         [ Guardrail 1: Target New Domains (DB Check) ]
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
   [ Domain Exists in DB ]        [ Domain is Net-New ]
              │                             │
              ▼                             ▼
       Status: IGNORED           [ Guardrail 2: AI Title Filter ]
    (domain_already_in_db)                  │
    (⊘ Existing Domain)          ┌──────────┴──────────┐
    (Not Saved / Exported)       ▼                     ▼
                           [Toggle: OFF]         [Toggle: ON]
                                 │                     │
                                 ▼                     ▼
                          Status: REQUIRED     [ 7-Tier AI Hierarchy ]
                         (★ Required Lead)             │
                                           ┌───────────┴───────────┐
                                           ▼                       ▼
                                   [ Tier 1–6 AND ]         [ Tier 7 OR ]
                                   [ Function Relevant ]    [ Non-Tech Function ]
                                           │                       │
                                           ▼                       ▼
                                    Status: REQUIRED        Status: IGNORED
                                   (★ Required Lead)      (disqualified_title)
```

#### 6.1 Guardrail 1: Targeting New Domains (CRM Database Domain Check)
- Queries PostgreSQL (`contacts` table indexed on `normalized_domain` and `email_domain`) to check if **ANY single contact** in the CRM database already exists with that company's domain.
- **If domain already exists in DB**:
  - `exists: false`, `required: false`, `ignored: true`
  - `guardrail_status: "domain_already_in_db"`
  - `guardrail_reason: "Company domain already exists in CRM database with existing contacts."`
  - **Result**: Ignored immediately. Never marked `Required`. Never saved to local storage or exported to CSV.

#### 6.2 Guardrail 2: AI Job Title Hierarchy & Relevance (Optional Control)
If the domain is net-new (Guardrail 1 passed):
- **When `AI Title Filter: OFF` (Default)**:
  - The contact is immediately qualified as **`REQUIRED`** (`required: true, exists: false, ignored: false, guardrail_status: "qualified"`).
  - Saves OpenAI tokens and provides instant qualification of net-new domain leads.
- **When `AI Title Filter: ON`**:
  - Evaluates job title via `gpt-4o-mini`:
    - **Tiers 1–6**: Owner/Founder, C-Suite, VP, Director, Senior Manager, Manager with relevant function (Product, Engineering, AI/Data, Operations, IT) $\rightarrow$ **`REQUIRED`** (`required: true`).
    - **Tier 7 / Non-Relevant**: Individual Contributors (Intern, Analyst, Associate, Specialist) or irrelevant functions (HR, Legal) $\rightarrow$ **`IGNORED`** (`required: false, ignored: true, guardrail_status: "disqualified_title"`).

---

### Step 7: UI Rendering, Badge Injection & Local Persistence (`content.js`)

```
                  [ Backend Evaluation Result ]
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
   [ Existing ]           [ Required Lead ]         [ Ignored ]
  (exists: true)     (exists: false, req: true)   (req: false, ign: true)
        │                       │                       │
        ▼                       ▼                       ▼
  • Green Row Style       • Orange Badge          • Slate / Grey Badge
  • "✓ Existing" Badge    • "★ Required Lead"     • "⊘ Existing Domain" OR
  • CRM Email Tooltip     • Saved to Storage      • "⊘ Excluded: Title"
  • NOT in CSV Export     • Exported in CSV       • NOT in CSV Export
```

1. **State Evaluation & DOM Badge Injection**:
   - **Existing Person (`exists: true`)**:
     - Attaches CSS class `contact-checker-existing` (green highlight).
     - Injects `<span class="contact-checker-existing-badge">✓ Existing</span>`.
     - Injects hover tooltip containing existing CRM email, stored title, and match source.
   - **Target Lead (`required: true, exists: false, ignored: false`)**:
     - Injects `<span class="contact-checker-required-badge">★ Required Lead</span>`.
     - Injects hover tooltip displaying qualification details.
     - **Session Persistence**: Adds the contact record into `chrome.storage.local`.
   - **Ignored Domain (`domain_already_in_db`)**:
     - Injects `<span class="contact-checker-ignored-badge">⊘ Existing Domain</span>`.
     - Injects tooltip explaining domain already exists in CRM database with existing contacts.
   - **Disqualified Title (`disqualified_title`)**:
     - Injects `<span class="contact-checker-ignored-badge">⊘ Excluded: Title</span>`.
     - Injects tooltip with seniority tier / function exclusion rationale.

2. **Control Dock & 1-Click CSV Export**:
   - The dock `#contact-checker-controls` includes:
     - `Required on page: X | Collected total: Y`
     - **Export Required Contacts (CSV)**: Compiles all `Required` contacts across pages into an RFC-4180 CSV file.
     - **AI Title Filter: OFF / ON**: Interactive toggle to turn Guardrail 2 on/off on the fly.
     - **Clear List**: Empties the stored collection.
     - **Activity**: Opens the diagnostic telemetry drawer.

---

## Contact State Transition Summary Matrix

| Starting State | Condition | Evaluation Path | Final Outcome | UI Badge | Saved in CSV Export? |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **Apollo Row Extracted** | Person matches CRM row & Company matches Domain | Deterministic Matcher (Methods 1–5) | `exists: true`<br>`required: false`<br>`ignored: false` | `✓ Existing` *(Green)* | **NO** |
| **Apollo Row Extracted** | Person matches CRM row & Ambiguous Domain | AI Domain Resolver (Model / Web Search) | `exists: true`<br>`required: false`<br>`ignored: false` | `✓ Existing` *(Green)* | **NO** |
| **Apollo Row Extracted** | Not in CRM as person, but company domain exists in DB | Guardrail 1 (DB Domain Check) | `exists: false`<br>`required: false`<br>`ignored: true` | `⊘ Existing Domain` *(Grey)* | **NO** |
| **Apollo Row Extracted** | Net-new domain & Title Guardrail is OFF | Guardrail 1 Passed (New Domain) | `exists: false`<br>`required: true`<br>`ignored: false` | `★ Required Lead` *(Orange)* | **YES** |
| **Apollo Row Extracted** | Net-new domain & Title Guardrail is ON (Tiers 1–6) | Guardrail 2 (7-Tier AI Hierarchy) | `exists: false`<br>`required: true`<br>`ignored: false` | `★ Required Lead` *(Orange)* | **YES** |
| **Apollo Row Extracted** | Net-new domain & Title Guardrail is ON (Tier 7 IC) | Guardrail 2 (7-Tier AI Hierarchy) | `exists: false`<br>`required: false`<br>`ignored: true` | `⊘ Excluded: Title` *(Grey)* | **NO** |
