(() => {
  const STATE_KEY = "__contactDatabaseChecker";
  const REQUIRED_CONTACTS_STORAGE_KEY =
    "contactCheckerRequiredContactsAll";
  const TITLE_GUARDRAIL_STORAGE_KEY =
    "contactCheckerTitleGuardrailEnabled";
  const INDIAN_GUARDRAIL_STORAGE_KEY =
    "contactCheckerIndianGuardrailEnabled";
  const BATCH_NUMBER_STORAGE_KEY =
    "contactCheckerBatchNumber";

  // ============================================================
  // TOGGLE OFF
  // ============================================================

  if (globalThis[STATE_KEY]?.active) {
    globalThis[STATE_KEY].cleanup();
    return;
  }

  // ============================================================
  // STATE
  // ============================================================

  const state = {
    active: true,
    batchNumber: 1,
    titleGuardrailEnabled: true,
    indianGuardrailEnabled: false,
    observer: null,
    timer: null,
    pageTimer: null,
    statusTimer: null,
    storageSaveTimer: null,
    settleTimer: null,
    settleRetryAttempts: 0,
    checkedContacts: new Map(),
    pendingContacts: new Set(),
    currentContacts: new Map(),
    requiredContactsAll: new Map(),
    syncedLeadKeys: new Set(),
    highlightedRows: new Set(),
    activityLog: [],
    activityPanelOpen: false,
    lastLoggedPageSignature: "",
    lastBackendSummary: null
  };

  globalThis[STATE_KEY] = state;

  // ============================================================
  // STYLES
  // ============================================================

  const style = document.createElement("style");

  style.id = "contact-checker-style";

  style.textContent = `
    /* EXCLUSIVE GREEN COLORING: Only REQUIRED leads get green highlight and green badge */
    .contact-checker-required-row {
      background-color: rgba(34, 197, 94, 0.14) !important;
      box-shadow: inset 4px 0 0 #16a34a !important;
    }

    .contact-checker-required-badge {
      display: inline-flex !important;
      align-items: center !important;
      margin-left: 8px !important;
      padding: 2px 7px !important;
      border-radius: 5px !important;
      background: #16a34a !important;
      color: white !important;
      font-size: 10px !important;
      font-weight: 700 !important;
      line-height: 16px !important;
      white-space: nowrap !important;
      box-shadow: 0 1px 3px rgba(22, 163, 74, 0.3) !important;
    }

    /* NEUTRAL STYLING: Existing, Ignored, Excluded, Not Recognized tags (NO green) */
    .contact-checker-existing {
      background-color: transparent !important;
      box-shadow: none !important;
    }

    .contact-checker-existing-badge {
      display: inline-flex !important;
      align-items: center !important;
      margin-left: 8px !important;
      padding: 2px 6px !important;
      border-radius: 5px !important;
      background: #475569 !important;
      color: white !important;
      font-size: 10px !important;
      font-weight: 700 !important;
      line-height: 16px !important;
      white-space: nowrap !important;
    }

    .contact-checker-ignored-badge {
      display: inline-flex !important;
      align-items: center !important;
      margin-left: 8px !important;
      padding: 2px 6px !important;
      border-radius: 5px !important;
      background: #64748b !important;
      color: white !important;
      font-size: 10px !important;
      font-weight: 700 !important;
      line-height: 16px !important;
      white-space: nowrap !important;
    }

    #contact-checker-controls {
      position: fixed;
      right: 20px;
      bottom: 70px;
      z-index: 2147483647;
      display: flex;
      align-items: center;
      gap: 10px;
      background: #111827;
      color: white;
      padding: 10px 12px;
      border-radius: 8px;
      font-family: Arial, sans-serif;
      font-size: 12px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.25);
    }

    #contact-checker-export-required,
    #contact-checker-clear-required,
    #contact-checker-guardrail-toggle,
    #contact-checker-indian-toggle,
    #contact-checker-activity-toggle,
    #contact-checker-clear-activity {
      border: 0;
      border-radius: 6px;
      background: #f97316;
      color: white;
      padding: 7px 10px;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
    }

    #contact-checker-guardrail-toggle,
    #contact-checker-indian-toggle {
      background: #374151 !important;
      transition: background 0.2s ease;
    }

    #contact-checker-clear-required {
      background: #6b7280;
    }

    #contact-checker-export-required:disabled,
    #contact-checker-clear-required:disabled,
    #contact-checker-activity-toggle:disabled,
    #contact-checker-clear-activity:disabled {
      cursor: default;
      opacity: 0.5;
    }


    #contact-checker-activity-toggle {
      background: #2563eb !important;
    }

    #contact-checker-clear-activity {
      background: #4b5563 !important;
      padding: 5px 8px !important;
      font-size: 11px !important;
    }

    #contact-checker-activity-panel {
      position: fixed;
      right: 20px;
      bottom: 130px;
      z-index: 2147483646;
      width: 460px;
      max-width: calc(100vw - 40px);
      max-height: 430px;
      display: none;
      flex-direction: column;
      overflow: hidden;
      background: #0f172a;
      color: #e5e7eb;
      border: 1px solid #334155;
      border-radius: 10px;
      font-family: Arial, sans-serif;
      font-size: 12px;
      box-shadow: 0 10px 35px rgba(0,0,0,0.35);
    }

    #contact-checker-activity-panel.open {
      display: flex;
    }

    .contact-checker-activity-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid #334155;
      background: #111827;
    }

    .contact-checker-activity-title {
      font-size: 13px;
      font-weight: 700;
      color: #fff;
    }

    .contact-checker-activity-summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 6px;
      padding: 9px 10px;
      border-bottom: 1px solid #334155;
      background: #111827;
    }

    .contact-checker-activity-metric {
      padding: 6px;
      border-radius: 6px;
      background: #1f2937;
      text-align: center;
    }

    .contact-checker-activity-metric strong {
      display: block;
      color: #fff;
      font-size: 13px;
    }

    .contact-checker-activity-metric span {
      color: #9ca3af;
      font-size: 10px;
    }

    #contact-checker-activity-list {
      overflow-y: auto;
      padding: 7px 8px 10px;
    }

    .contact-checker-activity-row {
      display: grid;
      grid-template-columns: 66px 132px 1fr;
      gap: 7px;
      align-items: start;
      padding: 6px;
      border-bottom: 1px solid rgba(148,163,184,0.12);
    }

    .contact-checker-activity-row:last-child {
      border-bottom: 0;
    }

    .contact-checker-activity-time {
      color: #94a3b8;
      font-variant-numeric: tabular-nums;
    }

    .contact-checker-activity-event {
      color: #93c5fd;
      font-weight: 700;
      word-break: break-word;
    }

    .contact-checker-activity-message {
      color: #e5e7eb;
      word-break: break-word;
    }

    .contact-checker-activity-row.warning
    .contact-checker-activity-event {
      color: #fbbf24;
    }

    .contact-checker-activity-row.error
    .contact-checker-activity-event {
      color: #f87171;
    }

    @keyframes contact-checker-spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }

    .contact-checker-spinner {
      display: inline-block;
      width: 13px;
      height: 13px;
      border: 2px solid rgba(255, 255, 255, 0.25);
      border-top-color: #f97316;
      border-radius: 50%;
      animation: contact-checker-spin 0.7s linear infinite;
      margin-right: 8px;
      vertical-align: middle;
      flex-shrink: 0;
    }

    #contact-checker-status {
      position: fixed;
      right: 20px;
      bottom: 20px;
      z-index: 2147483647;
      display: flex;
      align-items: center;
      background: #0f172a;
      color: #f8fafc;
      padding: 9px 14px;
      border-radius: 8px;
      border: 1px solid #334155;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      font-size: 13px;
      font-weight: 500;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
      transition: opacity 0.25s ease, transform 0.25s ease;
    }

    .contact-checker-live-badge {
      display: inline-flex;
      align-items: center;
      padding: 3px 8px;
      border-radius: 5px;
      font-size: 11px;
      font-weight: 600;
      background: #1e293b;
      color: #94a3b8;
      border: 1px solid #334155;
    }

    .contact-checker-live-badge.active {
      background: rgba(249, 115, 22, 0.15);
      color: #fb923c;
      border-color: rgba(249, 115, 22, 0.4);
    }
  `;

  document.documentElement.appendChild(style);

  // ============================================================
  // HELPERS
  // ============================================================

  function cleanText(value) {
    return (value || "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function activityClock(timestamp) {
    const date = timestamp
      ? new Date(timestamp)
      : new Date();

    if (
      Number.isNaN(
        date.getTime()
      )
    ) {
      return "--:--:--";
    }

    return date.toLocaleTimeString(
      [],
      {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      }
    );
  }

  function addActivity(
    event,
    message,
    level = "info",
    details = {}
  ) {
    const entry = {
      timestamp:
        new Date().toISOString(),
      event:
        event || "EVENT",
      message:
        message || "",
      level:
        level || "info",
      details:
        details || {}
    };

    state.activityLog.push(entry);

    if (state.activityLog.length > 400) {
      state.activityLog.splice(
        0,
        state.activityLog.length - 400
      );
    }

    console.log(
      `[ContactChecker] ${entry.event}: ${entry.message}`,
      entry.details
    );

    renderActivityPanel();
  }

  function appendBackendActivity(entries) {
    if (!Array.isArray(entries)) {
      return;
    }

    entries.forEach(entry => {
      state.activityLog.push({
        timestamp:
          entry?.timestamp ||
          new Date().toISOString(),

        event:
          entry?.event ||
          "BACKEND_EVENT",

        message:
          entry?.message ||
          "",

        level:
          entry?.level ||
          "info",

        details:
          entry?.details ||
          {}
      });

      console.log(
        `[ContactChecker API] ${entry?.event || "EVENT"}: ${entry?.message || ""}`,
        entry?.details || {}
      );
    });

    if (state.activityLog.length > 400) {
      state.activityLog.splice(
        0,
        state.activityLog.length - 400
      );
    }

    renderActivityPanel();
  }

  function renderActivityPanel() {
    let panel = document.getElementById(
      "contact-checker-activity-panel"
    );

    if (!panel) {
      panel = document.createElement("div");
      panel.id =
        "contact-checker-activity-panel";

      panel.innerHTML = `
        <div class="contact-checker-activity-header">
          <div class="contact-checker-activity-title">
            Contact Checker Activity
          </div>
          <button
            id="contact-checker-clear-activity"
            type="button"
          >Clear</button>
        </div>
        <div
          class="contact-checker-activity-summary"
          id="contact-checker-activity-summary"
        ></div>
        <div
          id="contact-checker-activity-list"
        ></div>
      `;

      panel
        .querySelector(
          "#contact-checker-clear-activity"
        )
        .addEventListener(
          "click",
          () => {
            state.activityLog = [];
            state.lastBackendSummary = null;
            renderActivityPanel();
          }
        );

      document.body.appendChild(panel);
    }

    panel.classList.toggle(
      "open",
      state.activityPanelOpen
    );

    const summary =
      state.lastBackendSummary || {};

    const summaryElement =
      panel.querySelector(
        "#contact-checker-activity-summary"
      );

    const metrics = [
      [
        summary.contacts_processed ?? 0,
        "Checked"
      ],
      [
        summary.existing ?? 0,
        "Existing"
      ],
      [
        summary.knowledge_lookups ?? 0,
        "LLM"
      ],
      [
        summary.web_searches ?? 0,
        "Web"
      ]
    ];

    summaryElement.replaceChildren();

    metrics.forEach(
      ([value, label]) => {
        const metric =
          document.createElement("div");

        metric.className =
          "contact-checker-activity-metric";

        const strong =
          document.createElement("strong");

        strong.textContent =
          String(value);

        const span =
          document.createElement("span");

        span.textContent =
          label;

        metric.append(
          strong,
          span
        );

        summaryElement.appendChild(
          metric
        );
      }
    );

    const list =
      panel.querySelector(
        "#contact-checker-activity-list"
      );

    list.replaceChildren();

    const entries =
      state.activityLog.slice(-120);

    entries.forEach(entry => {
      const row =
        document.createElement("div");

      row.className =
        `contact-checker-activity-row ${entry.level || "info"}`;

      const time =
        document.createElement("div");

      time.className =
        "contact-checker-activity-time";

      time.textContent =
        activityClock(
          entry.timestamp
        );

      const event =
        document.createElement("div");

      event.className =
        "contact-checker-activity-event";

      event.textContent =
        entry.event || "";

      const message =
        document.createElement("div");

      message.className =
        "contact-checker-activity-message";

      message.textContent =
        entry.message || "";

      row.append(
        time,
        event,
        message
      );

      list.appendChild(row);
    });

    if (state.activityPanelOpen) {
      list.scrollTop =
        list.scrollHeight;
    }
  }

  function toggleActivityPanel() {
    state.activityPanelOpen =
      !state.activityPanelOpen;

    renderActivityPanel();

    const button =
      document.getElementById(
        "contact-checker-activity-toggle"
      );

    if (button) {
      button.textContent =
        state.activityPanelOpen
          ? "Hide Activity"
          : "Activity";
    }
  }

  function showStatus(message, duration = 2500, isRunning = false) {
    clearTimeout(state.statusTimer);

    let status = document.getElementById(
      "contact-checker-status"
    );

    if (!status) {
      status = document.createElement("div");
      status.id = "contact-checker-status";
      document.body.appendChild(status);
    }

    if (isRunning) {
      status.innerHTML = `
        <span class="contact-checker-spinner"></span>
        <span>${message}</span>
      `;
    } else {
      status.innerHTML = `
        <span>${message}</span>
      `;
    }

    if (duration && !isRunning) {
      state.statusTimer = setTimeout(
        () => status?.remove(),
        duration
      );
    }
  }

  // ============================================================
  // FIND CONTACT LINKS
  // ============================================================

  function getApolloContactLinks() {
    const selectors = [
      'a[href*="/contacts/"]',
      'a[data-to*="/contacts/"]',
      'a[href*="/people/"]',
      'a[data-to*="/people/"]',
      'a[href*="#/people/"]',
      'a[href*="#/contacts/"]'
    ];

    const found = Array.from(
      document.querySelectorAll(selectors.join(","))
    );

    return found.filter(link => {
      const href = link.getAttribute("href") || link.getAttribute("data-to") || "";
      // Exclude navigation tabs or search filters
      if (href.endsWith("/people") || href.endsWith("/contacts") || href.includes("?")) {
        // Only accept if inside an actual data row
        return Boolean(link.closest('[role="row"]'));
      }
      return Boolean(link.closest('[role="row"]'));
    });
  }

  // ============================================================
  // GET TEMPORARY APOLLO KEY
  // ============================================================

  function getContactKey(link, index) {
    const value =
      link.getAttribute("data-to") ||
      link.getAttribute("href") ||
      "";

    const match = value.match(
      /\/(?:contacts|people)\/([^?#/]+)/
    );

    if (match?.[1]) {
      return `apollo-${match[1]}`;
    }

    return `apollo-row-${index}`;
  }


  // ============================================================
  // FIND A ROW CELL BY APOLLO COLUMN HEADER
  // ============================================================

  function findCellByHeader(
    row,
    cells,
    acceptedHeaders
  ) {
    const headers = Array.from(
      document.querySelectorAll(
        '[role="columnheader"]'
      )
    );

    const accepted = acceptedHeaders.map(
      value => cleanText(value).toLowerCase()
    );

    const header = headers.find(item => {
      const label = cleanText(
        item.innerText ||
        item.textContent ||
        item.getAttribute("aria-label") ||
        ""
      ).toLowerCase();

      return accepted.some(
        expected =>
          label === expected ||
          label.includes(expected)
      );
    });

    if (!header) {
      return null;
    }

    // Prefer ARIA column indices because Apollo can have a
    // checkbox/actions column before the visible data columns.
    const ariaColumnIndex =
      header.getAttribute("aria-colindex");

    if (ariaColumnIndex) {
      const indexedCell = row.querySelector(
        `[role="cell"][aria-colindex="${ariaColumnIndex}"]`
      );

      if (indexedCell) {
        return indexedCell;
      }
    }

    // Fallback: use visible header order.
    const headerIndex =
      headers.indexOf(header);

    return (
      headerIndex >= 0
        ? cells[headerIndex] || null
        : null
    );
  }

  // ============================================================
  // EXTRACT APOLLO ROW (Flexible & Resilient to Custom Column Layouts)
  // ============================================================

  function extractContact(link, index) {
    const row = link.closest('[role="row"]');

    if (!row) {
      return null;
    }

    const name = cleanText(
      link.innerText || link.textContent
    );

    if (!name) {
      return null;
    }

    // ----------------------------------------------------------
    // Find all Apollo cells inside this row
    // ----------------------------------------------------------

    const cells = Array.from(
      row.querySelectorAll('[role="cell"]')
    );

    const nameCellIndex = cells.findIndex(
      cell => cell.contains(link)
    );

    const nameCell = nameCellIndex !== -1
      ? cells[nameCellIndex]
      : (link.closest('[role="cell"]') || link.parentElement);

    // 1. Dynamic Title Detection (by header or next cell)
    let titleCell = findCellByHeader(
      row,
      cells,
      ["title", "job title", "position", "role"]
    );
    if (!titleCell && nameCellIndex !== -1 && nameCellIndex + 1 < cells.length) {
      titleCell = cells[nameCellIndex + 1];
    }

    // 2. Dynamic Company Detection (by header, company link, or adjacent cell)
    let companyCell = findCellByHeader(
      row,
      cells,
      ["company", "company name", "organization", "account"]
    );
    if (!companyCell) {
      const compLink = row.querySelector('a[href*="/accounts/"], a[href*="/companies/"], a[data-to*="/accounts/"], a[data-to*="/companies/"]');
      if (compLink) {
        companyCell = compLink.closest('[role="cell"]') || compLink.parentElement;
      }
    }
    if (!companyCell && nameCellIndex !== -1 && nameCellIndex + 2 < cells.length) {
      companyCell = cells[nameCellIndex + 2];
    }

    let jobTitle = cleanText(
      titleCell?.innerText || titleCell?.textContent || ""
    );

    let company = cleanText(
      companyCell?.innerText || companyCell?.textContent || ""
    );

    if (!company) {
      const compLink = row.querySelector('a[href*="/accounts/"], a[href*="/companies/"], a[data-to*="/accounts/"], a[data-to*="/companies/"]');
      if (compLink) {
        company = cleanText(compLink.innerText || compLink.textContent);
      }
    }

    // Location is optional
    const locationCell = findCellByHeader(
      row,
      cells,
      [
        "company location",
        "location",
        "headquarters",
        "headquarters location",
        "hq location"
      ]
    );

    const location = cleanText(
      locationCell?.innerText ||
      locationCell?.textContent ||
      ""
    );

    // Number of Employees is optional.
    const employeesCell = findCellByHeader(
      row,
      cells,
      [
        "# employees",
        "employees",
        "number of employees",
        "company size",
        "num employees"
      ]
    );

    let employeeCount = null;
    if (employeesCell) {
      const empText = cleanText(employeesCell.innerText || employeesCell.textContent || "");
      const empMatch = empText.replace(/,/g, "").match(/\d+/);
      if (empMatch) {
        employeeCount = parseInt(empMatch[0], 10);
      }
    }

    if (!jobTitle || !company) {
      console.log(
        "Contact Checker: incomplete row",
        {
          name,
          jobTitle,
          company
        }
      );

      return null;
    }

    const key = getContactKey(
      link,
      index
    );

    const linkedinLink = row.querySelector('a[href*="linkedin.com/in/"]');
    const linkedinUrl = linkedinLink ? (linkedinLink.getAttribute("href") || "") : "";

    return {
      key,
      name,
      job_title: jobTitle,
      company,
      location,
      linkedin_url: linkedinUrl,
      employee_count: employeeCount,
      row,
      link,
      nameCell
    };
  }

  // ============================================================
  // BADGES & HIGHLIGHTING (MUTUALLY EXCLUSIVE)
  // ============================================================

  function clearContactBadges(contact) {
    if (!contact?.nameCell) {
      return;
    }

    const badges = contact.nameCell.querySelectorAll(
      ".contact-checker-existing-badge, .contact-checker-required-badge, .contact-checker-ignored-badge"
    );

    badges.forEach(b => b.remove());
  }

  function highlightContact(
    contact,
    result
  ) {
    const row = contact.row;

    if (!row) {
      return;
    }

    clearContactBadges(contact);

    // Remove from required contacts if previously recorded
    state.requiredContactsAll.delete(contact.key);
    scheduleRequiredContactsSave();
    renderExportControls();

    row.classList.remove("contact-checker-required-row");
    row.classList.remove("contact-checker-existing");

    const badge =
      document.createElement("span");

    badge.className =
      "contact-checker-existing-badge";

    badge.textContent =
      "✓ Existing";

    if (result.email) {
      badge.title =
        `CRM email: ${result.email}`;
    }

    contact.link.insertAdjacentElement(
      "afterend",
      badge
    );
  }

  // ============================================================
  // PERSIST REQUIRED CONTACTS ACROSS RELOADS
  //
  // chrome.storage.local only — never sent anywhere, never touches
  // Apollo. This just survives a tab refresh or browser restart so
  // a long session isn't lost.
  // ============================================================

  function loadStoredRequiredContacts() {
    if (!chrome?.storage?.local) {
      return;
    }

    chrome.storage.local.get(
      [REQUIRED_CONTACTS_STORAGE_KEY, TITLE_GUARDRAIL_STORAGE_KEY, INDIAN_GUARDRAIL_STORAGE_KEY, BATCH_NUMBER_STORAGE_KEY],
      result => {
        if (result?.[BATCH_NUMBER_STORAGE_KEY]) {
          state.batchNumber = Number(result[BATCH_NUMBER_STORAGE_KEY]) || 1;
        }
        if (result?.[TITLE_GUARDRAIL_STORAGE_KEY] !== undefined) {
          state.titleGuardrailEnabled =
            result[TITLE_GUARDRAIL_STORAGE_KEY] === true;
        }
        if (result?.[INDIAN_GUARDRAIL_STORAGE_KEY] !== undefined) {
          state.indianGuardrailEnabled =
            result[INDIAN_GUARDRAIL_STORAGE_KEY] === true;
        }

        const stored =
          result?.[REQUIRED_CONTACTS_STORAGE_KEY];

        if (Array.isArray(stored) && stored.length) {
          stored.forEach(([key, value]) => {
            state.requiredContactsAll.set(key, value);
          });
          // Auto-sync loaded contacts to MySQL table immediately
          saveRequiredContactsNow();
        }

        renderExportControls();
      }
    );
  }

  function saveRequiredContactsNow() {
    if (!chrome?.storage?.local) {
      return;
    }

    const contactsList = Array.from(
      state.requiredContactsAll.entries()
    );

    chrome.storage.local.set({
      [REQUIRED_CONTACTS_STORAGE_KEY]: contactsList
    });

    // Only send contacts that have not been synced yet
    const unsyncedContacts = contactsList
      .filter(([key]) => !state.syncedLeadKeys.has(key))
      .map(([key, c]) => ({
        apollo_id: c.apollo_id || "",
        name: c.name || "",
        first_name: c.first_name || "",
        last_name: c.last_name || "",
        job_title: c.job_title || "",
        company: c.company || "",
        domain: c.domain || "",
        location: c.location || "",
        linkedin_url: c.linkedin_url || "",
        apollo_profile_url: c.apollo_profile_url || "",
        segment: c.segment || "Required_Lead",
        _key: key
      }));

    if (unsyncedContacts.length > 0 && chrome?.runtime?.sendMessage) {
      // Mark as synced to prevent redundant repeated calls
      unsyncedContacts.forEach(c => state.syncedLeadKeys.add(c._key));

      chrome.runtime.sendMessage({
        type: "SYNC_SAVED_LEADS",
        batch: `batch_${state.batchNumber || 1}`,
        contacts: unsyncedContacts
      }, (res) => {
        if (res?.success) {
          contactCheckerLog(`Synced ${unsyncedContacts.length} new lead(s) to MySQL apollo_saved_leads under batch_${state.batchNumber || 1}`);
        } else {
          // If error, unmark so it can retry
          unsyncedContacts.forEach(c => state.syncedLeadKeys.delete(c._key));
        }
      });
    }
  }

  function scheduleRequiredContactsSave() {
    clearTimeout(state.storageSaveTimer);

    state.storageSaveTimer = setTimeout(
      saveRequiredContactsNow,
      400
    );
  }

  // Apollo IDs come from the row's own link href/data-to attribute,
  // which is already present in the DOM the user is looking at.
  // Nothing is clicked or navigated to get this value.
  function getApolloIdFromKey(key) {
    if (!key || !key.startsWith("apollo-") || key.startsWith("apollo-row-")) {
      return "";
    }

    return key.slice("apollo-".length);
  }

  function recordRequiredContact(contact, result) {
    const apolloId = getApolloIdFromKey(contact.key);
    const nameParts = (contact.name || "").trim().split(/\s+/);
    const firstName = nameParts[0] || "";
    const lastName = nameParts.slice(1).join(" ") || "";
    const domain = result?.matched_domain || contact.domain || "";
    const apolloUrl = apolloId ? `https://app.apollo.io/#/people/${apolloId}` : "";

    state.requiredContactsAll.set(contact.key, {
      apollo_id: apolloId,
      first_name: firstName,
      last_name: lastName,
      name: contact.name,
      job_title: contact.job_title,
      company: contact.company,
      domain: domain,
      location: contact.location || "",
      linkedin_url: contact.linkedin_url || "",
      apollo_profile_url: apolloUrl
    });

    scheduleRequiredContactsSave();
  }

  function markRequired(contact, result) {
    const row = contact.row;

    if (row) {
      row.classList.remove("contact-checker-existing");
      row.classList.add("contact-checker-required-row");
      state.highlightedRows.add(row);
    }

    clearContactBadges(contact);

    recordRequiredContact(contact, result);
    renderExportControls();

    const badge =
      document.createElement("span");

    badge.className =
      "contact-checker-required-badge";

    badge.textContent =
      "★ Required Lead";

    if (result?.guardrail_reason) {
      const roleStr = result.role_type ? ` [${result.role_type}]` : "";
      badge.title = `Tier ${result.tier || ""}${roleStr}: ${result.guardrail_reason}`;
    } else {
      badge.title = "Target Lead: Net-new domain (Ready for CSV export)";
    }

    contact.link.insertAdjacentElement(
      "afterend",
      badge
    );
  }

  function markIgnored(contact, result) {
    const row = contact.row;

    if (row) {
      row.classList.remove("contact-checker-existing");
      row.classList.remove("contact-checker-required-row");
    }

    clearContactBadges(contact);

    // Strictly remove from required contacts if previously added
    state.requiredContactsAll.delete(contact.key);
    scheduleRequiredContactsSave();
    renderExportControls();

    const badge =
      document.createElement("span");

    badge.className =
      "contact-checker-ignored-badge";

    if (result?.guardrail_status === "domain_already_in_db") {
      badge.textContent =
        "⊘ Existing Domain";

      badge.title =
        result.guardrail_reason ||
        "Company domain already exists in CRM database with existing contacts.";
    } else if (result?.guardrail_status === "indian_name_disqualified") {
      badge.textContent =
        "⊘ Indian Origin";
      badge.style.background = "#ef4444";

      badge.title =
        result.guardrail_reason ||
        "Excluded: Pure Indian Name Origin.";
    } else if (result?.guardrail_status === "not_recognized_title" || result?.guardrail_status === "not_recognized") {
      badge.textContent =
        "⊘ Not Recognized";
      badge.style.background = "#64748b";

      badge.title =
        result.guardrail_reason ||
        "Title is not recognized in our database.";
    } else if (result?.guardrail_status === "disqualified_title") {
      badge.textContent =
        "⊘ Excluded: Title";

      badge.title =
        result.guardrail_reason ||
        "Excluded: Title belongs to non-required segment (Prio 3/4).";
    } else if (result?.guardrail_status === "company_limit_reached") {
      badge.textContent =
        "⊘ 1/Company Max";
      badge.style.background = "#6b7280";

      badge.title =
        result.guardrail_reason ||
        "Only 1 contact per company is allowed.";
    } else {
      badge.textContent =
        "⊘ Ignored";

      badge.title =
        result?.guardrail_reason ||
        "Ignored by guardrail rules.";
    }

    contact.link.insertAdjacentElement(
      "afterend",
      badge
    );
  }

  function applyContactResult(
    contact,
    result
  ) {
    if (result.exists) {
      highlightContact(
        contact,
        result
      );
    } else if (result.required && !result.ignored) {
      markRequired(
        contact,
        result
      );
    } else {
      markIgnored(
        contact,
        result
      );
    }
  }

  function getRequiredContacts() {
    return Array.from(
      state.currentContacts.values()
    ).filter(contact => {
      const result =
        state.checkedContacts.get(
          contact.key
        );

      return result?.exists === false && result?.required === true && !result?.ignored;
    });
  }

  // ============================================================
  // EXPORT REQUIRED CONTACTS (CSV) — PASSIVE ONLY
  //
  // Formatted with standard Apollo-compliant column names:
  // First Name, Last Name, Title, Company, Company Domain, Location, Person Linkedin Url
  // ============================================================

  function csvEscape(value) {
    const text = String(value ?? "");

    if (/[",\n]/.test(text)) {
      return `"${text.replace(/"/g, '""')}"`;
    }

    return text;
  }

  function buildRequiredContactsCSV() {
    const rows = Array.from(
      state.requiredContactsAll.values()
    );

    const header = [
      "First Name",
      "Last Name",
      "Title",
      "Company",
      "Company Domain",
      "Location",
      "Person Linkedin Url",
      "Apollo Profile Url"
    ];

    const lines = [header.join(",")];

    rows.forEach(row => {
      lines.push(
        [
          row.first_name || "",
          row.last_name || "",
          row.job_title || "",
          row.company || "",
          row.domain || "",
          row.location || "",
          row.linkedin_url || "",
          row.apollo_profile_url || ""
        ]
          .map(csvEscape)
          .join(",")
      );
    });

    return lines.join("\n");
  }

  function exportRequiredContactsCSV() {
    if (!state.requiredContactsAll.size) {
      showStatus("No required contacts collected yet");
      return;
    }

    const csv = buildRequiredContactsCSV();
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);

    const link = document.createElement("a");
    link.href = url;
    link.download = `required-contacts-${Date.now()}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();

    URL.revokeObjectURL(url);

    addActivity(
      "REQUIRED_CONTACTS_EXPORTED",
      `Exported ${state.requiredContactsAll.size} required contact(s) to CSV.`,
      "info",
      {
        count: state.requiredContactsAll.size
      }
    );

    showStatus(
      `Exported ${state.requiredContactsAll.size} required contact(s)`,
      3000
    );
  }

  function clearRequiredContactsList() {
    const count = state.requiredContactsAll.size;
    const prevBatch = state.batchNumber || 1;
    state.batchNumber = prevBatch + 1;

    state.requiredContactsAll.clear();
    state.syncedLeadKeys.clear();

    if (chrome?.storage?.local) {
      chrome.storage.local.set({
        [BATCH_NUMBER_STORAGE_KEY]: state.batchNumber
      });
      chrome.storage.local.remove(
        REQUIRED_CONTACTS_STORAGE_KEY
      );
    }

    addActivity(
      "REQUIRED_CONTACTS_CLEARED",
      `Cleared ${count} contact(s) from local list. Batch #${prevBatch} preserved in DB. Next scans will save to Batch #${state.batchNumber}.`,
      "info",
      {
        previous_batch: `batch_${prevBatch}`,
        new_batch: `batch_${state.batchNumber}`
      }
    );

    renderExportControls();
    showStatus(`List cleared. Next scans will save under Batch #${state.batchNumber} in Database.`, 3000);
  }

  function toggleTitleGuardrail() {
    state.titleGuardrailEnabled = !state.titleGuardrailEnabled;

    if (chrome?.storage?.local) {
      chrome.storage.local.set({
        [TITLE_GUARDRAIL_STORAGE_KEY]: state.titleGuardrailEnabled
      });
    }

    renderExportControls();

    showStatus(
      `AI Title Filter ${state.titleGuardrailEnabled ? "ENABLED (Tiers 1–6)" : "DISABLED (New domains auto-qualified)"}`,
      3500
    );

    addActivity(
      "GUARDRAIL_TOGGLED",
      `AI Title Filter toggled ${state.titleGuardrailEnabled ? "ON" : "OFF"}. Rescanning active contacts...`,
      "info",
      {
        title_guardrail_enabled: state.titleGuardrailEnabled
      }
    );

    // Clear evaluated contact cache so active page is re-evaluated with the new setting
    state.checkedContacts.clear();
    state.currentContacts.clear();
    scanApollo();
  }

  function toggleIndianGuardrail() {
    state.indianGuardrailEnabled = !state.indianGuardrailEnabled;

    if (chrome?.storage?.local) {
      chrome.storage.local.set({
        [INDIAN_GUARDRAIL_STORAGE_KEY]: state.indianGuardrailEnabled
      });
    }

    renderExportControls();

    showStatus(
      `Indian Name Filter ${state.indianGuardrailEnabled ? "ENABLED (Pure Indian excluded)" : "DISABLED"}`,
      3500
    );

    addActivity(
      "GUARDRAIL_TOGGLED",
      `Indian Name Filter toggled ${state.indianGuardrailEnabled ? "ON" : "OFF"}. Rescanning active contacts...`,
      "info",
      {
        indian_name_guardrail_enabled: state.indianGuardrailEnabled
      }
    );

    // Clear evaluated contact cache so active page is re-evaluated with the new setting
    state.checkedContacts.clear();
    state.currentContacts.clear();
    scanApollo();
  }

  function renderExportControls() {
    let controls = document.getElementById(
      "contact-checker-controls"
    );

    if (!controls) {
      controls = document.createElement("div");
      controls.id = "contact-checker-controls";
      controls.innerHTML = `
        <span id="contact-checker-live-status" class="contact-checker-live-badge">✓ Ready</span>
        <span id="contact-checker-required-count"></span>
        <button
          id="contact-checker-rescan-btn"
          type="button"
          style="background: #0284c7;"
        >⟳ Rescan Page</button>
        <button
          id="contact-checker-export-required"
          type="button"
        >Export Required Contacts (CSV)</button>
        <button
          id="contact-checker-guardrail-toggle"
          type="button"
        >AI Title Filter: OFF</button>
        <button
          id="contact-checker-indian-toggle"
          type="button"
        >Indian Name Filter: OFF</button>
        <button
          id="contact-checker-clear-required"
          type="button"
        >Clear List</button>
        <button
          id="contact-checker-activity-toggle"
          type="button"
        >Activity</button>
      `;

      controls
        .querySelector(
          "#contact-checker-rescan-btn"
        )
        .addEventListener(
          "click",
          () => {
            state.checkedContacts.clear();
            state.currentContacts.clear();
            showStatus("Rescanning page...", 1500, true);
            scanApollo();
          }
        );

      controls
        .querySelector(
          "#contact-checker-export-required"
        )
        .addEventListener(
          "click",
          exportRequiredContactsCSV
        );

      controls
        .querySelector(
          "#contact-checker-guardrail-toggle"
        )
        .addEventListener(
          "click",
          toggleTitleGuardrail
        );

      controls
        .querySelector(
          "#contact-checker-indian-toggle"
        )
        .addEventListener(
          "click",
          toggleIndianGuardrail
        );

      controls
        .querySelector(
          "#contact-checker-clear-required"
        )
        .addEventListener(
          "click",
          clearRequiredContactsList
        );

      controls
        .querySelector(
          "#contact-checker-activity-toggle"
        )
        .addEventListener(
          "click",
          toggleActivityPanel
        );

      document.body.appendChild(controls);

      renderActivityPanel();
    }

    const guardrailButton = controls.querySelector(
      "#contact-checker-guardrail-toggle"
    );

    if (guardrailButton) {
      if (state.titleGuardrailEnabled) {
        guardrailButton.textContent = "Title Guardrail: ON";
        guardrailButton.style.background = "#8b5cf6";
        guardrailButton.title =
          "Title Guardrail Active: Filtering contacts by 64K database rules & on-demand LLM evaluation";
      } else {
        guardrailButton.textContent = "Title Guardrail: OFF";
        guardrailButton.style.background = "#374151";
        guardrailButton.title =
          "Title Guardrail Inactive: All net-new domains are auto-qualified as Required";
      }
    }

    const indianButton = controls.querySelector(
      "#contact-checker-indian-toggle"
    );

    if (indianButton) {
      if (state.indianGuardrailEnabled) {
        indianButton.textContent = "Indian Name Filter: ON";
        indianButton.style.background = "#ef4444";
        indianButton.title =
          "Guardrail 3 Active: Excluding unambiguous pure Indian names";
      } else {
        indianButton.textContent = "Indian Name Filter: OFF";
        indianButton.style.background = "#374151";
        indianButton.title =
          "Guardrail 3 Inactive: Demographic name filter is disabled";
      }
    }

    const visibleRequiredCount =
      getRequiredContacts().length;

    const totalCollected =
      state.requiredContactsAll.size;

    const countLabel = controls.querySelector(
      "#contact-checker-required-count"
    );

    const countText =
      `Required on page: ${visibleRequiredCount} | Collected total: ${totalCollected}`;

    if (countLabel && countLabel.textContent !== countText) {
      countLabel.textContent = countText;
    }

    const exportButton = controls.querySelector(
      "#contact-checker-export-required"
    );

    if (exportButton) {
      exportButton.disabled = totalCollected === 0;
    }
  }

  // ============================================================
  // SCAN APOLLO
  // ============================================================

  function scanApollo() {
    if (!state.active) {
      return;
    }

    const links =
      getApolloContactLinks();

    if (!links.length) {
      state.currentContacts.clear();
      renderExportControls();

      console.log(
        "Contact Checker: no Apollo contacts found."
      );

      return;
    }

    const contactsToCheck = [];

    const currentContacts =
      new Map();

    links.forEach(
      (link, index) => {
        const contact =
          extractContact(
            link,
            index
          );

        if (!contact) {
          return;
        }

        // Avoid duplicates
        if (
          currentContacts.has(
            contact.key
          )
        ) {
          return;
        }

        currentContacts.set(
          contact.key,
          contact
        );

        // Already checked earlier
        if (
          state.checkedContacts.has(
            contact.key
          )
        ) {
          const cached =
            state.checkedContacts.get(
              contact.key
            );

          if (cached) {
            applyContactResult(
              contact,
              cached
            );
          }

          return;
        }

        // Currently in flight
        if (
          state.pendingContacts.has(
            contact.key
          )
        ) {
          return;
        }

        contactsToCheck.push(
          contact
        );
      }
    );

    state.currentContacts =
      currentContacts;

    // Settle retry engine
    if (
      contactsToCheck.some(
        contact =>
          contact.needsSettling
      )
      && state.settleRetryAttempts < 6
    ) {
      state.settleRetryAttempts += 1;

      clearTimeout(
        state.settleTimer
      );

      state.settleTimer = setTimeout(
        scanApollo,
        150
      );
    } else {
      state.settleRetryAttempts = 0;
    }

    const currentSignature =
      Array.from(
        currentContacts.keys()
      ).join("|");

    if (
      currentSignature &&
      currentSignature
        !== state.lastLoggedPageSignature
    ) {
      state.lastLoggedPageSignature =
        currentSignature;

      addActivity(
        "PAGE_SCANNED",
        `Apollo page scanned — ${currentContacts.size} contact(s) visible.`,
        "info",
        {
          visible_contacts:
            currentContacts.size,
          new_contacts_to_check:
            contactsToCheck.length
        }
      );
    }

    renderExportControls();

    if (!contactsToCheck.length) {
      return;
    }

    contactsToCheck.forEach(contact =>
      state.pendingContacts.add(contact.key)
    );

    showStatus(
      `Checking ${contactsToCheck.length} contact(s)...`,
      0,
      true
    );

    const liveStatus = document.getElementById("contact-checker-live-status");
    if (liveStatus) {
      liveStatus.className = "contact-checker-live-badge active";
      liveStatus.innerHTML = `<span class="contact-checker-spinner" style="width:10px;height:10px;margin-right:5px;border-width:1.5px;"></span> Checking ${contactsToCheck.length}...`;
    }

    addActivity(
      "API_BATCH_SENT",
      `Sending ${contactsToCheck.length} contact(s) to the local matching API.`,
      "info",
      {
        contacts:
          contactsToCheck.length
      }
    );

    // ==========================================================
    // SEND ONE BATCH TO PYTHON
    // ==========================================================

    chrome.runtime.sendMessage(
      {
        type: "MATCH_APOLLO",
        batch: `batch_${state.batchNumber || 1}`,
        title_guardrail_enabled:
          state.titleGuardrailEnabled === true,
        indian_name_guardrail_enabled:
          state.indianGuardrailEnabled === true,

        contacts:
          contactsToCheck.map(
            contact => {
              const apolloId = getApolloIdFromKey(contact.key);
              const nameParts = (contact.name || "").trim().split(/\s+/);
              return {
                key: contact.key,
                apollo_id: apolloId,
                name: contact.name,
                first_name: nameParts[0] || "",
                last_name: nameParts.slice(1).join(" ") || "",
                job_title: contact.job_title,
                company: contact.company,
                location: contact.location || "",
                linkedin_url: contact.linkedin_url || "",
                apollo_profile_url: apolloId ? `https://app.apollo.io/#/people/${apolloId}` : ""
              };
            }
          )
      },

      response => {
        if (
          chrome.runtime.lastError
        ) {
          contactsToCheck.forEach(contact =>
            state.pendingContacts.delete(contact.key)
          );

          console.error(
            "Contact Checker runtime error:",
            chrome.runtime.lastError.message
          );

          addActivity(
            "EXTENSION_RUNTIME_ERROR",
            chrome.runtime.lastError.message,
            "error"
          );

          const liveStatusErr = document.getElementById("contact-checker-live-status");
          if (liveStatusErr) {
            liveStatusErr.className = "contact-checker-live-badge";
            liveStatusErr.textContent = "⚠ Runtime Error";
          }

          showStatus(
            "Extension runtime error",
            3000,
            false
          );

          return;
        }

        if (!response?.success) {
          contactsToCheck.forEach(contact =>
            state.pendingContacts.delete(contact.key)
          );

          console.error(
            "Contact Checker API error:",
            response
          );

          addActivity(
            "API_ERROR",
            response?.error ||
            "Unknown API error",
            "error"
          );

          const liveStatusErr = document.getElementById("contact-checker-live-status");
          if (liveStatusErr) {
            liveStatusErr.className = "contact-checker-live-badge";
            liveStatusErr.textContent = "⚠ API Error";
          }

          showStatus(
            "Database connection error",
            3000,
            false
          );

          return;
        }

        appendBackendActivity(
          response.activity || []
        );

        state.lastBackendSummary =
          response.summary || null;

        renderActivityPanel();

        let matches = 0;
        let requiredCount = 0;
        let ignoredCount = 0;

        contactsToCheck.forEach(
          contact => {
            state.pendingContacts.delete(
              contact.key
            );

            const result =
              response.results?.[
                contact.key
              ];

            if (!result) {
              return;
            }

            state.checkedContacts.set(
              contact.key,
              result
            );

            if (result.exists) {
              matches++;
            } else if (result.required && !result.ignored) {
              requiredCount++;
            } else {
              ignoredCount++;
            }

            applyContactResult(
              contact,
              result
            );
          }
        );

        addActivity(
          "BATCH_APPLIED_TO_PAGE",
          `Batch complete: ${matches} existing, ${requiredCount} required lead(s), ${ignoredCount} ignored.`,
          "info",
          {
            existing: matches,
            required: requiredCount,
            ignored: ignoredCount,
            total: contactsToCheck.length
          }
        );

        const liveStatusDone = document.getElementById("contact-checker-live-status");
        if (liveStatusDone) {
          liveStatusDone.className = "contact-checker-live-badge";
          liveStatusDone.textContent = `✓ Checked (${matches} existing, ${requiredCount} req, ${ignoredCount} ign)`;
        }

        renderExportControls();

        showStatus(
          `✓ Checked ${contactsToCheck.length} contact(s) — ${matches} existing, ${requiredCount} required lead(s)`,
          3500,
          false
        );
      }
    );
  }

  // ============================================================
  // WATCH APOLLO
  // ============================================================

  function isExtensionNode(node) {
    const element = node?.nodeType === Node.ELEMENT_NODE
      ? node
      : node?.parentElement;

    return Boolean(
      element?.matches?.(
        `#contact-checker-status,
         #contact-checker-controls,
         #contact-checker-activity-panel,
         .contact-checker-existing-badge,
         .contact-checker-required-badge`
      ) ||
      element?.closest?.(
        `#contact-checker-status,
         #contact-checker-controls,
         #contact-checker-activity-panel`
      )
    );
  }

  function mutationNeedsScan(mutation) {
    if (isExtensionNode(mutation.target)) {
      return false;
    }

    const changedNodes = [
      ...mutation.addedNodes,
      ...mutation.removedNodes
    ];

    if (
      changedNodes.length > 0 &&
      changedNodes.every(isExtensionNode)
    ) {
      return false;
    }

    const affectsApolloRows = node => {
      const element = node?.nodeType === Node.ELEMENT_NODE
        ? node
        : node?.parentElement;

      return Boolean(
        element?.matches?.(
          `[role="row"],
           a[href*="/contacts/"],
           a[data-to*="/contacts/"],
           a[href*="/people/"],
           a[data-to*="/people/"]`
        ) ||
        element?.closest?.('[role="row"]') ||
        element?.querySelector?.(
          `[role="row"],
           a[href*="/contacts/"],
           a[data-to*="/contacts/"],
           a[href*="/people/"],
           a[data-to*="/people/"]`
        )
      );
    };

    return affectsApolloRows(mutation.target) ||
      changedNodes.some(node =>
        !isExtensionNode(node) &&
        affectsApolloRows(node)
      );
  }

  function scheduleScan(delay = 100) {
    clearTimeout(state.timer);

    state.timer = setTimeout(
      () => {
        scanApollo();
      },
      delay
    );
  }

  state.observer =
    new MutationObserver(
      mutations => {
        if (
          mutations.some(
            mutationNeedsScan
          )
        ) {
          scheduleScan();
        }
      }
    );

  state.observer.observe(
    document.body,
    {
      childList: true,
      subtree: true
    }
  );

  // ============================================================
  // CLEANUP / TURN OFF
  // ============================================================

  state.cleanup = function () {
    state.active = false;

    if (state.observer) {
      state.observer.disconnect();
    }

    clearTimeout(
      state.timer
    );

    clearTimeout(state.pageTimer);
    clearTimeout(state.statusTimer);
    clearTimeout(state.storageSaveTimer);
    clearTimeout(state.settleTimer);
    saveRequiredContactsNow();
    state.pendingContacts.clear();

    state.highlightedRows.forEach(
      row => {
        row.classList.remove(
          "contact-checker-existing"
        );
        row.classList.remove(
          "contact-checker-required-row"
        );
      }
    );

    document
      .querySelectorAll(
        `.contact-checker-existing-badge,
         .contact-checker-required-badge,
         .contact-checker-ignored-badge`
      )
      .forEach(
        badge => badge.remove()
      );

    document
      .getElementById(
        "contact-checker-style"
      )
      ?.remove();

    document
      .getElementById(
        "contact-checker-status"
      )
      ?.remove();

    document
      .getElementById(
        "contact-checker-controls"
      )
      ?.remove();

    document
      .getElementById(
        "contact-checker-activity-panel"
      )
      ?.remove();

    state.activityLog = [];
    state.lastBackendSummary = null;
    state.highlightedRows.clear();
    state.currentContacts.clear();

    delete globalThis[
      STATE_KEY
    ];

    console.log(
      "Contact Database Checker disabled."
    );
  };

  // ============================================================
  // START
  // ============================================================

  console.log(
    "Contact Database Checker enabled — Apollo mode."
  );

  addActivity(
    "EXTENSION_STARTED",
    "Contact Database Checker enabled in Apollo mode."
  );

  showStatus(
    "Contact Checker ON"
  );

  loadStoredRequiredContacts();
  scanApollo();

})();
