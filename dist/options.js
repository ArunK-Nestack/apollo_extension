const backendUrl = document.getElementById("backendUrl");
const apiToken = document.getElementById("apiToken");
const status = document.getElementById("status");

async function loadSettings() {
  const stored = await chrome.storage.local.get(["contactCheckerBackendUrl", "contactCheckerApiToken"]);
  backendUrl.value = stored.contactCheckerBackendUrl || "http://127.0.0.1:8000";
  apiToken.value = stored.contactCheckerApiToken || "";
}

document.getElementById("save").addEventListener("click", async () => {
  const url = backendUrl.value.trim().replace(/\/+$/, "");
  if (!/^https?:\/\/[^\s]+$/i.test(url)) {
    status.textContent = "Enter a valid HTTP or HTTPS backend URL.";
    return;
  }
  const parsed = new URL(url);
  const isLocal = ["127.0.0.1", "localhost"].includes(parsed.hostname);
  if (!isLocal && parsed.protocol !== "https:") {
    status.textContent = "Deployed backends must use HTTPS.";
    return;
  }
  if (!isLocal) {
    const granted = await chrome.permissions.request({ origins: [`${parsed.origin}/*`] });
    if (!granted) {
      status.textContent = "Backend permission was not granted.";
      return;
    }
  }
  await chrome.storage.local.set({
    contactCheckerBackendUrl: url,
    contactCheckerApiToken: apiToken.value.trim()
  });
  status.textContent = "Connection settings saved.";
});

loadSettings();
