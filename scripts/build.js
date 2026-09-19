#!/usr/bin/env node

/**
 * Enterprise Production Build Script
 * Validates and compiles both Apollo.io and Enrich.so Chrome Extensions.
 *
 * Steps:
 *  1. Validates JavaScript syntax for content.js and background.js across all extensions.
 *  2. Validates JSON structure of manifest.json files.
 *  3. Compiles production assets into `dist/` (Apollo) and `dist/enrich/` (Enrich.so).
 *  4. Reports bundle statistics ready for Chrome "Load Unpacked".
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '..');
const APOLLO_SRC = path.join(ROOT, 'extensions');
const ENRICH_SRC = path.join(ROOT, 'extensions_enrich');
const DIST_DIR = path.join(ROOT, 'dist');
const ENRICH_DIST = path.join(DIST_DIR, 'enrich');

console.log('======================================================================');
console.log('>>> [BUILD] Compiling Chrome Extensions Production Bundles');
console.log('======================================================================');

const t0 = Date.now();

function buildExtension(name, srcDir, destDir, additionalFiles = []) {
  console.log(`\n--- Building ${name} Extension ---`);
  if (!fs.existsSync(srcDir)) {
    console.error(`  [Error] Source directory not found: ${srcDir}`);
    process.exit(1);
  }

  // 1. Validate manifest.json
  const manifestPath = path.join(srcDir, 'manifest.json');
  if (!fs.existsSync(manifestPath)) {
    console.error(`  [Error] manifest.json missing from ${srcDir}`);
    process.exit(1);
  }

  try {
    const rawManifest = fs.readFileSync(manifestPath, 'utf8');
    const manifest = JSON.parse(rawManifest);
    if (!manifest.name || !manifest.version || manifest.manifest_version !== 3) {
      throw new Error('manifest.json must specify name, version, and manifest_version: 3');
    }
    console.log(`  ✓ Manifest validated: "${manifest.name}" v${manifest.version} (MV3)`);
  } catch (err) {
    console.error(`  ✗ manifest.json validation failed: ${err.message}`);
    process.exit(1);
  }

  // 2. Validate JavaScript syntax
  const jsFiles = ['content.js', 'background.js'];
  for (const file of jsFiles) {
    const filePath = path.join(srcDir, file);
    if (!fs.existsSync(filePath)) {
      console.error(`  ✗ Required file missing: ${file}`);
      process.exit(1);
    }
    try {
      const code = fs.readFileSync(filePath, 'utf8');
      new vm.Script(code, { filename: file });
      console.log(`  ✓ Syntax check passed: ${file} (${(code.length / 1024).toFixed(1)} KB)`);
    } catch (err) {
      console.error(`  ✗ Syntax error in ${file}: ${err.message}`);
      process.exit(1);
    }
  }

  // 3. Prepare target destination
  if (!fs.existsSync(destDir)) {
    fs.mkdirSync(destDir, { recursive: true });
  }

  // 4. Copy assets
  const assetsToCopy = ['manifest.json', 'content.js', 'background.js', ...additionalFiles];
  let totalBytes = 0;
  for (const asset of assetsToCopy) {
    const src = path.join(srcDir, asset);
    if (!fs.existsSync(src)) continue;
    const dest = path.join(destDir, asset);
    fs.copyFileSync(src, dest);
    const stat = fs.statSync(dest);
    totalBytes += stat.size;
    console.log(`  ✓ Copied: ${asset} (${(stat.size / 1024).toFixed(1)} KB)`);
  }

  return totalBytes;
}

// Ensure dist/ exists
if (!fs.existsSync(DIST_DIR)) {
  fs.mkdirSync(DIST_DIR, { recursive: true });
}

// Clean old files in dist (keep zip archives)
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

// 1. Build Apollo Extension into dist/ (and dist/apollo/)
const apolloBytes = buildExtension('Apollo.io', APOLLO_SRC, DIST_DIR);
const apolloSubDir = path.join(DIST_DIR, 'apollo');
buildExtension('Apollo.io (Subfolder)', APOLLO_SRC, apolloSubDir);

// 2. Build Enrich.so Extension into dist/enrich/
const enrichBytes = buildExtension('Enrich.so', ENRICH_SRC, ENRICH_DIST, ['styles.css']);

const elapsed = Date.now() - t0;

console.log('\n======================================================================');
console.log(`BUILD SUCCESSFUL in ${elapsed}ms!`);
console.log(`Apollo Bundle: ${(apolloBytes / 1024).toFixed(1)} KB -> ${DIST_DIR}`);
console.log(`Enrich Bundle: ${(enrichBytes / 1024).toFixed(1)} KB -> ${ENRICH_DIST}`);
console.log('\nTo load in Google Chrome:');
console.log('  1. Navigate to chrome://extensions');
console.log('  2. Enable "Developer mode" (top right toggle)');
console.log('  3. Click "Load unpacked" and select "dist" (for Apollo) or "dist/enrich" (for Enrich.so).');
console.log('======================================================================\n');
