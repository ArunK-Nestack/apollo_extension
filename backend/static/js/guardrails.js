// Guardrails Studio Controller

async function runSandboxTest() {
  const company = document.getElementById('sb-company').value.trim();
  const domain = document.getElementById('sb-domain').value.trim();
  const title = document.getElementById('sb-title').value.trim();
  const name = document.getElementById('sb-name').value.trim();

  const resultsDeck = document.getElementById('sandbox-results');
  resultsDeck.style.display = 'block';

  try {
    const res = await fetch('/api/v1/guardrails/sandbox', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_name: company,
        company_domain: domain,
        job_title: title,
        person_name: name
      })
    });
    const data = await res.json();

    document.getElementById('sb-final-decision').textContent = data.final_decision;
    document.getElementById('sb-final-decision').style.color = data.final_decision.includes('Required') ? 'var(--accent-emerald)' : 'var(--text-muted)';
    document.getElementById('sb-latency').textContent = `Latency: ${data.latency_ms}ms`;

    // Layer 1
    const l1 = data.layer1_domain || {};
    const l1Badge = document.getElementById('sb-l1-badge');
    l1Badge.className = l1.passed ? 'badge badge-green' : 'badge badge-rose';
    l1Badge.textContent = l1.passed ? 'Net-New Domain' : 'Existing in CRM';
    document.getElementById('sb-l1-desc').textContent = l1.reason || l1.match_type;

    // Layer 2
    const l2 = data.layer2_title || {};
    const l2Badge = document.getElementById('sb-l2-badge');
    l2Badge.className = l2.passed ? 'badge badge-green' : 'badge badge-rose';
    l2Badge.textContent = l2.segment || 'Title Evaluated';
    document.getElementById('sb-l2-desc').textContent = l2.reason || (l2.passed ? 'Required Segment' : 'Excluded Segment');

    // Layer 3
    const l3 = data.layer3_indian_name || {};
    const l3Badge = document.getElementById('sb-l3-badge');
    l3Badge.className = l3.passed ? 'badge badge-green' : 'badge badge-amber';
    l3Badge.textContent = l3.passed ? 'Passed Filter' : 'Filtered';
    document.getElementById('sb-l3-desc').textContent = l3.reason;

    // Layer 4
    const l4 = data.layer4_company_dedup || {};
    const l4Badge = document.getElementById('sb-l4-badge');
    l4Badge.className = l4.passed ? 'badge badge-green' : 'badge badge-amber';
    l4Badge.textContent = l4.passed ? 'Accepted ★' : 'Duplicate ⊘';
    document.getElementById('sb-l4-desc').textContent = l4.reason;

  } catch (e) {
    alert('Error running sandbox evaluation: ' + e.message);
  }
}
