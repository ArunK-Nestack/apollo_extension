# Apollo Extension & Lead Engine: Complete Commands & Operations Guide

A comprehensive, step-by-step operations manual detailing all executable commands, utilities, test suites, and workflows in the project.

---

## Quick Reference Navigation

1. [Starting the Backend & Database Services](#1-starting-the-backend--database-services)
2. [Interactive Lead & Batch Management (All-in-One CLI)](#2-interactive-lead--batch-management-all-in-one-cli)
3. [Exporting Clean Batches to CSV](#3-exporting-clean-batches-to-csv)
4. [Auditing & Deduplication Checking](#4-auditing--deduplication-checking)
5. [Updating the Domain Deduplication Cache](#5-updating-the-domain-deduplication-cache)
6. [Extension Build & Packaging](#6-extension-build--packaging)
7. [Automated QA & Simulation Testing](#7-automated-qa--simulation-testing)
8. [Database Cleanup & Maintenance](#8-database-cleanup--maintenance)

---

## 1. Starting the Backend & Database Services

### **A. Launching the FastAPI Backend Server**
Starts the local HTTP API server that handles real-time deduplication, title qualification, and database synchronization for the Chrome Extension.

```powershell
# Standard local development server
python -m uvicorn backend.api:app --host 0.0.0.0 --port 8000 --reload
```

* **When to run:** Before opening Apollo in your browser to scrape leads.
* **Wait Time:** Allow **~30–35 seconds** upon first startup for the domain prefix trie to load into memory (`[DomainTrie] Instantly loaded...`).
* **Health Check URL:** Visit `http://localhost:8000/health` in your browser to verify it is running.

---

### **B. Verifying Database Connectivity**
Quickly checks if your MySQL database connection parameters (`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_NAME` in `.env`) are working.

```powershell
python backend/test_db.py
```

* **When to run:** If you receive database connection errors or after changing your `.env` credentials.

---

## 2. Interactive Lead & Batch Management (All-in-One CLI)

The central command-line utility for managing batches, uploading CRM contact files, and exporting leads.

```powershell
python scripts/manage_batches.py
```

### **What this command does:**
1. **Initial Screen:** Instantly connects to MySQL and renders an overview table of all batches currently in `apollo_saved_leads`, showing lead counts, distinct domains, and date added.
2. **Interactive Menu Actions:**
   * **`[1] Delete a batch from Apollo Saved Leads`**: Prompts for batch number or name, displays records to be removed, and requires explicit confirmation (`Type 'yes'`) before deleting.
   * **`[2] Add / Upload file data to master CRM (emails table)`**: Prompts for a CSV or Excel file (`.csv`, `.xlsx`, `.xls`), automatically extracts `email`, `full_name`, and `domain`, and executes chunked `INSERT IGNORE` queries.
   * **`[3] Export a batch to CSV`**: Exports any batch with optional 1-lead-per-domain deduplication into the `exports/` folder.
   * **`[4] Refresh batch statistics`**: Re-queries the database and updates the summary table.
   * **`[5] Exit`**: Safely closes database connections and exits.

---

## 3. Exporting Clean Batches to CSV

### **Option A: Via Interactive CLI (Recommended)**
```powershell
python scripts/manage_batches.py
# Select Option [3] -> Choose batch number -> Select Mode (Unique by domain or All)
```

### **Option B: One-Shot Standalone Exporter**
Exports a batch directly without the full menu:

```powershell
python scripts/export_batch.py
```
* **Prompt:** Enter the batch name (e.g. `vijay_nestacktech_com-sep-new`).
* **Output:** Saves a CSV deduplicated by `company_domain` (prioritizing leads with Job Titles and LinkedIn URLs) as `{batch_name}_unique_leads.csv`.

---

## 4. Auditing & Deduplication Checking

### **A. Comprehensive 4-Layer Batch Audit**
Runs all 4 deduplication layers (*Exact Match, Person-Name LCS Anchor, Prefix Trie, DNS MX*) against a specific batch:

```powershell
python scratch/audit_vijay_new_changes.py
```
* **What it outputs:**
  * Total leads in batch
  * Layer 1 blocked (Exact CRM duplicates)
  * Layer 2 blocked (Person name exists at parent CRM company)
  * Layer 3 blocked (Dealership branch stem matches known CRM domain)
  * True net-new unique domains count and percentage
  * Qualified decision-maker titles breakdown

---

### **B. Quick Batch Comparison Script**
Compares a batch against other batches in `apollo_saved_leads` and exact matches in the `emails` table:

```powershell
python scripts/audit_batch_comparison.py
```

---

## 5. Updating the Domain Deduplication Cache

If you recently imported thousands of new CRM contacts into MySQL and want to refresh the local domain slug cache file (`data/domain_slugs_cache.txt`):

```powershell
python scripts/cache_trie_domains.py
```

* **When to run:** After running bulk Freshsales imports or adding historical client lists into the `emails` table.
* **Duration:** Takes ~15–20 seconds to fetch all unique domains from MySQL and rewrite `data/domain_slugs_cache.txt`.

---

## 6. Extension Build & Packaging

### **A. Rebuilding the Extension Assets**
Copies and prepares the source code from `extensions/` into the standalone `dist/` directory:

```powershell
npm run build
# Or directly:
node scripts/build.js
```
* **Output:** Generates `dist/content.js`, `dist/background.js`, and `dist/manifest.json`.
* **When to run:** Whenever you modify any JavaScript or CSS file in `extensions/` before loading it into Chrome.

---

### **B. Creating the Production ZIP Archive**
Packages the built extension into an installable `.zip` archive:

```powershell
npm run package
# Or directly:
node scripts/package.js
```
* **Output:** Creates `dist/apollo-extension.zip`.
* **When to run:** When sharing the extension with team members or preparing for Chrome Web Store distribution.

---

## 7. Automated QA & Simulation Testing

### **A. Run the Full Backend Automated QA Suite**
Executes 15 comprehensive automated test assertions simulating 5 QA personas (*Fuzzing, Deduplication, Guardrails, Connection Pooling, Load Stress*):

```powershell
python tests/run_all_testers.py
```
* **What it validates:**
  * Unicode and international character safety
  * SQL injection sanitization
  * 4-layer deduplication accuracy on branch dealership URLs
  * Negative controls (confirming unique leads are never falsely blocked)
  * Concurrent request handling (20 simultaneous pages / 500 contacts)
  * Thread-safety of in-memory Prefix Trie and connection pool recycling

---

### **B. Test Real 34-Lead Extension Simulation**
Simulates the Chrome extension parsing and classifying the 34 real captured leads:

```powershell
node tests/test_34_page_simulation.js
```
* **What it validates:**
  * Decision-maker title detection
  * Disqualification of procurement/entry roles
  * Delta sync formatting and local storage buffering

---

### **C. Test Extension Runtime Lifecycle**
Validates content script startup, toggle states, and memory cleanup:

```powershell
node tests/test_extension_runtime.js
```

---

## 8. Database Cleanup & Maintenance

### **Automated Deduplication & Cleanup Script**
Removes duplicate leads across batches in `apollo_saved_leads` and purges any leads that already exist in the master `emails` CRM table:

```powershell
python scripts/cleanup_saved_leads.py
```
* **Action:**
  1. Deletes any record in `apollo_saved_leads` whose domain exists in `emails`.
  2. For duplicate leads across batches with the same domain, keeps only the most senior lead and removes the rest.

---

## 9. Apollo Direct Search & CRM Qualification CLI (Hands-Free Pipeline)

Direct, automated search and qualification from Python using Apollo's official REST API—completely bypassing the Chrome Extension and manual browser clicking.

```powershell
python scripts/apollo_search_direct.py
```

### **Features & Workflow:**
1. **Engine Banner:** Displays connection status across all 5 lookup vaults (AWS RDS 7.28M CRM, 64K Titles, 28K Indian Surnames Trie, LLM Auto-Cache, and MySQL Staging).
2. **Account Selection:** Prompts to select today's active account from `config/apollo_accounts.json` with masked keys and health checks.
3. **Saved Search Inspection:** Lists your saved web searches, displays active filters (locations, titles, employee bands), and probes total data volume in the base pool.
4. **Keyword Refinement:** Prompts for search bar keywords (e.g. "Fintech", "Cybersecurity") to narrow search pools and stay safely under Apollo's 100-page limit.
5. **10-Lead Preview Table:** Verifies all 8 extension columns before starting.
6. **Streaming Qualification:** Paces at 1.5s per page (40 req/min, zero 429 errors) and applies all 4 layers in real-time (RDS CRM Domain Check, Indian Demographic Origin Filter, 64K Titles, and 1/Company Deduplication).
7. **Continuous Loop:** Automatically prompts for the next keyword upon completion.
8. **Apollo-Compliant Export:** Automatically exports 75-column standard CSVs to `dist/exports/`.

---

## Operational Workflow Cheat Sheet

```
Daily Operations Flow:
─────────────────────────────────────────────────────────────────────────────
Option A: Hands-Free Backend Pipeline (Recommended)
   └─► python scripts/apollo_search_direct.py
   
Option B: Chrome Extension Browser Flow
   1. Start Backend Server:
      └─► python -m uvicorn backend.api:app --port 8000
   2. Scrape Leads on Apollo:
      └─► Open Chrome -> app.apollo.io -> Extension auto-evaluates rows
      
Common Management & Auditing:
   └─► python scripts/manage_batches.py
   └─► python tests/run_all_testers.py
─────────────────────────────────────────────────────────────────────────────
```
