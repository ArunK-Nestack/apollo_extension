#!/usr/bin/env node

/**
 * Enterprise Extension Packaging Script
 * Compiles and packages both Apollo.io and Enrich.so extensions into standalone distribution ZIP archives.
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const DIST_DIR = path.join(ROOT, 'dist');
const APOLLO_ZIP = path.join(DIST_DIR, 'apollo-extension.zip');
const ENRICH_ZIP = path.join(DIST_DIR, 'enrich-extension.zip');
const ENRICH_DIST = path.join(DIST_DIR, 'enrich');

// 1. Run build first
console.log('Running build step prior to packaging...\n');
require('./build.js');

console.log('Packaging extension bundles into zip archives...');

function packageZip(zipFile, filesList, folderCwd) {
  if (fs.existsSync(zipFile)) {
    fs.unlinkSync(zipFile);
  }

  if (process.platform === 'win32') {
    const pathsStr = filesList.map(f => `'${path.join(folderCwd, f)}'`).join(', ');
    const cmd = `powershell -Command "Compress-Archive -Path ${pathsStr} -DestinationPath '${zipFile}' -Force"`;
    execSync(cmd, { stdio: 'inherit' });
  } else {
    const filesStr = filesList.join(' ');
    const cmd = `cd "${folderCwd}" && zip -r "${path.basename(zipFile)}" ${filesStr}`;
    execSync(cmd, { stdio: 'inherit' });
  }

  const stat = fs.statSync(zipFile);
  console.log(`  ✓ Package created: ${path.basename(zipFile)} (${(stat.size / 1024).toFixed(1)} KB)`);
}

try {
  // Package Apollo
  packageZip(APOLLO_ZIP, ['manifest.json', 'content.js', 'background.js'], DIST_DIR);

  // Package Enrich.so
  packageZip(ENRICH_ZIP, ['manifest.json', 'content.js', 'background.js', 'styles.css'], ENRICH_DIST);

  console.log('\n======================================================================');
  console.log('ALL PACKAGING COMPLETED SUCCESSFULLY!');
  console.log(`  1. Apollo Extension Package: ${APOLLO_ZIP}`);
  console.log(`  2. Enrich Extension Package: ${ENRICH_ZIP}`);
  console.log('======================================================================\n');
} catch (err) {
  console.error(`Packaging failed: ${err.message}`);
  process.exit(1);
}
