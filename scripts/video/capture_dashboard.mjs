#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
//
// Capture layer for the evidence-first demo video (docs/demo/VIDEO_PLAN.md).
//
// Opens the LIVE public MemoryStand dashboard, clicks "Load seeded demo tenant" so the
// preview strip fills with real /recall results from the deployed API, waits for the
// live health pill to go green, and screenshots it. No mock data, no local server --
// this is the same public URL a judge would open.
//
// Playwright is not installed in this repo. Point PLAYWRIGHT_PACKAGE at an existing
// installation (see docs/demo/VIDEO_PLAN.md's Build section) rather than npm-installing
// anything here.
//
// Usage:
//   PLAYWRIGHT_PACKAGE=/path/to/playwright node scripts/video/capture_dashboard.mjs

import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..");
const OUT_DIR = path.join(ROOT, "artifacts", "video", "capture", "screenshots");

const DASHBOARD_URL = "https://main.d19xad9aeccy3e.amplifyapp.com";

async function loadPlaywright() {
  const pkgDir = process.env.PLAYWRIGHT_PACKAGE;
  if (pkgDir) {
    const entry = path.join(pkgDir, "index.mjs");
    if (!existsSync(entry)) {
      throw new Error(`PLAYWRIGHT_PACKAGE=${pkgDir} has no index.mjs at ${entry}`);
    }
    return import(pathToFileURL(entry).href);
  }
  return import("playwright");
}

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  const { chromium } = await loadPlaywright();

  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

    console.log(`navigating to ${DASHBOARD_URL}`);
    // "networkidle" can never fire on this page: the status pill's own health check
    // and the preview strip's auto-load keep the network non-idle well past a normal
    // page load. Wait for "load" instead, then poll explicit app-state conditions below
    // (the status dot, then the preview result) rather than a network-quiescence proxy.
    await page.goto(DASHBOARD_URL, { waitUntil: "load", timeout: 45_000 });

    // The status pill starts "checking..." and flips to a green dot once /health
    // resolves. Wait for that rather than a fixed sleep, so the capture always shows
    // a settled, truthful connection state.
    await page.waitForFunction(
      () => {
        const dot = document.querySelector("#statusDot");
        return !!dot && dot.className.includes("ok");
      },
      { timeout: 45_000 },
    );
    console.log("live API status pill: connected");

    await page.click("#loadSeededDemo");
    // The dashboard auto-runs this same preview fetch on page load, so the click may
    // race a spinner that is already mid-flight. Wait for the click's OWN spinner to
    // appear and then clear, rather than for text content that both the initial and
    // the click-triggered load use identically.
    await page.waitForSelector("#previewResult .spinner", { timeout: 10_000 }).catch(() => {});
    await page.waitForFunction(
      () => {
        const el = document.querySelector("#previewResult");
        if (!el) return false;
        if (el.querySelector(".spinner")) return false;
        return !!el.querySelector(".preview-summary, .error-banner, .memory-row");
      },
      { timeout: 20_000 },
    );
    // Let any final re-layout (fonts, badges) settle before the screenshot.
    await page.waitForTimeout(500);
    console.log("seeded demo tenant loaded");

    const heroPath = path.join(OUT_DIR, "01-dashboard-hero.png");
    await page.screenshot({ path: heroPath, clip: { x: 0, y: 0, width: 1920, height: 760 } });
    console.log(`wrote ${heroPath}`);

    const fullPath = path.join(OUT_DIR, "02-dashboard-full.png");
    await page.screenshot({ path: fullPath, fullPage: false });
    console.log(`wrote ${fullPath}`);
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
