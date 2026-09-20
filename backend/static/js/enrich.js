// Enrich.so Station Controller

async function testEnrichMatch() {
  const dom    = document.getElementById('enrich-domain').value.trim();
  const title  = document.getElementById('enrich-title').value.trim();
  const resBox = document.getElementById('enrich-match-result');

  resBox.style.display = 'block';
  resBox.textContent = `Testing /match-enrich for ${dom}…`;

  try {
    const res = await fetch('/match-enrich', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contacts: [{ key: 'c1', company_domain: dom, job_title: title, name: 'Sample Lead' }]
      })
    });
    const data = await res.json();
    resBox.textContent = JSON.stringify(data, null, 2);

    const passed = data?.results?.[0]?.passed ?? (data?.passed ?? null);
    if (passed === true && window.showToast)  window.showToast('Match Result', 'Domain passed all CRM checks.', 'success');
    if (passed === false && window.showToast) window.showToast('Match Result', 'Domain exists in CRM — filtered.', 'warning');

  } catch (e) {
    resBox.textContent = 'Error: ' + e.message;
    if (window.showToast) window.showToast('Request Failed', e.message, 'error');
  }
}
