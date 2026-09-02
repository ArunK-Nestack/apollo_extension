function contactCheckerBackgroundLog(event, details = {}) {
  const timestamp = new Date().toISOString();
  console.log(`[ContactChecker BG] ${timestamp} ${event}`, details);
}

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;

  contactCheckerBackgroundLog("EXTENSION_TOGGLE_CLICKED", {
    tabId: tab.id,
    url: tab.url || ""
  });

  try {
    chrome.tabs.sendMessage(tab.id, { type: "TOGGLE_CONTACT_CHECKER" }, async (response) => {
      if (chrome.runtime.lastError || !response?.success) {
        try {
          await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            files: ["content.js"]
          });
          contactCheckerBackgroundLog("CONTENT_SCRIPT_INJECTED", { tabId: tab.id });
        } catch (err) {
          console.error("Could not inject Contact Checker:", err);
        }
      }
    });
  } catch (error) {
    console.error("Could not activate Contact Checker:", error);
  }
});

async function callBackendApi(endpoint, body) {
  const hosts = ["http://127.0.0.1:8000", "http://localhost:8000"];
  let lastError = null;

  for (const host of hosts) {
    try {
      const response = await fetch(`${host}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.status} ${response.statusText}`);
      }

      return await response.json();
    } catch (err) {
      lastError = err;
    }
  }

  throw lastError || new Error("Unable to connect to Contact Checker Backend on port 8000.");
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  let endpoint;
  let body;

  if (message.type === "CHECK_EMAILS") {
    endpoint = "/check";
    body = { emails: message.emails };
  } else if (message.type === "MATCH_APOLLO") {
    endpoint = "/match-apollo";
    body = {
      contacts: message.contacts,
      batch: message.batch || "batch_1",
      title_guardrail_enabled: message.title_guardrail_enabled !== false,
      indian_name_guardrail_enabled: message.indian_name_guardrail_enabled !== false
    };
  } else if (message.type === "SYNC_SAVED_LEADS") {
    endpoint = "/sync-saved-leads";
    body = {
      batch: message.batch || "batch_1",
      contacts: message.contacts || [],
      replace_all: Boolean(message.replace_all)
    };
  } else if (message.type === "EVALUATE_PENDING_TITLES" || message.type === "EVALUATE_PENDING_BATCH") {
    endpoint = "/evaluate-pending-titles";
    body = {
      batch: message.batch || "batch_1",
      titles: message.titles || [],
      names: message.names || []
    };
  } else if (message.type === "FLUSH_QUEUES") {
    endpoint = "/flush-pending-queues";
    body = {};
  } else {
    return;
  }

  const startedAt = performance.now();

  contactCheckerBackgroundLog("API_REQUEST_STARTED", {
    endpoint,
    contactCount: Array.isArray(body.contacts) ? body.contacts.length : undefined,
    emailCount: Array.isArray(body.emails) ? body.emails.length : undefined
  });

  callBackendApi(endpoint, body)
    .then((data) => {
      const durationMs = Math.round(performance.now() - startedAt);
      contactCheckerBackgroundLog("API_REQUEST_COMPLETE", {
        endpoint,
        durationMs,
        summary: data.summary || null
      });

      sendResponse({
        success: true,
        results: data.results,
        activity: data.activity || [],
        summary: data.summary || {}
      });
    })
    .catch((error) => {
      const durationMs = Math.round(performance.now() - startedAt);
      console.error("Contact Checker API error:", error);
      contactCheckerBackgroundLog("API_REQUEST_ERROR", {
        endpoint,
        durationMs,
        error: error?.message || String(error)
      });

      sendResponse({
        success: false,
        error: error.message
      });
    });

  return true;
});

// Keep-alive heartbeat to prevent Service Worker sleep drops
chrome.alarms.create("keepAlive", { periodInMinutes: 0.33 }); // ~20 seconds
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "keepAlive") {
    // console.log("Heartbeat"); 
  }
});
