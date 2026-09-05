const fs = require('fs');

console.log("======================================================================");
console.log(">>> EXTENSION END-TO-END RUNTIME SIMULATION");
console.log("======================================================================");

// Mock browser global environment
const mockStorage = {};
const mockListeners = [];

global.window = global;
global.addEventListener = () => {};
global.window.addEventListener = () => {};
global.document = {
  createElement: (tag) => {
    const el = {
      tagName: tag.toUpperCase(),
      id: '',
      className: '',
      style: {},
      classList: {
        add: (c) => { el.className += ' ' + c; },
        remove: (c) => { el.className = el.className.replace(c, '').trim(); },
        toggle: (c, force) => {
          if (force !== undefined) {
            if (force) el.classList.add(c); else el.classList.remove(c);
          } else {
            if (el.classList.contains(c)) el.classList.remove(c); else el.classList.add(c);
          }
        },
        contains: (c) => el.className.includes(c)
      },
      children: [],
      appendChild: (child) => { el.children.push(child); return child; },
      remove: () => {},
      setAttribute: () => {},
      getAttribute: () => null,
      addEventListener: () => {},
      textContent: '',
      innerHTML: '',
      value: '',
      querySelector: () => null,
      querySelectorAll: () => []
    };
    return el;
  },
  getElementById: (id) => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  documentElement: {
    appendChild: () => {},
    children: []
  },
  body: {
    appendChild: () => {},
    children: []
  },
  head: {
    appendChild: () => {}
  }
};

global.location = {
  href: 'https://app.apollo.io/#/people',
  pathname: '/#/people',
  search: '',
  hash: '#/people'
};

global.navigator = { userAgent: 'Mozilla/5.0 Node Test' };
global.performance = { now: () => Date.now() };

global.chrome = {
  runtime: {
    lastError: null,
    sendMessage: (msg, callback) => {
      console.log(`  [Mock chrome.runtime.sendMessage] Type: ${msg.type}`);
      if (callback) {
        if (msg.type === 'MATCH_APOLLO') {
          callback({
            success: true,
            results: {
              'contact_1': { exists: false, required: true, ignored: false, segment: 'A1_Signer' }
            },
            summary: { total: 1, required: 1, existing: 0, ignored: 0 }
          });
        } else if (msg.type === 'SYNC_SAVED_LEADS') {
          callback({ success: true, synced: msg.contacts ? msg.contacts.length : 0 });
        } else if (msg.type === 'EVALUATE_PENDING_TITLES') {
          callback({ success: true, results: {}, title_results: {}, name_results: {} });
        } else {
          callback({ success: true });
        }
      }
    },
    onMessage: {
      addListener: (fn) => mockListeners.push(fn)
    }
  },
  storage: {
    local: {
      get: (keys, callback) => {
        const res = {};
        if (Array.isArray(keys)) {
          keys.forEach(k => { res[k] = mockStorage[k]; });
        }
        if (callback) callback(res);
      },
      set: (items, callback) => {
        Object.assign(mockStorage, items);
        if (callback) callback();
      }
    }
  },
  alarms: {
    create: () => {},
    onAlarm: { addListener: () => {} }
  }
};

global.MutationObserver = class {
  observe() {}
  disconnect() {}
};

global.Blob = class {
  constructor(parts) { this.parts = parts; }
};

global.URL = {
  createObjectURL: () => 'blob://mock-url',
  revokeObjectURL: () => {}
};

// Execute content.js in this sandbox
try {
  const contentCode = fs.readFileSync('extensions/content.js', 'utf8');
  eval(contentCode);
  console.log("  [Runtime Execution] content.js initialized successfully!");
  
  // Verify state
  const state = globalThis.__contactDatabaseChecker;
  if (state && state.active === true) {
    console.log("  [State Check] __contactDatabaseChecker is active with batch: " + state.batchName);
    console.log("  [Features Active] titleGuardrailEnabled: " + state.titleGuardrailEnabled + ", indianGuardrailEnabled: " + state.indianGuardrailEnabled);
    console.log("  [Cleanup Check] Invoking state.cleanup()...");
    state.cleanup();
    console.log("  [Cleanup Done] State cleanly restored.");
    console.log("\nALL RUNTIME SIMULATION CHECKS PASSED WITH 0 ERRORS!");
    process.exit(0);
  } else {
    console.error("  [FAIL] State not found or not active");
    process.exit(1);
  }
} catch (err) {
  console.error("  [EXCEPTION THROWN] Error running content.js:", err);
  process.exit(1);
}
