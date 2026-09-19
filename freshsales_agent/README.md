# Freshsales CRM Continuous Upsert & Tagging Agent

Automated watcher agent to process verified **Good email files** from a designated Input Folder, filter out **33 country-code TLD domains**, perform **non-overwrite upserts with auto-tagging** in Freshsales CRM, and record comprehensive run metrics into a local **SQLite database**.

---

## Default Configured Paths

- **Input Folder:** `G:\My Drive\crm automation\freshsales input`
- **Reports Folder:** `G:\My Drive\crm automation\freshsales reports`
- **SQLite Database:** `freshsales_agent/data/crm_automation.db`

---

## Automated Workflow

When the agent is running (`python -m app.main watch` or `python -m app.main` inside `freshsales_agent`):

1. **Continuous Queue Detection:** Monitors the input folder for new CSV files.
2. **In-File Deduplication:** Drops duplicate emails within the file.
3. **Layer 1: 33 TLD Domain Filter:** Excludes emails ending with blocked country TLDs (`.co.uk`, `.de`, `.fr`, `.ca`, `.ee`, etc.) and logs `tld_filtered_count`.
4. **CRM Batch Lookup & Non-Overwrite Resolution:**
   - **Brand New Contacts:** Created with all mapped fields + fallback owner + run tag (`CREATED`).
   - **Existing Contacts:** Fills empty fields only (never overwrites existing CRM data) + applies run tag (refreshes `updated_at` date in Freshsales) (`UPDATED`).
5. **Bulk Upsert & 60s Polling:** Dispatches records in batches of 100 to the Freshsales Bulk API and polls until finished.
6. **SQLite Database Logging:** Records row counts, creator/updater stats, and success percentages into `file_run_metrics` table.
7. **Per-File CSV Audit Report:** Saves detailed row-by-row audit in `freshsales reports/<file_name>_crm_audit.csv`.
8. **Auto-Deletion:** Deletes the processed source file from the input folder and immediately checks for the next file.

---

## 33 Blocked Country TLDs (DELETE_TLDS)

```
.ee, .fi, .fr, .hr, .hu, .ie, .lv, .no, .pt, .se, .uk,
.at, .be, .ca, .ch, .de, .lu, .bg, .cy, .cz, .dk, .es,
.gr, .is, .it, .li, .lt, .mt, .nl, .pl, .ro, .si, .sk
```

---

## Setup & Configuration

1. Copy `.env.example` to `.env`:
   ```powershell
   copy freshsales_agent\.env.example freshsales_agent\.env
   ```
2. Edit `.env` with your Freshsales API Key and domain:
   ```env
   FRESHSALES_API_KEY=your_real_api_key
   FRESHSALES_DOMAIN=https://your-company.myfreshworks.com/crm/sales
   DEFAULT_OWNER_ID=your_sales_rep_owner_id
   ```

---

## How to Run

Navigate into `freshsales_agent` or run directly from the workspace:

### 1. Check Configuration & Paths
```powershell
python freshsales_agent/app/main.py config
```

### 2. View SQLite Run Metrics & Historical Stats Table
```powershell
python freshsales_agent/app/main.py stats
```

### 3. Start Continuous Folder Automation (Recommended)
```powershell
python freshsales_agent/app/main.py watch
```
*(or `python freshsales_agent/app/main.py`)*

### 4. Run Single Batch (Once)
```powershell
python freshsales_agent/app/main.py process --once
```

### 5. Run Unit Test Suite
```powershell
python freshsales_agent/tests_agent.py
```
