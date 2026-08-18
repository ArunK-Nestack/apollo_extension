(() => {
  const STATE_KEY = "__contactDatabaseChecker";

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
    observer: null,
    timer: null,
    pageTimer: null,
    statusTimer: null,
    checkedContacts: new Map(),
    pendingContacts: new Set(),
    currentContacts: new Map(),
    selectionRun: null,
    highlightedRows: new Set()
  };

  globalThis[STATE_KEY] = state;

  // ============================================================
  // STYLES
  // ============================================================

  const style = document.createElement("style");

  style.id = "contact-checker-style";

  style.textContent = `
    .contact-checker-existing {
      background-color: rgba(34, 197, 94, 0.16) !important;
      box-shadow: inset 4px 0 0 #16a34a !important;
    }

    .contact-checker-existing-badge {
      display: inline-flex !important;
      align-items: center !important;
      margin-left: 8px !important;
      padding: 2px 6px !important;
      border-radius: 5px !important;
      background: #16a34a !important;
      color: white !important;
      font-size: 10px !important;
      font-weight: 700 !important;
      line-height: 16px !important;
      white-space: nowrap !important;
    }

    .contact-checker-required-badge {
      display: inline-flex !important;
      align-items: center !important;
      margin-left: 8px !important;
      padding: 2px 6px !important;
      border-radius: 5px !important;
      background: #f97316 !important;
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

    #contact-checker-credit-limit {
      width: 64px;
      border: 1px solid #4b5563;
      border-radius: 6px;
      background: #fff;
      color: #111827;
      padding: 6px;
      font-size: 12px;
    }

    #contact-checker-select-required,
    #contact-checker-stop-selection {
      border: 0;
      border-radius: 6px;
      background: #f97316;
      color: white;
      padding: 7px 10px;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
    }

    #contact-checker-stop-selection {
      display: none;
      background: #dc2626;
    }

    #contact-checker-select-required:disabled,
    #contact-checker-stop-selection:disabled {
      cursor: default;
      opacity: 0.5;
    }

    #contact-checker-status {
      position: fixed;
      right: 20px;
      bottom: 20px;
      z-index: 2147483647;
      background: #111827;
      color: white;
      padding: 10px 14px;
      border-radius: 8px;
      font-family: Arial, sans-serif;
      font-size: 13px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.25);
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

  function showStatus(message, duration = 2500) {
    clearTimeout(state.statusTimer);

    let status = document.getElementById(
      "contact-checker-status"
    );

    if (!status) {
      status = document.createElement("div");
      status.id = "contact-checker-status";
      document.body.appendChild(status);
    }

    status.textContent = message;

    if (duration) {
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
    return Array.from(
      document.querySelectorAll(`
        a[href*="/contacts/"],
        a[data-to*="/contacts/"],
        a[href*="/people/"],
        a[data-to*="/people/"]
      `)
    );
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
  // EXTRACT APOLLO ROW
  // ============================================================

  function extractContact(link, index) {
    const row = link.closest('[role="row"]');

    if (!row) {
      console.log(
        "Contact Checker: no row found for",
        cleanText(link.innerText)
      );

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

    // Find which cell contains the person's name
    const nameCellIndex = cells.findIndex(
      cell => cell.contains(link)
    );

    if (nameCellIndex === -1) {
      console.log(
        "Contact Checker: name cell not found",
        name
      );

      return null;
    }

    const nameCell =
      cells[nameCellIndex];

    const titleCell =
      cells[nameCellIndex + 1];

    const companyCell =
      cells[nameCellIndex + 2];

    if (!titleCell || !companyCell) {
      console.log(
        "Contact Checker: missing adjacent cells",
        {
          name,
          cellCount: cells.length,
          nameCellIndex
        }
      );

      return null;
    }

    const jobTitle = cleanText(
      titleCell.innerText
    );

    const company = cleanText(
      companyCell.innerText
    );

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

    return {
      key,
      name,
      job_title: jobTitle,
      company,
      row,
      link,
      nameCell
    };
  }

  // ============================================================
  // HIGHLIGHT MATCH
  // ============================================================

  function highlightContact(
    contact,
    result
  ) {
    const row = contact.row;

    if (!row) {
      return;
    }

    row.classList.add(
      "contact-checker-existing"
    );

    state.highlightedRows.add(row);

    if (
      !contact.nameCell.querySelector(
        ".contact-checker-existing-badge"
      )
    ) {
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
  }

  function markRequired(contact) {
    if (
      contact.nameCell.querySelector(
        ".contact-checker-required-badge"
      )
    ) {
      return;
    }

    const badge =
      document.createElement("span");

    badge.className =
      "contact-checker-required-badge";

    badge.textContent =
      "Required";

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
    } else {
      markRequired(contact);
    }
  }

  function getRowCheckbox(row) {
    return Array.from(
      row.querySelectorAll(
      `input[type="checkbox"],
       [role="checkbox"]`
      )
    ).find(isCheckboxAvailable) || null;
  }

  function isCheckboxSelected(checkbox) {
    return checkbox.checked === true ||
      checkbox.getAttribute("aria-checked") === "true" ||
      checkbox.getAttribute("data-state") === "checked";
  }

  function isCheckboxAvailable(checkbox) {
    return checkbox &&
      checkbox.isConnected &&
      !checkbox.disabled &&
      checkbox.getAttribute("aria-disabled") !== "true" &&
      checkbox.getClientRects().length > 0;
  }

  function getRequiredContacts() {
    return Array.from(
      state.currentContacts.values()
    ).filter(contact => {
      const result =
        state.checkedContacts.get(
          contact.key
        );

      return result?.exists === false;
    });
  }

  function getPageSignature() {
    return Array.from(
      state.currentContacts.keys()
    ).join("|");
  }

  function nextFrame() {
    return new Promise(resolve =>
      requestAnimationFrame(resolve)
    );
  }

  function findNextPageButton() {
    const selectors = [
      '[aria-label="Next page"]',
      '[aria-label="Next"]',
      '[aria-label*="next" i]',
      '[title="Next page"]',
      '[title*="next" i]',
      '[data-testid*="next" i]',
      '[data-cy*="next" i]',
      '[data-test*="next" i]'
    ];

    const explicitMatch = selectors
      .flatMap(selector =>
        Array.from(
          document.querySelectorAll(selector)
        )
      )
      .find(button => {
        if (!isCheckboxAvailable(button)) {
          return false;
        }

        const label = [
          button.textContent,
          button.getAttribute("aria-label"),
          button.getAttribute("title"),
          button.getAttribute("data-testid"),
          button.getAttribute("data-cy"),
          button.getAttribute("data-test")
        ]
          .filter(Boolean)
          .join(" ")
          .trim()
          .toLowerCase();

        return label === "next" ||
          label.includes("next page") ||
          label.includes("page next") ||
          label.includes("pagination next") ||
          /next.*(page|result)|(page|result).*next/.test(label);
      });

    if (explicitMatch) {
      return explicitMatch;
    }

    const controls = Array.from(
      document.querySelectorAll(
        'button, [role="button"]'
      )
    ).filter(isCheckboxAvailable);

    const labeledMatch = controls.find(control => {
      const label = [
        control.textContent,
        control.getAttribute("aria-label"),
        control.getAttribute("title"),
        control.getAttribute("data-testid"),
        control.getAttribute("data-cy")
      ]
        .filter(Boolean)
        .join(" ")
        .trim()
        .toLowerCase();

      if (
        label === "next" ||
        label.includes("next page") ||
        label.includes("pagination next")
      ) {
        return true;
      }

      const pagination = control.closest(
        `nav,
         [role="navigation"],
         [class*="pagination" i],
         [data-testid*="pagination" i],
         [data-cy*="pagination" i]`
      );

      return Boolean(
        pagination &&
        control.querySelector(
          `[data-icon*="chevron-right" i],
           [data-icon*="arrow-right" i],
           [aria-label*="right" i],
           [class*="chevron-right" i],
           [class*="arrow-right" i]`
        )
      );
    });

    if (labeledMatch) {
      return labeledMatch;
    }

    const pagination = document.querySelector(
      `nav[aria-label*="pagination" i],
       [role="navigation"][aria-label*="page" i],
       [class*="pagination" i],
       [data-testid*="pagination" i],
       [data-cy*="pagination" i]`
    );

    if (!pagination) {
      return null;
    }

    const paginationButtons = Array.from(
      pagination.querySelectorAll(
        'button, [role="button"]'
      )
    ).filter(isCheckboxAvailable);

    return paginationButtons.at(-1) || null;
  }

  function waitForPageChange(
    previousSignature,
    attempts = 0
  ) {
    clearTimeout(state.pageTimer);

    state.pageTimer = setTimeout(
      () => {
        const run = state.selectionRun;

        if (
          !run ||
          run.waitingForPage !== previousSignature
        ) {
          return;
        }

        scanApollo();

        if (
          getPageSignature() !== previousSignature
        ) {
          return;
        }

        if (attempts >= 15) {
          stopSelectionRun(
            "Page change timed out | {selected} selected"
          );
          return;
        }

        waitForPageChange(
          previousSignature,
          attempts + 1
        );
      },
      500
    );
  }

  function stopSelectionRun(message) {
    const run = state.selectionRun;

    clearTimeout(state.pageTimer);
    state.pageTimer = null;
    state.selectionRun = null;
    renderSelectionControls();

    if (message) {
      showStatus(
        message.replace(
          "{selected}",
          String(run?.selected || 0)
        ),
        5000
      );
    }
  }

  async function continueSelectionRun() {
    const run = state.selectionRun;

    if (!run || run.busy) {
      return;
    }

    const signature = getPageSignature();

    if (!signature) {
      return;
    }

    if (
      run.waitingForPage &&
      run.waitingForPage === signature
    ) {
      return;
    }

    if (run.waitingForPage) {
      run.waitingForPage = null;
      clearTimeout(state.pageTimer);
      state.pageTimer = null;
    }

    const unresolved = Array.from(
      state.currentContacts.keys()
    ).some(key =>
      !state.checkedContacts.has(key)
    );

    if (unresolved) {
      return;
    }

    if (run.visitedPages.has(signature)) {
      stopSelectionRun(
        "Stopped: page repeated | {selected} selected"
      );
      return;
    }

    run.busy = true;
    run.visitedPages.add(signature);

    const candidates =
      getRequiredContacts().filter(contact =>
        !run.processedKeys.has(contact.key)
      );

    for (let index = 0;
      index < candidates.length && run.remaining > 0;
      index++) {
      const contact = candidates[index];
      const checkbox = getRowCheckbox(contact.row);

      run.processedKeys.add(contact.key);

      if (!isCheckboxAvailable(checkbox)) {
        run.skipped++;
        continue;
      }

      if (!isCheckboxSelected(checkbox)) {
        checkbox.click();
      }

      run.selected++;
      run.remaining--;

      if (run.selected % 10 === 0) {
        renderSelectionControls();
        await nextFrame();
      }
    }

    run.busy = false;
    renderSelectionControls();

    if (!state.selectionRun) {
      return;
    }

    if (run.remaining === 0) {
      stopSelectionRun(
        "Credit cap reached | {selected} selected"
      );
      return;
    }

    const nextButton = findNextPageButton();

    if (!nextButton) {
      stopSelectionRun(
        "No next page | {selected} selected"
      );
      return;
    }

    run.waitingForPage = signature;
    nextButton.click();
    showStatus(
      `Moving to next page | ${run.remaining} left`,
      2000
    );
    waitForPageChange(signature);
  }

  function selectRequiredContacts() {
    const input = document.getElementById(
      "contact-checker-credit-limit"
    );

    const limit = Number.parseInt(
      input?.value,
      10
    );

    if (!Number.isInteger(limit) || limit < 1) {
      showStatus("Enter credit limit of at least 1");
      return;
    }

    state.selectionRun = {
      remaining: limit,
      selected: 0,
      skipped: 0,
      busy: false,
      waitingForPage: null,
      processedKeys: new Set(),
      visitedPages: new Set()
    };

    renderSelectionControls();
    continueSelectionRun();
  }

  function renderSelectionControls() {
    let controls = document.getElementById(
      "contact-checker-controls"
    );

    if (!controls) {
      controls = document.createElement("div");
      controls.id = "contact-checker-controls";
      controls.innerHTML = `
        <span id="contact-checker-required-count"></span>
        <label>
          Credit cap
          <input
            id="contact-checker-credit-limit"
            type="number"
            min="1"
            step="1"
            value="50"
          >
        </label>
        <button
          id="contact-checker-select-required"
          type="button"
        >Select Across Pages</button>
        <button
          id="contact-checker-stop-selection"
          type="button"
        >Stop</button>
      `;

      controls
        .querySelector(
          "#contact-checker-select-required"
        )
        .addEventListener(
          "click",
          selectRequiredContacts
        );

      controls
        .querySelector(
          "#contact-checker-stop-selection"
        )
        .addEventListener(
          "click",
          () => stopSelectionRun(
            "Stopped | {selected} selected"
          )
        );

      document.body.appendChild(controls);
    }

    const requiredCount =
      getRequiredContacts().length;

    const countLabel = controls.querySelector(
      "#contact-checker-required-count"
    );

    const countText =
      `Required: ${requiredCount}`;

    if (countLabel.textContent !== countText) {
      countLabel.textContent = countText;
    }

    const run = state.selectionRun;
    const selectButton = controls.querySelector(
      "#contact-checker-select-required"
    );
    const stopButton = controls.querySelector(
      "#contact-checker-stop-selection"
    );
    const creditInput = controls.querySelector(
      "#contact-checker-credit-limit"
    );

    selectButton.disabled =
      Boolean(run) || state.currentContacts.size === 0;
    selectButton.textContent = run
      ? `${run.selected} selected | ${run.remaining} left`
      : "Select Across Pages";
    stopButton.style.display = run ? "block" : "none";
    creditInput.disabled = Boolean(run);
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
      renderSelectionControls();

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

    renderSelectionControls();

    if (!contactsToCheck.length) {
      continueSelectionRun();
      return;
    }

    contactsToCheck.forEach(contact =>
      state.pendingContacts.add(contact.key)
    );

    showStatus(
      `Checking ${contactsToCheck.length} contact(s)...`
    );

    // ==========================================================
    // SEND ONE BATCH TO PYTHON
    // ==========================================================

    chrome.runtime.sendMessage(
      {
        type: "MATCH_APOLLO",

        contacts:
          contactsToCheck.map(
            contact => ({
              key:
                contact.key,

              name:
                contact.name,

              job_title:
                contact.job_title,

              company:
                contact.company
            })
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

          if (state.selectionRun) {
            stopSelectionRun(
              "Selection stopped: extension error | {selected} selected"
            );
          }

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

          showStatus(
            "Database connection error"
          );

          if (state.selectionRun) {
            stopSelectionRun(
              "Selection stopped: database error | {selected} selected"
            );
          }

          return;
        }

        let matches = 0;

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
            }

            applyContactResult(
              contact,
              result
            );
          }
        );

        renderSelectionControls();
        continueSelectionRun();

        showStatus(
          `Checker ON — ${matches} existing contact(s) found`
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
         .contact-checker-existing-badge,
         .contact-checker-required-badge`
      ) ||
      element?.closest?.(
        `#contact-checker-status,
         #contact-checker-controls`
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

  function scheduleScan() {
    clearTimeout(state.timer);

    state.timer = setTimeout(
      () => {
        if ("requestIdleCallback" in globalThis) {
          requestIdleCallback(
            scanApollo,
            { timeout: 700 }
          );
        } else {
          scanApollo();
        }
      },
      300
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
    state.selectionRun = null;
    state.pendingContacts.clear();

    state.highlightedRows.forEach(
      row => {
        row.classList.remove(
          "contact-checker-existing"
        );
      }
    );

    document
      .querySelectorAll(
        `.contact-checker-existing-badge,
         .contact-checker-required-badge`
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

  showStatus(
    "Contact Checker ON"
  );

  scanApollo();

})();
