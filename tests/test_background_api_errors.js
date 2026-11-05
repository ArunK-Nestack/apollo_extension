const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

function loadBackground(fetchImpl) {
  const context = {
    console,
    performance,
    AbortController,
    setTimeout,
    clearTimeout,
    fetch: fetchImpl,
    chrome: {
      action: { onClicked: { addListener() {} } },
      tabs: { sendMessage() {} },
      scripting: { executeScript() {} },
      storage: { local: { async get() { return {}; } } },
      runtime: { lastError: null, onMessage: { addListener() {} } },
      alarms: { create() {}, onAlarm: { addListener() {} } }
    }
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync("extensions/background.js", "utf8"), context);
  return context;
}

(async () => {
  let attempts = 0;
  const context = loadBackground(async () => {
    attempts += 1;
    throw new TypeError("Failed to fetch");
  });

  await assert.rejects(
    context.callBackendApi("/match-apollo", { contacts: [] }),
    /Cannot reach backend at http:\/\/127\.0\.0\.1:8000/
  );
  assert.strictEqual(attempts, 1, "default loopback backend must be attempted once");
  console.log("Background API error handling tests passed.");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
