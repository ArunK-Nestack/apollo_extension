# Apollo Contact Database Checker - System Architecture

This document provides a comprehensive, end-to-end overview of how the Apollo Contact Database Checker system operates. It covers the Chrome extension front-end, the FastAPI back-end, database layers, and the AI evaluation pipelines.

---

## 1. System Overview

The system is a deterministic, high-performance scraper and CRM cross-referencing tool. It injects a UI into `app.apollo.io`, instantly scans loaded contacts, cross-references their companies/domains against a known CRM database (via a Python backend), passes their titles and names through AI-powered guardrails, and saves the final "Required" leads directly to a MySQL database using optimized Delta Syncing.

### Core Technologies:
- **Frontend**: Vanilla JavaScript (Chrome Extension V3)
- **Backend**: Python 3 (FastAPI)
- **Database**: MySQL (AWS RDS) or PostgreSQL
- **AI/LLM**: OpenAI GPT-4o-mini
- **Connection Pool**: DBUtils

---

## 2. Frontend Architecture (Chrome Extension)

The extension lives entirely in the browser and uses a content script to monitor the DOM.

### 2.1 Scraping & Mutation Observer
- **`content.js`** injects directly into Apollo.
- A `MutationObserver` watches the DOM for changes. Instead of synchronously running heavy `querySelector` logic on every DOM change, the observer checks if non-extension elements mutated and instantly defers to a `200ms` debounced scanner (`scheduleScan`). This prevents browser UI freezing.
- When `scanApollo()` fires, it extracts rows, parsing the `Apollo ID`, `Name`, `Title`, `Company`, `Domain`, and `LinkedIn URL`.

### 2.2 Network Delta Syncing
- All identified "Required" leads are cached in `chrome.storage.local`. 
- **Delta Syncing**: The extension maintains a Set of `syncedLeadKeys`. As new rows are scanned and verified, the extension *only* uploads the newly discovered leads to the backend (`replace_all: false`), ensuring network payloads stay extremely small (O(1) instead of O(N^2)).
- On page refreshes, the extension loads the local cache and re-hydrates `syncedLeadKeys` to preserve the delta-sync state.

### 2.3 Storage and Background Worker
- **Unlimited Storage**: The `manifest.json` specifies `"unlimitedStorage"`, allowing the extension to handle massive batches without crashing due to Chrome's default 5MB limit.
- **Heartbeat**: Chrome automatically kills Service Workers (`background.js`) after 30 seconds of idle time. To prevent dropped API requests when you resume scraping, a `chrome.alarms` heartbeat pings the background script every 20 seconds, keeping it fully awake.

---

## 3. Backend Architecture (FastAPI)

The backend acts as the source of truth, managing CRM lookups, database persistence, and AI validation.

### 3.1 Connection Pooling
- The `get_connection()` factory utilizes **`DBUtils.PooledDB`** for MySQL. 
- Instead of opening and closing database TCP connections on every API request (which adds ~50-100ms of latency), the backend maintains a permanent pool of up to 20 open connections. This allows for instant query execution.

### 3.2 CRM Cross-Referencing (`/match-apollo`)
1. **Domain Extraction**: The backend extracts the canonical root domain (e.g., `stripe.com`) from the provided Apollo website link or company name.
2. **CRM Lookup**: It queries the `emails` table (the master CRM list).
3. **Outcome**: 
   - If the domain matches a CRM record, the company is marked as **Existing** and ignored.
   - If the domain is net-new, the contact is passed to the AI Engine for role validation.

### 3.3 Database Syncing (`/sync-saved-leads`)
- Receives the Delta Sync payload from the frontend.
- Uses `INSERT ... ON DUPLICATE KEY UPDATE` to effortlessly merge new contacts into the `apollo_saved_leads` table.
- Can process a `replace_all: true` payload to fully overwrite a batch if the AI Guardrails prune the local list.

---

## 4. AI Evaluation Engine

To prevent scraping low-level employees, interns, or mismatched personas, the system employs a two-tier Job Title Guardrail.

### 4.1 Fast Regex Pruning
- Before hitting the LLM, the backend runs a strict regex check against `HIGH_PRIORITY_SUBSTRINGS` (e.g., "Director", "Head") and `EXCLUDED_ENTRY_SUBSTRINGS` (e.g., "Intern", "Student").
- This instantly categorizes obvious titles with 0ms latency and 0 API cost.

### 4.2 LLM Batching (`/evaluate-pending-batch`)
- If a title is ambiguous, the frontend adds it to a pending queue.
- Once the queue reaches 50 titles (or a page navigation occurs), it sends the batch to the backend.
- The backend chunks the titles and sends a single prompt to **GPT-4o-mini**, asking it to evaluate all titles simultaneously as a JSON array.
- This batching strategy reduces OpenAI API latency and costs by >90% compared to evaluating titles one by one.

### 4.3 Indian Name Guardrail
- Similar to the title guardrail, Indian names are flagged and passed to the LLM to filter out off-shore generic technical roles while retaining decision-makers.

---

## 5. Database Schema

### `apollo_saved_leads`
The core persistence table for the finalized scraping list.
- `batch`: The active batch string (e.g., "batch_1").
- `apollo_id`, `company_domain`: Form a `UNIQUE KEY` alongside `batch` to prevent cross-page duplicates.
- **Indexes**: Includes `idx_company_domain` for fast reverse-lookups.

### `emails`
The CRM master table used for deduplication.
- Scanned against the extracted canonical domain from Apollo.

### `detected_companies`
An internal cache table.
- Maps obscure Apollo company names to actual website domains using LLM fallback if Apollo fails to provide a link. This prevents repeatedly asking the LLM to guess the domain of the same company.

---

## 6. Development & Deployment

- **Dependencies**: Listed in `requirements.txt` (FastAPI, PyMySQL, DBUtils, OpenAI).
- **Run Server**: `uvicorn backend.api:app --reload --port 8000`
- **Install Extension**: Load the `extensions` folder unpacked in `chrome://extensions/`.
