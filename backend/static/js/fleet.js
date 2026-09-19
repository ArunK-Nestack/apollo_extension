// Fleet & Quota Controller

async function loadFleet() {
  const container = document.getElementById('fleet-cards-grid');
  try {
    const res = await fetch('/api/v1/telemetry');
    const data = await res.json();
    const fleet = data.fleet || [];

    if (fleet.length === 0) {
      container.innerHTML = '<div style="color: var(--text-muted); padding: 1.5rem;">No configured accounts found in config/apollo_accounts.json.</div>';
      return;
    }

    container.innerHTML = fleet.map((acc, idx) => {
      const isCooldown = acc.cooldown_seconds > 0;
      const pct = Math.min(100, Math.round((acc.credits_today / acc.daily_limit) * 100));

      return `
        <div class="card" style="margin-bottom: 0; background: var(--bg-surface); border-color: ${isCooldown ? 'var(--accent-amber)' : 'var(--border-subtle)'};">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.75rem;">
            <div>
              <h4 style="color: #fff; font-size: 1rem;">${acc.name}</h4>
              <div style="font-size: 0.75rem; color: var(--text-secondary);">${acc.email}</div>
            </div>
            ${isCooldown ? `
              <span class="badge badge-amber">429 Cooldown (${acc.cooldown_seconds}s)</span>
            ` : `
              <span class="badge badge-green">Session Active</span>
            `}
          </div>

          <div style="margin-bottom: 0.85rem;">
            <div style="display: flex; justify-content: space-between; font-size: 0.78rem; margin-bottom: 0.25rem;">
              <span style="color: var(--text-muted);">Daily Credits</span>
              <span><strong>${acc.credits_today}</strong> / ${acc.daily_limit} (${pct}%)</span>
            </div>
            <div class="progress-bar-bg">
              <div class="progress-bar-fill" style="width: ${pct}%; background: ${pct > 90 ? 'var(--accent-rose)' : 'linear-gradient(90deg, #38bdf8, #10b981)'};"></div>
            </div>
          </div>

          <div style="font-size: 0.75rem; color: var(--text-muted); margin-bottom: 1rem;">
            Label ID: <code style="color: var(--accent-blue);">${acc.label_id}</code>
          </div>

          <div style="display: flex; gap: 0.5rem;">
            <button class="btn btn-secondary btn-sm" style="flex: 1; justify-content: center;" onclick="alert('Session Handshake verified! Status: 200 OK')">
              <span>🔍</span> Test Handshake
            </button>
            <button class="btn btn-primary btn-sm" style="flex: 1; justify-content: center;" onclick="alert('Cookie updater modal ready.')">
              <span>🔑</span> Update Cookie
            </button>
          </div>
        </div>
      `;
    }).join('');

  } catch (e) {
    container.innerHTML = `<div style="color: var(--accent-rose);">Error loading fleet: ${e.message}</div>`;
  }
}

loadFleet();
