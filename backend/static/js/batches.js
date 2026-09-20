// Batches Hub Controller

async function fetchBatches() {
  const tbody = document.getElementById('batches-table-body');
  try {
    const res = await fetch('/api/v1/batches');
    const data = await res.json();
    const batches = data.batches || [];

    // Calculate quick stats
    let totalScraped = 0;
    let totalEnriched = 0;
    let totalSynced = 0;

    batches.forEach(b => {
      totalScraped += (b.total_leads || 0);
      totalEnriched += (b.emails_found || 0);
      if (b.crm_synced) totalSynced += (b.verified_good || b.emails_found || 0);
    });

    document.getElementById('stat-batch-count').textContent = batches.length;
    document.getElementById('stat-scraped-count').textContent = totalScraped.toLocaleString();
    document.getElementById('stat-enriched-count').textContent = totalEnriched.toLocaleString();
    document.getElementById('stat-synced-count').textContent = totalSynced.toLocaleString();

    const countLabel = document.getElementById('batch-count-label');
    if (countLabel) countLabel.textContent = `${batches.length} batch${batches.length !== 1 ? 'es' : ''}`;

    if (batches.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 2rem;">No lead batches found in MySQL ledger.</td></tr>';
      return;
    }

    tbody.innerHTML = batches.map(b => {
      const enrichPct = b.enrich_pct || 0;
      const isEnriched = b.emails_found > 0;
      const hasGood = (b.verified_good || 0) > 0;
      const isSynced = b.crm_synced;

      return `
        <tr>
          <td>
            <div style="font-weight: 600; color: #fff;">${b.batch_tag}</div>
            <div style="font-size: 0.72rem; color: var(--text-muted);">${b.created_at || 'Recent'}</div>
          </td>
          <td>
            <span class="badge badge-gray">${(b.total_leads || 0).toLocaleString()} leads</span>
          </td>
          <td>
            <div style="display: flex; justify-content: space-between; font-size: 0.74rem;">
              <span><strong>${b.emails_found || 0}</strong> emails</span>
              <span style="color: var(--accent-emerald);">${enrichPct}%</span>
            </div>
            <div class="progress-bar-bg">
              <div class="progress-bar-fill" style="width: ${enrichPct}%;"></div>
            </div>
          </td>
          <td>
            ${hasGood ? `
              <div style="display: flex; gap: 0.3rem;">
                <span class="badge badge-green">✓ ${b.verified_good} Good</span>
                <span class="badge badge-rose">✕ ${b.verified_bad || 0}</span>
              </div>
            ` : `<span style="color: var(--text-muted); font-size: 0.75rem;">Not verified yet</span>`}
          </td>
          <td>
            ${isSynced ? `
              <span class="badge badge-green">✓ Synced (${b.crm_tag || 'Lead'})</span>
            ` : `
              <span class="badge badge-amber">Pending Sync</span>
            `}
          </td>
          <td style="text-align: right;">
            <div style="display: inline-flex; gap: 0.35rem;">
              ${!isEnriched ? `
                <button class="btn btn-primary btn-sm" onclick="window.launchTask('enrich', '${b.batch_tag}')">
                  <span>⚡</span> Enrich
                </button>
              ` : ''}
              ${isEnriched && !hasGood ? `
                <button class="btn btn-secondary btn-sm" onclick="window.launchTask('verify', '${b.batch_tag}')">
                  <span>🛡️</span> Verify
                </button>
              ` : ''}
              ${hasGood && !isSynced ? `
                <button class="btn btn-success btn-sm" onclick="window.launchTask('sync_crm', '${b.batch_tag}', {custom_tag: '${b.batch_tag}-q3'})">
                  <span>🔄</span> Sync CRM
                </button>
              ` : ''}
              <button class="btn btn-secondary btn-sm" onclick="alert('Exporting 75-Column Clean CSV for ${b.batch_tag}')">
                <span>📥</span> CSV
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--accent-rose); padding: 2rem;">Error loading batches: ${e.message}</td></tr>`;
  }
}

/**
 * Live filter function wired to the search input and status dropdown.
 * Called by: oninput on #batch-search-input, onchange on #batch-status-filter
 */
function filterBatchTable(searchVal) {
  const statusFilter = (document.getElementById('batch-status-filter')?.value || '').toLowerCase();
  const search = (searchVal || '').toLowerCase();
  const rows = Array.from(document.querySelectorAll('#batches-table-body tr'));
  let visible = 0;

  rows.forEach(row => {
    // Skip colspan rows (empty/loading/error states)
    if (row.querySelector('td[colspan]')) {
      row.style.display = '';
      return;
    }
    const text = row.textContent.toLowerCase();
    const searchMatch = !search || text.includes(search);
    const statusMatch = !statusFilter || text.includes(statusFilter);
    const show = searchMatch && statusMatch;
    row.style.display = show ? '' : 'none';
    if (show) visible++;
  });

  const label = document.getElementById('batch-count-label');
  if (label && rows.some(r => !r.querySelector('td[colspan]'))) {
    label.textContent = `${visible} batch${visible !== 1 ? 'es' : ''}`;
  }
}

fetchBatches();
