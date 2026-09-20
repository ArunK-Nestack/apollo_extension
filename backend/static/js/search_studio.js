// Search Studio & AI Slicing Controller

let currentSearches = [];
let selectedSearchObj = null;

async function loadSearchCatalog() {
  const select = document.getElementById('search-select');
  try {
    const res = await fetch('/api/v1/search/catalog');
    const data = await res.json();
    currentSearches = data.searches || [];

    if (currentSearches.length === 0) {
      select.innerHTML = '<option value="">No saved searches found in config</option>';
      const el = document.getElementById('ss-total-searches');
      if (el) el.textContent = '0';
      return;
    }

    select.innerHTML = '<option value="">-- Choose a Saved Search --</option>' +
      currentSearches.map((s, idx) =>
        `<option value="${idx}">${s.name || s.label || 'Saved Search ' + (idx + 1)} (${s.account_email || 'Default'})</option>`
      ).join('');

    // Populate catalog KPI stats
    const totalSearchesEl = document.getElementById('ss-total-searches');
    if (totalSearchesEl) totalSearchesEl.textContent = currentSearches.length;

    const totalLeads = currentSearches.reduce((acc, s) => acc + (s.estimated_leads || s.total_leads || 0), 0);
    const totalLeadsEl = document.getElementById('ss-total-leads');
    if (totalLeadsEl) totalLeadsEl.textContent = totalLeads > 0 ? totalLeads.toLocaleString() : '—';

  } catch (e) {
    select.innerHTML = `<option value="">Error loading searches: ${e.message}</option>`;
  }
}

function onSelectSearch() {
  const idx = document.getElementById('search-select').value;
  if (idx === '') {
    selectedSearchObj = null;
    document.getElementById('filter-titles').textContent = 'Select a search above';
    document.getElementById('filter-location').textContent = '--';
    return;
  }
  selectedSearchObj = currentSearches[idx];
  const params = selectedSearchObj.params || selectedSearchObj.filters || {};
  const titles = params.person_titles || ['Executives / Decision Makers'];
  const locs   = params.person_locations || ['United States'];

  document.getElementById('filter-titles').textContent   = Array.isArray(titles) ? titles.join(', ') : titles;
  document.getElementById('filter-location').textContent = Array.isArray(locs)   ? locs.join(', ')   : locs;
}

// Called by "Generate Slices" button in HTML (was incorrectly named generateAISlices before)
async function generateSlices() {
  if (!selectedSearchObj) {
    if (window.showToast) window.showToast('No Search Selected', 'Please select a saved search first.', 'warning');
    return;
  }

  const tbody    = document.getElementById('slices-table-body');
  const launchBtn = document.getElementById('launch-scrape-btn');
  if (launchBtn) launchBtn.style.display = 'none';

  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--accent-blue);padding:2.5rem;">' +
    '<span>⚡</span> Calling gpt-4o-mini &amp; probing Apollo volumes in parallel (0 credits)...</td></tr>';

  try {
    const params = selectedSearchObj.params || selectedSearchObj.filters || {};
    const payload = {
      search_name:   selectedSearchObj.name || 'Saved Search',
      account_email: selectedSearchObj.account_email || 'default@example.com',
      titles:        params.person_titles || [],
      locations:     params.person_locations || [],
      keywords:      params.q_organization_keyword_tags || [],
    };

    const res  = await fetch('/api/v1/search/slices', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data  = await res.json();
    const items = data.items || [];

    if (items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:2rem;">No slicing recommendations generated.</td></tr>';
      return;
    }

    tbody.innerHTML = items.map(it => `
      <tr>
        <td style="text-align:center;">
          <input type="checkbox" class="slice-checkbox" value="${it.keyword}" ${it.recommended ? 'checked' : ''} onchange="updateSelectedCount()">
        </td>
        <td><strong style="color:#fff;">${it.keyword}</strong></td>
        <td><span class="badge badge-gray">${it.category}</span></td>
        <td>
          ${it.recommended
            ? '<span class="badge badge-green">✓ Fresh Recommendation</span>'
            : '<span class="badge badge-amber">Already Used (Deduplicated)</span>'}
        </td>
        <td><span style="color:var(--accent-blue);font-weight:600;">${(it.estimated_leads || 0).toLocaleString()}</span> leads</td>
        <td style="color:var(--text-muted);">${it.pages || 0} pages</td>
      </tr>
    `).join('');

    updateSelectedCount();
    if (launchBtn) launchBtn.style.display = '';

    // Populate slice + credit KPI stats
    const totalEstLeads = items.reduce((a, i) => a + (i.estimated_leads || 0), 0);
    const sliceCountEl  = document.getElementById('ss-slice-count');
    if (sliceCountEl) sliceCountEl.textContent = items.length;
    const estCreditsEl  = document.getElementById('ss-est-credits');
    if (estCreditsEl) estCreditsEl.textContent = Math.round(totalEstLeads / 25).toLocaleString();

  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--accent-rose);padding:2rem;">Error: ${e.message}</td></tr>`;
  }
}

function updateSelectedCount() {
  const checked = document.querySelectorAll('.slice-checkbox:checked').length;
  const el = document.getElementById('selected-slices-count');
  if (el) el.textContent = `${checked} slice${checked !== 1 ? 's' : ''} selected`;
}

function toggleAllSlices(master) {
  document.querySelectorAll('.slice-checkbox').forEach(cb => cb.checked = master.checked);
  updateSelectedCount();
}

// Called by "Launch Scrape" button in HTML (was incorrectly named startDirectScrape before)
function launchScrape() {
  const selected = Array.from(document.querySelectorAll('.slice-checkbox:checked')).map(cb => cb.value);
  if (selected.length === 0) {
    if (window.showToast) window.showToast('No Slices Selected', 'Please select at least one slice to scrape.', 'warning');
    return;
  }
  const searchName = selectedSearchObj ? selectedSearchObj.name : 'Custom Search';
  const batchTag   = `scrape_${searchName.toLowerCase().replace(/[^a-z0-9]/g, '_')}_${Date.now().toString().slice(-4)}`;
  window.launchTask('scrape', batchTag, { slices: selected, search_name: searchName });
}

loadSearchCatalog();
