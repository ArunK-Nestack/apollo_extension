// MillionVerifier Station Controller

function startVerification() {
  const batch = document.getElementById('mv-batch-select').value;
  if (!batch) {
    if (window.showToast) window.showToast('No Batch Selected', 'Please select an enriched batch first.', 'warning');
    return;
  }
  window.launchTask('verify', batch);
}
