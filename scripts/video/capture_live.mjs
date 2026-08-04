#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
//
// MemoryStand -- live-capture layer for the submission demo video.
//
// This drives the REAL deployed dashboard (Amplify Hosting) against the REAL deployed
// API (a Lambda Function URL backed by CockroachDB Cloud), with Playwright, and saves:
//
//   1. PNG screenshots of the dashboard's landing state and all four panels after each
//      is actually driven (memory admission, incident feed / recall, the outcome gate,
//      cross-examine).
//   2. A JSON receipt file recording every API response byte the browser or this script
//      itself received, plus its SHA-256 digest, so every number the video shows can be
//      traced back to a response that was actually received -- not typed in by hand.
//
// Modelled on the LedgerLens reference (scripts/video/capture_evidence_first.mjs in the
// ledgerlens repo): same PLAYWRIGHT_PACKAGE resolution strategy, same
// fetch-bytes-then-hash discipline. This script fails loudly (throws, non-zero exit) if
// the dashboard or the API is unreachable, or if a driven action does not come back
// with a 2xx response -- it never produces a blank or placeholder frame silently.
//
// Usage:
//   MEMORYSTAND_SHARED_SECRET=... node scripts/video/capture_live.mjs
//   # or, with the memorystand AWS profile configured for SSM:
//   node scripts/video/capture_live.mjs
//
// Env overrides (all optional):
//   MEMORYSTAND_ROOT             repo root (default: cwd)
//   MEMORYSTAND_VIDEO_OUTPUT     output dir (default: <root>/artifacts/video/capture)
//   MEMORYSTAND_DASHBOARD_URL    default: https://main.d19xad9aeccy3e.amplifyapp.com
//   MEMORYSTAND_API_BASE         default: the deployed Lambda Function URL
//   MEMORYSTAND_TENANT_ID        default: the seeded demo tenant
//   MEMORYSTAND_AGENT_ID         default: the seeded demo agent
//   MEMORYSTAND_SHARED_SECRET    the x-memorystand-secret value for /ingest and /decide;
//                                 if unset, this script shells out to `aws ssm
//                                 get-parameter` using MEMORYSTAND_AWS_PROFILE
//   MEMORYSTAND_AWS_PROFILE      default: memorystand
//   PLAYWRIGHT_PACKAGE           explicit playwright package directory

import { execFileSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const root = path.resolve(process.env.MEMORYSTAND_ROOT || process.cwd());
const outputRoot = path.resolve(
  process.env.MEMORYSTAND_VIDEO_OUTPUT || path.join(root, "artifacts/video/capture"),
);

const dashboardUrl =
  process.env.MEMORYSTAND_DASHBOARD_URL || "https://main.d19xad9aeccy3e.amplifyapp.com";
const apiBase = (
  process.env.MEMORYSTAND_API_BASE ||
  "https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws"
).replace(/\/+$/, "");
const tenantId = process.env.MEMORYSTAND_TENANT_ID || "9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10";
const agentId = process.env.MEMORYSTAND_AGENT_ID || "1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061";
const awsProfile = process.env.MEMORYSTAND_AWS_PROFILE || "memorystand";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

async function exists(candidate) {
  try {
    await fs.access(candidate);
    return true;
  } catch {
    return false;
  }
}

async function findPlaywrightPackage() {
  const configured = process.env.PLAYWRIGHT_PACKAGE;
  const candidates = [
    configured,
    path.join(root, "artifacts/video-tools/node_modules/playwright"),
    "/Users/tom/Documents/GitHub/HVE-V1.0/website/node_modules/playwright",
  ].filter(Boolean);

  const npxRoot = path.join(os.homedir(), ".npm/_npx");
  if (await exists(npxRoot)) {
    for (const entry of await fs.readdir(npxRoot)) {
      candidates.push(path.join(npxRoot, entry, "node_modules/playwright"));
    }
  }

  for (const candidate of candidates) {
    if (candidate && (await exists(path.join(candidate, "index.mjs")))) {
      return candidate;
    }
  }
  return null;
}

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    const packageRoot = await findPlaywrightPackage();
    if (!packageRoot) {
      throw new Error(
        "Playwright was not found. Set PLAYWRIGHT_PACKAGE to a playwright package " +
          "directory (e.g. .../HVE-V1.0/website/node_modules/playwright).",
      );
    }
    return import(pathToFileURL(path.join(packageRoot, "index.mjs")).href);
  }
}

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

async function fetchBytes(url, opts) {
  const response = await fetch(url, {
    ...opts,
    headers: {
      "User-Agent": "MemoryStand demo video capture",
      Accept: "application/json",
      ...(opts && opts.headers ? opts.headers : {}),
    },
  });
  const bytes = Buffer.from(await response.arrayBuffer());
  return { response, bytes };
}

function receiptFromBytes(label, url, response, bytes, extra) {
  let payload = null;
  try {
    payload = JSON.parse(bytes.toString("utf8"));
  } catch {
    payload = null;
  }
  // Node's native fetch Response exposes .status/.ok as properties; Playwright's
  // page-observed Response exposes them as methods. Normalize both shapes so the
  // receipt always serializes an actual number/boolean instead of silently dropping a
  // non-serializable function reference from the JSON output.
  const status = typeof response.status === "function" ? response.status() : response.status;
  const ok = typeof response.ok === "function" ? response.ok() : response.ok;
  return {
    label,
    url,
    status,
    ok,
    sha256: sha256(bytes),
    bytes: bytes.length,
    payload,
    ...(extra || {}),
  };
}

function resolveSharedSecret() {
  if (process.env.MEMORYSTAND_SHARED_SECRET) {
    return process.env.MEMORYSTAND_SHARED_SECRET;
  }
  try {
    const out = execFileSync(
      "aws",
      [
        "ssm",
        "get-parameter",
        "--name",
        "/memorystand/shared_secret",
        "--with-decryption",
        "--region",
        "us-west-2",
        "--query",
        "Parameter.Value",
        "--output",
        "text",
        "--profile",
        awsProfile,
      ],
      { encoding: "utf8" },
    );
    const secret = out.trim();
    if (!secret) throw new Error("empty secret value returned by SSM");
    return secret;
  } catch (err) {
    throw new Error(
      "Could not resolve the MemoryStand shared secret. Set MEMORYSTAND_SHARED_SECRET " +
        "directly, or configure the '" +
        awsProfile +
        "' AWS profile for SSM access. Underlying error: " +
        (err && err.message ? err.message : String(err)),
    );
  }
}

async function screenshotPanel(page, selector, name) {
  const locator = page.locator(selector).first();
  await locator.waitFor({ state: "visible", timeout: 30000 });
  await locator.evaluate((element) => {
    const top = element.getBoundingClientRect().top + window.scrollY - 40;
    window.scrollTo({ top: Math.max(0, top), behavior: "instant" });
  });
  await page.waitForTimeout(500);
  const destination = path.join(outputRoot, name);
  await page.screenshot({ path: destination, fullPage: false });
  return destination;
}

// The deployed dashboard (frontend/app.js) hard-codes a 10-second client-side
// AbortController timeout on every fetch. A cold Lambda execution environment --
// reliably seen on the first mutating request of a fresh browser session, spinning up
// a new CockroachDB Cloud connection -- can take longer than that, which makes the
// dashboard itself abort the request (net::ERR_ABORTED at the network layer; no HTTP
// response ever arrives) and render its own "Timed out waiting for..." error banner.
// That is a real, observed property of this live deployment, not a flake to paper
// over silently -- so this retries the SAME user action (clicking submit again) up to
// a few times, logging every retry, until a real response comes back. A once-cold
// Lambda is warm for the rest of the session, so later actions normally succeed first try.
async function submitAndAwaitApiResponse(page, { submitSelector, resultHeroSelector, urlIncludes, maxAttempts = 3 }) {
  let lastError = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const [response] = await Promise.all([
        page.waitForResponse((r) => r.url().includes(urlIncludes), { timeout: 30000 }),
        page.locator(submitSelector).click(),
      ]);
      if (!response.ok()) {
        throw new Error(`HTTP ${response.status()}`);
      }
      await page.locator(resultHeroSelector).waitFor({ state: "visible", timeout: 15000 });
      return response;
    } catch (err) {
      lastError = err;
      console.warn(
        `  attempt ${attempt}/${maxAttempts} for a request matching '${urlIncludes}' did not complete ` +
          `(${err.message}); retrying the same click...`,
      );
      await page.waitForTimeout(1000);
    }
  }
  throw new Error(
    `Gave up waiting for a live response matching '${urlIncludes}' after ${maxAttempts} attempts: ` +
      `${lastError ? lastError.message : "unknown error"}`,
  );
}

async function addCaptureStyle(page) {
  await page.addStyleTag({
    content: `
      * { animation: none !important; transition: none !important; }
      html { scroll-behavior: auto !important; }
      ::-webkit-scrollbar { width: 0 !important; height: 0 !important; }
    `,
  });
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

await fs.mkdir(outputRoot, { recursive: true });

const sharedSecret = resolveSharedSecret();

console.log(`Dashboard: ${dashboardUrl}`);
console.log(`API base:  ${apiBase}`);
console.log(`Tenant:    ${tenantId}`);
console.log(`Output:    ${outputRoot}`);

// -- Phase 1: direct, unauthenticated GET fetches, before touching the browser at all.
// These prove the API is reachable independent of the dashboard, and their bytes are
// hashed exactly as received -- nothing here is rendered or reformatted first.

const directReceipts = {};

{
  const { response, bytes } = await fetchBytes(`${apiBase}/health`);
  const receipt = receiptFromBytes("health", `${apiBase}/health`, response, bytes);
  if (
    !receipt.ok ||
    !receipt.payload ||
    receipt.payload.database !== "reachable" ||
    receipt.payload.kill_switch !== false
  ) {
    throw new Error(
      `Live API health check failed or database not reachable: ${JSON.stringify(receipt.payload)}`,
    );
  }
  directReceipts.health = receipt;
  console.log(`GET /health -> ${receipt.status}, sha256=${receipt.sha256.slice(0, 12)}...`);
}

{
  const url = `${apiBase}/recall?tenant_id=${encodeURIComponent(tenantId)}&q=${encodeURIComponent(
    "on-call incident",
  )}&k=3`;
  const { response, bytes } = await fetchBytes(url);
  const receipt = receiptFromBytes("recall-direct", url, response, bytes);
  if (!receipt.ok || !receipt.payload || !Array.isArray(receipt.payload.results)) {
    throw new Error(`Live API /recall failed: HTTP ${receipt.status}`);
  }
  if (receipt.payload.results.length === 0) {
    throw new Error(
      "Live API /recall returned zero results for the seeded demo tenant -- the tenant " +
        "may have been reset. Re-seed it before capturing.",
    );
  }
  directReceipts.recall = receipt;
  console.log(
    `GET /recall -> ${receipt.status}, ${receipt.payload.results.length} result(s), ` +
      `sha256=${receipt.sha256.slice(0, 12)}...`,
  );
}

// -- Phase 2: drive the real dashboard with Playwright.

const { chromium } = await loadPlaywright();
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 1,
  colorScheme: "dark",
  locale: "en-US",
});
// frontend/app.js hard-codes a 10-second client-side AbortController timeout on every
// fetch (a UX safety net for a human on unreliable wifi). Measured round trips against
// this live deployment from this network are frequently 11-15 seconds even for a fast,
// successful request -- the deployed Lambda + CockroachDB Cloud round trip itself, not
// a cold-start artifact -- which means the dashboard's own guard aborts real, healthy
// requests before they finish. This capture's job is to observe REAL responses, not to
// reproduce a human's impatience threshold, so every fetch's AbortSignal is stripped
// before it reaches the network layer: the HTTP request and response that follow are
// still the genuine live traffic, just not truncated by a UI-only guard.
await context.addInitScript(() => {
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    if (init && "signal" in init) {
      const rest = { ...init };
      delete rest.signal;
      return realFetch(input, rest);
    }
    return realFetch(input, init);
  };
});

const page = await context.newPage();

// Every response the browser itself receives from the live API, captured as it happens
// (not re-fetched afterwards) -- this is the exact byte sequence the on-screen dashboard
// rendered from.
const browserApiReceipts = {};
page.on("response", async (response) => {
  const url = response.url();
  if (!url.startsWith(apiBase)) return;
  let key = null;
  if (url.includes("/ingest")) key = "ingest";
  else if (url.includes("/decide")) key = "decide";
  else if (url.includes("/confirm_outcome")) key = "confirm_outcome";
  else if (url.includes("/timemachine")) key = "timemachine-browser";
  if (!key) return;
  try {
    const bytes = await response.body();
    browserApiReceipts[key] = receiptFromBytes(key, url, response, bytes);
  } catch (err) {
    // A response can finish (e.g. a redirect or an aborted request) without a body;
    // that is not itself a failure of this capture -- the explicit waitForResponse
    // calls below are what this script actually depends on.
    console.warn(`  (could not read response body for ${key}: ${err.message})`);
  }
});

const screenshotFiles = [];
let decisionId = null;

try {
  await page.goto(dashboardUrl, { waitUntil: "networkidle", timeout: 60000 });
  await addCaptureStyle(page);

  // The dashboard's own connection pill and its seeded-demo preview strip each fire an
  // independent request to the live API on page load (see frontend/app.js:
  // checkConnection() and runPreview()). A cold Lambda execution environment can take
  // longer than the dashboard's own 10s client-side fetch timeout to answer the very
  // first request of a session, which aborts that one request and flips the pill to
  // "API unreachable" even though the API is actually fine (a following request
  // succeeds). Rather than fail loudly on what is a known cold-start artifact of the
  // dashboard's own client-side timeout, retry using the dashboard's own "Load seeded
  // demo tenant" button -- a real control an operator would click -- up to a few times
  // before concluding the API is genuinely unreachable.
  let dashboardReachable = false;
  let lastPreviewErrorText = "";
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      await page
        .locator("#statusText")
        .filter({ hasText: "API reachable" })
        .waitFor({ state: "visible", timeout: 20000 });
      await page.locator("#previewResult .preview-summary").waitFor({ state: "visible", timeout: 20000 });
      const previewErrorCount = await page.locator("#previewResult .error-banner").count();
      if (previewErrorCount === 0) {
        dashboardReachable = true;
        break;
      }
      lastPreviewErrorText = await page.locator("#previewResult .error-banner").innerText();
    } catch {
      // fall through to retry below
    }
    console.warn(`  attempt ${attempt}/5: dashboard not yet showing live data, retrying...`);
    await page.locator("#loadSeededDemo").click();
    await page.waitForTimeout(1000);
  }
  if (!dashboardReachable) {
    throw new Error(
      "Dashboard never reached a live 'API reachable' + real preview-data state after 5 attempts." +
        (lastPreviewErrorText ? ` Last preview error: ${lastPreviewErrorText}` : ""),
    );
  }

  screenshotFiles.push(await screenshotPanel(page, "body", "01-dashboard-landing.png"));
  console.log("captured landing state");

  // Fill the shared secret once; both /ingest and /decide read it from the same field.
  // The topbar is `position: sticky`, so this field stays on screen through every
  // panel screenshot below -- it MUST NOT render as plaintext on camera. Flipping the
  // input's own `type` to "password" masks it with dots in every future screenshot
  // without touching its `.value`, so /ingest and /decide still read the real secret.
  await page.locator("#sharedSecret").fill(sharedSecret);
  await page.evaluate(() => {
    document.getElementById("sharedSecret").setAttribute("type", "password");
  });

  // ---- Panel 2: memory admission (POST /ingest) --------------------------------
  await page.locator("#ingestContent").fill(
    "payments-service reads from orders_v2, per the current db-failover runbook.",
  );
  await page.locator("#ingestEntity").fill("payments-service");
  await page.locator("#ingestKey").fill("reads_from_table");
  await page.locator("#ingestValue").fill("orders_v2");
  await page.locator("#ingestSource").fill("runbook:db-failover");

  await submitAndAwaitApiResponse(page, {
    submitSelector: "#ingestSubmit",
    resultHeroSelector: "#ingestResult .verdict-hero",
    urlIncludes: "/ingest",
  });
  screenshotFiles.push(await screenshotPanel(page, "#panel-ingest", "02-memory-admission.png"));
  console.log("captured panel 2: memory admission");

  // Chain panel 2's output into panel 1, exactly the way an operator would in the UI:
  // click the dashboard's own "use as produced in panel 1" button rather than typing
  // the memory id in by hand.
  const useAsProducedButton = page.locator("#ingestResult button", { hasText: "use as produced in panel 1" });
  if (await useAsProducedButton.count()) {
    await useAsProducedButton.click();
  }

  // ---- Panel 1: incident feed / recall + decide (POST /decide) -----------------
  // backend/decisions.py parses task_id as a UUID (the dashboard's own placeholder text
  // "inc-4471" is a hint, not a valid value -- filling it verbatim 500s the request), so
  // this generates a real one, matching how scripts/demo.sh derives its own TASK_ID.
  const taskId = crypto.randomUUID();
  await page.locator("#decideTaskId").fill(taskId);

  await submitAndAwaitApiResponse(page, {
    submitSelector: "#decideSubmit",
    resultHeroSelector: "#decideResult .verdict-hero",
    urlIncludes: "/decide",
  });
  screenshotFiles.push(await screenshotPanel(page, "#panel-decide", "03-incident-feed-recall.png"));
  console.log("captured panel 1: incident feed / recall");

  decisionId = await page.locator("#confirmDecisionId").inputValue();
  if (!decisionId) {
    throw new Error("Panel 1's decide response did not populate a decision id.");
  }

  // ---- Panel 3: the outcome gate (POST /confirm_outcome, no auth required) -----
  await page.locator("#confirmRef").fill("INC-4471");

  await submitAndAwaitApiResponse(page, {
    submitSelector: "#confirmSubmit",
    resultHeroSelector: "#confirmResult .verdict-hero",
    urlIncludes: "/confirm_outcome",
  });
  const modelCallsText = await page.locator("#modelCallsNumber").innerText();
  if (modelCallsText.trim() !== "0") {
    throw new Error(
      `Expected the outcome-gate promotion path to make 0 model calls; the dashboard shows ${modelCallsText}.`,
    );
  }
  screenshotFiles.push(await screenshotPanel(page, "#panel-memorystand", "04-standing-model-calls.png"));
  console.log(`captured panel 3: outcome gate (model calls: ${modelCallsText.trim()})`);

  // ---- Panel 4: cross-examine (GET /timemachine, open route) -------------------
  await submitAndAwaitApiResponse(page, {
    submitSelector: "#timemachineSubmit",
    resultHeroSelector: "#timemachineResult .verdict-hero",
    urlIncludes: "/timemachine",
  });
  screenshotFiles.push(await screenshotPanel(page, "#panel-timemachine", "05-cross-examine.png"));
  console.log("captured panel 4: cross-examine");
} finally {
  await context.close();
  await browser.close();
}

for (const key of ["ingest", "decide", "confirm_outcome", "timemachine-browser"]) {
  if (!browserApiReceipts[key]) {
    throw new Error(`Never observed a live API response for '${key}' -- capture is incomplete.`);
  }
}

// -- Phase 3: an independent, post-hoc direct fetch of /timemachine for the same
// decision, using the id the browser just produced live. This is NOT the same
// response the browser rendered from (a fresh request, a moment later) -- it exists
// so a viewer can independently reproduce the cross-examine numbers with plain curl,
// not just trust a screenshot.
{
  const url = `${apiBase}/timemachine?tenant_id=${encodeURIComponent(tenantId)}&decision_id=${encodeURIComponent(
    decisionId,
  )}`;
  const { response, bytes } = await fetchBytes(url);
  const receipt = receiptFromBytes("timemachine-direct-replay", url, response, bytes, {
    note: "independent re-fetch of the same decision, taken after the browser's own request",
  });
  if (!receipt.ok) {
    throw new Error(`Independent replay of /timemachine failed: HTTP ${receipt.status}`);
  }
  directReceipts.timemachineReplay = receipt;
  console.log(`GET /timemachine (independent replay) -> ${receipt.status}`);
}

// -- Assemble the receipts file.

const screenshotEntries = await Promise.all(
  screenshotFiles.map(async (file) => {
    const bytes = await fs.readFile(file);
    return {
      path: path.relative(root, file),
      sha256: sha256(bytes),
      bytes: bytes.length,
    };
  }),
);

function slim(receipt) {
  if (!receipt) return null;
  const { payload, ...rest } = receipt;
  return { ...rest, payload };
}

const receiptDoc = {
  schemaVersion: "1.0",
  capturedAtUtc: new Date().toISOString(),
  candidateOnly: true,
  canClaimAGI: false,
  dashboardUrl,
  apiBase,
  tenantId,
  agentId,
  decisionId,
  evidenceClasses: {
    liveAws: "Deployed Lambda Function URL + CockroachDB Cloud (us-west-2); real requests, real responses.",
  },
  directFetches: {
    health: slim(directReceipts.health),
    recallDirect: slim(directReceipts.recall),
    timemachineReplay: slim(directReceipts.timemachineReplay),
  },
  browserObservedResponses: {
    ingest: slim(browserApiReceipts.ingest),
    decide: slim(browserApiReceipts.decide),
    confirmOutcome: slim(browserApiReceipts.confirm_outcome),
    timemachine: slim(browserApiReceipts["timemachine-browser"]),
  },
  screenshots: screenshotEntries,
  limitations: [
    "Embeddings on this deployment fall back to a deterministic local stub -- the AWS " +
      "account has near-zero Bedrock quota. Recall latency shown here is real; recall " +
      "relevance is not semantically meaningful under the stub.",
    "This capture mutates the live demo tenant (one new memory, one new decision, one " +
      "confirmed outcome) -- it is real traffic against a real deployment, not a replay.",
  ],
};

const receiptsPath = path.join(outputRoot, "receipts.json");
await fs.writeFile(receiptsPath, `${JSON.stringify(receiptDoc, null, 2)}\n`);

console.log(`\nLive capture complete: ${outputRoot}`);
console.log(`  screenshots: ${screenshotEntries.length}`);
console.log(`  receipts:    ${receiptsPath}`);
console.log(`  decision id: ${decisionId}`);
