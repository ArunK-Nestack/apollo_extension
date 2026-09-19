// ============================================================
// Enrich.so Standalone Extension Background Service Worker
// ============================================================

function enrichLog(event, details = {}) {
  const timestamp = new Date().toISOString();
  console.log(`[EnrichMatcher BG] ${timestamp} ${event}`, details);
}

// Toggle on action click
chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id) return;

  enrichLog("EXTENSION_TOGGLE_CLICKED", {
    tabId: tab.id,
    url: tab.url || ""
  });

  try {
    chrome.tabs.sendMessage(tab.id, { type: "TOGGLE_ENRICH_CHECKER" }, async (response) => {
      if (chrome.runtime.lastError || !response?.success) {
        try {
          await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            files: ["content.js"]
          });
          await chrome.scripting.insertCSS({
            target: { tabId: tab.id },
            files: ["styles.css"]
          });
          enrichLog("CONTENT_SCRIPT_INJECTED", { tabId: tab.id });
        } catch (err) {
          console.error("Could not inject Enrich Checker:", err);
        }
      }
    });
  } catch (error) {
    console.error("Could not activate Enrich Checker:", error);
  }
});

// Call backend API on port 8000 with clean timeout handling
async function callBackendApi(endpoint, body) {
  const host = "http://127.0.0.1:8000";
  const controller = new AbortController();
  const timeoutId = setTimeout(() => {
    controller.abort(new Error("Timeout: Backend took longer than 30 seconds"));
  }, 30000);

  try {
    const response = await fetch(`${host}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal
    });
    clearTimeout(timeoutId);

    if (!response.ok) {
      const errText = await response.text().catch(() => "");
      throw new Error(`API error ${response.status}: ${errText || response.statusText}`);
    }

    return await response.json();
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === "AbortError" || String(err).includes("aborted")) {
      throw new Error("Backend request timed out (30s). Please check if backend is running on port 8000.");
    }
    throw err;
  }
}

// 100% Detection-Free Hardware-Level Mouse Click Dispatcher
async function dispatchHardwareClick(tabId, x, y) {
  const target = { tabId };
  try {
    await chrome.debugger.attach(target, "1.3");

    // 1. Move mouse to target coordinates
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseMoved",
      x: Math.round(x),
      y: Math.round(y)
    });

    // 2. Mouse Press (left button)
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mousePressed",
      x: Math.round(x),
      y: Math.round(y),
      button: "left",
      clickCount: 1
    });

    // 3. Realistic human click duration (65 - 110ms)
    await new Promise((r) => setTimeout(r, 65 + Math.random() * 45));

    // 4. Mouse Release (generates isTrusted: true event)
    await chrome.debugger.sendCommand(target, "Input.dispatchMouseEvent", {
      type: "mouseReleased",
      x: Math.round(x),
      y: Math.round(y),
      button: "left",
      clickCount: 1
    });

    await chrome.debugger.detach(target);
    return { success: true, hardware: true };
  } catch (err) {
    enrichLog("DEBUGGER_DISPATCH_FALLBACK", { error: err.message });
    try {
      await chrome.debugger.detach(target);
    } catch (_) {}
    return { success: false, fallback: true, error: err.message };
  }
}

// Message Router
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "MATCH_ENRICH") {
    callBackendApi("/match-enrich", {
      contacts: message.contacts,
      companies: message.companies || [],
      batch: message.batch || "enrich_batch_1",
      collect_mode: message.collect_mode || "companies",
      title_guardrail_enabled: message.title_guardrail_enabled !== false,
      auto_save: message.auto_save !== false
    })
      .then((data) => sendResponse({ success: true, data }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true; // Keep async channel open
  }

  if (message.type === "DISPATCH_HARDWARE_CLICK") {
    const tabId = sender.tab?.id;
    if (!tabId) {
      sendResponse({ success: false, error: "No active tab ID found" });
      return true;
    }

    dispatchHardwareClick(tabId, message.x, message.y)
      .then((res) => sendResponse(res))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }
});
