// Freshsales CRM Sync Controller

function startCRMSync() {
  const file = document.getElementById('crm-file-select').value;
  const tag  = document.getElementById('crm-tag-input').value.trim();
  if (!file) {
    if (window.showToast) window.showToast('No Dataset Selected', 'Please select a verified Good dataset first.', 'warning');
    return;
  }
  window.launchTask('sync_crm', file.replace('.csv', ''), { custom_tag: tag || 'verified' });
}
