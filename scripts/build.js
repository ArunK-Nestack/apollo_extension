#!/usr/bin/env node

/**
 * Apollo Extension Production Build Script
 * Equivalent to `npm run build` in modern web frameworks.
 *
 * Steps:
 *  1. Validates JavaScript syntax for content.js and background.js.
 *  2. Validates JSON structure of manifest.json.
 *  3. Creates a clean `dist/` directory.
 *  4. Copies validated extension assets into `dist/`.
 *  5. Reports bundle statistics ready for Chrome "Load Unpacked".
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const SRC_DIR = path.join(ROOT, 'extensions');
const DIST_DIR = path.join(ROOT, 'dist');

console.log('======================================================================');
console.log('>>> [BUILD] Compiling Apollo Extension Production Bundle');
console.log('======================================================================');

const t0 = Date.now();

// 1. Validate Source Directory
if (!fs.existsSync(SRC_DIR)) {
  console.error(`[Error] Source directory not found: ${SRC_DIR}`);
  process.exit(1);
}

// 2. Validate manifest.json
const manifestPath = path.join(SRC_DIR, 'manifest.json');
if (!fs.existsSync(manifestPath)) {
  console.error(`[Error] manifest.json missing from ${SRC_DIR}`);
  process.exit(1);
}

let manifest;
try {
  const rawManifest = fs.readFileSync(manifestPath, 'utf8');
  manifest = JSON.parse(rawManifest);
  if (!manifest.name || !manifest.version || manifest.manifest_version !== 3) {
    throw new Error('manifest.json must specify name, version, and manifest_version: 3');
  }
  console.log(`  ✓ Manifest validated: "${manifest.name}" v${manifest.version} (MV${manifest.manifest_version})`);
} catch (err) {
  console.error(`  ✗ manifest.json validation failed: ${err.message}`);
  process.exit(1);
}

// 3. Validate JavaScript Files
const jsFiles = ['content.js', 'background.js'];
for (const file of jsFiles) {
  const filePath = path.join(SRC_DIR, file);
  if (!fs.existsSync(filePath)) {
    console.error(`  ✗ Required file missing: ${file}`);
    process.exit(1);
  }
  try {
    const code = fs.readFileSync(filePath, 'utf8');
    // Compile to check syntax without running
    new vm.Script(code, { filename: file });
    console.log(`  ✓ Syntax check passed: ${file} (${(code.length / 1024).toFixed(1)} KB)`);
  } catch (err) {
    console.error(`  ✗ Syntax error in ${file}: ${err.message}`);
    process.exit(1);
  }
}

// 4. Clean & Prepare dist/
if (!fs.existsSync(DIST_DIR)) {
  fs.mkdirSync(DIST_DIR, { recursive: true });
}

// Clean old files in dist (keep zip or certificates if any)
const existingDistFiles = fs.readdirSync(DIST_DIR);
for (const f of existingDistFiles) {
  if (!f.endsWith('.zip') && !f.endsWith('.pem') && !f.endsWith('.crx')) {
    const full = path.join(DIST_DIR, f);
    if (fs.statSync(full).isDirectory()) {
      fs.rmSync(full, { recursive: true, force: true });
    } else {
      fs.unlinkSync(full);
    }
  }
}

// 5. Copy Verified Assets to dist/
const assetsToCopy = ['manifest.json', 'content.js', 'background.js'];
let totalBytes = 0;
for (const asset of assetsToCopy) {
  const src = path.join(SRC_DIR, asset);
  const dest = path.join(DIST_DIR, asset);
  fs.copyFileSync(src, dest);
  const stat = fs.statSync(dest);
  totalBytes += stat.size;
  console.log(`  ✓ Copied to dist/: ${asset} (${(stat.size / 1024).toFixed(1)} KB)`);
}

const elapsed = Date.now() - t0;

console.log('\n======================================================================');
console.log(`BUILD SUCCESSFUL in ${elapsed}ms! Total Bundle Size: ${(totalBytes / 1024).toFixed(1)} KB`);
console.log(`Output Directory: ${DIST_DIR}`);
console.log('To load in Chrome:');
console.log('  1. Navigate to chrome://extensions');
console.log('  2. Enable "Developer mode" (top right toggle)');
console.log('  3. Click "Load unpacked" and select the "dist" folder.');
console.log('======================================================================\n');
