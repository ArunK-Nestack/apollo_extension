
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
  const BATCH_NAME_STORAGE_KEY =
    "contactCheckerBatchName";
  const EXTENSION_ENABLED_STORAGE_KEY =
    "contactCheckerExtensionEnabled";

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
    batchName: "batch_1",
    titleGuardrailEnabled: true,
    indianGuardrailEnabled: true,
    hasUnsavedRequiredContacts: false,
    isUpdatingDom: false,
    observer: null,
    timer: null,
    pageTimer: null,
    statusTimer: null,
    storageSaveTimer: null,
    settleTimer: null,
    settleRetryAttempts: 0,
    checkedContacts: new Map(),
    pendingContacts: new Map(), // key -> timestamp (supports TTL auto-expiration)
    currentContacts: new Map(),
    requiredContactsAll: new Map(),
    requiredCompanyMap: new Map(),
    syncedLeadKeys: new Set(),
    syncingLeadKeys: new Set(),
    highlightedRows: new Set(),
    activityLog: [],
    activityPanelOpen: false,
    lastLoggedPageSignature: "",
    lastBackendSummary: null,
    isEvaluatingBatch: false,
    lastEvaluatedPendingTimestamp: 0,
    lastNavigatedKey: "",
    domainColumnWarningLogged: false,
    consecutiveExtractionFailures: 0
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

    #contact-checker-domain-warning {
      position: fixed;
      right: 20px;
      bottom: 128px;
      z-index: 2147483647;
      max-width: 360px;
      background: #451a03;
      color: #fef3c7;
      border: 1px solid #f59e0b;
      border-radius: 8px;
      padding: 10px 12px;
      font-family: Arial, sans-serif;
      font-size: 12px;
      line-height: 1.45;
      box-shadow: 0 4px 15px rgba(0,0,0,0.25);
    }

    #contact-checker-domain-warning strong {
      display: block;
      color: #fde68a;
      font-size: 12px;
      margin-bottom: 4px;
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
    #contact-checker-dedupe-btn,
    #contact-checker-clear-required,
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

    #contact-checker-dedupe-btn {
      background: #eab308 !important;
      color: #111827 !important;
    }

    #contact-checker-clear-required {
      background: #6b7280;
    }

    #contact-checker-export-required:disabled,
    #contact-checker-dedupe-btn:disabled,
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

  function ensureStylesInjected() {
    if (!document.getElementById("contact-checker-style")) {
      (document.head || document.documentElement || document.body)?.appendChild(style);
    }
  }
  ensureStylesInjected();

  // ============================================================
  // HELPERS
  // ============================================================

  function cleanText(value) {
    return (value || "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function normalizeDomain(value) {
    if (!value) return "";
    let dom = String(value).trim().toLowerCase().replace(/,/g, ".");
    try {
      if (!dom.startsWith("http://") && !dom.startsWith("https://") && !dom.startsWith("//")) {
        dom = "https://" + dom;
      }
      const parsed = new URL(dom);
      dom = parsed.hostname || parsed.pathname;
    } catch {
      dom = dom.replace(/^https?:\/\//, "").replace(/^\/\//, "").split("/")[0].split(":")[0];
    }
    dom = dom.replace(/^www\d*\./, "").split("/")[0].split(":")[0].trim();
    return dom;
  }

  function cleanCompanyName(rawCompany) {
    if (!rawCompany) return "";
    let text = String(rawCompany).trim();
    // Strip employee count suffixes like "· 150 employees" or "• 50 employees"
    text = text.replace(/[·•|].*?(?:employees|people|workers|emp).*$/i, "");
    // Strip standalone employee count phrases
    text = text.replace(/[-–—]\s*\d+[\d,]*\s*(?:employees|people|emp).*$/i, "");
    // Strip trailing parenthesis metadata like "(YC W21)" or "(formerly XYZ)"
    text = text.replace(/\s*\((?:formerly|yc|acquired|seed|series\s+[a-z]).*?\)/gi, "");
    // Strip trailing punctuation
    text = text.replace(/[·•|–—-]+$/, "").trim();
    return cleanText(text);
  }

  function extractCompanyFromLinkedInUrl(url) {
    if (!url) return "";
    const match = url.match(/linkedin\.com\/company\/([^/?#]+)/i);
    if (!match) return "";
    let slug = decodeURIComponent(match[1]).trim();
    slug = slug.replace(/^www\./i, "");
    slug = slug.replace(/\.(?:com|org|io|net|co|edu|gov)$/i, "");
    slug = slug.replace(/[-_.]+/g, " ").trim();
    if (!slug) return "";
    return slug
      .split(/\s+/)
      .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
      .join(" ");
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
        ?.addEventListener(
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

    if (summaryElement) {
      if (typeof summaryElement.replaceChildren === "function") {
        summaryElement.replaceChildren();
      } else {
        summaryElement.innerHTML = "";
      }

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

          if (typeof metric.append === "function") {
            metric.append(strong, span);
          } else {
            metric.appendChild(strong);
            metric.appendChild(span);
          }

          summaryElement.appendChild(
            metric
          );
        }
      );
    }

    const list =
      panel.querySelector(
        "#contact-checker-activity-list"
      );

    if (list) {
      if (typeof list.replaceChildren === "function") {
        list.replaceChildren();
      } else {
        list.innerHTML = "";
      }

      const entries =
        state.activityLog.slice(-120);

      entries.forEach(entry => {
        const row =
          document.createElement("div");

        row.className =
          `contact-checker-activity-row ${entry.level || "info"}`;

        const time =
          document.createElement("span");

        time.className =
          "contact-checker-activity-time";

        time.textContent =
          activityClock(entry.timestamp);

        const event =
          document.createElement("span");

        event.className =
          "contact-checker-activity-event";

        event.textContent =
          entry.event || "";

        const message =
          document.createElement("span");

        message.className =
          "contact-checker-activity-message";

        message.textContent =
          entry.message || "";

        if (typeof row.append === "function") {
          row.append(
            time,
            event,
            message
          );
        } else {
          row.appendChild(time);
          row.appendChild(event);
          row.appendChild(message);
        }

        list.appendChild(row);
      });
    }

    if (state.activityPanelOpen && list) {
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
  // APOLLO DOMAIN COLUMN VISIBILITY
  // ============================================================

  function isApolloDomainColumnVisible() {
    if (document.querySelector('[data-id="account.domain"]')) {
      return true;
    }

    const headers = Array.from(
      document.querySelectorAll('[role="columnheader"], th')
    );

    for (const header of headers) {
      const label = cleanText(
        header.innerText ||
        header.textContent ||
        header.getAttribute("aria-label") ||
        ""
      ).toLowerCase();

      if (
        label.includes("domain") ||
        label === "domain" ||
        label.includes("company · domain") ||
        label.includes("company domain") ||
        label === "email domain" ||
        label === "primary domain"
      ) {
        return true;
      }

      const dataId = (
        header.getAttribute("data-id") ||
        header.closest("[data-id]")?.getAttribute("data-id") ||
        ""
      ).toLowerCase();

      if (dataId.includes("domain") || dataId === "account.domain") {
        return true;
      }
    }

    return Boolean(document.querySelector('[data-id="account.domain"]'));
  }

  function updateDomainColumnWarning() {
    const bannerId = "contact-checker-domain-warning";
    let banner = document.getElementById(bannerId);
    const onPeopleTable = getApolloContactLinks().length > 0;
    const domainVisible = isApolloDomainColumnVisible();

    if (!state.active || !onPeopleTable || domainVisible) {
      banner?.remove();
      state.domainColumnWarningLogged = false;
      return;
    }

    if (!banner) {
      banner = document.createElement("div");
      banner.id = bannerId;
      banner.setAttribute("data-contact-checker", "true");
      banner.innerHTML = `
        <strong>Add Apollo Domain column</strong>
        <span>Open <b>Columns</b> (+) on this table and enable <b>Domain</b>. Duplicate detection uses that column first; website-only is less accurate.</span>
      `;

      const controls = document.getElementById("contact-checker-controls");
      if (controls?.parentNode) {
        controls.parentNode.insertBefore(banner, controls);
      } else {
        document.body.appendChild(banner);
      }
    }

    if (!state.domainColumnWarningLogged) {
      state.domainColumnWarningLogged = true;
      addActivity(
        "DOMAIN_COLUMN_HIDDEN",
        "Apollo Domain column is hidden — enable it via Columns (+) for accurate duplicate detection.",
        "warning"
      );
    }
  }

  // ============================================================
  // FIND CONTACT LINKS
  // ============================================================

  function getApolloContactLinks() {
    // 1. Specific contact name cell selectors (strictly within name column/cell)
    const specificSelectors = [
      '[data-id="contact.name"] a[href*="/people/"]',
      '[data-id="contact.name"] a[data-to*="/people/"]',
      '[data-id="contact.name"] a[href*="/contacts/"]',
      '[data-id="contact.name"] a[data-to*="/contacts/"]',
      '[data-id="contact.name"] a',
      '[data-testid="contact-name-cell"] a',
      '[data-interaction-boundary="Contact Name Cell"] a'
    ];

    let found = Array.from(
      document.querySelectorAll(specificSelectors.join(","))
    );

    // 2. Fallback only if no specific contact name elements found
    if (!found.length) {
      const fallbackSelectors = [
        '[role="row"] a[href*="/contacts/"]',
        '[role="row"] a[data-to*="/contacts/"]',
        '[role="row"] a[href*="/people/"]',
        '[role="row"] a[data-to*="/people/"]',
        '[role="row"] a[href*="#/people/"]',
        '[role="row"] a[href*="#/contacts/"]',
        'tr a[href*="/contacts/"]',
        'tr a[data-to*="/contacts/"]',
        'tr a[href*="/people/"]',
        'tr a[data-to*="/people/"]',
        '[id^="table-row-"] a[href*="/people/"]',
        '[id^="table-row-"] a[data-to*="/people/"]'
      ];
      found = Array.from(
        document.querySelectorAll(fallbackSelectors.join(","))
      );
    }

    const uniqueLinks = [];
    const seen = new Set();

    for (const link of found) {
      if (seen.has(link)) continue;
      seen.add(link);

      // Exclude action buttons, exit-to-app icons, or header controls
      if (
        link.closest('[data-id="actions"]') ||
        link.closest('[data-interaction-boundary="People Finder - Actions Cell"]') ||
        link.closest('[data-id="leftActions"]') ||
        link.getAttribute("data-icon-button-variant") ||
        link.closest('[role="columnheader"], thead, [role="presentation"]')
      ) {
        continue;
      }

      // Must not be an icon-only button with no name text
      const text = cleanText(link.innerText || link.textContent || "");
      if (!text && link.querySelector('.mdi-exit-to-app, .apollo-icon, svg, i')) {
        continue;
      }

      const href = link.getAttribute("href") || link.getAttribute("data-to") || "";
      // Exclude navigation tabs or search filters
      if (
        href.endsWith("/people") ||
        href.endsWith("/contacts") ||
        (href.includes("?") && !href.includes("/people/") && !href.includes("/contacts/"))
      ) {
        continue;
      }

      if (link.closest('[role="row"], tr, .zp_DZKPa, [id^="table-row-"]')) {
        uniqueLinks.push(link);
      }
    }

    return uniqueLinks;
  }

  // ============================================================
  // GET TEMPORARY APOLLO KEY
  // ============================================================

  function getContactKey(link, index, name = "", company = "") {
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

    const normName = cleanText(name).toLowerCase().replace(/[^a-z0-9]/g, "");
    const normComp = cleanCompanyName(company).toLowerCase().replace(/[^a-z0-9]/g, "");
    if (normName && normComp) {
      return `apollo-${normName}_${normComp}`;
    }

    return `apollo-row-${index}`;
  }


  // ============================================================
  // FIND A ROW CELL BY APOLLO COLUMN HEADER (Cached Snapshot O(1))
  // ============================================================

  function getApolloHeadersList() {
    const headers = Array.from(
      document.querySelectorAll(
        '[role="columnheader"], th'
      )
    );

    return headers.map((header, idx) => ({
      element: header,
      index: idx,
      ariaColumnIndex: header.getAttribute("aria-colindex"),
      label: cleanText(
        header.innerText ||
        header.textContent ||
        header.getAttribute("aria-label") ||
        ""
      ).toLowerCase()
    }));
  }

  function findCellByHeader(
    row,
    cells,
    acceptedHeaders,
    headersList = null
  ) {
    const headers = headersList || getApolloHeadersList();

    const accepted = acceptedHeaders.map(
      value => cleanText(value).toLowerCase()
    );

    // 1. Pass 1: exact label match
    let header = headers.find(item => {
      const label = item.label !== undefined
        ? item.label
        : cleanText(
            item.innerText ||
            item.textContent ||
            item.getAttribute("aria-label") ||
            ""
          ).toLowerCase();

      return accepted.some(expected => label === expected);
    });

    // 2. Pass 2: partial label match, but filter out compound sub-columns when looking for company/title
    if (!header) {
      const compoundSubWords = [
        "links", "link", "domain", "social", "industries",
        "industry", "keywords", "keyword", "number of employees",
        "employees", "score"
      ];
      header = headers.find(item => {
        const label = item.label !== undefined
          ? item.label
          : cleanText(
              item.innerText ||
              item.textContent ||
              item.getAttribute("aria-label") ||
              ""
            ).toLowerCase();

        return accepted.some(expected => {
          if (!label.includes(expected)) return false;
          // If searching for "company", do not match compound headers like "company · links" or "company · domain"
          if (expected === "company" || expected === "company name" || expected === "account") {
            const hasCompound = compoundSubWords.some(sub => label.includes(sub));
            if (hasCompound) return false;
          }
          return true;
        });
      });
    }

    if (!header) {
      return null;
    }

    // Prefer ARIA column indices because Apollo can have a
    // checkbox/actions column before the visible data columns.
    const ariaColumnIndex = header.ariaColumnIndex !== undefined
      ? header.ariaColumnIndex
      : header.getAttribute?.("aria-colindex");

    if (ariaColumnIndex) {
      // Check gridcell wrapper first (new Apollo layout), then inner cell
      const indexedCell =
        row.querySelector(`[role="gridcell"][aria-colindex="${ariaColumnIndex}"]`) ||
        row.querySelector(`[role="cell"][aria-colindex="${ariaColumnIndex}"]`) ||
        row.querySelector(`td[aria-colindex="${ariaColumnIndex}"]`) ||
        row.querySelector(`[aria-colindex="${ariaColumnIndex}"]`);

      if (indexedCell) {
        // Prefer inner cell with data-id or ancestor cell with data-id/role; fall back to the matched element
        return indexedCell.querySelector('[role="cell"], [data-id]') ||
          indexedCell.closest('[role="cell"], [data-id], td') ||
          indexedCell;
      }
    }

    // Fallback: use visible header order against the cells array.
    // cells[] are the direct gridcell/cell children of the row (positionally stable).
    const headerIndex = header.index !== undefined ? header.index : headers.indexOf(header);
    const outerCell = headerIndex >= 0 ? cells[headerIndex] || null : null;
    if (!outerCell) return null;

    // If the direct child is a gridcell, return the inner data cell (has data-id / text)
    const innerCell = outerCell.querySelector('[role="cell"], [data-id]');
    return innerCell || outerCell;
  }

  // ============================================================
  // FIND ROW CELL BY DATA-ID (Supports Pinned & Split Sub-tables)
  // ============================================================

  function findCellByDataId(row, dataId, index = null) {
    if (!row && (index === null || index === undefined)) return null;

    // 1. Direct query inside row
    if (row) {
      let cell = row.querySelector(`[data-id="${dataId}"]`);
      if (cell) return cell;

      const rowId = row.getAttribute("id");
      if (rowId) {
        cell = document.querySelector(`[id="${rowId}"] [data-id="${dataId}"]`);
        if (cell) return cell;
      }
    }

    // 2. Query matching aria-rowindex
    const rowIndex = row?.getAttribute?.("aria-rowindex");
    if (rowIndex !== null && rowIndex !== undefined && rowIndex !== "") {
      const childMatch = document.querySelector(
        `[data-id="${dataId}"] [aria-rowindex="${rowIndex}"], [data-id="${dataId}"][aria-rowindex="${rowIndex}"]`
      );
      if (childMatch) {
        return childMatch.closest(`[data-id="${dataId}"]`) || childMatch;
      }

      const ancestorMatch = document.querySelector(
        `[role="row"][aria-rowindex="${rowIndex}"] [data-id="${dataId}"], [aria-rowindex="${rowIndex}"] [data-id="${dataId}"]`
      );
      if (ancestorMatch) return ancestorMatch;
    }

    // 3. Positional index fallback: Nth row -> Nth cell across table
    if (index !== null && index !== undefined && index >= 0) {
      const allCells = document.querySelectorAll(`[data-id="${dataId}"]`);
      if (allCells.length > index) {
        return allCells[index];
      }
    }

    return null;
  }

  // ============================================================
  // EXTRACT APOLLO ROW (Flexible & Resilient to Custom Column Layouts)
  // ============================================================

  function extractContact(link, index, headersList = null) {
    const row = link.closest('[role="row"], tr, .zp_DZKPa, [id^="table-row-"]');

    if (!row) {
      return null;
    }

    // Name: try link text first, nested text node / span, or enclosing name cell container
    const nameCellContainer = link.closest(
      '[data-id="contact.name"], [data-testid="contact-name-cell"], [data-interaction-boundary="Contact Name Cell"], [role="gridcell"], [role="cell"]'
    );
    let rawName = cleanText(
      link.innerText ||
      link.textContent ||
      link.querySelector('span, div, [class*="name"]')?.innerText ||
      link.querySelector('span, div, [class*="name"]')?.textContent ||
      nameCellContainer?.innerText ||
      nameCellContainer?.textContent ||
      ""
    );
    if (rawName.includes("\n")) {
      rawName = rawName.split("\n")[0].trim();
    }
    const name = cleanText(rawName);

    if (!name) {
      return null;
    }

    // ----------------------------------------------------------
    // Find all Apollo cells inside this row
    // ----------------------------------------------------------

    // Direct children of the row = one slot per column (positionally stable).
    // Apollo's column wrappers (e.g. zp_bhxgG) have no ARIA role — the
    // role="cell" / role="gridcell" elements sit one or two levels deeper.
    const cells = Array.from(row.children);

    const nameCellIndex = cells.findIndex(cell => cell.contains(link));

    // nameCell: the inner element with data-id="contact.name" (for badge placement)
    const nameCellOuter = nameCellIndex !== -1 ? cells[nameCellIndex] : null;
    const nameCell = (
      nameCellOuter?.querySelector('[data-id="contact.name"]') ||
      nameCellOuter?.querySelector('[role="cell"]') ||
      nameCellOuter ||
      link.closest('[data-id="contact.name"], [role="cell"], td') ||
      link.parentElement
    );

    // 1. Dynamic Title Detection (data-id first, then header or next cell)
    let titleCell = findCellByDataId(row, "contact.job_title", index) ||
      findCellByHeader(
        row,
        cells,
        ["title", "job title", "position", "role"],
        headersList
      );
    if (!titleCell && nameCellIndex !== -1 && nameCellIndex + 1 < cells.length) {
      const nextOuter = cells[nameCellIndex + 1];
      titleCell = nextOuter?.querySelector('[role="cell"], [data-id]') || nextOuter;
    }
    let rawTitle = cleanText(titleCell?.innerText || titleCell?.textContent || "");
    if (rawTitle.includes("\n")) {
      rawTitle = rawTitle.split("\n")[0].trim();
    }
    let jobTitle = rawTitle || "Unknown";

    // 2. Dynamic Company Detection (data-id first, then header, company link, or adjacent cell)
    let companyCell = findCellByDataId(row, "contact.account", index) ||
      findCellByDataId(row, "account.name", index) ||
      findCellByHeader(
        row,
        cells,
        ["company", "company name", "organization", "account"],
        headersList
      );
    if (!companyCell) {
      const rowId = row.getAttribute("id");
      const compLink = (rowId ? document.querySelector(`[id="${rowId}"]`) || row : row).querySelector(
        'a[href*="/accounts/"], a[href*="/companies/"], a[href*="/organizations/"], a[data-to*="/accounts/"], a[data-to*="/companies/"], a[data-to*="/organizations/"]'
      );
      if (compLink) {
        companyCell = compLink.closest('[role="cell"], td') || compLink.parentElement;
      }
    }
    if (!companyCell && nameCellIndex !== -1 && nameCellIndex + 2 < cells.length) {
      const compOuter = cells[nameCellIndex + 2];
      const candidateCell = compOuter?.querySelector('[role="cell"], [data-id]') || compOuter;
      const dataId = candidateCell?.getAttribute("data-id") || "";
      if (!dataId.includes("social") && !dataId.includes("domain") && !dataId.includes("industries") && !dataId.includes("keywords") && !dataId.includes("employees")) {
        companyCell = candidateCell;
      }
    }

    let compLink = (companyCell || row).querySelector(
      'a[href*="/accounts/"], a[href*="/companies/"], a[href*="/organizations/"], a[data-to*="/accounts/"], a[data-to*="/companies/"], a[data-to*="/organizations/"]'
    );
    let rawCompanyName = compLink
      ? (compLink.innerText || compLink.textContent)
      : (companyCell?.innerText || companyCell?.textContent || "");

    if (rawCompanyName.includes("\n")) {
      rawCompanyName = rawCompanyName.split("\n")[0].trim();
    }
    let company = cleanCompanyName(rawCompanyName);

    if (!company) {
      const fallbackCompLink = row.querySelector(
        'a[href*="/accounts/"], a[href*="/companies/"], a[href*="/organizations/"], a[data-to*="/accounts/"], a[data-to*="/companies/"], a[data-to*="/organizations/"]'
      );
      if (fallbackCompLink) {
        company = cleanCompanyName(fallbackCompLink.innerText || fallbackCompLink.textContent);
      }
    }

    // Modern Apollo Layout Fallback: Extract from Company LinkedIn in account.social
    if (!company) {
      const socialCell = findCellByDataId(row, "account.social", index) || row.querySelector('[data-id="account.social"]');
      const companyLinkedInLink = (socialCell || row).querySelector(
        'a[href*="linkedin.com/company/"], a[data-href*="linkedin.com/company/"]'
      );
      if (companyLinkedInLink) {
        const href = companyLinkedInLink.getAttribute("data-href") || companyLinkedInLink.getAttribute("href") || "";
        const fromSlug = extractCompanyFromLinkedInUrl(href);
        if (fromSlug) {
          company = cleanCompanyName(fromSlug);
        }
      }
    }

    // 3. Domain column (primary) + website link (fallback / second-pass candidate)
    let columnDomain = "";
    const domainCell = findCellByDataId(row, "account.domain", index) ||
      findCellByHeader(
        row,
        cells,
        ["company · domain", "domain", "company domain", "website"],
        headersList
      );
    if (domainCell) {
      const rawDomainText = cleanText(domainCell.innerText || domainCell.textContent || "");
      if (rawDomainText && rawDomainText !== "-") {
        const domainMatch = rawDomainText.match(/([a-z0-9][a-z0-9.-]*\.[a-z]{2,})/i);
        if (domainMatch) {
          columnDomain = extractRootDomain(domainMatch[1]);
        }
      }
    }

    let websiteUrl = "";
    const socialCell = findCellByDataId(row, "account.social", index) || row.querySelector('[data-id="account.social"]');
    const websiteLink =
      (socialCell || row).querySelector('a[aria-label="website link"]') ||
      row.querySelector('[data-id="account.social"] a[aria-label="website link"]') ||
      row.querySelector('a[aria-label="website link"]');
    if (websiteLink) {
      const href = (
        websiteLink.getAttribute("data-href") ||
        websiteLink.getAttribute("href") ||
        ""
      ).trim();
      if (href) {
        websiteUrl = href;
      }
    }

    // Fallback 1: globe icon without aria-label
    if (!websiteUrl) {
      const globeIcon = (socialCell || row).querySelector("a > .apollo-icon-link") || row.querySelector("a > .apollo-icon-link");
      if (globeIcon) {
        const parentLink = globeIcon.closest("a");
        const href = (
          parentLink?.getAttribute("data-href") ||
          parentLink?.getAttribute("href") ||
          ""
        ).trim();
        if (href && !href.includes("apollo.io")) {
          websiteUrl = href;
        }
      }
    }

    // Fallback 2: any external HTTP link in the row that is not social media or Apollo
    if (!websiteUrl) {
      const candidateLinks = Array.from((socialCell || row).querySelectorAll('a[href^="http://"], a[href^="https://"], a[data-href^="http://"], a[data-href^="https://"]'));
      const foundWebLink = candidateLinks.find(a => {
        const h = (a.getAttribute("data-href") || a.getAttribute("href") || "").toLowerCase();
        return h && !h.includes("apollo.io") && !h.includes("linkedin.com") && !h.includes("twitter.com") && !h.includes("x.com") && !h.includes("facebook.com");
      });
      if (foundWebLink) {
        websiteUrl = (foundWebLink.getAttribute("data-href") || foundWebLink.getAttribute("href") || "").trim();
      }
    }

    const websiteDomain = websiteUrl ? extractRootDomain(websiteUrl) : "";
    const lookupDomain = columnDomain || websiteDomain;

    // If company is still empty, derive from domain or Unknown
    if (!company) {
      if (lookupDomain) {
        const root = lookupDomain.split(".")[0].replace(/[-_.]+/g, " ");
        company = root.split(" ").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
      } else {
        company = "Unknown";
      }
    }

    // 4. Email Detection from row (if revealed / mailto link or email text)
    let email = "";
    const emailsCell = findCellByDataId(row, "contact.emails", index);
    const mailtoLink = (emailsCell || row).querySelector('a[href^="mailto:"]');
    if (mailtoLink) {
      email = (mailtoLink.getAttribute("href") || "").replace(/^mailto:/i, "").split("?")[0].trim();
    }
    if (!email && emailsCell) {
      const emailMatch = (emailsCell.textContent || "").match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/);
      if (emailMatch) {
        email = emailMatch[0].trim();
      }
    }
    if (!email) {
      const emailMatch = (row.textContent || "").match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/);
      if (emailMatch) {
        email = emailMatch[0].trim();
      }
    }

    // Location is optional
    const locationCell = findCellByDataId(row, "contact.location", index) ||
      findCellByHeader(
        row,
        cells,
        [
          "company location",
          "location",
          "headquarters",
          "headquarters location",
          "hq location"
        ],
        headersList
      );

    const location = cleanText(
      locationCell?.innerText ||
      locationCell?.textContent ||
      ""
    );

    // Number of Employees is optional.
    const employeesCell = findCellByDataId(row, "account.number_of_employees", index) ||
      findCellByHeader(
        row,
        cells,
        [
          "company · number of employees",
          "# employees",
          "employees",
          "number of employees",
          "company size",
          "num employees"
        ],
        headersList
      );

    let employeeCount = null;
    if (employeesCell) {
      const empText = cleanText(employeesCell.textContent || "");
      const empMatch = empText.replace(/,/g, "").match(/\d+/);
      if (empMatch) {
        employeeCount = parseInt(empMatch[0], 10);
      }
    }

    const key = getContactKey(
      link,
      index,
      name,
      company
    );

    const linkedinLink = row.querySelector('a[href*="linkedin.com/in/"]');
    const linkedinUrl = linkedinLink ? (linkedinLink.getAttribute("href") || "") : "";

    return {
      key,
      name,
      job_title: jobTitle,
      company,
      domain: lookupDomain,
      company_domain: columnDomain,
      website_link: websiteUrl,
      email: email,
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

    state.isUpdatingDom = true;
    try {
      const badges = contact.nameCell.querySelectorAll(
        "[data-contact-checker], .contact-checker-existing-badge, .contact-checker-required-badge, .contact-checker-ignored-badge"
      );
      badges.forEach(b => b.remove());
    } finally {
      state.isUpdatingDom = false;
    }
  }

  function setContactBadge(contact, className, text, title, bgColor = "") {
    if (!contact?.link) return;
    const nameCell = contact.nameCell || contact.link.closest('[role="cell"], td') || contact.link.parentElement;
    if (!nameCell) return;

    const existingBadge = nameCell.querySelector(".contact-checker-existing-badge, .contact-checker-required-badge, .contact-checker-ignored-badge");
    if (existingBadge) {
      if (existingBadge.className === className && existingBadge.textContent === text) {
        if (title) existingBadge.title = title;
        return; // Idempotent: already has the exact badge, do not touch DOM
      }
      state.isUpdatingDom = true;
      try {
        existingBadge.remove();
      } finally {
        state.isUpdatingDom = false;
      }
    }

    state.isUpdatingDom = true;
    try {
      const badge = document.createElement("span");
      badge.className = className;
      badge.setAttribute("data-contact-checker", "true");
      badge.textContent = text;
      if (title) badge.title = title;
      if (bgColor) badge.style.background = bgColor;

      contact.link.insertAdjacentElement("afterend", badge);
    } finally {
      state.isUpdatingDom = false;
    }
  }

  function highlightContact(
    contact,
    result
  ) {
    const row = contact.row;

    if (row) {
      if (row.classList.contains("contact-checker-required-row")) {
        row.classList.remove("contact-checker-required-row");
      }
      if (!row.classList.contains("contact-checker-existing")) {
        row.classList.add("contact-checker-existing");
      }
      state.highlightedRows.add(row);
    }

    // Remove from required contacts if previously recorded
    if (state.requiredContactsAll.has(contact.key)) {
      // DO NOT delete from requiredContactsAll to retain Collected Total
      // state.requiredContactsAll.delete(contact.key);

      // Instead, mark as synced so we ignore it for saving to DB again
      state.syncedLeadKeys.add(contact.key);
    }

    const title = result.email ? `CRM email: ${result.email}` : "";
    setContactBadge(contact, "contact-checker-existing-badge", "✓ Existing", title);
  }

  // ============================================================
  // PERSIST REQUIRED CONTACTS ACROSS RELOADS
  // ============================================================


  // ============================================================
  // PERSIST REQUIRED CONTACTS ACROSS RELOADS
  // ============================================================

  function loadStoredRequiredContacts() {
    if (!chrome?.storage?.local) {
      return;
    }

    chrome.storage.local.get(
      [REQUIRED_CONTACTS_STORAGE_KEY, TITLE_GUARDRAIL_STORAGE_KEY, INDIAN_GUARDRAIL_STORAGE_KEY, BATCH_NUMBER_STORAGE_KEY, BATCH_NAME_STORAGE_KEY],
      result => {
        if (result?.[BATCH_NAME_STORAGE_KEY]) {
          state.batchName = String(result[BATCH_NAME_STORAGE_KEY]).trim();
        } else if (result?.[BATCH_NUMBER_STORAGE_KEY]) {
          state.batchNumber = Number(result[BATCH_NUMBER_STORAGE_KEY]) || 1;
          state.batchName = `batch_${state.batchNumber}`;
        }
        if (result?.[TITLE_GUARDRAIL_STORAGE_KEY] !== undefined) {
          state.titleGuardrailEnabled =
            result[TITLE_GUARDRAIL_STORAGE_KEY] !== false;
        } else {
          state.titleGuardrailEnabled = true;
        }
        if (result?.[INDIAN_GUARDRAIL_STORAGE_KEY] !== undefined) {
          state.indianGuardrailEnabled =
            result[INDIAN_GUARDRAIL_STORAGE_KEY] !== false;
        } else {
          state.indianGuardrailEnabled = true;
        }

        const stored =
          result?.[REQUIRED_CONTACTS_STORAGE_KEY];

        state.requiredCompanyMap.clear();

        if (Array.isArray(stored) && stored.length) {
          stored.forEach(([key, value]) => {
            state.requiredContactsAll.set(key, value);
            state.syncedLeadKeys.add(key); // Mark as already synced so we don't re-upload on refresh
            const compKey = getCompanyDedupeKey(value.company, value.domain);
            if (compKey) {
              state.requiredCompanyMap.set(compKey, {
                key: key,
                apollo_id: value.apollo_id || "",
                name: value.name || `${value.first_name || ""} ${value.last_name || ""}`.trim(),
                company: value.company
              });
            }
          });
          // Call save to handle edge cases, but it will be a fast O(1) no-op if all are in syncedLeadKeys
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

    if (state.hasUnsavedRequiredContacts) {
      chrome.storage.local.set({
        [REQUIRED_CONTACTS_STORAGE_KEY]: contactsList
      });
      state.hasUnsavedRequiredContacts = false;
    }

    // Only send contacts that have not been synced yet and are not currently in-flight
    const contactsToSync = contactsList
      .filter(([key]) => !state.syncedLeadKeys.has(key) && !state.syncingLeadKeys.has(key))
      .map(([key, c]) => ({
        apollo_id: c.apollo_id || "",
        name: c.name || "",
        first_name: c.first_name || "",
        last_name: c.last_name || "",
        job_title: c.job_title || "",
        company: c.company || "",
        domain: c.domain || "",
        company_domain: c.company_domain || c.domain || "",
        website_link: c.website_link || (c.domain ? `https://${c.domain}` : ""),
        location: c.location || "",
        linkedin_url: c.linkedin_url || "",
        apollo_profile_url: c.apollo_profile_url || "",
        segment: c.segment || "Required_Lead",
        _key: key
      }));

    if (chrome?.runtime?.sendMessage && contactsToSync.length > 0) {
      contactsToSync.forEach(c => state.syncingLeadKeys.add(c._key));

      const activeBatch = state.batchName || `batch_${state.batchNumber || 1}`;
      chrome.runtime.sendMessage({
        type: "SYNC_SAVED_LEADS",
        batch: activeBatch,
        contacts: contactsToSync,
        replace_all: false
      }, (res) => {
        const lastErr = chrome.runtime?.lastError;
        if (!lastErr && res?.success) {
          contactsToSync.forEach(c => {
            state.syncingLeadKeys.delete(c._key);
            state.syncedLeadKeys.add(c._key);
          });
          contactCheckerLog(`Synced ${contactsToSync.length} lead(s) to MySQL apollo_saved_leads under ${activeBatch}`);
        } else {
          // If error or disconnected, release so it can retry later
          contactsToSync.forEach(c => state.syncingLeadKeys.delete(c._key));
          contactCheckerLog(`Notice: sync to MySQL delayed or failed: ${lastErr?.message || res?.error || "network unavailable"}`);
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

  const MULTI_PART_TLDS_JS = new Set([
    "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "net.nz", "org.nz",
    "co.in", "net.in", "org.in", "gen.in", "firm.in",
    "co.za", "org.za",
    "com.br", "org.br",
    "com.sg", "org.sg",
    "co.jp", "ne.jp", "or.jp",
    "gc.ca"
  ]);

  function extractRootDomain(rawUrlOrDomain) {
    if (!rawUrlOrDomain) return "";
    let dom = String(rawUrlOrDomain).trim().toLowerCase().replace(/,/g, ".");

    // Strip mailto: prefix
    if (dom.startsWith("mailto:")) {
      dom = dom.slice("mailto:".length);
    }

    // If an email address is passed, take the domain portion after '@'
    if (dom.includes("@")) {
      dom = dom.split("@").pop();
    }

    // Handle protocol-relative URLs (e.g. '//www.example.com/path')
    if (dom.startsWith("//")) {
      dom = dom.replace(/^\/+/, "");
    }

    // Remove protocol and query
    if (dom.includes("://")) {
      try {
        const u = new URL(dom.startsWith("http") ? dom : `https://${dom}`);
        dom = u.hostname || dom;
      } catch (e) {
        dom = dom.replace(/^[a-zA-Z]+:\/\//, "");
      }
    }

    // Strip ports, paths, query
    dom = dom.split("/")[0].split(":")[0].split("?")[0].trim();
    // Strip leading www.
    dom = dom.replace(/^www\d*\./, "");
    // Strip trailing FQDN dots (e.g. 'example.com.')
    dom = dom.replace(/\.+$/, "");

    const parts = dom.split(".");
    if (parts.length <= 2) {
      return dom;
    }

    const lastTwo = `${parts[parts.length - 2]}.${parts[parts.length - 1]}`;
    if (MULTI_PART_TLDS_JS.has(lastTwo)) {
      if (parts.length >= 3) {
        return `${parts[parts.length - 3]}.${lastTwo}`;
      }
      return dom;
    }

    return `${parts[parts.length - 2]}.${parts[parts.length - 1]}`;
  }

  function getSeniorityScore(jobTitle) {
    if (!jobTitle) return 0;
    const t = String(jobTitle).toLowerCase().trim();
    if (/\b(founder|co-founder|ceo|cto|cmo|cro|coo|cfo|cpo|cio|chief|owner|managing partner|chair)\b/.test(t) || /(?<!vice\s)(?<!vice-)(?<!asst\s)(?<!assistant\s)\bpresident\b/.test(t)) {
      return 100; // Tier 1: C-Suite / Founders
    }
    if (/\b(vp|svp|evp|avp|vice president|head of|general manager|gm)\b/.test(t) || t.startsWith("head ") || t.startsWith("head,") || t.startsWith("head -")) {
      return 80; // Tier 2: Vice President / Head of
    }
    if (/\b(director|principal|lead architect|group product manager|staff)\b/.test(t)) {
      return 60; // Tier 3: Director / Principal
    }
    if (/\b(manager|team lead|lead|senior|sr\.?)\b/.test(t)) {
      return 40; // Tier 4: Manager / Senior
    }
    return 20; // Tier 5: Individual contributor
  }

  function getCompanyDedupeKey(companyName, rawDomain = "") {
    const rootDomain = extractRootDomain(rawDomain);
    if (rootDomain) return rootDomain;

    if (!companyName) return "";
    let clean = String(companyName).toLowerCase().trim();
    clean = clean.replace(/[·•|].*?(?:employees|people|workers|emp).*$/i, "");
    clean = clean.replace(/[-–—]\s*\d+[\d,]*\s*(?:employees|people|emp).*$/i, "");
    clean = clean.replace(/\s*\((?:formerly|yc|acquired|seed|series\s+[a-z]).*?\)/i, "");
    clean = clean.replace(/[^\w\s.-]/g, " ").trim();
    const legalWords = new Set(["inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "technologies", "technology", "tech", "services", "solutions", "group", "holdings", "holding", "pvt", "private", "gmbh", "co", "company", "international", "global", "consulting", "enterprises", "media", "labs"]);
    const words = clean.split(/\s+/).filter(w => !legalWords.has(w.replace(/\.$/, "")));
    return words.join("").replace(/[^a-z0-9]/g, "") || clean.replace(/[^a-z0-9]/g, "");
  }

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
    const rawDomain = contact.company_domain || contact.domain || result?.matched_domain || "";
    const rootDomain = extractRootDomain(rawDomain);
    const apolloUrl = apolloId ? `https://app.apollo.io/#/people/${apolloId}` : "";
    const compKey = getCompanyDedupeKey(contact.company, rawDomain);
    const incomingScore = getSeniorityScore(contact.job_title);

    // Seniority-based Lead Election: 1 unique lead per company in local storage
    if (compKey && state.requiredCompanyMap.has(compKey)) {
      const prevElected = state.requiredCompanyMap.get(compKey);
      if (prevElected && prevElected.key && prevElected.key !== contact.key) {
        const prevScore = prevElected.score || 0;
        if (incomingScore > prevScore) {
          // Replace lower-ranking previous lead with higher-ranking decision maker
          state.requiredContactsAll.delete(prevElected.key);
          state.syncedLeadKeys.delete(prevElected.key);
          state.hasUnsavedRequiredContacts = true;
        } else {
          // Current contact is lower or equal rank -> do not replace existing superior lead
          return;
        }
      }
    }

    const isPending = (result?.guardrail_status === "pending_title_eval") || (result?.segment === "Pending_Evaluation");
    const segmentVal = result?.segment || (isPending ? "Pending_Evaluation" : "Required_Lead");
    const websiteLink = contact.website_link || (rootDomain ? `https://${rootDomain}` : "");

    state.requiredContactsAll.set(contact.key, {
      apollo_id: apolloId,
      first_name: firstName,
      last_name: lastName,
      name: contact.name,
      job_title: contact.job_title,
      company: contact.company,
      domain: rootDomain || rawDomain,
      company_domain: contact.company_domain || rootDomain || rawDomain,
      website_link: websiteLink,
      location: contact.location || "",
      linkedin_url: contact.linkedin_url || "",
      apollo_profile_url: apolloUrl,
      segment: segmentVal,
      is_pending_eval: isPending,
      seniority_score: incomingScore
    });
    state.hasUnsavedRequiredContacts = true;

    if (compKey) {
      state.requiredCompanyMap.set(compKey, {
        key: contact.key,
        name: contact.name,
        title: contact.job_title,
        score: incomingScore,
        company: contact.company,
        domain: rootDomain || rawDomain,
        website_link: websiteLink
      });
    }
  }

  function markRequired(contact, result) {
    const row = contact.row;

    if (row) {
      if (row.classList.contains("contact-checker-existing")) {
        row.classList.remove("contact-checker-existing");
      }
      if (!row.classList.contains("contact-checker-required-row")) {
        row.classList.add("contact-checker-required-row");
      }
      state.highlightedRows.add(row);
    }

    recordRequiredContact(contact, result);

    const isPending = (result?.guardrail_status === "pending_title_eval") || (result?.segment === "Pending_Evaluation");
    let title = isPending
      ? `Novel Title Provisionally Accepted (Pending 50-item AI batch evaluation)`
      : "Target Lead: Net-new domain (Ready for CSV export)";

    if (result?.guardrail_reason) {
      const roleStr = result.role_type ? ` [${result.role_type}]` : "";
      title = isPending
        ? result.guardrail_reason
        : `Tier ${result.tier || ""}${roleStr}: ${result.guardrail_reason}`;
    }

    setContactBadge(contact, "contact-checker-required-badge", "★ Required Lead", title);
  }

  function markIgnored(contact, result) {
    const row = contact.row;

    if (row) {
      if (row.classList.contains("contact-checker-existing")) {
        row.classList.remove("contact-checker-existing");
      }
      if (row.classList.contains("contact-checker-required-row")) {
        row.classList.remove("contact-checker-required-row");
      }
      state.highlightedRows.delete(row);
    }

    // Passive browsing or re-evaluations must NEVER delete an already-collected
    // required lead from local storage (state.requiredContactsAll)!
    // Collected leads are only pruned upon explicit user action or higher seniority election.

    let badgeText = "⊘ Ignored";
    let badgeBg = "#64748b";
    let badgeTitle = result?.guardrail_reason || "Ignored by guardrail rules.";

    if (result?.guardrail_status === "contact_already_in_db") {
      badgeText = "⊘ Existing Contact";
      badgeBg = "#475569";
      badgeTitle = result.guardrail_reason || "Contact (full name & domain) already exists in CRM database.";
    } else if (result?.guardrail_status === "email_already_in_db") {
      badgeText = "⊘ Existing Email";
      badgeBg = "#475569";
      badgeTitle = result.guardrail_reason || "Contact email already exists in CRM database.";
    } else if (result?.guardrail_status === "domain_already_in_db") {
      badgeText = "⊘ Existing Domain";
      badgeBg = "#475569";
      badgeTitle = result.guardrail_reason || "Company domain already exists in CRM database with existing contacts.";
    } else if (result?.guardrail_status === "indian_name_disqualified") {
      badgeText = "⊘ Indian Origin";
      badgeBg = "#ef4444";
      badgeTitle = result.guardrail_reason || "Excluded: Pure Indian Name Origin.";
    } else if (result?.guardrail_status === "not_recognized_title" || result?.guardrail_status === "not_recognized") {
      badgeText = "⊘ Not Recognized";
      badgeBg = "#64748b";
      badgeTitle = result.guardrail_reason || "Title is not recognized in our database.";
    } else if (result?.guardrail_status === "disqualified_title") {
      badgeText = "⊘ Excluded: Title";
      badgeBg = "#64748b";
      badgeTitle = result.guardrail_reason || "Excluded: Title belongs to non-required segment.";
    } else if (result?.guardrail_status === "company_limit_reached") {
      badgeText = "⊘ 1/Company Max";
      badgeBg = "#6b7280";
      badgeTitle = result.guardrail_reason || "Only 1 contact per company is allowed.";
    }

    setContactBadge(contact, "contact-checker-ignored-badge", badgeText, badgeTitle, badgeBg);
  }

  function applyContactResult(
    contact,
    result
  ) {
    // 1. If this contact was ALREADY collected into required contacts in this session:
    // Retain its required status, green badge, and local storage record across all page navigations!
    if (state.requiredContactsAll.has(contact.key)) {
      markRequired(
        contact,
        result
      );
      return;
    }

    if (result.exists) {
      highlightContact(
        contact,
        result
      );
    } else if (result.required && !result.ignored) {
      // Cross-page Local Storage Deduplication: check if this company is already in local storage from an earlier page
      const compKey = getCompanyDedupeKey(contact.company, result?.matched_domain || contact.domain);
      const existingCompanyLead = state.requiredCompanyMap.get(compKey);

      const normCurrentKey = String(contact.key || "").replace(/^apollo-/, "").toLowerCase();
      const normExistingKey = existingCompanyLead
        ? String(existingCompanyLead.key || existingCompanyLead.apollo_id || "").replace(/^apollo-/, "").toLowerCase()
        : "";

      if (existingCompanyLead && normExistingKey && normExistingKey !== normCurrentKey) {
        // A different contact from this company was already saved as required on an earlier page
        result.required = false;
        result.ignored = true;
        result.guardrail_status = "company_limit_reached";
        result.guardrail_reason = `Company '${contact.company}' already has a lead in local storage (${existingCompanyLead.name}). Max 1 contact per company.`;
        markIgnored(
          contact,
          result
        );
      } else {
        markRequired(
          contact,
          result
        );
      }
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
      // If contact is in collected required contacts, it counts as required on page
      if (state.requiredContactsAll.has(contact.key)) {
        return true;
      }
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
    let text = String(value ?? "").trim();

    // Prevent CSV formula injection in spreadsheet software (Excel, Sheets)
    if (/^[=+\-@\t\r]/.test(text)) {
      text = "'" + text;
    }

    if (/[",\r\n]/.test(text)) {
      return `"${text.replace(/"/g, '""')}"`;
    }

    return text;
  }

  function buildRequiredContactsCSV() {
    const rawRows = Array.from(
      state.requiredContactsAll.values()
    );

    // Pre-Export Single-Pass Strict Sweep: Exactly 1 Lead per Canonical Root Domain
    const uniqueCompanyMap = new Map();
    rawRows.forEach(row => {
      const rootDom = extractRootDomain(row.domain);
      const dedupeKey = rootDom || getCompanyDedupeKey(row.company);
      const score = row.seniority_score || getSeniorityScore(row.job_title);

      if (!uniqueCompanyMap.has(dedupeKey)) {
        uniqueCompanyMap.set(dedupeKey, { row, score });
      } else {
        const existing = uniqueCompanyMap.get(dedupeKey);
        if (score > existing.score) {
          uniqueCompanyMap.set(dedupeKey, { row, score });
        }
      }
    });

    const rows = Array.from(uniqueCompanyMap.values()).map(item => item.row);

    const header = [
      "First Name",
      "Last Name",
      "Title",
      "Company",
      "Company Domain",
      "Website Link",
      "Location",
      "Person Linkedin Url",
      "Apollo Profile Url"
    ];

    const lines = [header.join(",")];

    rows.forEach(row => {
      const rootDom = extractRootDomain(row.domain) || row.domain || "";
      const webLink = row.website_link || (rootDom ? `https://${rootDom}` : "");

      lines.push(
        [
          row.first_name || "",
          row.last_name || "",
          row.job_title || "",
          row.company || "",
          rootDom,
          webLink,
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

  function checkAndFlushPendingTitles(force = false, callback = null) {
    if (state.isEvaluatingBatch) {
      if (callback) callback();
      return;
    }

    // Cooldown guard to avoid rapid consecutive triggers in a loop
    if (!force && Date.now() - state.lastEvaluatedPendingTimestamp < 10000) {
      if (callback) callback();
      return;
    }

    const pendingContacts = [];
    const pendingTitles = new Set();
    const pendingNames = new Set();

    state.requiredContactsAll.forEach((c, key) => {
      if (c.segment === "Pending_Evaluation" || c.is_pending_eval || c.is_pending_indian_eval) {
        pendingContacts.push({ key, contact: c });
        if (c.job_title) pendingTitles.add(c.job_title);
        if (c.name) pendingNames.add(c.name);
      }
    });

    if (pendingContacts.length === 0) {
      if (callback) callback();
      return;
    }

    // Only auto-trigger when count hits 50 or force is requested (e.g. before export)
    if (!force && pendingContacts.length < 50) {
      if (callback) callback();
      return;
    }

    state.lastEvaluatedPendingTimestamp = Date.now();
    const titlesList = Array.from(pendingTitles);
    const namesList = Array.from(pendingNames);
    showStatus(`⚡ 50-Item Batch: Queuing ${pendingContacts.length} pending titles & names to DB (no AI)...`, 0, true);
    state.isEvaluatingBatch = true;

    // Bug #2 Fix: Helper to rescue contacts when AI batch evaluation fails.
    function rescuePendingContacts(reason) {
      state.isEvaluatingBatch = false;
      addActivity("AI_BATCH_FAILED", `Pending queue failed (${reason}). Resetting ${pendingContacts.length} contact(s) to re-evaluate on next scan.`, "error");
      pendingContacts.forEach(({ key, contact }) => {
        contact.is_pending_eval = false;
        contact.is_pending_indian_eval = false;
        state.requiredContactsAll.set(key, contact);
        state.checkedContacts.delete(key);
        state.pendingContacts.delete(key);
      });
      showStatus(`⚠ Pending queue failed — ${pendingContacts.length} contacts reset for retry`, 3000, false);
      if (callback) callback();
    }

    try {
      chrome.runtime.sendMessage({
        type: "EVALUATE_PENDING_TITLES",
        titles: titlesList,
        names: namesList,
        batch: state.batchName || "batch_1"
      }, (response) => {
        if (chrome.runtime.lastError) {
          rescuePendingContacts(chrome.runtime.lastError.message);
          return;
        }
        state.isEvaluatingBatch = false;
        if (!response?.success) {
          rescuePendingContacts(response?.error || "backend error");
          return;
        }

        const titleEvals = response.title_results || response.results || {};
        const nameEvals = response.name_results || {};
        let excludedTitlesCount = 0;
        let excludedIndianCount = 0;
        let keptCount = 0;

        pendingContacts.forEach(({ key, contact }) => {
          const titleEval = titleEvals[contact.job_title];
          const nameEval = nameEvals[contact.name];

          let shouldExclude = false;
          let excludeReason = "";

          const titleLlmFailed = titleEval?.status === "llm_failed";
          if (titleLlmFailed) {
            contact.is_pending_eval = true;
            contact.segment = "Pending_Evaluation";
            state.requiredContactsAll.set(key, contact);
            return;
          }

          // 1. Check Indian demographic evaluation
          if (nameEval && nameEval.is_indian === true) {
            shouldExclude = true;
            excludeReason = nameEval.reason || "Demographic Filter: Pure Indian Origin";
            excludedIndianCount++;
          }
          // 2. Check Job title hierarchy evaluation
          else if (titleEval && titleEval.required === false) {
            shouldExclude = true;
            excludeReason = titleEval.reason || "Disqualified Title";
            excludedTitlesCount++;
          }

          if (shouldExclude) {
            // Prune / delete from local storage
            state.requiredContactsAll.delete(key);
            const compKey = getCompanyDedupeKey(contact.company, contact.domain);
            if (compKey && state.requiredCompanyMap.get(compKey)?.key === key) {
              state.requiredCompanyMap.delete(compKey);
            }
          } else {
            // Keep and upgrade
            keptCount++;
            if (titleEval?.segment) {
              contact.segment = titleEval.segment;
            }
            contact.is_pending_eval = false;
            contact.is_pending_indian_eval = false;
            state.requiredContactsAll.set(key, contact);
          }
        });

        if (excludedTitlesCount > 0 || excludedIndianCount > 0 || keptCount > 0) {
          state.hasUnsavedRequiredContacts = true;
        }
        saveRequiredContactsNow();
        renderExportControls();

        // Refresh badges safely without recursion loop
        scheduleScan(100);

        const totalExcluded = excludedTitlesCount + excludedIndianCount;
        showStatus(`⚡ Evaluated ${pendingContacts.length} contacts: ${keptCount} kept, ${totalExcluded} excluded (${excludedTitlesCount} title, ${excludedIndianCount} demographic)!`, 5000);
        addActivity("PENDING_BATCH_EVALUATED", `Queued ${pendingContacts.length} pending contacts to DB: ${keptCount} kept locally, ${totalExcluded} excluded (${excludedTitlesCount} title, ${excludedIndianCount} definite Indian name). Run manage_batches [5]/[6] for LLM audit.`, "info", {
          total_evaluated: pendingContacts.length,
          kept: keptCount,
          excluded_titles: excludedTitlesCount,
          excluded_indian: excludedIndianCount
        });

        if (callback) callback();
      });
    } catch (sendErr) {
      rescuePendingContacts(`sendMessage threw: ${sendErr?.message || sendErr}`);
    }
  }

  function exportRequiredContactsCSV() {
    if (!state.requiredContactsAll.size) {
      showStatus("No required contacts collected yet");
      return;
    }

    // Flush any pending queue items and evaluate any pending novel titles before generating final CSV
    checkAndFlushPendingTitles(true, () => {
      const csv = buildRequiredContactsCSV();
      const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);

      const link = document.createElement("a");
      link.href = url;
      const batchTag = (state.batchName || "batch_1").replace(/[^a-zA-Z0-9_-]/g, "_");
      link.download = `required-contacts-${batchTag}-${Date.now()}.csv`;
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
    });
  }

  function deduplicateStoredContacts() {
    const totalBefore = state.requiredContactsAll.size;
    if (!totalBefore) {
      showStatus("No stored contacts to deduplicate", 2500);
      return;
    }

    const uniqueCompanies = new Map(); // compKey -> { key, score }
    const duplicateKeys = [];

    state.requiredContactsAll.forEach((contact, key) => {
      const compKey = getCompanyDedupeKey(contact.company, contact.domain);
      if (!compKey) {
        uniqueCompanies.set(key, { key, score: 0 });
        return;
      }

      const score = contact.seniority_score || getSeniorityScore(contact.job_title);
      if (uniqueCompanies.has(compKey)) {
        const existing = uniqueCompanies.get(compKey);
        if (score > existing.score) {
          // Incoming lead is higher-ranking (e.g. CEO vs Manager): discard previous lead
          duplicateKeys.push(existing.key);
          uniqueCompanies.set(compKey, { key, score });
        } else {
          // Existing lead has equal or higher rank: discard incoming lead
          duplicateKeys.push(key);
        }
      } else {
        uniqueCompanies.set(compKey, { key, score });
      }
    });

    if (duplicateKeys.length === 0) {
      showStatus(`✓ 100% Unique: All ${totalBefore} leads in local storage are already distinct companies!`, 3500);
      addActivity(
        "DEDUPLICATION_CHECK",
        `Checked ${totalBefore} leads in local storage. All are unique companies (0 duplicates found).`,
        "info"
      );
      return;
    }

    // Delete duplicates from state.requiredContactsAll, state.syncedLeadKeys, and state.syncingLeadKeys
    duplicateKeys.forEach(key => {
      state.requiredContactsAll.delete(key);
      state.syncedLeadKeys.delete(key);
      state.syncingLeadKeys.delete(key);
    });
    state.hasUnsavedRequiredContacts = true;

    // Rebuild state.requiredCompanyMap
    state.requiredCompanyMap.clear();
    state.requiredContactsAll.forEach((contact, key) => {
      const compKey = getCompanyDedupeKey(contact.company, contact.domain);
      if (compKey) {
        state.requiredCompanyMap.set(compKey, {
          key: contact.apollo_id || key,
          name: contact.name || `${contact.first_name || ""} ${contact.last_name || ""}`.trim(),
          company: contact.company
        });
      }
    });

    // Save cleaned list directly to chrome.storage.local
    saveRequiredContactsNow();

    const totalAfter = state.requiredContactsAll.size;
    renderExportControls();

    addActivity(
      "DEDUPLICATION_COMPLETE",
      `Removed ${duplicateKeys.length} duplicate company contact(s). ${totalAfter} unique companies remain in local storage.`,
      "warning",
      {
        duplicates_removed: duplicateKeys.length,
        unique_companies_remaining: totalAfter
      }
    );

    showStatus(
      `⚡ Pruned ${duplicateKeys.length} duplicate company leads! (${totalAfter} unique companies remain)`,
      4500
    );

    // Rescan active Apollo page so any duplicate row updates its badge to 1/Company Max
    state.checkedContacts.clear();
    state.currentContacts.clear();
    scanApollo();
  }

  function clearRequiredContactsList() {
    const count = state.requiredContactsAll.size;
    const prevBatch = state.batchName || `batch_${state.batchNumber || 1}`;

    state.requiredContactsAll.clear();
    state.requiredCompanyMap.clear();
    state.syncedLeadKeys.clear();
    state.syncingLeadKeys.clear();
    state.hasUnsavedRequiredContacts = false;

    if (chrome?.storage?.local) {
      chrome.storage.local.set({
        [BATCH_NUMBER_STORAGE_KEY]: state.batchNumber,
        [BATCH_NAME_STORAGE_KEY]: state.batchName
      });
      chrome.storage.local.remove(
        REQUIRED_CONTACTS_STORAGE_KEY
      );
    }

    addActivity(
      "REQUIRED_CONTACTS_CLEARED",
      `Cleared ${count} contact(s) from local list. Leads under '${prevBatch}' preserved in DB. Next scans will save under '${state.batchName}'.`,
      "info",
      {
        previous_batch: prevBatch,
        new_batch: state.batchName
      }
    );

    renderExportControls();
    showStatus(`List cleared. Next scans will save under '${state.batchName}' in Database.`, 3000);
  }

  function renderExportControls() {
    let controls = document.getElementById(
      "contact-checker-controls"
    );

    // If an older controls dock exists in DOM without the dedupe button, refresh it
    if (controls && !controls.querySelector("#contact-checker-dedupe-btn")) {
      controls.remove();
      controls = null;
    }

    if (!controls) {
      controls = document.createElement("div");
      controls.id = "contact-checker-controls";
      controls.innerHTML = `
        <span id="contact-checker-live-status" class="contact-checker-live-badge">✓ Ready</span>
        <div class="contact-checker-batch-pill" style="display:inline-flex;align-items:center;background:#1e293b;border:1px solid #334155;border-radius:6px;padding:2px 8px;gap:5px;">
          <span style="color:#94a3b8;font-size:11px;font-weight:700;">🏷️ Batch:</span>
          <input
            id="contact-checker-batch-input"
            type="text"
            value="${state.batchName || 'batch_1'}"
            placeholder="Batch Tag"
            title="Custom batch name tag for Database saves and CSV downloads"
            style="background:transparent;border:none;color:#38bdf8;font-weight:700;font-size:12px;width:110px;outline:none;"
          />
        </div>
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
          id="contact-checker-dedupe-btn"
          type="button"
          style="background: #eab308; color: #111827; font-weight: 700;"
          title="Scan and delete all duplicate contacts from the same company in local storage"
        >⚡ Deduplicate List</button>
        <button
          id="contact-checker-clear-required"
          type="button"
        >Clear List</button>
        <button
          id="contact-checker-activity-toggle"
          type="button"
        >Activity</button>
      `;

      const batchInput = controls.querySelector("#contact-checker-batch-input");
      if (batchInput) {
        batchInput.addEventListener("change", (e) => {
          const val = cleanText(e.target.value).replace(/[^a-zA-Z0-9_-]/g, "_") || "batch_1";
          state.batchName = val;
          batchInput.value = val;
          if (chrome?.storage?.local) {
            chrome.storage.local.set({ [BATCH_NAME_STORAGE_KEY]: val });
          }
          state.syncedLeadKeys.clear();
          saveRequiredContactsNow();
          showStatus(`✓ Batch set to '${val}' — syncing leads to MySQL 'apollo_saved_leads'`, 3500);
          addActivity("BATCH_RENAMED", `Batch name updated to '${val}'. Stored leads synced under this batch tag in database.`, "info", { batch: val });
        });
      }

      controls
        .querySelector(
          "#contact-checker-rescan-btn"
        )
        ?.addEventListener(
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
        ?.addEventListener(
          "click",
          exportRequiredContactsCSV
        );

      controls
        .querySelector(
          "#contact-checker-dedupe-btn"
        )
        ?.addEventListener(
          "click",
          deduplicateStoredContacts
        );

      controls
        .querySelector(
          "#contact-checker-clear-required"
        )
        ?.addEventListener(
          "click",
          clearRequiredContactsList
        );

      controls
        .querySelector(
          "#contact-checker-activity-toggle"
        )
        ?.addEventListener(
          "click",
          toggleActivityPanel
        );

      document.body.appendChild(controls);

      renderActivityPanel();
    }

    const batchInputExisting = controls.querySelector("#contact-checker-batch-input");
    if (batchInputExisting && document.activeElement !== batchInputExisting && batchInputExisting.value !== (state.batchName || "batch_1")) {
      batchInputExisting.value = state.batchName || "batch_1";
    }

    const visibleRequiredCount =
      getRequiredContacts().length;

    const totalCollected =
      state.requiredContactsAll.size;

    const countLabel = controls.querySelector(
      "#contact-checker-required-count"
    );

    const isScanning = state.pendingContacts.size > 0;
    const countText = isScanning
      ? `Required on page: ${visibleRequiredCount} (scanning ${state.pendingContacts.size}...) | Collected total: ${totalCollected}`
      : `Required on page: ${visibleRequiredCount} | Collected total: ${totalCollected}`;

    if (countLabel && countLabel.textContent !== countText) {
      countLabel.textContent = countText;
    }

    const exportButton = controls.querySelector(
      "#contact-checker-export-required"
    );

    if (exportButton) {
      exportButton.disabled = totalCollected === 0;
    }

    const dedupeButton = controls.querySelector(
      "#contact-checker-dedupe-btn"
    );

    if (dedupeButton) {
      dedupeButton.disabled = totalCollected === 0;
    }
  }

  // ============================================================
  // SCAN APOLLO
  // ============================================================

  function scanApollo() {
    if (!state.active) {
      return;
    }

    // Auto-expire pending requests older than 10s to prevent stuck/frozen rows
    const now = Date.now();
    for (const [k, ts] of state.pendingContacts.entries()) {
      if (now - ts > 10000) {
        state.pendingContacts.delete(k);
      }
    }

    const links = getApolloContactLinks();

    if (!links.length) {
      state.currentContacts.clear();
      updateDomainColumnWarning();
      renderExportControls();
      return;
    }

    const headersList = getApolloHeadersList();
    const contactsToCheck = [];
    const currentContacts = new Map();
    let failedExtractionCount = 0;

    links.forEach((link, index) => {
      let contact = null;
      try {
        contact = extractContact(link, index, headersList);
      } catch (err) {
        console.warn("[ContactChecker] Failed to extract contact at index", index, err);
      }

      if (!contact) {
        failedExtractionCount++;
        return;
      }

      if (currentContacts.has(contact.key)) {
        return;
      }

      currentContacts.set(contact.key, contact);

      // Already checked earlier on this page view
      if (state.checkedContacts.has(contact.key)) {
        const cached = state.checkedContacts.get(contact.key);
        if (cached) {
          applyContactResult(contact, cached);
        }
        return;
      }

      // Currently in flight
      if (state.pendingContacts.has(contact.key)) {
        return;
      }

      contactsToCheck.push(contact);
    });

    state.currentContacts = currentContacts;
    updateDomainColumnWarning();

    // Check if real data body rows actually exist in the table
    const tableBodyRows = document.querySelectorAll(
      '[id^="table-row-"], [role="rowgroup"] [role="row"]:not([role="columnheader"]), .zp_Gjvi9 [role="row"], tbody tr'
    );

    if (currentContacts.size === 0 && links.length > 0 && tableBodyRows.length > 0) {
      state.consecutiveExtractionFailures = (state.consecutiveExtractionFailures || 0) + 1;
      // Only warn user if extraction repeatedly failed across multiple scans (not transient loading)
      if (state.consecutiveExtractionFailures >= 3) {
        addActivity(
          "DOM_PARSE_FAILURE",
          `⚠ Apollo DOM changed? Found ${links.length} contact link(s) across ${tableBodyRows.length} rows but could not extract data from any of them. The scraper may be blind.`,
          "error"
        );
        showStatus("⚠ Apollo layout change detected — scraper may need an update", 5000, false);
      }
    } else if (currentContacts.size > 0) {
      state.consecutiveExtractionFailures = 0;
      // If layout warning is showing, dismiss it immediately
      const statusEl = document.getElementById("contact-checker-status");
      if (statusEl && statusEl.textContent.includes("layout change detected")) {
        statusEl.remove();
        clearTimeout(state.statusTimer);
      }
    }

    const currentSignature = Array.from(currentContacts.keys()).join("|");
    if (currentSignature && currentSignature !== state.lastLoggedPageSignature) {
      state.lastLoggedPageSignature = currentSignature;
      addActivity(
        "PAGE_SCANNED",
        `Apollo page scanned — ${currentContacts.size} contact(s) visible.`,
        "info",
        {
          visible_contacts: currentContacts.size,
          new_contacts_to_check: contactsToCheck.length
        }
      );
    }

    renderExportControls();

    if (!contactsToCheck.length) {
      return;
    }

    // Mark contacts as pending with current timestamp
    contactsToCheck.forEach(contact =>
      state.pendingContacts.set(contact.key, Date.now())
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
        contacts: contactsToCheck.length
      }
    );

    // Send batch to backend
    try {
      chrome.runtime.sendMessage(
        {
          type: "MATCH_APOLLO",
          batch: state.batchName || `batch_${state.batchNumber || 1}`,
          title_guardrail_enabled: state.titleGuardrailEnabled === true,
          indian_name_guardrail_enabled: state.indianGuardrailEnabled === true,
          contacts: contactsToCheck.map(contact => {
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
              company_domain: contact.company_domain || contact.domain || "",
              website_link: contact.website_link || "",
              email: contact.email || "",
              location: contact.location || "",
              linkedin_url: contact.linkedin_url || "",
              apollo_profile_url: apolloId ? `https://app.apollo.io/#/people/${apolloId}` : ""
            };
          })
        },
        response => {
          contactsToCheck.forEach(contact => state.pendingContacts.delete(contact.key));

          if (chrome.runtime.lastError) {
            console.error("Contact Checker runtime error:", chrome.runtime.lastError.message);
            addActivity("EXTENSION_RUNTIME_ERROR", chrome.runtime.lastError.message, "error");
            const liveStatusErr = document.getElementById("contact-checker-live-status");
            if (liveStatusErr) {
              liveStatusErr.className = "contact-checker-live-badge";
              liveStatusErr.textContent = "⚠ Runtime Error";
            }
            showStatus("Extension runtime error — will retry on next scan", 3000, false);
            return;
          }

          if (!response?.success) {
            console.error("Contact Checker API error:", response);
            addActivity("API_ERROR", response?.error || "Unknown API error", "error");
            const liveStatusErr = document.getElementById("contact-checker-live-status");
            if (liveStatusErr) {
              liveStatusErr.className = "contact-checker-live-badge";
              liveStatusErr.textContent = "⚠ API Error";
            }
            showStatus("Database connection error", 3000, false);
            return;
          }

          appendBackendActivity(response.activity || []);
          state.lastBackendSummary = response.summary || null;
          renderActivityPanel();

          let matches = 0;
          let requiredCount = 0;
          let ignoredCount = 0;

          contactsToCheck.forEach(contact => {
            const result = response.results?.[contact.key];
            if (!result) return;

            state.checkedContacts.set(contact.key, result);

            if (result.exists) {
              matches++;
            } else if (result.required && !result.ignored) {
              requiredCount++;
            } else {
              ignoredCount++;
            }

            applyContactResult(contact, result);
          });

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

          scheduleRequiredContactsSave();
          renderExportControls();
          checkAndFlushPendingTitles(false);

          showStatus(
            `✓ Checked ${contactsToCheck.length} contact(s) — ${matches} existing, ${requiredCount} required lead(s)`,
            3500,
            false
          );
        }
      );
    } catch (sendErr) {
      contactsToCheck.forEach(contact => state.pendingContacts.delete(contact.key));
      console.error("Contact Checker: sendMessage threw synchronously:", sendErr);
      addActivity("EXTENSION_RUNTIME_ERROR", `sendMessage failed: ${sendErr?.message || sendErr}`, "error");
      const liveEl = document.getElementById("contact-checker-live-status");
      if (liveEl) {
        liveEl.className = "contact-checker-live-badge";
        liveEl.textContent = "⚠ Reconnecting...";
      }
      showStatus("Extension disconnected — will retry on next scan", 3000, false);
    }
  }

  // ============================================================
  // WATCH APOLLO
  // ============================================================

  function isExtensionNode(node) {
    const element = node?.nodeType === Node.ELEMENT_NODE
      ? node
      : node?.parentElement;

    if (!element) return false;

    return Boolean(
      element.hasAttribute?.("data-contact-checker") ||
      element.closest?.("[data-contact-checker]") ||
      element.id?.startsWith("contact-checker-") ||
      element.closest?.("#contact-checker-status, #contact-checker-controls, #contact-checker-activity-panel, #contact-checker-domain-warning") ||
      element.classList?.contains("contact-checker-existing-badge") ||
      element.classList?.contains("contact-checker-required-badge") ||
      element.classList?.contains("contact-checker-ignored-badge") ||
      element.closest?.(".contact-checker-existing-badge, .contact-checker-required-badge, .contact-checker-ignored-badge")
    );
  }

  function mutationNeedsScan(mutation) {
    if (state.isUpdatingDom) {
      return false;
    }

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

    const nonExtensionAdded = Array.from(mutation.addedNodes).filter(n => !isExtensionNode(n));
    const nonExtensionRemoved = Array.from(mutation.removedNodes).filter(n => !isExtensionNode(n));

    if (nonExtensionAdded.length === 0 && nonExtensionRemoved.length === 0) {
      return false;
    }

    return true;
  }

  function scheduleScan(delay = 100) {
    if (state.isUpdatingDom) {
      return;
    }

    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      scanApollo();
    }, delay);
  }

  // Instant SPA Hash & Route Navigation Hook
  function onPageNavigation() {
    if (!state.active) return;
    const currentNavKey = `${location.pathname}${location.search}${location.hash}`;
    if (state.lastNavigatedKey === currentNavKey) {
      return; // URL has not changed
    }

    state.lastNavigatedKey = currentNavKey;
    // Retain recent checked contacts to make flipping between pages 0ms
    if (state.checkedContacts.size > 300) {
      const keysToDelete = Array.from(state.checkedContacts.keys()).slice(0, state.checkedContacts.size - 200);
      keysToDelete.forEach(k => state.checkedContacts.delete(k));
    }
    state.currentContacts.clear();
    startObserver();
    scheduleScan(100);
  }

  window.addEventListener("popstate", onPageNavigation, { passive: true });
  window.addEventListener("hashchange", onPageNavigation, { passive: true });

  if (window.history && window.history.pushState) {
    const originalPushState = window.history.pushState;
    window.history.pushState = function (...args) {
      const result = originalPushState.apply(this, args);
      onPageNavigation();
      return result;
    };
  }
  if (window.history && window.history.replaceState) {
    const originalReplaceState = window.history.replaceState;
    window.history.replaceState = function (...args) {
      const result = originalReplaceState.apply(this, args);
      onPageNavigation();
      return result;
    };
  }

  function startObserver() {
    if (state.observer) {
      state.observer.disconnect();
    }
    state.observer = new MutationObserver(mutations => {
      if (!state.active || state.isUpdatingDom) return;
      if (mutations.some(mutationNeedsScan)) {
        scheduleScan(100);
      }
    });

    const target =
      document.querySelector('[data-id="scrollable-table-container"], [role="treegrid"], [role="grid"], [data-testid="table-refetch-content"], .zp_table, [role="main"], #main-app') ||
      document.body;

    state.observerTarget = target;
    state.observer.observe(target, {
      childList: true,
      subtree: true
    });
  }

  // ============================================================
  // CLEANUP / TURN OFF
  // ============================================================

  state.cleanup = function (persist = true) {
    state.active = false;
    if (persist && chrome?.storage?.local) {
      chrome.storage.local.set({ [EXTENSION_ENABLED_STORAGE_KEY]: false });
    }

    if (state.observer) {
      state.observer.disconnect();
      state.observer = null;
    }

    window.removeEventListener?.("popstate", onPageNavigation);
    window.removeEventListener?.("hashchange", onPageNavigation);

    clearTimeout(state.timer);
    clearTimeout(state.pageTimer);
    clearTimeout(state.statusTimer);
    clearTimeout(state.storageSaveTimer);
    clearTimeout(state.settleTimer);
    saveRequiredContactsNow();
    state.pendingContacts.clear();

    state.highlightedRows.forEach(row => {
      row.classList.remove("contact-checker-existing");
      row.classList.remove("contact-checker-required-row");
    });

    document.querySelectorAll(
      `.contact-checker-existing-badge,
       .contact-checker-required-badge,
       .contact-checker-ignored-badge`
    ).forEach(badge => badge.remove());

    document.getElementById("contact-checker-style")?.remove();
    document.getElementById("contact-checker-status")?.remove();
    document.getElementById("contact-checker-controls")?.remove();
    document.getElementById("contact-checker-domain-warning")?.remove();
    document.getElementById("contact-checker-activity-panel")?.remove();

    state.activityLog = [];
    state.lastBackendSummary = null;
    state.highlightedRows.clear();
    state.currentContacts.clear();
    state.requiredCompanyMap.clear();

    console.log("Contact Database Checker disabled.");
  };

  // ============================================================
  // ACTIVATION & STARTUP
  // ============================================================

  function activateExtension(persist = true) {
    state.active = true;
    if (persist && chrome?.storage?.local) {
      chrome.storage.local.set({ [EXTENSION_ENABLED_STORAGE_KEY]: true });
    }

    console.log("Contact Database Checker enabled — Apollo mode.");
    addActivity("EXTENSION_STARTED", "Contact Database Checker enabled in Apollo mode.");
    ensureStylesInjected();
    renderExportControls();
    showStatus("Contact Checker ON");
    startObserver();
    loadStoredRequiredContacts();
    scanApollo();
  }

  chrome.runtime.onMessage?.addListener((message, sender, sendResponse) => {
    if (message.type === "TOGGLE_CONTACT_CHECKER") {
      if (state.active) {
        state.cleanup(true);
        showStatus("Contact Checker OFF");
      } else {
        activateExtension(true);
      }
      sendResponse({ success: true, active: state.active });
      return true;
    }
  });

  // Check stored ON/OFF toggle state on initialization
  if (chrome?.storage?.local) {
    chrome.storage.local.get([EXTENSION_ENABLED_STORAGE_KEY], (res) => {
      if (res && res[EXTENSION_ENABLED_STORAGE_KEY] === false) {
        state.active = false;
        console.log("Contact Database Checker is currently toggled OFF in settings.");
        return;
      }
      activateExtension(false);
    });
  } else {
    activateExtension(false);
  }

})();
