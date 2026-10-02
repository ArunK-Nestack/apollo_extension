// ============================================================
// Enrich.so Standalone Lead Finder Matcher & Auto-Paginator
// ============================================================

(function () {
  "use strict";

  if (window.__ENRICH_CHECKER_LOADED__) {
    console.log("[EnrichMatcher] Already initialized on this tab.");
    return;
  }
  window.__ENRICH_CHECKER_LOADED__ = true;

  // State
  let isRunning = false;
  let isProcessing = false;
  let isAutoPaginating = false;
  let pagesProcessed = 0;
  let targetCount = 0;
  let ignoredCount = 0;
  let existingCount = 0;
  let savedToDbCount = 0;
  let uniqueCompaniesCount = 0;
  let collectMode = "companies"; // "companies" or "contacts"
  let currentBatchName = "enrich_" + new Date().toISOString().slice(0, 10).replace(/-/g, "_");

  // Load saved settings from local storage if present
  try {
    if (chrome?.storage?.local) {
      chrome.storage.local.get(["enrich_batch_name", "enrich_collect_mode"], (res) => {
        if (res?.enrich_batch_name) {
          currentBatchName = res.enrich_batch_name;
          if (hudElements?.batchInput) {
            hudElements.batchInput.value = currentBatchName;
          }
        }
        if (res?.enrich_collect_mode) {
          collectMode = res.enrich_collect_mode;
          if (typeof updateModeUI === "function") {
            updateModeUI();
          }
        }
        setHUDStatus(`Ready (Mode: ${collectMode === "companies" ? "Companies" : "Contacts"} | Batch: ${currentBatchName})`);
      });
    }
  } catch (_) {}

  // Helper: Sleep with random jitter
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // Extract clean domain from any URL or string
  function cleanDomain(raw) {
    if (!raw) return "";
    let d = raw.trim().toLowerCase();
    d = d.replace(/^https?:\/\//, "").replace(/^www\d*\./, "");
    d = d.split("/")[0].split(":")[0].split("?")[0].trim();
    return d;
  }

  // Remove any stray badges from sidebar, navigation, filters, or footers
  function removeStrayBadges() {
    const strays = document.querySelectorAll(
      'aside .enrich-status-badge, ' +
      'nav .enrich-status-badge, ' +
      'header .enrich-status-badge, ' +
      'footer .enrich-status-badge, ' +
      '[class*="filter"] .enrich-status-badge, ' +
      '[class*="sidebar"] .enrich-status-badge, ' +
      '[class*="Showing"] .enrich-status-badge'
    );
    strays.forEach((el) => el.remove());
  }

  // Cached DOM elements inside Floating HUD
  let hudElements = null;

  // Floating Control HUD (Initialized ONCE, properties updated dynamically)
  function initHUD() {
    let hud = document.getElementById("enrich-matcher-hud");
    if (!hud) {
      hud = document.createElement("div");
      hud.id = "enrich-matcher-hud";
      hud.className = "enrich-hud-container";
      document.body.appendChild(hud);

      hud.innerHTML = `
        <div class="enrich-hud-header">
          <span class="enrich-hud-title">⚡ Enrich Matcher</span>
          <span id="enrich-hud-badge" class="enrich-hud-badge status-idle">OFF</span>
        </div>
        <div class="enrich-hud-mode-container" style="margin-bottom: 10px; background: #27272a; padding: 6px 8px; border-radius: 8px; border: 1px solid #3f3f46;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
            <span style="font-size: 11px; font-weight: 700; color: #d4d4d8;">Save To:</span>
            <span id="enrich-mode-badge" style="font-size: 10px; font-weight: 700; color: #c084fc; text-transform: uppercase;">Unique Companies</span>
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px;">
            <button id="enrich-mode-companies-btn" type="button" style="padding: 5px 6px; font-size: 11px; font-weight: 700; border-radius: 5px; border: 1px solid #9333ea; background: #581c87; color: #fff; cursor: pointer; text-align: center; transition: all 0.15s ease;">🏢 Companies</button>
            <button id="enrich-mode-contacts-btn" type="button" style="padding: 5px 6px; font-size: 11px; font-weight: 600; border-radius: 5px; border: 1px solid #3f3f46; background: #18181b; color: #a1a1aa; cursor: pointer; text-align: center; transition: all 0.15s ease;">👤 Contacts</button>
          </div>
        </div>
        <div class="enrich-hud-batch-container">
          <label class="batch-label" for="enrich-hud-batch-input">Batch Name (Saved to DB):</label>
          <input id="enrich-hud-batch-input" type="text" class="enrich-batch-input" value="${currentBatchName}" placeholder="Enter Batch Name" />
        </div>
        <div class="enrich-hud-stats">
          <div class="stat-item"><span class="stat-label">Unique Companies:</span> <span id="enrich-stat-companies" class="stat-val text-purple" style="color: #a855f7; font-weight: 700;">0</span></div>
          <div class="stat-item"><span class="stat-label">Target (New):</span> <span id="enrich-stat-target" class="stat-val text-green">0</span></div>
          <div class="stat-item"><span class="stat-label">Saved to DB:</span> <span id="enrich-stat-saved" class="stat-val text-emerald">0</span></div>
          <div class="stat-item"><span class="stat-label">Existing in CRM:</span> <span id="enrich-stat-existing" class="stat-val text-blue">0</span></div>
          <div class="stat-item"><span class="stat-label">Ignored / Skipped:</span> <span id="enrich-stat-ignored" class="stat-val text-gray">0</span></div>
        </div>
        <div class="enrich-hud-controls">
          <button id="enrich-hud-toggle-btn" class="enrich-hud-btn btn-start" type="button">Turn ON</button>
          <button id="enrich-hud-autopage-btn" class="enrich-hud-btn btn-start-paging" type="button" disabled>Auto-Paginate</button>
        </div>
        <div id="enrich-hud-status-msg" class="enrich-hud-status">Ready (Batch: ${currentBatchName})</div>
      `;

      hudElements = {
        badge: hud.querySelector("#enrich-hud-badge"),
        modeBadge: hud.querySelector("#enrich-mode-badge"),
        modeCompaniesBtn: hud.querySelector("#enrich-mode-companies-btn"),
        modeContactsBtn: hud.querySelector("#enrich-mode-contacts-btn"),
        batchInput: hud.querySelector("#enrich-hud-batch-input"),
        statCompanies: hud.querySelector("#enrich-stat-companies"),
        statTarget: hud.querySelector("#enrich-stat-target"),
        statSaved: hud.querySelector("#enrich-stat-saved"),
        statIgnored: hud.querySelector("#enrich-stat-ignored"),
        statExisting: hud.querySelector("#enrich-stat-existing"),
        toggleBtn: hud.querySelector("#enrich-hud-toggle-btn"),
        autopageBtn: hud.querySelector("#enrich-hud-autopage-btn"),
        statusMsg: hud.querySelector("#enrich-hud-status-msg"),
      };

      // Bind Mode Buttons
      hudElements.modeCompaniesBtn.addEventListener("click", (e) => {
        e.preventDefault();
        setCollectMode("companies");
      });
      hudElements.modeContactsBtn.addEventListener("click", (e) => {
        e.preventDefault();
        setCollectMode("contacts");
      });

      // Bind Turn ON / OFF button
      hudElements.toggleBtn.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        isRunning = !isRunning;
        if (!isRunning) isAutoPaginating = false;

        console.log(`[EnrichMatcher] Toggle button clicked! isRunning: ${isRunning}`);
        updateHUDUI();

        if (isRunning) {
          removeStrayBadges();
          setHUDStatus("Active - scanning visible leads...");
          await processVisibleRows();
        } else {
          setHUDStatus("Stopped. Matcher is OFF.");
        }
      });

      // Bind Auto-Paginate button
      hudElements.autopageBtn.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!isRunning) return;

        isAutoPaginating = !isAutoPaginating;
        console.log(`[EnrichMatcher] Auto-page button clicked! isAutoPaginating: ${isAutoPaginating}`);
        updateHUDUI();

        if (isAutoPaginating) {
          setHUDStatus("Auto-pagination initiated...");
          runAutoPaginationLoop();
        } else {
          setHUDStatus("Auto-pagination paused.");
        }
      });

      // Bind Batch input with debounce/instant capture
      hudElements.batchInput.addEventListener("input", (e) => {
        const val = e.target.value.trim();
        if (val) {
          currentBatchName = val;
          try {
            chrome.storage?.local?.set({ enrich_batch_name: val });
          } catch (_) {}
        }
      });
    }

    updateModeUI();
    updateHUDUI();
  }

  function setCollectMode(mode) {
    collectMode = mode;
    try {
      if (chrome?.storage?.local) {
        chrome.storage.local.set({ enrich_collect_mode: mode });
      }
    } catch (_) {}
    updateModeUI();
    setHUDStatus(`Mode: ${mode === "companies" ? "🏢 Unique Companies (Table: enrich_companies)" : "👤 Lead Contacts (Table: enrich_saved_leads)"}`);
  }

  function updateModeUI() {
    if (!hudElements?.modeBadge) return;
    if (collectMode === "companies") {
      hudElements.modeBadge.textContent = "Unique Companies";
      hudElements.modeBadge.style.color = "#c084fc";
      if (hudElements.modeCompaniesBtn) {
        hudElements.modeCompaniesBtn.style.background = "#581c87";
        hudElements.modeCompaniesBtn.style.borderColor = "#9333ea";
        hudElements.modeCompaniesBtn.style.color = "#ffffff";
        hudElements.modeCompaniesBtn.style.fontWeight = "700";
      }
      if (hudElements.modeContactsBtn) {
        hudElements.modeContactsBtn.style.background = "#18181b";
        hudElements.modeContactsBtn.style.borderColor = "#3f3f46";
        hudElements.modeContactsBtn.style.color = "#a1a1aa";
        hudElements.modeContactsBtn.style.fontWeight = "600";
      }
    } else {
      hudElements.modeBadge.textContent = "Lead Contacts";
      hudElements.modeBadge.style.color = "#34d399";
      if (hudElements.modeCompaniesBtn) {
        hudElements.modeCompaniesBtn.style.background = "#18181b";
        hudElements.modeCompaniesBtn.style.borderColor = "#3f3f46";
        hudElements.modeCompaniesBtn.style.color = "#a1a1aa";
        hudElements.modeCompaniesBtn.style.fontWeight = "600";
      }
      if (hudElements.modeContactsBtn) {
        hudElements.modeContactsBtn.style.background = "#065f46";
        hudElements.modeContactsBtn.style.borderColor = "#10b981";
        hudElements.modeContactsBtn.style.color = "#ffffff";
        hudElements.modeContactsBtn.style.fontWeight = "700";
      }
    }
  }

  // Update HUD UI non-destructively
  function updateHUDUI() {
    if (!hudElements) return;

    hudElements.toggleBtn.textContent = isRunning ? "Turn OFF" : "Turn ON";
    hudElements.toggleBtn.className = `enrich-hud-btn ${isRunning ? "btn-stop" : "btn-start"}`;

    hudElements.autopageBtn.disabled = !isRunning;
    hudElements.autopageBtn.textContent = isAutoPaginating ? "Stop Auto-Page" : "Auto-Paginate";
    hudElements.autopageBtn.className = `enrich-hud-btn ${isAutoPaginating ? "btn-stop-paging" : "btn-start-paging"}`;

    hudElements.badge.textContent = isRunning ? (isAutoPaginating ? "AUTO-PAGING" : "ACTIVE") : "OFF";
    hudElements.badge.className = `enrich-hud-badge ${isRunning ? "status-active" : "status-idle"}`;

    if (hudElements.statCompanies) hudElements.statCompanies.textContent = uniqueCompaniesCount;
    hudElements.statTarget.textContent = targetCount;
    hudElements.statSaved.textContent = savedToDbCount;
    hudElements.statIgnored.textContent = ignoredCount;
    hudElements.statExisting.textContent = existingCount;
  }

  function setHUDStatus(msg, isError = false) {
    if (hudElements?.statusMsg) {
      hudElements.statusMsg.textContent = msg;
      hudElements.statusMsg.style.color = isError ? "#f87171" : "#71717a";
    }
    console.log(`[EnrichMatcher] Status: ${msg}`);
  }

  // Precise Lead Row Scraper:
  // Strictly targets actual table rows inside the main results table.
  // Filters out sidebars, filters, footers, and employee-count elements.
  function scrapeLeadRows() {
    // Clean up any old stray badges first
    removeStrayBadges();

    // 1. Find all checkboxes that belong to lead table rows (exclude sidebar/header/footer)
    const allCheckboxes = Array.from(
      document.querySelectorAll('input[type="checkbox"], button[role="checkbox"], [role="checkbox"]')
    );

    const leadRows = [];
    const seenRowElements = new Set();

    allCheckboxes.forEach((cb) => {
      // Must NOT be inside sidebar, filter panel, or table header
      if (cb.closest('aside, nav, header, [class*="sidebar"], [class*="filter"], [class*="header"], thead')) {
        return;
      }

      // Find the enclosing row element (avoid matching ancestor grid container)
      const row = cb.closest('tr, div[role="row"], div[class*="border-b"]') || cb.parentElement?.parentElement?.parentElement;
      if (!row || seenRowElements.has(row)) return;

      // Ensure row is in the main body and not in the table header
      if (row.querySelector("th") || row.closest("thead")) return;

      seenRowElements.add(row);
      leadRows.push({ row, checkbox: cb });
    });

    const leads = [];

    leadRows.forEach(({ row, checkbox }, idx) => {
      // 1. Find Job Title inside this row (exclude employee counts e.g. "106 employees")
      let title = "";
      const titleCandidates = row.querySelectorAll(
        'p.text-2xs.text-ink-500, p[class*="text-ink-500"], [class*="text-2xs"]'
      );

      let titleEl = null;
      for (const cand of titleCandidates) {
        const text = cand.getAttribute("title") || cand.textContent.trim();
        // Skip employee counts or metadata
        if (/\b\d+[\s,-]*employees\b/i.test(text) || /\bemployees\b/i.test(text) || /\bmatches\b/i.test(text)) {
          continue;
        }
        if (text.length >= 2) {
          title = text;
          titleEl = cand;
          break;
        }
      }

      // 2. Find Person Name in the same cell or near the job title
      let nameEl = null;
      let name = "";

      if (titleEl && titleEl.parentElement) {
        nameEl = titleEl.parentElement.querySelector('p.text-sm.font-medium.text-ink, p[class*="font-medium text-ink"], [class*="font-medium"][class*="text-ink"]');
      }

      if (!nameEl) {
        // Find first font-medium element in row that is NOT the company name
        const fontMediums = row.querySelectorAll('p.text-sm.font-medium.text-ink, p[class*="font-medium text-ink"], [class*="font-medium"][class*="text-ink"]');
        if (fontMediums.length > 0) {
          nameEl = fontMediums[0];
        }
      }

      if (nameEl) {
        name = nameEl.textContent.trim();
      }

      if (!name || name.length < 2 || name.toLowerCase() === "name" || name.toLowerCase() === "person") {
        return;
      }

      // 3. Find Company Name & Website Domain from Company column
      let company = "";
      let domain = "";
      let websiteLink = "";

      // Find company links in row (excluding social media links and enrich)
      const links = row.querySelectorAll('a[href^="http"]');
      for (const a of links) {
        const href = a.href || "";
        const host = cleanDomain(href);
        if (
          host &&
          !host.includes("enrich.so") &&
          !host.includes("linkedin.com") &&
          !host.includes("twitter.com") &&
          !host.includes("x.com") &&
          !host.includes("google.com")
        ) {
          domain = host;
          websiteLink = href;
          company = a.textContent.trim();
          break;
        }
      }

      // Fallback: Check text matching domain regex in the row
      if (!domain) {
        const pTags = row.querySelectorAll("p, span, div, a");
        for (const p of pTags) {
          const text = p.textContent.trim();
          if (/\b[a-zA-Z0-9-]+\.(com|io|ai|net|org|co|tech|app|de|uk|ca|fr|in|global|cloud)\b/i.test(text)) {
            const m = text.match(/\b[a-zA-Z0-9-]+\.[a-z]{2,}\b/i);
            if (m && !m[0].includes("enrich.so") && !m[0].includes("linkedin.com")) {
              domain = cleanDomain(m[0]);
              if (!company && p.tagName.toLowerCase() === "a") {
                company = p.textContent.trim();
              }
              break;
            }
          }
        }
      }

      // 4. Location
      let location = "";
      const locEl = row.querySelector('svg.lucide-map-pin, [class*="map-pin"]')?.parentElement;
      if (locEl) {
        location = locEl.textContent.trim();
      }

      const sig = `${name}:::${title}:::${company}:::${domain}`;
      const key = `enrich_${name.toLowerCase().replace(/[^a-z0-9]/g, "")}_${domain || "nodom"}_${idx}`;

      leads.push({
        sig,
        key,
        name,
        job_title: title,
        company,
        domain,
        location,
        website_link: websiteLink,
        rowElement: row,
        nameElement: nameEl,
        checkbox
      });
    });

    return leads;
  }

  // Scrape all unique company names and domains visible on the page
  function scrapeVisibleCompanies() {
    const companies = [];
    const seenDomains = new Set();
    const rows = document.querySelectorAll('tr, div[role="row"], div[class*="border-b"]');

    rows.forEach((row) => {
      if (row.querySelector("th") || row.closest("thead, aside, nav, header")) return;

      let company = "";
      let domain = "";
      let websiteLink = "";

      const links = row.querySelectorAll('a[href^="http"]');
      for (const a of links) {
        const href = a.href || "";
        const host = cleanDomain(href);
        if (
          host &&
          !host.includes("enrich.so") &&
          !host.includes("linkedin.com") &&
          !host.includes("twitter.com") &&
          !host.includes("x.com") &&
          !host.includes("google.com")
        ) {
          domain = host;
          websiteLink = href;
          company = a.textContent.trim();
          break;
        }
      }

      if (!domain) {
        const pTags = row.querySelectorAll("p, span, div, a");
        for (const p of pTags) {
          const text = p.textContent.trim();
          if (/\b[a-zA-Z0-9-]+\.(com|io|ai|net|org|co|tech|app|de|uk|ca|fr|in|global|cloud)\b/i.test(text)) {
            const m = text.match(/\b[a-zA-Z0-9-]+\.[a-z]{2,}\b/i);
            if (m && !m[0].includes("enrich.so") && !m[0].includes("linkedin.com")) {
              domain = cleanDomain(m[0]);
              if (!company && p.tagName.toLowerCase() === "a") {
                company = p.textContent.trim();
              }
              break;
            }
          }
        }
      }

      if (domain && !seenDomains.has(domain)) {
        seenDomains.add(domain);
        companies.push({ company, domain, website_link: websiteLink });
      }
    });

    return companies;
  }

  // Inject or update status pill next to person name in Person column ONLY
  function injectStatusBadge(nameEl, result) {
    if (!nameEl || !nameEl.parentElement) return;

    // Remove any existing badge in this parent container to guarantee clean single badge
    const existing = nameEl.parentElement.querySelectorAll(".enrich-status-badge");
    existing.forEach((b) => b.remove());

    const badge = document.createElement("span");
    badge.className = "enrich-status-badge";

    if (result.required) {
      if (collectMode === "companies" || result.status === "new_company_saved") {
        badge.className = "enrich-status-badge badge-company-saved";
        badge.textContent = "🏢 NEW COMPANY";
        badge.title = result.reason || "Net-new company domain saved to enrich_companies table";
      } else {
        badge.className = "enrich-status-badge badge-target";
        badge.textContent = "TARGET (NEW)";
        badge.title = result.reason || "Net-new company domain & qualified decision maker";
      }
    } else if (result.exists || result.status === "existing_in_db") {
      badge.className = "enrich-status-badge badge-existing";
      badge.textContent = collectMode === "companies" ? "IN CRM (SKIPPED)" : "EXISTING IN CRM";
      badge.title = result.reason || "Company domain or contact already exists in database";
    } else if (result.status === "disqualified_title") {
      badge.className = "enrich-status-badge badge-title-skip";
      badge.textContent = "TITLE SKIPPED";
      badge.title = result.reason || "Job title rejected by guardrails";
    } else if (result.status === "indian_name_disqualified") {
      badge.className = "enrich-status-badge badge-ignored";
      badge.textContent = "NAME SKIPPED";
      badge.title = result.reason || "Demographic name filter";
    } else if (result.status === "company_limit_reached") {
      badge.className = "enrich-status-badge badge-ignored";
      badge.textContent = "1/COMPANY LIMIT";
      badge.title = result.reason || "Max 1 contact per company domain allowed";
    } else if (result.status === "no_domain" || result.status === "no_domain_source") {
      badge.className = "enrich-status-badge badge-ignored";
      badge.textContent = "NO DOMAIN";
      badge.title = result.reason || "No company domain found in lead card";
    } else if (result.status === "db_error") {
      badge.className = "enrich-status-badge badge-title-skip";
      badge.textContent = "DB CONNECTING";
      badge.title = result.reason || "Database connecting...";
    } else {
      badge.className = "enrich-status-badge badge-neutral";
      badge.textContent = "SKIPPED";
      badge.title = result.reason || "";
    }

    nameEl.parentElement.appendChild(badge);
  }

  let activeProcessingPromise = null;

  // Poll for lead rows to appear on screen (handles page transitions and network delay)
  async function waitForLeads(timeoutMs = 10000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (!isRunning) return [];
      const leads = scrapeLeadRows();
      if (leads.length > 0) return leads;
      await sleep(350);
    }
    return scrapeLeadRows();
  }

  // Main Lead Processing for Visible Table with Strict Mutex and Evaluation Cache
  async function processVisibleRows(waitForRows = false) {
    if (!isRunning) return { success: false, reason: "not_running" };

    // If an analysis cycle is already running, await it so caller never bypasses analysis!
    if (activeProcessingPromise) {
      console.log("[EnrichMatcher] In-flight processing already active, awaiting completion...");
      return await activeProcessingPromise;
    }

    activeProcessingPromise = (async () => {
      isProcessing = true;
      try {
        return await _doProcessVisibleRows(waitForRows);
      } finally {
        activeProcessingPromise = null;
        isProcessing = false;
      }
    })();

    return await activeProcessingPromise;
  }

  async function _doProcessVisibleRows(waitForRows = false) {
    try {
      // Sync batch name
      if (hudElements?.batchInput && hudElements.batchInput.value.trim()) {
        currentBatchName = hudElements.batchInput.value.trim();
      }

      let allLeads = scrapeLeadRows();
      if (!allLeads.length && waitForRows) {
        setHUDStatus("Waiting for page leads to load...");
        allLeads = await waitForLeads(10000);
      }

      if (!allLeads.length) {
        setHUDStatus("Active - 0 leads detected. Waiting for table rows to load...");
        return { success: false, reason: "no_leads", count: 0 };
      }

      // Strict filter: only send leads whose rows have NOT yet been evaluated for this specific lead signature
      const unexaminedLeads = allLeads.filter((l) => {
        const hasBadge = l.nameElement?.parentElement?.querySelector(".enrich-status-badge");
        return !hasBadge || l.rowElement.dataset.enrichEvaluatedSig !== l.sig;
      });

      if (unexaminedLeads.length === 0) {
        console.log("[EnrichMatcher] All visible leads on page already evaluated.");
        return { success: true, count: allLeads.length, unexamined: 0, newTargets: 0 };
      }

      setHUDStatus(`Analyzing ${unexaminedLeads.length} visible leads against database...`);

      const payload = unexaminedLeads.map((l) => ({
        key: l.key,
        name: l.name,
        job_title: l.job_title,
        company: l.company,
        domain: l.domain,
        location: l.location,
        website_link: l.website_link
      }));

      const visibleCompanies = scrapeVisibleCompanies();

      const response = await new Promise((resolve) => {
        try {
          chrome.runtime.sendMessage(
            {
              type: "MATCH_ENRICH",
              contacts: payload,
              companies: visibleCompanies,
              batch: currentBatchName,
              collect_mode: collectMode,
              auto_save: true
            },
            (res) => {
              if (chrome.runtime.lastError) {
                const err = chrome.runtime.lastError.message || "";
                if (err.includes("Extension context invalidated")) {
                  setHUDStatus("Extension updated: Please refresh (F5) this tab.", true);
                } else {
                  setHUDStatus(`Communication error: ${err}`, true);
                }
                resolve({ success: false, error: err });
              } else {
                resolve(res);
              }
            }
          );
        } catch (ex) {
          const msg = String(ex);
          if (msg.includes("Extension context invalidated")) {
            setHUDStatus("Extension updated: Please refresh (F5) this tab.", true);
          } else {
            setHUDStatus(`Error: ${ex.message}`, true);
          }
          resolve({ success: false, error: ex.message });
        }
      });

      if (!response?.success || !response.data?.results) {
        setHUDStatus(`Backend error: ${response?.error || "Check backend on port 8000"}`, true);
        return { success: false, error: response?.error || "Backend error" };
      }

      const results = response.data.results;
      const newlySaved = response.data.saved_to_db_count || 0;
      savedToDbCount += newlySaved;
      if (response.data.batch_total_companies !== undefined) {
        uniqueCompaniesCount = response.data.batch_total_companies;
      } else if (response.data.saved_companies_count) {
        uniqueCompaniesCount += response.data.saved_companies_count;
      }
      let newTargetsOnPage = 0;

      unexaminedLeads.forEach((l) => {
        // Tag row with the specific lead signature evaluated
        l.rowElement.dataset.enrichEvaluatedSig = l.sig;

        const res = results[l.key];
        if (!res) return;

        injectStatusBadge(l.nameElement, res);

        if (res.required) {
          targetCount++;
          newTargetsOnPage++;
          // Auto-select row checkbox ONCE for Target leads ONLY in contacts mode!
          // In companies mode, we do NOT click checkboxes so we never spend Enrich reveal credits on people
          if (collectMode === "contacts" && l.checkbox && l.rowElement.dataset.enrichCheckboxDoneSig !== l.sig) {
            l.rowElement.dataset.enrichCheckboxDoneSig = l.sig;
            const isChecked = l.checkbox.checked || l.checkbox.getAttribute("aria-checked") === "true";
            if (!isChecked) {
              l.checkbox.click();
              l.checkbox.dispatchEvent(new Event("change", { bubbles: true }));
            }
          }
        } else if (res.exists || res.status === "existing_in_db") {
          existingCount++;
        } else {
          ignoredCount++;
        }
      });

      updateHUDUI();
      if (collectMode === "companies") {
        setHUDStatus(`Checked ${unexaminedLeads.length} leads: ${newTargetsOnPage} Target Co(s) | 🏢 ${uniqueCompaniesCount} unique in '${currentBatchName}'`);
      } else {
        setHUDStatus(`Checked ${unexaminedLeads.length} leads: ${newTargetsOnPage} Target(s) | 🏢 ${uniqueCompaniesCount} unique companies in '${currentBatchName}'`);
      }
      return { success: true, count: allLeads.length, unexamined: unexaminedLeads.length, newTargets: newTargetsOnPage };
    } catch (err) {
      setHUDStatus(`Processing error: ${err.message}`, true);
      return { success: false, error: err.message };
    }
  }

  // Check if an element is visible in the viewport
  function isVisible(el) {
    if (!el) return false;
    try {
      const style = window.getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
        return false;
      }
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    } catch (_) {
      return false;
    }
  }

  // Find Next Page Button in DOM:
  // Strictly targets the bottom table pagination controls using Enrich's exact Phosphor CaretRight SVG.
  function findNextPageButton() {
    // Strategy 0: Exact SVG Path Match for Phosphor CaretRight (Enrich's exact pagination chevron)
    // <path d="M181.66,133.66l-80,80a8,8,0,0,1-11.32-11.32L164.69,128,90.34,53.66a8,8,0,0,1,11.32-11.32l80,80A8,8,0,0,1,181.66,133.66Z">
    const exactPathMatches = Array.from(
      document.querySelectorAll('path[d*="181.66"], path[d*="M181.66,133.66"]')
    ).filter((p) => {
      const el = p.closest('button, a, div[role="button"]') || p.parentElement;
      if (!el || !isVisible(el)) return false;
      const rect = el.getBoundingClientRect();
      // Must NOT be in the left filter sidebar (left < 260)
      if (rect.left < 260) return false;
      // Must NOT be in the top navigation header (top < 120)
      if (rect.top < 120) return false;
      // Must NOT be inside sidebar or filter panel
      if (el.closest('aside, header, [class*="sidebar"], [class*="filter"], [class*="Filter"], nav[aria-label="Sidebar"]')) {
        return false;
      }
      return true;
    });

    if (exactPathMatches.length > 0) {
      // Pick the button located furthest down the page (highest rect.top, which is the bottom pagination footer)
      exactPathMatches.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
      const targetPath = exactPathMatches[0];
      const targetBtn = targetPath.closest('button, a, div[role="button"]') || targetPath.closest('svg')?.parentElement || targetPath.closest('svg');
      if (targetBtn && isVisible(targetBtn) && !targetBtn.hasAttribute("disabled") && !targetBtn.classList.contains("disabled")) {
        return targetBtn;
      }
    }

    // 1. Locate all clickable buttons that are outside the sidebar and header
    const candidateButtons = Array.from(
      document.querySelectorAll('button, a, div[role="button"], span[role="button"]')
    ).filter((el) => {
      if (!isVisible(el)) return false;
      const rect = el.getBoundingClientRect();
      // Must NOT be in the left filter sidebar (left < 260)
      if (rect.left < 260) return false;
      // Must NOT be in the top navigation header (top < 120)
      if (rect.top < 120) return false;
      // Must NOT be inside sidebar or filter panel
      if (el.closest('aside, header, [class*="sidebar"], [class*="filter"], [class*="Filter"], nav[aria-label="Sidebar"]')) {
        return false;
      }
      // Must NOT be a filter, search, export, or columns button
      const txt = (el.textContent || "").toLowerCase().trim();
      if (
        txt.includes("filter") ||
        txt.includes("export") ||
        txt.includes("industry") ||
        txt.includes("linkedin") ||
        txt.includes("search") ||
        txt.includes("column")
      ) {
        return false;
      }
      return true;
    });

    if (!candidateButtons.length) return null;

    // Strategy 1: Find inside the footer containing "Showing ..."
    const showingEl = Array.from(document.querySelectorAll("p, span, div")).find((el) => {
      const t = el.textContent.trim();
      return /\bshowing\s+\d+/i.test(t) && isVisible(el) && el.getBoundingClientRect().left >= 260;
    });

    if (showingEl) {
      // Find the common footer row
      let footerRow = showingEl.parentElement;
      for (let i = 0; i < 4; i++) {
        if (!footerRow) break;
        const bList = footerRow.querySelectorAll('button, a, div[role="button"]');
        if (bList.length >= 3) {
          // Found the pagination button group container
          const fBtns = Array.from(bList).filter(isVisible);

          // Look for single chevron right (>)
          for (const btn of fBtns) {
            if (btn.hasAttribute("disabled") || btn.classList.contains("disabled")) continue;
            const t = btn.textContent.trim();
            const aria = (btn.getAttribute("aria-label") || "").toLowerCase();
            if (t === ">" && t !== ">>" && t !== "»") return btn;
            if (aria === "next" || aria === "next page" || aria === "go to next page") return btn;

            const hasSvg = btn.querySelector("svg");
            const isDouble = btn.querySelector('[class*="chevrons-right"]') || t.includes(">>") || t.includes("»");
            if (hasSvg && !isDouble && t !== "<<" && t !== "<" && t !== "«") {
              return btn;
            }
          }

          // Look for next numeric sibling after active page button
          const activeBtn = fBtns.find((b) => {
            const cls = (b.getAttribute("class") || "") + " " + (b.getAttribute("aria-current") || "");
            return cls.includes("active") || cls.includes("page") || cls.includes("bg-ink") || cls.includes("bg-black") || cls.includes("bg-zinc");
          });
          if (activeBtn) {
            const currNum = parseInt(activeBtn.textContent.trim(), 10);
            if (!isNaN(currNum)) {
              const nextNumBtn = fBtns.find((b) => b.textContent.trim() === String(currNum + 1));
              if (nextNumBtn && !nextNumBtn.hasAttribute("disabled")) {
                return nextNumBtn;
              }
            }
          }

          // In standard pagination (<< < 1 2 ... 20000 > >>), penultimate is Next
          if (fBtns.length >= 4) {
            const penUlt = fBtns[fBtns.length - 2];
            if (!penUlt.hasAttribute("disabled") && !penUlt.classList.contains("disabled")) {
              return penUlt;
            }
          }
          break;
        }
        footerRow = footerRow.parentElement;
      }
    }

    // Strategy 2: Geometrical selection - the Next button in bottom area with highest top coordinate
    const validChevrons = candidateButtons.filter((btn) => {
      if (btn.hasAttribute("disabled") || btn.classList.contains("disabled")) return false;
      const text = btn.textContent.trim();
      const aria = (btn.getAttribute("aria-label") || "").toLowerCase();
      if (text === ">>" || text === "»" || text === "«" || text === "<") return false;
      if (aria === "next" || aria === "next page" || aria === "go to next page") return true;
      if (text === ">") return true;
      const svg = btn.querySelector("svg");
      const isDouble = btn.querySelector('[class*="chevrons-right"]');
      return Boolean(svg && !isDouble);
    });

    if (validChevrons.length > 0) {
      // Pick the button located furthest down the page (highest rect.top)
      validChevrons.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
      return validChevrons[0];
    }

    // Strategy 3: Next sequential page number button
    const pageNumButtons = candidateButtons.filter((b) => /^\d+$/.test(b.textContent.trim()));
    if (pageNumButtons.length > 0) {
      const activeBtn = pageNumButtons.find((b) => {
        const cls = b.getAttribute("class") || "";
        return cls.includes("bg-") || cls.includes("active") || b.getAttribute("aria-current") === "page";
      }) || pageNumButtons[0];

      const currNum = parseInt(activeBtn.textContent.trim(), 10) || 1;
      const nextBtn = pageNumButtons.find((b) => b.textContent.trim() === String(currNum + 1));
      if (nextBtn && !nextBtn.hasAttribute("disabled")) {
        return nextBtn;
      }
    }

    return null;
  }

  // Find active page number from pagination bar
  function getActivePageNumber() {
    const activeBtn = Array.from(document.querySelectorAll('button, div[role="button"], span[role="button"]')).find((b) => {
      const cls = (b.getAttribute("class") || "") + " " + (b.getAttribute("aria-current") || "");
      const isNum = /^\d+$/.test(b.textContent.trim());
      return isNum && (cls.includes("active") || cls.includes("bg-ink") || cls.includes("bg-black") || cls.includes("page"));
    });
    return activeBtn ? parseInt(activeBtn.textContent.trim(), 10) : null;
  }

  // Poll until next page has actually loaded new rows
  async function waitForPageTransition(signatureBefore, pageBefore, timeoutMs = 12000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (!isRunning || !isAutoPaginating) return false;
      await sleep(350);

      const currentLeads = scrapeLeadRows();
      if (currentLeads.length > 0) {
        const currentSig = currentLeads.map((l) => l.name).slice(0, 3).join("::");
        if (signatureBefore && currentSig && currentSig !== signatureBefore) {
          return true;
        }
      }

      const currPage = getActivePageNumber();
      if (pageBefore && currPage && currPage !== pageBefore) {
        return true;
      }
    }
    return false;
  }

  // Dispatch click to next page button with hardware CDP fallback
  async function clickNextPageButton(nextBtn) {
    if (!nextBtn) return;
    nextBtn.scrollIntoView({ behavior: "auto", block: "nearest" });
    await sleep(150);

    const rect = nextBtn.getBoundingClientRect();
    const x = rect.left + rect.width * (0.4 + Math.random() * 0.2);
    const y = rect.top + rect.height * (0.4 + Math.random() * 0.2);

    try {
      await new Promise((resolve) => {
        chrome.runtime.sendMessage(
          { type: "DISPATCH_HARDWARE_CLICK", x, y },
          (res) => resolve(res)
        );
      });
    } catch (_) {}

    try {
      nextBtn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window }));
      nextBtn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window }));
      nextBtn.click();
    } catch (_) {}
  }

  // 100% Detection-Free Auto-Pagination Loop with Strict Analysis Gating
  async function runAutoPaginationLoop() {
    while (isRunning && isAutoPaginating) {
      // Step 1: Detect and analyze all visible leads on current page BEFORE touching pagination
      setHUDStatus("Step 1/3: Analyzing page leads against CRM database...");
      const procResult = await processVisibleRows(true);

      if (!isRunning || !isAutoPaginating) break;

      if (!procResult.success) {
        if (procResult.reason === "no_leads") {
          setHUDStatus("0 leads detected on current page. Auto-pagination paused.", true);
        } else {
          setHUDStatus(`Analysis stopped (${procResult.error || procResult.reason}). Auto-pagination paused.`, true);
        }
        isAutoPaginating = false;
        updateHUDUI();
        break;
      }

      // Step 2: Verification check - ensure all rows on page have evaluated badges
      const visibleLeads = scrapeLeadRows();
      const evaluatedCount = visibleLeads.filter((l) =>
        l.nameElement?.parentElement?.querySelector(".enrich-status-badge")
      ).length;

      setHUDStatus(
        `Step 2/3: Evaluated ${evaluatedCount}/${visibleLeads.length} leads (${procResult.newTargets || 0} Targets). Pacing...`
      );

      // Brief visual settling delay
      await sleep(1000);

      pagesProcessed++;
      updateHUDUI();

      // Micro-break every 25 pages
      if (pagesProcessed % 25 === 0) {
        const restSec = 45 + Math.floor(Math.random() * 25);
        for (let s = restSec; s > 0; s--) {
          if (!isRunning || !isAutoPaginating) return;
          setHUDStatus(`Pacing rest break: Resuming in ${s}s...`);
          await sleep(1000);
        }
      }

      // Capture current page signature before clicking next
      const currentSignature = visibleLeads.map((l) => l.name).slice(0, 3).join("::");
      const activePageBefore = getActivePageNumber();

      // Step 3: Humanized pacing delay before advancing to Next Page
      const jitterMs = 3000 + Math.floor(Math.random() * 2200);
      setHUDStatus(`Step 3/3: Humanized pacing delay (${(jitterMs / 1000).toFixed(1)}s) before Next Page...`);
      await sleep(jitterMs);

      if (!isRunning || !isAutoPaginating) break;

      // Find Next Page button
      const nextBtn = findNextPageButton();
      if (!nextBtn) {
        setHUDStatus("End of records or Next button not found. Auto-pagination stopped.");
        isAutoPaginating = false;
        updateHUDUI();
        break;
      }

      setHUDStatus(`Paginating: Dispatching click to Next Page (Page ${pagesProcessed + 1})...`);
      await clickNextPageButton(nextBtn);

      // Wait for the new page leads to actually load and render in the DOM
      setHUDStatus("Waiting for Next Page table data to load...");
      const transitioned = await waitForPageTransition(currentSignature, activePageBefore, 12000);

      if (!transitioned) {
        console.log("[EnrichMatcher] Page content not changed yet, re-trying click once...");
        const retryBtn = findNextPageButton();
        if (retryBtn) {
          await clickNextPageButton(retryBtn);
          const retryOk = await waitForPageTransition(currentSignature, activePageBefore, 8000);
          if (!retryOk) {
            setHUDStatus("Next page did not load new leads. Auto-pagination paused.", true);
            isAutoPaginating = false;
            updateHUDUI();
            break;
          }
        } else {
          setHUDStatus("Next button no longer available. Auto-pagination stopped.");
          isAutoPaginating = false;
          updateHUDUI();
          break;
        }
      }

      // Pause for table DOM to settle before scanning new page
      await sleep(1000);
    }
  }

  // MutationObserver with Strict Element Filters and Unexamined Row Detection
  let mutationDebounceTimer = null;
  const observer = new MutationObserver((mutations) => {
    if (!isRunning || isAutoPaginating || isProcessing) return;

    // Filter out mutations caused by HUD, status badges, or checkboxes
    let hasExternalMutation = false;
    for (const m of mutations) {
      const el = m.target.nodeType === Node.ELEMENT_NODE ? m.target : m.target.parentElement;
      if (!el) continue;
      if (
        el.id === "enrich-matcher-hud" ||
        el.closest?.("#enrich-matcher-hud") ||
        el.classList?.contains("enrich-status-badge") ||
        el.closest?.(".enrich-status-badge") ||
        el.getAttribute?.("role") === "checkbox" ||
        el.closest?.("[role='checkbox']") ||
        el.type === "checkbox" ||
        el.closest?.("aside, [class*='sidebar'], [class*='filter']")
      ) {
        continue;
      }
      hasExternalMutation = true;
      break;
    }

    if (!hasExternalMutation) return;

    clearTimeout(mutationDebounceTimer);
    mutationDebounceTimer = setTimeout(() => {
      if (isRunning && !isAutoPaginating && !isProcessing) {
        // Only run if there are actual un-evaluated rows on screen
        const unexamined = scrapeLeadRows().filter((l) => {
          const hasBadge = l.nameElement?.parentElement?.querySelector(".enrich-status-badge");
          return !hasBadge || l.rowElement.dataset.enrichEvaluatedSig !== l.sig;
        });
        if (unexamined.length > 0) {
          processVisibleRows();
        }
      }
    }, 1500);
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true
  });

  // Message listener from extension action icon
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === "TOGGLE_ENRICH_CHECKER") {
      isRunning = !isRunning;
      if (!isRunning) isAutoPaginating = false;
      updateHUDUI();
      if (isRunning) processVisibleRows();
      sendResponse({ success: true, running: isRunning });
      return true;
    }
  });

  // Initial HUD Mount
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initHUD);
  } else {
    initHUD();
  }

  console.log("[EnrichMatcher] Initialized on Enrich.so Lead Finder.");
})();
