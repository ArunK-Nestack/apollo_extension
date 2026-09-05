#!/usr/bin/env node

/**
 * Apollo Extension Packaging Script
 * Runs the build first, then packages dist/ into a distribution zip archive.
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const DIST_DIR = path.join(ROOT, 'dist');
const ZIP_FILE = path.join(DIST_DIR, 'apollo-extension.zip');

// 1. Run build first
console.log('Running build step prior to packaging...\n');
require('./build.js');

console.log('Packaging extension bundle into zip archive...');

if (fs.existsSync(ZIP_FILE)) {
  fs.unlinkSync(ZIP_FILE);
}

try {
  if (process.platform === 'win32') {
    // Windows PowerShell native compression
    const cmd = `powershell -Command "Compress-Archive -Path '${DIST_DIR}\\manifest.json', '${DIST_DIR}\\content.js', '${DIST_DIR}\\background.js' -DestinationPath '${ZIP_FILE}' -Force"`;
    execSync(cmd, { stdio: 'inherit' });
  } else {
    // Unix/Linux zip command
    const cmd = `cd "${DIST_DIR}" && zip -r "apollo-extension.zip" manifest.json content.js background.js`;
    execSync(cmd, { stdio: 'inherit' });
  }

  const stat = fs.statSync(ZIP_FILE);
  console.log(`\n✓ Successfully created distribution package:`);
  console.log(`  Path: ${ZIP_FILE}`);
  console.log(`  Size: ${(stat.size / 1024).toFixed(1)} KB`);
  console.log(`Ready for distribution or deployment!`);
} catch (err) {
  console.error(`Packaging failed: ${err.message}`);
  process.exit(1);
}
