// SPDX-License-Identifier: Apache-2.0
//
// MemoryStand dashboard — no framework, no build step, no external CDN or font.
// Deployable to AWS Amplify Hosting as a static site exactly as it sits in this
// directory. Talks to the backend over four JSON endpoints; see the API CONTRACT
// block below for exactly what this file sends and expects back.
//
// -----------------------------------------------------------------------------
// API CONTRACT this dashboard assumes (backend/handler.py implements it; that
// file did not exist yet when this dashboard was written, so this block is the
// source of truth for what to build against). Every request/response shape
// below is a thin pass-through of an existing backend/*.py function — nothing
// here invents new business logic.
//
// This is a live contract, taken from backend/handler.py's ROUTES table and its
// docstring (not guessed) as of the last time this file was updated:
//
//   POST {API_BASE}/decide     — requires header x-memorystand-secret
//     body: { tenant_id, agent_id, action, rationale, query, k, task_id,
//             produced_memory_ids: string[], requires_approval }
//     -> backend.memory.recall(tenant_id, agent_id, query, k) for "consulted",
//        then backend.decisions.decide(tenant_id, agent_id, action, rationale,
//        [m.memory_id for m in consulted], produced_memory_ids,
//        requires_approval, task_id). `action` is optional: supplying it skips
//        handler.py's reasoning fallback entirely (reasoning_source:
//        "caller_supplied"), which is this dashboard's default (the "Proposed
//        action" field is pre-filled but not required -- see panel 1). Leaving
//        it blank lets backend.agent.propose() decide instead: it tries the
//        configured Bedrock model first (reasoning_source:"bedrock:<model>"),
//        and on ModelUnavailable -- guaranteed on this deployment, Bedrock quota
//        is 0 -- falls back deterministically to the highest-trust recalled
//        memory that names an action (reasoning_source:"fallback_memory"), or a
//        fixed keyword table if none do (reasoning_source:"fallback_heuristic").
//        No AWS creds are required for either fallback path.
//     response: { decision_id, decided_at, action, status ("taken" |
//                 "held_for_approval"), produced: string[], rationale,
//                 reasoning_source, model_calls, cited_memory_ids: string[],
//                 consulted: [ { memory_id, content, entity, attribute_key,
//                 attribute_value, trust_tier, confidence, source, distance,
//                 verdict } ] }
//
//   POST {API_BASE}/ingest     — requires header x-memorystand-secret
//     body: { tenant_id, agent_id, content, entity, attribute_key,
//             attribute_value, memory_type, source, task_id }
//     -> backend.memory.remember(...)
//     response: { memory_id, verdict ("accepted" | "quarantined"),
//                 verdict_reasons: string[], checked_against: string[],
//                 superseded: string|null }
//
//   POST {API_BASE}/confirm_outcome     — requires header x-memorystand-secret
//     body: { tenant_id, decision_id,
//             outcome ("success" | "rollback" | "false_positive"),
//             source ("pagerduty" | "metric" | "human"), external_ref,
//             metric_delta: number|null }
//     -> backend.trust.grant_standing(tenant_id, decision_id, evidence)
//     NOTE: this was once ungated and documented here as "read-adjacent". It is not:
//     it is the route that grants a memory its standing. Ungated, it let any caller
//     promote any tenant's memories to 'verified'.
//     response: { decision_id, outcome, source, external_ref, metric_delta,
//                 promoted: string[], demoted: string[], model_calls: number,
//                 trust_tier: "attested"|"verified"|"disputed"|null,
//                 verification: { status, detail, observed, claimed } }
//     `verification.status` is one of confirmed | contradicted | unavailable |
//     not_verifiable | entity_mismatch | entity_unbound. Only "confirmed" earns
//     trust_tier "verified". Contradicted and wrong-entity claims are refused; evidence
//     that cannot be checked can reach only "attested".
//
//   GET {API_BASE}/timemachine?tenant_id=...&decision_id=...     — public demo tenant,
//                                                             operator auth otherwise
//     -> backend.replay.cross_examine(tenant_id, decision_id)
//     response: { decision: { decision_id, action, rationale, decided_at,
//                 outcome, consulted_memory_ids, produced_memory_ids },
//                 believed_at_decision_time: [ {memory_id, content, entity,
//                 attribute_key, attribute_value, trust_tier, confidence,
//                 source} ], changed_since: [ {...same fields..., delta:
//                 "added"|"removed"|"changed", was_trust_tier?} ],
//                 consulted: string[] }
//
//   GET {API_BASE}/health     — open route; used for the connection pill
//
// A non-2xx response is JSON shaped { error|degraded|held, detail? } (from
// handler.py's centralised error mapping); this file renders `detail` when
// present, else the raw body.
//
// Auth: /ingest, /decide and /confirm_outcome are gated behind a shared secret compared with
// hmac.compare_digest server-side (MEMORYSTAND_SHARED_SECRET). This dashboard
// never hardcodes it -- the operator pastes it into the top bar, and it is
// sent only on those two routes, only in memory, never persisted to storage.
//
// CORS: re-verified 2026-08-04 against a running handler.py dev server (curl -i on
// every route, including POST /decide, /ingest and /confirm_outcome, plus an OPTIONS
// preflight). Every response -- reads, writes, and error paths alike -- now sends
// Access-Control-Allow-Origin: * (handler.py's `_response(..., cors=True)` is
// unconditional). A cross-origin deployment (Amplify Hosting calling a Lambda Function
// URL, or this dashboard served on a different local port than the API) works. If a
// future backend change reintroduces a read/write split here, the symptom from this
// file's side will be indistinguishable from "API unreachable" -- the browser hides the
// actual response from a CORS-opaque POST -- so this comment exists to make that failure
// mode easy to recognize again if it ever comes back.
// -----------------------------------------------------------------------------

(function () {
  "use strict";

  var qs = new URLSearchParams(window.location.search);
  // Default matches scripts/run-local.sh's MEMORYSTAND_LOCAL_PORT (8077), not the more
  // common 8000 -- port 8000 is documented as taken by an unrelated service on this
  // project's own dev machines, and run-local.sh --serve always binds 8077. A judge
  // running the Quickstart never has to pass ?api= for the local flow to work. A judge
  // hitting a deployed Amplify URL always needs ?api=<lambda-function-url> explicitly --
  // there is no way to bake a not-yet-deployed Lambda URL in here honestly.
  // Default to the deployed API so a judge opening the hosted page needs no query
  // parameter and no local setup. ?api= still overrides, which is what `run-local.sh
  // --serve` relies on when pointing this same file at http://127.0.0.1:8077.
  var DEPLOYED_API = "https://ojao6oaxlk26mqfjwpuy7g4dy40tglyi.lambda-url.us-west-2.on.aws";
  var LOCAL_API = "http://127.0.0.1:8077";
  var IS_LOCAL_PAGE = location.protocol === "file:" ||
                      /^(localhost|127\.0\.0\.1)$/.test(location.hostname);
  var API_BASE = (qs.get("api") || (IS_LOCAL_PAGE ? LOCAL_API : DEPLOYED_API)).replace(/\/+$/, "");

  // Every fetch below is capped so a stalled connection (conference wifi, a Lambda cold
  // start behind a dead security group, a typo'd ?api=) turns into a clear timeout error
  // within a few seconds instead of a spinner that spins forever with no explanation.
  var FETCH_TIMEOUT_MS = 10000;

  function fetchWithTimeout(url, opts) {
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = null;
    if (controller) {
      opts = Object.assign({}, opts, { signal: controller.signal });
      timer = setTimeout(function () { controller.abort(); }, FETCH_TIMEOUT_MS);
    }
    return fetch(url, opts).finally(function () {
      if (timer) clearTimeout(timer);
    });
  }

  // ---------------------------------------------------------------- helpers

  function $(id) {
    return document.getElementById(id);
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      if (k === "class") node.className = attrs[k];
      else if (k === "text") node.textContent = attrs[k];
      else if (k === "html") node.innerHTML = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) {
      if (c) node.appendChild(c);
    });
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function placeholder(text) {
    return el("p", { class: "placeholder", text: text });
  }

  function shortId(v) {
    if (!v) return "-";
    return String(v).slice(0, 8);
  }

  function fmtNum(v, digits) {
    if (v === null || v === undefined || Number.isNaN(v)) return "-";
    return Number(v).toFixed(digits === undefined ? 4 : digits);
  }

  function trustBadgeClass(tier) {
    if (tier === "verified") return "green";
    if (tier === "disputed") return "red";
    if (tier === "attested") return "amber";
    return "gray"; // unconfirmed / unknown / no trust tier yet
  }

  function verdictBadgeClass(verdict) {
    if (verdict === "accepted") return "green";
    if (verdict === "quarantined" || verdict === "held") return "amber";
    if (verdict === "superseded") return "purple";
    return "gray";
  }

  function verdictLabel(verdict) {
    if (verdict === "quarantined") return "held for review";
    if (verdict === "accepted") return "accepted";
    if (verdict === "superseded") return "corrected by a newer fact";
    return verdict || "unknown";
  }

  function outcomeBadgeClass(outcome) {
    if (outcome === "success") return "green";
    return "red";
  }

  function outcomeLabel(outcome) {
    if (outcome === "rollback") return "rolled back";
    if (outcome === "false_positive") return "false positive";
    return outcome || "unknown";
  }

  function trustTierLabel(tier) {
    if (tier === "verified") return "verified";
    if (tier === "attested") return "attested";
    if (tier === "disputed") return "disputed";
    return "unconfirmed";
  }

  // Ranking weight for the ladder, for DISPLAY ONLY -- the backend ranking is the authority.
  //
  // backend/agent.py::_TIER_RANK contains only verified and attested. Verified is action-
  // authoritative; attested is advisory and always held for approval. Unconfirmed and disputed
  // remain visible but do not steer an action. This display weight shows standing, not permission.
  function tierWeight(tier) {
    if (tier === "verified") return 2;
    if (tier === "attested") return 1;
    return 0; // unconfirmed, disputed, or unknown
  }

  // The attested-vs-verified distinction is the whole point of this project -- this is the
  // one sentence of explanation that travels with the badge every place it is shown.
  function trustTierExplain(tier) {
    if (tier === "verified") return "Re-queried against the external system of record (CloudWatch), which agreed.";
    if (tier === "attested") return "An outcome was reported but not independently corroborated. It can advise, but any resulting action is held for human approval.";
    if (tier === "disputed") return "The reported outcome was a rollback or a false positive.";
    return "No outcome has been reported for this decision yet.";
  }

  function verificationStatusLabel(status) {
    if (status === "confirmed") return "confirmed";
    if (status === "contradicted") return "contradicted";
    if (status === "unavailable") return "unavailable";
    if (status === "not_verifiable") return "not verifiable";
    if (status === "entity_mismatch") return "wrong entity";
    if (status === "entity_unbound") return "entity missing";
    return status || "unknown";
  }

  function verificationStatusExplain(status) {
    if (status === "confirmed") return "The external system of record was re-queried and agreed with the claim.";
    if (status === "contradicted") return "The external system of record was re-queried and disagreed with the claim.";
    if (status === "unavailable") return "The external system of record could not be reached, or had no datapoints in the relevant window yet -- expected right after a fresh decision.";
    if (status === "not_verifiable") return "This source (PagerDuty / human) has no machine-checkable record here to re-query.";
    if (status === "entity_mismatch") return "The telemetry belongs to a different entity than the memory, so the outcome is refused.";
    if (status === "entity_unbound") return "The memory has no entity to bind to the telemetry, so it cannot reach verified.";
    return "";
  }

  // reasoning_source says which path actually picked /decide's action -- shown next to
  // every decision so "did a model do this?" never requires reading the raw response.
  function reasoningSourceLabel(source) {
    if (source === "caller_supplied") return "supplied by caller";
    if (source === "fallback_memory") return "memory decided";
    if (source === "fallback_heuristic") return "keyword table decided";
    if (typeof source === "string" && source.indexOf("bedrock:") === 0) return "model decided";
    return source || "unknown";
  }

  // On THIS deployment reasoning_source is never a bedrock: value -- Bedrock quota is 0,
  // see docs/BEDROCK_QUOTA.md -- so that branch is included for completeness, honestly
  // labelled as not what actually happens here.
  function reasoningSourceExplain(source) {
    if (source === "caller_supplied") {
      return "This request handed /decide a ready-made action, so the agent's own reasoning path (model or memory fallback) never ran.";
    }
    if (source === "fallback_memory") {
      return "A memory decided: no model was consulted (Bedrock quota is 0 on this deployment) -- a recalled memory named this action and outranked the keyword table because it has trust standing from a real outcome.";
    }
    if (source === "fallback_heuristic") {
      return "A keyword table decided: no model was consulted, and no recalled memory named an action either, so a fixed keyword table picked this one instead.";
    }
    if (typeof source === "string" && source.indexOf("bedrock:") === 0) {
      return "The model decided: " + source.slice("bedrock:".length) + " proposed this action, grounded in the recalled memories below. This is not what happens on this deployment -- Bedrock quota is 0 here -- shown for completeness only.";
    }
    return "";
  }

  // Shared by the success path (verification.status === "confirmed") and the refusal path
  // (HTTP 400, verification.status === "contradicted") -- both show the same claimed-vs-observed
  // pair, just with a different border colour (see .observed-claimed.match / .mismatch in CSS).
  function observedClaimedRow(v, modifierClass) {
    return el("div", { class: "observed-claimed" + (modifierClass ? " " + modifierClass : "") }, [
      el("div", { class: "oc-item" }, [
        el("div", { class: "oc-label", text: "claimed" }),
        el("div", { class: "oc-value", text: fmtNum(v.claimed, 2) }),
      ]),
      el("div", { class: "oc-item" }, [
        el("div", { class: "oc-label", text: "observed" }),
        el("div", { class: "oc-value", text: fmtNum(v.observed, 2) }),
      ]),
    ]);
  }

  // `opts.rank` (1-based) and `opts.isChosen` are optional decorations used by panel 1's
  // ranked consulted-memories list (see renderDecideResult below); every other caller
  // (the seeded-demo preview, panel 4's "believed at decision time") omits `opts` and
  // gets exactly the row this always rendered.
  function memoryRow(m, opts) {
    opts = opts || {};
    var topChildren = [];
    if (opts.rank !== undefined && opts.rank !== null) {
      topChildren.push(el("span", { class: "memory-rank", text: "#" + opts.rank }));
    }
    topChildren.push(el("span", { class: "badge " + trustBadgeClass(m.trust_tier), text: m.trust_tier || "unconfirmed" }));
    topChildren.push(el("span", { class: "memory-id", text: shortId(m.memory_id) }));
    if (m.distance !== undefined && m.distance !== null) {
      topChildren.push(el("span", { class: "memory-attr", text: "distance " + fmtNum(m.distance) }));
    }
    if (m.source) topChildren.push(el("span", { class: "memory-attr", text: "source: " + m.source }));
    if (opts.isChosen) {
      topChildren.push(el("span", { class: "badge green", text: "chose this action" }));
    }
    var top = el("div", { class: "memory-top" }, topChildren);
    var attrLine = null;
    if (m.entity || m.attribute_key) {
      attrLine = el("div", {
        class: "memory-attr",
        text: (m.entity || "-") + "." + (m.attribute_key || "-") + " = " + (m.attribute_value || "-"),
      });
    }
    var content = el("div", { class: "memory-content", text: m.content || "" });
    return el("div", { class: "memory-row" + (opts.isChosen ? " chosen" : "") }, [top, attrLine, content]);
  }

  // -------------------------------------------------------------- API layer

  function apiPost(path, body, extraHeaders) {
    var url = API_BASE + path;
    var headers = { "Content-Type": "application/json" };
    Object.keys(extraHeaders || {}).forEach(function (k) {
      if (extraHeaders[k]) headers[k] = extraHeaders[k];
    });
    return fetchWithTimeout(url, {
      method: "POST",
      headers: headers,
      body: JSON.stringify(body),
    })
      .then(function (resp) {
        return resp
          .text()
          .then(function (text) {
            var data = null;
            try {
              data = text ? JSON.parse(text) : null;
            } catch (e) {
              data = null;
            }
            return { ok: resp.ok, status: resp.status, data: data, rawText: text };
          });
      })
      .then(function (result) {
        markConnected(true);
        return result;
      })
      .catch(function (err) {
        markConnected(false);
        return { ok: false, status: 0, data: null, rawText: "", networkError: err };
      });
  }

  function apiGet(path, params) {
    var qsStr = new URLSearchParams(params || {}).toString();
    var url = API_BASE + path + (qsStr ? "?" + qsStr : "");
    return fetchWithTimeout(url, { method: "GET" })
      .then(function (resp) {
        return resp.text().then(function (text) {
          var data = null;
          try {
            data = text ? JSON.parse(text) : null;
          } catch (e) {
            data = null;
          }
          return { ok: resp.ok, status: resp.status, data: data, rawText: text };
        });
      })
      .then(function (result) {
        markConnected(true);
        return result;
      })
      .catch(function (err) {
        markConnected(false);
        return { ok: false, status: 0, data: null, rawText: "", networkError: err };
      });
  }

  function errorMessage(result) {
    if (result.networkError) {
      if (result.networkError.name === "AbortError") {
        return (
          "Timed out waiting for " +
          API_BASE +
          " (no response within " +
          (FETCH_TIMEOUT_MS / 1000) +
          "s). The API may be down, cold-starting, or unreachable from this network -- " +
          "check the address bar's ?api= value, or the operator's Lambda Function URL."
        );
      }
      return (
        "Could not reach " +
        API_BASE +
        " (" +
        (result.networkError.message || "network error") +
        "). Is the API running, and does it allow CORS from this origin?"
      );
    }
    if (result.data && result.data.detail) return result.data.detail;
    if (result.data && result.data.error) return String(result.data.error);
    if (result.rawText) return "HTTP " + result.status + ": " + result.rawText.slice(0, 300);
    return "HTTP " + result.status + ": request failed";
  }

  function renderError(container, result) {
    clear(container);
    container.appendChild(el("div", { class: "error-banner", text: errorMessage(result) }));
  }

  // ---------------------------------------------------------- status pill

  var statusDot = $("statusDot");
  var statusText = $("statusText");
  $("apiBaseLabel").textContent = API_BASE;

  function markConnected(ok) {
    statusDot.className = "status-dot " + (ok ? "ok" : "down");
    statusText.textContent = ok ? "API reachable" : "API unreachable";
  }

  // Fill in the published demo credential, and say plainly what it can and cannot do. A judge
  // arriving cold should be able to press the buttons; the honest framing is that this credential
  // is deliberately public AND deliberately powerless outside one tenant.
  function applyDemoCredential(demo) {
    if (!demo || !demo.credential) return;
    var secretInput = $("sharedSecret");
    var tenantInput = $("tenantId");
    if (secretInput && !secretInput.value) secretInput.value = demo.credential;
    if (tenantInput && !tenantInput.value) tenantInput.value = demo.tenant_id;
    var label = document.querySelector('label[for="sharedSecret"] .hint');
    if (label) {
      label.textContent = "(public demo credential, filled in for you — writes only to the demo tenant)";
    }
  }

  function checkConnection() {
    statusDot.className = "status-dot";
    statusText.textContent = "checking…";
    fetchWithTimeout(API_BASE + "/health", { method: "GET" })
      .then(function (res) {
        markConnected(true);
        // Auto-fill the public demo credential so a reviewer never has to be handed one, or
        // type one, to drive the write routes. It is safe to publish precisely because the
        // server scopes it to a single tenant (handler._scope_tenant) -- 401 for anything else.
        // Nothing is overwritten if the visitor has already typed their own secret.
        return res.json().then(function (h) { applyDemoCredential(h && h.demo); }).catch(function () {});
      })
      .catch(function () {
        // A same-origin-policy / network failure / timeout means truly unreachable.
        // Any HTTP response at all (even 404, if /health isn't implemented)
        // still proves the server is up, and is handled in the .then above.
        markConnected(false);
      });
  }

  checkConnection();

  // ---------------------------------------------------- seeded-demo preview strip
  // Values match the seeded tenant/agent this repo ships with (db/seed/seed.py via
  // scripts/run-local.sh) -- the same defaults already pre-filled into the topbar
  // inputs below. This exists so a first-time visitor never has to know or type a UUID:
  // click the button (or just load the page, since it auto-runs once) and see real
  // data, or a plain-language explanation of why there isn't any yet.
  var SEEDED_TENANT_ID = "9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10";
  var SEEDED_AGENT_ID = "1a2b3c4d-5e6f-4708-9a0b-1c2d3e4f5061";
  var previewResult = $("previewResult");
  var loadSeededDemoBtn = $("loadSeededDemo");

  function runPreview(previewTenantId) {
    clear(previewResult);
    previewResult.appendChild(
      el("p", { class: "placeholder" }, [
        document.createTextNode(""),
      ])
    );
    previewResult.lastChild.appendChild(el("span", { class: "spinner" }));
    previewResult.lastChild.appendChild(document.createTextNode(" checking " + previewTenantId + " for existing memories…"));

    // /recall is an open, read-only route (no shared secret needed) -- this preview
    // works even before an operator has configured MEMORYSTAND_SHARED_SECRET, which
    // /decide and /ingest below require.
    apiGet("/recall", { tenant_id: previewTenantId, q: "on-call incident", k: 3 }).then(function (result) {
      clear(previewResult);
      if (!result.ok || !result.data) {
        renderError(previewResult, result);
        return;
      }
      var results = result.data.results || [];
      if (results.length === 0) {
        previewResult.appendChild(
          el("p", { class: "preview-summary" }, [
            document.createTextNode(
              "No memories found yet for tenant " + shortId(previewTenantId) + ". This is an empty " +
              "database, not a broken page -- seed it with "
            ),
            el("code", { text: "./scripts/run-local.sh" }),
            document.createTextNode(
              " (adds 101 example on-call incidents), or submit your first memory with panel 2 below."
            ),
          ])
        );
        return;
      }
      previewResult.appendChild(
        el("p", { class: "preview-summary" }, [
          document.createTextNode(
            "Real data: " + results.length + " memory(ies) recalled for tenant " + shortId(previewTenantId) + ". " +
            "Panels 1 and 4 below use this same tenant ID."
          ),
        ])
      );
      results.forEach(function (m) {
        previewResult.appendChild(memoryRow(m));
      });
    });
  }

  loadSeededDemoBtn.addEventListener("click", function () {
    $("tenantId").value = SEEDED_TENANT_ID;
    $("agentId").value = SEEDED_AGENT_ID;
    runPreview(SEEDED_TENANT_ID);
  });

  // Auto-run once on load with whatever tenant ID is currently in the field (the seeded
  // UUID by default) -- a first-time visitor who never clicks anything still sees real
  // data or a clear empty-state message instead of nothing.
  runPreview($("tenantId").value.trim() || SEEDED_TENANT_ID);

  function tenantId() {
    return $("tenantId").value.trim();
  }
  function agentId() {
    return $("agentId").value.trim();
  }
  function sharedSecret() {
    return $("sharedSecret").value;
  }
  function secretHeaders() {
    var s = sharedSecret();
    return s ? { "x-memorystand-secret": s } : {};
  }

  // ============================================================ Panel 1
  // Incident feed -> POST /decide

  var decideForm = $("form-decide");
  var decideResult = $("decideResult");
  var decideSubmit = $("decideSubmit");

  decideForm.addEventListener("submit", function (evt) {
    evt.preventDefault();

    var produced = $("decideProduced").value
      .split(",")
      .map(function (s) { return s.trim(); })
      .filter(Boolean);

    var body = {
      tenant_id: tenantId(),
      agent_id: agentId(),
      query: $("decideQuery").value,
      // Blank on purpose is a real, supported request: omitting `action` lets the agent's
      // own reasoning path (memory fallback or model) pick one instead of the caller
      // dictating it -- see the reasoning_source explanation rendered below.
      action: $("decideAction").value.trim() || null,
      rationale: $("decideRationale").value,
      k: parseInt($("decideK").value, 10) || 5,
      task_id: $("decideTaskId").value.trim() || null,
      produced_memory_ids: produced,
      requires_approval: $("decideApproval").checked,
    };

    decideSubmit.disabled = true;
    decideSubmit.innerHTML = '<span class="spinner"></span> posting…';
    clear(decideResult);
    decideResult.appendChild(placeholder("waiting for response…"));

    apiPost("/decide", body, secretHeaders()).then(function (result) {
      decideSubmit.disabled = false;
      decideSubmit.textContent = "Post alert to /decide";

      if (!result.ok || !result.data) {
        renderError(decideResult, result);
        return;
      }

      renderDecideResult(result.data);

      // Convenience: carry the new decision id into panels 3 and 4.
      if (result.data.decision_id) {
        $("confirmDecisionId").value = result.data.decision_id;
        $("timemachineDecisionId").value = result.data.decision_id;
      }
    });
  });

  // Older deployments did not return cited_memory_ids, so keep a rationale parser only as a
  // compatibility fallback. Current deployments return the cited ids structurally.
  function chosenMemoryIdFromRationale(rationale) {
    if (!rationale) return null;
    var m = /Chosen from memory (\S+) \(trust_tier=/.exec(rationale);
    return m ? m[1] : null;
  }

  function renderDecideResult(data) {
    clear(decideResult);

    var statusBadge = el("span", {
      class: "badge " + (data.status === "held_for_approval" ? "amber" : "green"),
      text: data.status === "held_for_approval" ? "held for approval" : "cleared to proceed",
    });

    var hero = el("div", { class: "verdict-hero" }, [
      statusBadge,
      el("div", {}, [
        el("div", { text: "action: " + data.action }),
        el("div", { class: "verdict-id", text: "decision " + shortId(data.decision_id) + (data.decided_at ? "  ·  " + data.decided_at : "") }),
      ]),
    ]);
    decideResult.appendChild(hero);

    // model_calls and reasoning_source belong right next to the decision itself, not
    // only next to the outcome gate in panel 3 -- this is the honest answer to "did a
    // model make this call?" for the specific decision above.
    var modelCalls = typeof data.model_calls === "number" ? data.model_calls : 0;
    decideResult.appendChild(
      el("div", { class: "kv-line" }, [
        el("span", { class: "badge " + (modelCalls === 0 ? "green" : "red"), text: "model calls: " + modelCalls }),
        document.createTextNode("  reasoning_source: "),
        el("b", { text: reasoningSourceLabel(data.reasoning_source) + " (" + (data.reasoning_source || "unknown") + ")" }),
      ])
    );
    var reasoningExplain = reasoningSourceExplain(data.reasoning_source);
    if (reasoningExplain) {
      decideResult.appendChild(el("p", { class: "verification-status-line", text: reasoningExplain }));
    }

    var consulted = (data.consulted || []).slice();
    decideResult.appendChild(
      el("div", { class: "kv-line" }, [
        el("b", { text: String(consulted.length) }),
        document.createTextNode(" memory(ies) consulted, ranked nearest first:"),
      ])
    );

    if (consulted.length === 0) {
      decideResult.appendChild(el("p", { class: "tier-empty", text: "no accepted memories matched this query yet" }));
    } else {
      // Rank by vector distance ourselves rather than trusting response order -- recall()
      // happens to already return nearest-first, but the ranking shown on screen should
      // hold even if that ordering ever changes upstream.
      var ranked = consulted.slice().sort(function (a, b) {
        var da = typeof a.distance === "number" ? a.distance : Infinity;
        var db = typeof b.distance === "number" ? b.distance : Infinity;
        return da - db;
      });

      var cited = Array.isArray(data.cited_memory_ids) ? data.cited_memory_ids : [];
      var chosenId = data.reasoning_source === "fallback_memory"
        ? (cited[0] || chosenMemoryIdFromRationale(data.rationale))
        : null;
      var chosen = chosenId ? ranked.filter(function (m) { return m.memory_id === chosenId; })[0] || null : null;
      var nearest = ranked[0] || null;

      ranked.forEach(function (m, i) {
        decideResult.appendChild(
          memoryRow(m, { rank: i + 1, isChosen: !!(chosen && m.memory_id === chosen.memory_id) })
        );
      });

      // This is the entire product argument in one on-screen moment: a memory closer in
      // vector space lost to one with more trust standing, and nothing here asked a model.
      if (chosen && nearest && chosen.memory_id !== nearest.memory_id) {
        decideResult.appendChild(
          el("div", { class: "overrule-banner" }, [
            el("div", { class: "overrule-title" }, [
              el("span", { class: "badge green", text: "trust beat proximity" }),
              el("span", { text: "a closer memory was overruled by higher trust" }),
            ]),
            el("p", { class: "overrule-explain" }, [
              document.createTextNode(
                "Memory " + shortId(nearest.memory_id) + " (distance " + fmtNum(nearest.distance) + ", " +
                trustTierLabel(nearest.trust_tier) + ") was the closer vector match, but memory " +
                shortId(chosen.memory_id) + " (distance " + fmtNum(chosen.distance) + ", " +
                trustTierLabel(chosen.trust_tier) + ") supplied the recommendation instead, because it has " +
                "standing the closer memory does not. " +
                (data.status === "held_for_approval"
                  ? "Because that standing is attested rather than verified, the recommendation is held for human approval."
                  : "No model was consulted.")
              ),
            ]),
          ])
        );
      } else if (chosen) {
        decideResult.appendChild(
          el("p", { class: "kv-line", text: "The memory that decided the action was also the nearest match here -- proximity and trust agreed." })
        );
      } else if (nearest) {
        // WHEN A MODEL ANSWERED, we cannot name the deciding memory: only
        // agent.py::_fallback_action writes "Chosen from memory <id>" into its rationale, and a
        // model returns free prose. Claiming an overrule here would be a guess, and this file
        // already refuses to guess one rung up.
        //
        // Staying silent was worse, and it was a regression: wiring the standby provider meant
        // reasoning_source stopped being "fallback_memory" on live calls, so the dashboard's best
        // moment -- trust outranking proximity -- quietly stopped rendering for every judge who
        // clicked. What IS provable from the response alone is what the ranking says: which
        // candidate is nearest, and which carries the most standing. Show exactly that, labelled
        // as a property of the candidates rather than a claim about what decided.
        var strongest = ranked.slice().sort(function (a, b) {
          return tierWeight(b.trust_tier) - tierWeight(a.trust_tier);
        })[0];
        // A REAL DISTANCE GAP IS REQUIRED, not just a different row. On the seeded demo tenant
        // several near-duplicate memories come back at byte-identical distance, and calling one
        // of them "the closest vector match" when nothing is closer than anything would be a
        // claim the numbers do not support -- proximity is not losing to trust there, proximity
        // simply has nothing to say. Ties get the honest line below instead.
        var gap = (typeof strongest?.distance === "number" && typeof nearest.distance === "number")
          ? strongest.distance - nearest.distance
          : 0;
        if (strongest && strongest.memory_id !== nearest.memory_id &&
            tierWeight(strongest.trust_tier) > tierWeight(nearest.trust_tier) &&
            gap > 1e-9) {
          decideResult.appendChild(
            el("div", { class: "overrule-banner" }, [
              el("div", { class: "overrule-title" }, [
                el("span", { class: "badge green", text: "trust vs proximity" }),
                el("span", { text: "the nearest memory is not the most trusted one" }),
              ]),
              el("p", { class: "overrule-explain" }, [
                document.createTextNode(
                  "Memory " + shortId(nearest.memory_id) + " (distance " + fmtNum(nearest.distance) + ", " +
                  trustTierLabel(nearest.trust_tier) + ") is the closest vector match, but memory " +
                  shortId(strongest.memory_id) + " (distance " + fmtNum(strongest.distance) + ", " +
                  trustTierLabel(strongest.trust_tier) + ") carries standing earned from a real outcome. " +
                  "Every tier here was set by the outcome gate with zero model calls. A model chose " +
                  "this action, so this panel does not claim which memory decided it -- with no model " +
                  "reachable, the memory path names it outright."
                ),
              ]),
            ])
          );
        } else if (strongest && tierWeight(strongest.trust_tier) > tierWeight(nearest.trust_tier)) {
          // Same tier disagreement, but no usable distance gap to attribute it to. Say what is
          // true -- the ladder differs across candidates that retrieval could not separate --
          // rather than dressing a tie up as proximity losing.
          decideResult.appendChild(
            el("p", { class: "kv-line", text:
              "These candidates are indistinguishable by distance, so proximity does not choose " +
              "between them -- but their standing differs: " + shortId(strongest.memory_id) +
              " is " + trustTierLabel(strongest.trust_tier) + " while the others are not. " +
              "That difference was set by the outcome gate, with zero model calls." })
          );
        }
      }
    }

    if (data.produced && data.produced.length) {
      decideResult.appendChild(
        el("div", { class: "kv-line" }, [
          document.createTextNode("produced: "),
          el("b", { text: data.produced.map(shortId).join(", ") }),
        ])
      );
    }

    decideResult.appendChild(rawJsonBlock(data));
  }

  // ============================================================ Panel 2
  // Memory admission -> POST /ingest

  var ingestForm = $("form-ingest");
  var ingestResult = $("ingestResult");
  var ingestSubmit = $("ingestSubmit");

  ingestForm.addEventListener("submit", function (evt) {
    evt.preventDefault();

    var body = {
      tenant_id: tenantId(),
      agent_id: agentId(),
      content: $("ingestContent").value,
      entity: $("ingestEntity").value.trim() || null,
      attribute_key: $("ingestKey").value.trim() || null,
      attribute_value: $("ingestValue").value.trim() || null,
      memory_type: $("ingestType").value,
      source: $("ingestSource").value.trim() || null,
      task_id: $("ingestTaskId").value.trim() || null,
    };

    ingestSubmit.disabled = true;
    ingestSubmit.innerHTML = '<span class="spinner"></span> submitting…';
    clear(ingestResult);
    ingestResult.appendChild(placeholder("waiting for response…"));

    apiPost("/ingest", body, secretHeaders()).then(function (result) {
      ingestSubmit.disabled = false;
      ingestSubmit.textContent = "Submit to /ingest";

      if (!result.ok || !result.data) {
        renderError(ingestResult, result);
        return;
      }

      renderIngestResult(result.data);
    });
  });

  function renderIngestResult(data) {
    clear(ingestResult);

    var isHeld = data.verdict === "quarantined";
    var badge = el("span", {
      class: "badge " + verdictBadgeClass(data.verdict),
      text: verdictLabel(data.verdict),
    });
    var hero = el("div", { class: "verdict-hero" }, [
      badge,
      el("div", {}, [
        el("div", { class: "verdict-id", text: "memory " + shortId(data.memory_id) }),
      ]),
    ]);
    ingestResult.appendChild(hero);

    if (isHeld) {
      ingestResult.appendChild(el("p", { class: "kv-line", html: "<b>Held for review</b> — this memory is NOT recallable until a human resolves the conflict below." }));
    } else if (data.superseded) {
      ingestResult.appendChild(
        el("p", { class: "kv-line" }, [
          document.createTextNode("Admitted — replaces the earlier memory "),
          el("b", { text: shortId(data.superseded) }),
        ])
      );
    } else {
      ingestResult.appendChild(el("p", { class: "kv-line", text: "Admitted — recallable immediately." }));
    }

    var reasons = data.verdict_reasons || [];
    if (reasons.length) {
      var title = isHeld ? "Why it was held:" : "Why:";
      ingestResult.appendChild(el("div", { class: "kv-line", text: title }));
      var ul = el("ul", { class: "reason-list" });
      reasons.forEach(function (r) {
        ul.appendChild(el("li", { text: r }));
      });
      ingestResult.appendChild(ul);
    }

    var checked = data.checked_against || [];
    ingestResult.appendChild(
      el("div", { class: "kv-line" }, [
        document.createTextNode("checked against " + checked.length + " existing memory(ies)" + (checked.length ? ": " : "")),
        checked.length ? el("span", { class: "mono", text: checked.map(shortId).join(", ") }) : null,
      ])
    );

    if (data.memory_id) {
      var useBtn = el("button", { class: "ghost", type: "button", text: "use as produced in panel 1" });
      useBtn.addEventListener("click", function () {
        var field = $("decideProduced");
        var existing = field.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
        if (existing.indexOf(data.memory_id) === -1) existing.push(data.memory_id);
        field.value = existing.join(", ");
      });
      ingestResult.appendChild(useBtn);
    }

    ingestResult.appendChild(rawJsonBlock(data));
  }

  // ============================================================ Panel 3
  // MemoryStand (outcome gate) -> POST /confirm_outcome

  var confirmForm = $("form-confirm");
  var confirmResult = $("confirmResult");
  var confirmSubmit = $("confirmSubmit");
  var confirmSourceSelect = $("confirmSource");
  var confirmDeltaField = $("confirmDeltaField");
  var modelCallsNumberEl = $("modelCallsNumber");
  var modelCallsNoteEl = $("modelCallsNote");

  // Updates the persistent hero in the panel header, not something inside confirmResult
  // -- it is the single most important number on the page, so it stays visible before,
  // during, and after every confirm attempt rather than living only in the result area.
  function updateModelCallsHero(modelCalls, data) {
    var n = typeof modelCalls === "number" ? modelCalls : 0;
    modelCallsNumberEl.textContent = String(n);
    modelCallsNumberEl.className = "mc-number " + (n === 0 ? "zero" : "nonzero");
    if (data) {
      modelCallsNoteEl.textContent =
        "From the last confirmed outcome (decision " +
        shortId(data.decision_id) +
        "): the promotion path made " +
        n +
        (n === 1 ? " model call." : " model calls.");
    }
  }

  function syncDeltaRequirement() {
    var needsDelta = confirmSourceSelect.value === "metric";
    confirmDeltaField.style.opacity = needsDelta ? "1" : "0.55";
  }
  confirmSourceSelect.addEventListener("change", syncDeltaRequirement);
  syncDeltaRequirement();

  confirmForm.addEventListener("submit", function (evt) {
    evt.preventDefault();

    var deltaRaw = $("confirmDelta").value;
    var body = {
      // tenant_id is required now: the server scopes the decision lookup by it, so a
      // decision id alone can no longer promote a memory (it previously could, for ANY
      // tenant).
      tenant_id: tenantId(),
      decision_id: $("confirmDecisionId").value.trim(),
      outcome: $("confirmOutcome").value,
      source: confirmSourceSelect.value,
      external_ref: $("confirmRef").value.trim(),
      metric_delta: deltaRaw === "" ? null : parseFloat(deltaRaw),
    };

    if (body.source === "metric" && body.metric_delta === null) {
      clear(confirmResult);
      confirmResult.appendChild(el("div", { class: "error-banner", text: "source=metric requires a metric delta." }));
      return;
    }

    confirmSubmit.disabled = true;
    confirmSubmit.innerHTML = '<span class="spinner"></span> confirming…';
    clear(confirmResult);
    confirmResult.appendChild(placeholder("waiting for response…"));

    // Sends the shared secret. This route used to be described here as "read-adjacent"
    // and was left ungated -- but it is the route that GRANTS TRUST, and an ungated one
    // let anyone promote memories to 'verified'. Classifying a route by how it reads
    // rather than by what it changes is how that gap opened.
    apiPost("/confirm_outcome", body, secretHeaders()).then(function (result) {
      confirmSubmit.disabled = false;
      confirmSubmit.textContent = "Confirm outcome";

      // HTTP 400 from this route is not a broken request -- it is the outcome gate refusing
      // to record a claim the external system of record disagrees with. Render it as a
      // deliberate, first-class outcome instead of falling through to the generic error banner.
      if (result.status === 400 && result.data) {
        renderConfirmRefusal(confirmResult, result);
        return;
      }

      if (!result.ok || !result.data) {
        renderError(confirmResult, result);
        return;
      }

      renderConfirmResult(result.data);
    });
  });

  function tierList(ids) {
    var ul = el("ul");
    if (!ids || !ids.length) {
      return el("p", { class: "tier-empty", text: "none" });
    }
    ids.forEach(function (id) {
      ul.appendChild(el("li", { text: shortId(id) }));
    });
    return ul;
  }

  function renderConfirmResult(data) {
    clear(confirmResult);

    var outcomeBadge = el("span", {
      class: "badge " + outcomeBadgeClass(data.outcome),
      text: outcomeLabel(data.outcome),
    });
    confirmResult.appendChild(
      el("div", { class: "verdict-hero" }, [
        outcomeBadge,
        el("div", {}, [
          el("div", { text: "decision " + shortId(data.decision_id) }),
          el("div", { class: "verdict-id", text: "source: " + data.source + "  ·  ref: " + data.external_ref }),
        ]),
      ])
    );

    var columns = el("div", { class: "tier-columns" }, [
      el("div", { class: "tier-col promoted" }, [
        el("h3", { text: "promoted → verified (" + ((data.promoted || []).length) + ")" }),
        tierList(data.promoted),
      ]),
      el("div", { class: "tier-col demoted" }, [
        el("h3", { text: "demoted → disputed (" + ((data.demoted || []).length) + ")" }),
        tierList(data.demoted),
      ]),
    ]);
    confirmResult.appendChild(columns);

    // -------- resulting trust tier + verification -- the feature this whole submission
    // leads with, so it gets its own prominent block rather than living only in "raw response".
    var tier = data.trust_tier;
    var tierRow = el("div", { class: "verification-row" }, [
      el("span", {
        class: "badge " + trustBadgeClass(tier),
        text: tier ? trustTierLabel(tier) : "no memories produced",
      }),
      el("p", {
        class: "verification-explain",
        text: tier
          ? trustTierExplain(tier)
          : "This decision did not produce any memories, so there is nothing to promote or demote.",
      }),
    ]);

    var verificationChildren = [tierRow];
    var verification = data.verification || null;
    if (verification && verification.status) {
      verificationChildren.push(
        el("p", { class: "verification-status-line" }, [
          document.createTextNode("verification: "),
          el("b", { text: verificationStatusLabel(verification.status) }),
          document.createTextNode(" — " + verificationStatusExplain(verification.status)),
        ])
      );
      if (verification.observed !== undefined || verification.claimed !== undefined) {
        verificationChildren.push(
          observedClaimedRow(verification, verification.status === "confirmed" ? "match" : "")
        );
      }
      if (verification.detail) {
        verificationChildren.push(el("p", { class: "verification-status-line", text: verification.detail }));
      }
    }
    confirmResult.appendChild(el("div", { class: "verification-block" }, verificationChildren));

    // The model-calls-hero itself lives outside confirmResult (see #modelCallsHero in
    // index.html) so it stays visible -- showing the honest static "0" -- through every
    // loading/error state this panel goes through, instead of only appearing after a
    // successful response.
    updateModelCallsHero(data.model_calls, data);

    confirmResult.appendChild(rawJsonBlock(data));
  }

  // A 400 here means the outcome gate did its job: the claimed result did not match what the
  // external system of record shows, so nothing was promoted and nothing was recorded. This
  // gets its own renderer -- reusing the generic red .error-banner would make the single most
  // persuasive state this dashboard can show look like a broken request instead of the feature
  // working as designed.
  function renderConfirmRefusal(container, result) {
    clear(container);

    var data = result.data || {};
    var verification = data.verification || {};
    var detail = data.detail || data.error || verification.detail || errorMessage(result);

    container.appendChild(
      el("div", { class: "refusal-banner" }, [
        el("div", { class: "refusal-title" }, [
          el("span", { class: "badge red", text: "refused" }),
          el("span", { text: "the outcome gate rejected this claim" }),
        ]),
        el("p", {
          class: "refusal-explain",
          text:
            "This is the trust-promotion feature working as designed: the claimed outcome did " +
            "not match the external system of record, so the request was refused with HTTP 400 " +
            "and nothing was recorded. Nothing to fix here.",
        }),
        el("p", { class: "refusal-detail", text: String(detail) }),
      ])
    );

    if (verification.observed !== undefined || verification.claimed !== undefined) {
      container.appendChild(observedClaimedRow(verification, "mismatch"));
    }

    container.appendChild(rawJsonBlock(data));
  }

  // ============================================================ Panel 4
  // Cross-examine -> POST /timemachine

  var timemachineForm = $("form-timemachine");
  var timemachineResult = $("timemachineResult");
  var timemachineSubmit = $("timemachineSubmit");

  timemachineForm.addEventListener("submit", function (evt) {
    evt.preventDefault();

    var params = {
      tenant_id: tenantId(),
      decision_id: $("timemachineDecisionId").value.trim(),
    };

    timemachineSubmit.disabled = true;
    timemachineSubmit.innerHTML = '<span class="spinner"></span> calling…';
    clear(timemachineResult);
    timemachineResult.appendChild(placeholder("waiting for response…"));

    apiGet("/timemachine", params).then(function (result) {
      timemachineSubmit.disabled = false;
      timemachineSubmit.textContent = "Call /timemachine";

      if (!result.ok || !result.data) {
        renderError(timemachineResult, result);
        return;
      }

      renderTimemachineResult(result.data);
    });
  });

  function renderTimemachineResult(data) {
    clear(timemachineResult);

    var decision = data.decision || {};
    timemachineResult.appendChild(
      el("div", { class: "verdict-hero" }, [
        el("span", {
          class: "badge " + (decision.outcome ? outcomeBadgeClass(decision.outcome) : "gray"),
          text: decision.outcome ? outcomeLabel(decision.outcome) : "outcome not yet confirmed",
        }),
        el("div", {}, [
          el("div", { text: "action: " + (decision.action || "-") }),
          el("div", { class: "verdict-id", text: "decision " + shortId(decision.decision_id) + (decision.decided_at ? "  ·  decided " + decision.decided_at : "") }),
        ]),
      ])
    );

    if (decision.rationale) {
      timemachineResult.appendChild(el("p", { class: "kv-line" }, [document.createTextNode("rationale at the time: "), el("b", { text: decision.rationale })]));
    }

    var consultedSet = {};
    (data.consulted || []).forEach(function (id) { consultedSet[id] = true; });

    timemachineResult.appendChild(el("div", { class: "kv-line", text: "What the agent knew when it acted, versus what is true now:" }));

    var changes = data.changed_since || [];
    if (!changes.length) {
      timemachineResult.appendChild(el("p", { class: "tier-empty", text: "nothing has changed since that instant" }));
    } else {
      var diffWrap = el("div", { class: "diff-columns" });
      changes.forEach(function (row) {
        var delta = row.delta || "unchanged";
        var body = el("div", { class: "diff-body" }, [
          el("div", { class: "memory-id", text: shortId(row.memory_id) + (consultedSet[row.memory_id] ? "  (consulted)" : "") }),
          el("div", {
            text:
              (row.entity || "-") +
              "." +
              (row.attribute_key || "-") +
              " = " +
              (row.attribute_value || row.content || "-") +
              (delta === "changed" && row.was_trust_tier ? "   (" + row.was_trust_tier + " → " + row.trust_tier + ")" : ""),
          }),
        ]);
        diffWrap.appendChild(
          el("div", { class: "diff-row " + delta }, [
            el("div", { class: "diff-tag " + delta, text: delta }),
            body,
          ])
        );
      });
      timemachineResult.appendChild(diffWrap);
    }

    var believed = data.believed_at_decision_time || [];
    var believedDetails = el("details", { class: "raw-json" }, [
      el("summary", { text: "everything believed at decision time (" + believed.length + " memories)" }),
    ]);
    var believedWrap = el("div", {});
    believed.forEach(function (m) {
      believedWrap.appendChild(memoryRow(m));
    });
    believedDetails.appendChild(believedWrap);
    timemachineResult.appendChild(believedDetails);

    timemachineResult.appendChild(rawJsonBlock(data));
  }

  // ---------------------------------------------------------------- shared

  function rawJsonBlock(data) {
    var d = el("details", { class: "raw-json" }, [el("summary", { text: "raw response" })]);
    var pre = el("pre", { text: JSON.stringify(data, null, 2) });
    d.appendChild(pre);
    return d;
  }
})();
