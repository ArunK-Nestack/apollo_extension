/**
 * Apollo Operations Hub — Global Shell Controller v2.0
 * Runs on every page: sidebar, command palette, toasts, job console, telemetry
 */

(function () {
  'use strict';

  /* ================================================================
     SVG ICON HELPER
     ================================================================ */
  function icon(name, size = 16) {
    const icons = {
      search:    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>`,
      layers:    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></svg>`,
      zap:       `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
      users:     `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
      shield:    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
      check:     `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
      refresh:   `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>`,
      sparkles:  `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v1m0 16v1M4.22 4.22l.7.7m12.16 12.16.7.7M3 12H2m20 0h-1M4.22 19.78l.7-.7M18.36 5.64l.7-.7M12 6a6 6 0 0 0 0 12"/></svg>`,
      terminal:  `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>`,
      copy:      `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
      chevronup: `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>`,
      x:         `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
      db:        `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>`,
      cpu:       `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2" ry="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>`,
      plus:      `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
      bell:      `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>`,
      ok:        `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
      err:       `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
      warn:      `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
      info:      `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
      download:  `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`,
    };
    return icons[name] || '';
  }
  window.icon = icon;

  /* ================================================================
     TOAST NOTIFICATIONS
     ================================================================ */
  function ensureToastContainer() {
    let c = document.getElementById('toast-container');
    if (!c) {
      c = document.createElement('div');
      c.id = 'toast-container';
      document.body.appendChild(c);
    }
    return c;
  }

  window.showToast = function (title, message, type = 'info', duration = 4000) {
    const container = ensureToastContainer();
    const iconMap = { success: icon('ok', 18), error: icon('err', 18), warning: icon('warn', 18), info: icon('info', 18) };
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
      <span class="toast-icon">${iconMap[type] || iconMap.info}</span>
      <div class="toast-body">
        <div class="toast-title">${title}</div>
        ${message ? `<div class="toast-msg">${message}</div>` : ''}
      </div>
      <button style="background:none;border:none;cursor:pointer;color:var(--text-muted);padding:2px;align-self:flex-start;margin-top:2px;" onclick="this.parentElement.remove()">${icon('x', 14)}</button>
    `;
    container.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('removing');
      setTimeout(() => toast.remove(), 220);
    }, duration);
  };

  /* ================================================================
     COMMAND PALETTE
     ================================================================ */
  const PALETTE_ITEMS = [
    { group: 'Navigate', label: 'Batch Pipeline Hub',        icon: 'layers',   href: '/batches',        shortcut: 'G B' },
    { group: 'Navigate', label: 'Search Studio (AI)',        icon: 'zap',      href: '/search-studio',  shortcut: 'G S' },
    { group: 'Navigate', label: 'Fleet & Quotas',            icon: 'users',    href: '/fleet',           shortcut: 'G F' },
    { group: 'Navigate', label: 'Guardrails Sandbox',        icon: 'shield',   href: '/guardrails',      shortcut: 'G G' },
    { group: 'Navigate', label: 'MillionVerifier Station',   icon: 'check',    href: '/verifier',        shortcut: 'G V' },
    { group: 'Navigate', label: 'Freshsales CRM Sync',       icon: 'refresh',  href: '/freshsales-sync', shortcut: 'G C' },
    { group: 'Navigate', label: 'Enrich.so Station',         icon: 'sparkles', href: '/enrich',          shortcut: 'G E' },
    { group: 'Actions',  label: 'Launch New Batch',          icon: 'plus',     action: () => window.location.href = '/search-studio' },
    { group: 'Actions',  label: 'Test Guardrails Sandbox',   icon: 'shield',   action: () => window.location.href = '/guardrails' },
  ];

  function buildPalette() {
    const overlay = document.getElementById('cmd-palette-overlay');
    if (!overlay) return;
    const input = document.getElementById('cmd-palette-input');
    const list  = document.getElementById('cmd-palette-list');
    let selected = 0;

    function renderItems(filter = '') {
      const f = filter.toLowerCase();
      const filtered = PALETTE_ITEMS.filter(i => i.label.toLowerCase().includes(f));
      const groups = [...new Set(filtered.map(i => i.group))];
      list.innerHTML = groups.map(g => {
        const items = filtered.filter(i => i.group === g);
        return `
          <div class="cmd-group-label">${g}</div>
          ${items.map((item, idx) => `
            <div class="cmd-item ${idx === 0 && g === groups[0] && f === '' ? 'selected' : ''}"
                 data-idx="${PALETTE_ITEMS.indexOf(item)}"
                 onclick="window._cmdActivate(${PALETTE_ITEMS.indexOf(item)})">
              <span style="color:var(--text-secondary)">${icon(item.icon, 16)}</span>
              <span>${item.label}</span>
              ${item.shortcut ? `<span class="cmd-item-shortcut">${item.shortcut}</span>` : ''}
            </div>
          `).join('')}
        `;
      }).join('');
    }

    window._cmdActivate = function(idx) {
      const item = PALETTE_ITEMS[idx];
      closePalette();
      if (item.href) window.location.href = item.href;
      else if (item.action) item.action();
    };

    function openPalette() {
      overlay.classList.add('open');
      input.value = '';
      renderItems('');
      input.focus();
    }
    function closePalette() {
      overlay.classList.remove('open');
    }

    input.addEventListener('input', () => renderItems(input.value));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closePalette(); });
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); openPalette(); }
      if (e.key === 'Escape' && overlay.classList.contains('open')) closePalette();
    });

    const trigger = document.getElementById('cmd-palette-trigger');
    if (trigger) trigger.addEventListener('click', openPalette);

    renderItems('');
  }

  /* ================================================================
     ACTIVE NAV HIGHLIGHTING
     ================================================================ */
  const path = window.location.pathname;
  document.querySelectorAll('.nav-item[data-href]').forEach(item => {
    const href = item.getAttribute('data-href');
    if (href && (path === href || (href !== '/' && path.startsWith(href)))) {
      item.classList.add('active');
    }
  });

  /* ================================================================
     JOB CONSOLE (Bottom Drawer)
     ================================================================ */
  const console_el  = document.getElementById('job-console');
  const terminal_el = document.getElementById('job-terminal');
  const status_el   = document.getElementById('job-console-status-text');
  const progress_el = document.getElementById('console-progress-fill');
  const elapsed_el  = document.getElementById('job-elapsed');

  let autoScroll = true;
  let elapsedSec = 0;
  let elapsedTimer = null;
  let eventSource = null;
  let logBuffer   = '';

  function toggleConsole() {
    if (console_el) console_el.classList.toggle('expanded');
  }
  window.toggleConsole = toggleConsole;

  function startElapsed() {
    elapsedSec = 0;
    clearInterval(elapsedTimer);
    elapsedTimer = setInterval(() => {
      elapsedSec++;
      const m = Math.floor(elapsedSec / 60).toString().padStart(2, '0');
      const s = (elapsedSec % 60).toString().padStart(2, '0');
      if (elapsed_el) elapsed_el.textContent = `${m}:${s}`;
    }, 1000);
  }

  function stopElapsed() { clearInterval(elapsedTimer); }

  function appendLog(text, cls = 'log-info') {
    if (!terminal_el) return;
    const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
    const line = document.createElement('div');
    line.className = cls;
    line.innerHTML = `<span class="log-ts">[${ts}]</span>${text}`;
    terminal_el.appendChild(line);
    if (autoScroll) terminal_el.scrollTop = terminal_el.scrollHeight;
  }

  function connectTaskStream(taskId) {
    if (eventSource) eventSource.close();
    if (terminal_el) terminal_el.innerHTML = '';
    if (progress_el) progress_el.style.width = '0%';
    if (console_el) { console_el.classList.add('has-job'); console_el.classList.remove('has-error', 'has-success'); console_el.classList.add('expanded'); }
    startElapsed();

    const completeRow = document.getElementById('console-complete-row');
    if (completeRow) completeRow.style.display = 'none';

    eventSource = new EventSource(`/api/v1/tasks/${taskId}/stream`);

    eventSource.onmessage = function(e) {
      try {
        const payload = JSON.parse(e.data);
        if (payload.message) {
          let cls = 'log-info';
          const msg = payload.message;
          if (/success|complete|done|✓|added|synced/i.test(msg)) cls = 'log-success';
          else if (/warn|skip|429|limit/i.test(msg))              cls = 'log-warn';
          else if (/error|fail|exception/i.test(msg))             cls = 'log-error';
          appendLog(msg, cls);
          if (status_el) status_el.textContent = payload.message;
        }
        if (progress_el && payload.percent != null) {
          progress_el.style.width = `${payload.percent}%`;
        }
        if (payload.percent >= 100) {
          stopElapsed();
          eventSource.close();
          if (console_el) { console_el.classList.remove('has-job'); console_el.classList.add('has-success'); }
          if (completeRow) completeRow.style.display = 'flex';
          localStorage.removeItem('active_task_id');
          showToast('Task Complete', 'Background job finished successfully.', 'success');
        }
      } catch (_) {}
    };

    eventSource.onerror = function() {
      eventSource.close();
      stopElapsed();
      if (console_el) { console_el.classList.remove('has-job'); console_el.classList.add('has-error'); }
      appendLog('Connection closed or task ended.', 'log-warn');
    };
  }
  window.connectTaskStream = connectTaskStream;

  // Auto-scroll toggle
  const scrollToggle = document.getElementById('autoscroll-toggle');
  if (scrollToggle) {
    scrollToggle.addEventListener('click', () => {
      autoScroll = !autoScroll;
      scrollToggle.textContent = autoScroll ? '↓ Auto-scroll On' : '↓ Auto-scroll Off';
      scrollToggle.classList.toggle('on', autoScroll);
    });
  }

  // Copy logs button
  const copyLogsBtn = document.getElementById('copy-logs-btn');
  if (copyLogsBtn) {
    copyLogsBtn.addEventListener('click', () => {
      if (terminal_el) {
        navigator.clipboard.writeText(terminal_el.innerText);
        showToast('Copied', 'Logs copied to clipboard.', 'success', 2000);
      }
    });
  }

  /* ================================================================
     GLOBAL TASK LAUNCHER
     ================================================================ */
  window.launchTask = async function(taskType, batchTag, params = {}) {
    try {
      const res = await fetch('/api/v1/tasks/launch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_type: taskType, batch_tag: batchTag, parameters: params })
      });
      const data = await res.json();
      if (data.task_id) {
        localStorage.setItem('active_task_id', data.task_id);
        connectTaskStream(data.task_id);
        showToast('Task Launched', `Job ${taskType} dispatched.`, 'info');
      }
    } catch(e) {
      showToast('Launch Failed', e.message, 'error');
    }
  };

  /* ================================================================
     TELEMETRY POLLING
     ================================================================ */
  async function pollTelemetry() {
    try {
      const res = await fetch('/api/v1/telemetry');
      if (!res.ok) return;
      const data = await res.json();

      // Sidebar health card
      const dbEl    = document.getElementById('sidebar-db-count');
      const trieEl  = document.getElementById('sidebar-trie-count');
      const sysPill = document.getElementById('header-sys-pill-text');

      if (dbEl && data.database) {
        const cnt = data.database.total_emails;
        dbEl.textContent = cnt ? `${(cnt/1e6).toFixed(2)}M` : 'OK';
      }
      if (trieEl && data.trie) {
        const nc = data.trie.node_count;
        trieEl.textContent = nc ? `${(nc/1e6).toFixed(2)}M` : 'Loaded';
      }
      if (sysPill) {
        sysPill.textContent = '4 Nodes Healthy';
      }

      // Update fleet in header (if fleet data present)
      const fleetEl = document.getElementById('header-fleet-summary');
      if (fleetEl && data.fleet && data.fleet.length > 0) {
        const ready = data.fleet.filter(a => a.status === 'ready').length;
        const total = data.fleet.length;
        fleetEl.textContent = `${ready}/${total} Accounts Ready`;
      }
    } catch (_) {}
  }

  /* ================================================================
     RESUME TASK FROM LOCAL STORAGE
     ================================================================ */
  const savedTask = localStorage.getItem('active_task_id');
  if (savedTask) {
    connectTaskStream(savedTask);
  }

  /* ================================================================
     INIT
     ================================================================ */
  document.addEventListener('DOMContentLoaded', () => {
    buildPalette();
    pollTelemetry();
    setInterval(pollTelemetry, 8000);
  });
})();
