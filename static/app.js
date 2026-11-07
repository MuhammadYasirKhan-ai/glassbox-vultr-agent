"use strict";
const $ = (id) => document.getElementById(id);
const STEPS = [
  ["plan", "Plan", "Model writes code"],
  ["policy", "Policy gate", "Static code screening"],
  ["sandbox", "Sandbox run", "Throwaway container"],
  ["explain", "Explain", "From executed output only"],
  ["verify", "Verify", "Claim-check every number"],
];
let file = null;
let busy = false;

function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}
const chip = (text, cls = "") => h("span", { class: `chip ${cls}`.trim(), text });
const fmt = (n) => (typeof n === "number" ? (Math.abs(n) >= 100 ? n.toFixed(1) : n.toFixed(3)) : String(n ?? "–"));

function setBusy(b, msg = "") {
  busy = b;
  $("run").disabled = b || !file;
  $("demo").disabled = b;
  $("status").textContent = msg;
}
function showAlert(msg) {
  const box = $("alert");
  box.replaceChildren();
  if (msg) box.append(h("div", { class: "banner bad", text: msg }));
}

async function loadHealth() {
  const badges = $("badges");
  try {
    const r = await fetch("/healthz");
    const d = await r.json();
    $("maxkb").textContent = d.limits.max_upload_kb;
    const items = [
      chip(d.provider === "vultr" ? "LLM: Vultr Serverless Inference" : `LLM: Gemini (${d.model})`, d.provider === "vultr" ? "ok" : "warn"),
      chip(d.gvisor ? "Sandbox: Docker + gVisor" : "Sandbox: Docker", "ok"),
      chip(`${d.limits.timeout_s}s · ${d.limits.memory} · no network`),
    ];
    if (!d.llm_ready) items.push(chip("LLM key missing", "bad"));
    badges.replaceChildren(...items);
  } catch {
    badges.replaceChildren(chip("Backend unreachable", "bad"));
  }
}

function parseHeader(text) {
  const line = text.replace(/^\uFEFF/, "").split(/\r?\n/)[0] || "";
  const cols = [];
  let cur = "", q = false;
  for (const ch of line) {
    if (ch === '"') q = !q;
    else if (ch === "," && !q) { cols.push(cur.trim()); cur = ""; }
    else cur += ch;
  }
  cols.push(cur.trim());
  return cols.filter(Boolean);
}

async function setFile(f) {
  file = f;
  const pill = $("filePill");
  const sel = $("target");
  sel.replaceChildren(h("option", { value: "", text: "(last column)" }));
  if (!f) { pill.classList.remove("show"); setBusy(false); return; }
  pill.textContent = `${f.name} · ${(f.size / 1000).toFixed(1)} KB`;
  pill.classList.add("show");
  try {
    for (const c of parseHeader(await f.slice(0, 8192).text())) sel.append(h("option", { value: c, text: c }));
  } catch { /* header preview is optional */ }
  setBusy(false);
}

function resetPipeline() {
  $("pipeline").hidden = false;
  $("steps").replaceChildren(...STEPS.map(([id, t, s]) => h("li", { "data-step": id }, h("b", { text: t }), h("span", { text: s }))));
}
function mark(stage, state, note) {
  const li = $("steps").querySelector(`[data-step="${stage}"]`);
  if (!li) return;
  li.className = state;
  if (note) li.querySelector("span").textContent = note;
}
function setActive(stage, attempt) {
  const idx = STEPS.findIndex((s) => s[0] === stage);
  STEPS.forEach(([id], i) => {
    if (i < idx) mark(id, "done");
    else if (i === idx) mark(id, "active", id === "plan" && attempt > 1 ? `Attempt ${attempt} (self-healing)` : undefined);
    else mark(id, "");
  });
}

async function streamNdjson(url, opts, onEvent) {
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    let msg = `Request failed (${resp.status})`;
    try { const j = await resp.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* keep default */ }
    throw new Error(msg);
  }
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

async function runAnalysis() {
  if (!file || busy) return;
  showAlert("");
  $("results").replaceChildren();
  $("demoCard").hidden = true;
  resetPipeline();
  const fd = new FormData();
  fd.append("file", file);
  fd.append("goal", $("goal").value.trim() || "Explain what drives the target");
  fd.append("target", $("target").value);
  setBusy(true, "Working… this usually takes 20–60 seconds.");
  let finalSeen = false;
  try {
    await streamNdjson("/api/analyze/stream", { method: "POST", body: fd }, (ev) => {
      if (ev.type === "stage" && ev.stage !== "queued") setActive(ev.stage, ev.attempt);
      else if (ev.type === "attempt") {
        if (ev.outcome === "success") mark("sandbox", "done", `${ev.duration_s}s · exit ${ev.exit_code}`);
        else mark(ev.outcome.startsWith("blocked") ? "policy" : "sandbox", "fail", `Attempt ${ev.n}: ${ev.outcome}`);
      } else if (ev.type === "final") { finalSeen = true; renderFinal(ev); }
      else if (ev.type === "error") throw new Error(ev.message);
    });
    if (!finalSeen) throw new Error("The connection closed before a result arrived.");
  } catch (e) {
    showAlert(e.message);
    document.querySelectorAll("#steps .active").forEach((li) => (li.className = "fail"));
  } finally {
    setBusy(false);
  }
}

function copyBtn(label, getText) {
  return h("button", { type: "button", text: label, onclick: async (e) => {
    try { await navigator.clipboard.writeText(getText()); e.target.textContent = "Copied"; setTimeout(() => (e.target.textContent = label), 1500); } catch { /* clipboard blocked */ }
  } });
}
function downloadBtn(label, name, getText) {
  return h("button", { type: "button", text: label, onclick: () => {
    const url = URL.createObjectURL(new Blob([getText()], { type: "application/json" }));
    const a = h("a", { href: url, download: name });
    document.body.append(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  } });
}

function renderFinal(d) {
  const out = $("results");
  out.replaceChildren();
  const timeline = h("section", { class: "card" }, h("h2", { text: "Execution timeline" }));
  for (const a of d.attempts) {
    const ok = a.outcome === "success";
    const body = ok ? a.code : a.policy_violations.length ? a.policy_violations.join("\n") : a.stderr_tail;
    timeline.append(h("details", ok ? {} : { open: "" },
      h("summary", {}, `Attempt ${a.n}`, chip(a.outcome, ok ? "ok" : "bad"), a.duration_s ? chip(`${a.duration_s}s · exit ${a.exit_code}`) : null),
      h("pre", { text: body || "(no output)" })));
  }
  if (!d.ok) {
    out.append(timeline);
    showAlert(d.error || "No verified result was produced.");
    document.querySelectorAll("#steps .active").forEach((li) => (li.className = "fail"));
    return;
  }
  STEPS.forEach(([id]) => mark(id, "done"));
  const r = d.result;
  const uplift = typeof r.model_score === "number" && typeof r.baseline_score === "number" ? r.model_score - r.baseline_score : null;

  const summary = h("section", { class: "card" }, h("h2", { text: "2 · Executed result" }),
    h("div", { class: "metrics" },
      metric("Task", `${r.task ?? "–"}`),
      metric(r.metric_name ?? "Score", fmt(r.model_score)),
      metric("Baseline", fmt(r.baseline_score)),
      metric("Uplift", uplift == null ? "–" : (uplift >= 0 ? "+" : "") + fmt(uplift))));
  const imps = Array.isArray(r.importances) ? r.importances : [];
  const max = Math.max(...imps.map((i) => Math.abs(Number(i.mean) || 0)), 1e-9);
  const bars = h("div", { class: "bars", role: "list", "aria-label": "Permutation importance" });
  for (const i of imps) {
    const fill = h("div", { class: "fill" });
    fill.style.width = `${Math.max(2, (Math.abs(Number(i.mean) || 0) / max) * 100)}%`;
    bars.append(h("div", { class: "bar", role: "listitem" },
      h("span", { class: "name", title: String(i.feature), text: String(i.feature) }),
      h("div", { class: "track" }, fill),
      h("span", { class: "val", text: `${fmt(Number(i.mean))} ± ${fmt(Number(i.std))}` })));
  }
  summary.append(h("h2", { text: "Permutation importance (held-out data)" }), bars);
  for (const b64 of d.figures) summary.append(h("img", { class: "figure", alt: "Chart produced inside the sandbox", src: `data:image/png;base64,${b64}` }));
  if (Array.isArray(r.caveats) && r.caveats.length)
    summary.append(h("div", { class: "banner warn", text: "Caveats: " + r.caveats.join(" · ") }));

  const bad = d.unverified.length > 0;
  const expl = h("section", { class: "card" }, h("h2", { text: "3 · Explanation" }),
    h("p", { class: "expl", text: d.explanation }),
    h("div", { class: `banner ${bad ? "bad" : "ok"}`,
      text: bad ? `Claim check: these numbers do not appear in the executed output: ${d.unverified.join(", ")}`
                : "Claim check passed: every number above traces back to the executed output." }));

  const rc = d.receipt;
  const rcText = () => JSON.stringify(rc, null, 2);
  const receipt = h("section", { class: "card" }, h("h2", { text: "4 · Receipt" }),
    h("div", { class: "row" },
      chip(rc.container_destroyed ? "Container destroyed ✓" : "Container NOT confirmed destroyed", rc.container_destroyed ? "ok" : "bad"),
      chip(rc.secrets_in_sandbox ? "Secrets in sandbox" : "No secrets in sandbox", rc.secrets_in_sandbox ? "bad" : "ok"),
      chip(`${rc.attempts} attempt${rc.attempts > 1 ? "s" : ""} · ${rc.duration_s}s`)),
    h("div", { class: "flags", "aria-label": "Isolation flags enforced" }, rc.isolation_flags.map((f) => chip(f))),
    h("details", {}, h("summary", { text: "Full receipt (JSON)" }), h("pre", { text: rcText() })),
    h("div", { class: "actions" }, copyBtn("Copy receipt", rcText), downloadBtn("Download receipt", "glassbox-receipt.json", rcText)));
  out.append(summary, expl, receipt, timeline);
  out.scrollIntoView({ behavior: "smooth", block: "start" });
}
function metric(label, value) {
  return h("div", { class: "metric" }, h("small", { text: label }), h("strong", { text: value }));
}

async function runDemo() {
  if (busy) return;
  showAlert("");
  $("results").replaceChildren();
  $("pipeline").hidden = true;
  const card = $("demoCard"), list = $("attacks"), verdict = $("demoVerdict");
  card.hidden = false;
  list.replaceChildren();
  verdict.replaceChildren();
  setBusy(true, "Launching hostile code in sandboxes (about 30 seconds)…");
  const rows = {};
  try {
    await streamNdjson("/api/containment-demo/stream", {}, (ev) => {
      if (ev.type === "plan") {
        ev.attacks.forEach((name, i) => {
          rows[i] = { badge: chip("running…"), ev: h("div", { class: "ev", text: "" }) };
          list.append(h("div", { class: "attack" }, h("strong", { text: name }), rows[i].badge, rows[i].ev));
        });
      } else if (ev.type === "case_result") {
        const r = rows[ev.index];
        if (!r) return;
        r.badge.className = `chip ${ev.contained ? "ok" : "bad"}`;
        r.badge.textContent = ev.contained ? "CONTAINED" : "ESCAPED";
        r.ev.textContent = `exit ${ev.exit_code}${ev.timed_out ? " · killed on timeout" : ""} · container destroyed: ${ev.container_destroyed ? "yes" : "no"} · ${ev.evidence}`;
      } else if (ev.type === "final") {
        verdict.replaceChildren(chip(ev.all_contained ? "All attacks contained" : "CONTAINMENT FAILURE", ev.all_contained ? "ok" : "bad"));
      } else if (ev.type === "error") throw new Error(ev.message);
    });
  } catch (e) {
    showAlert(e.message);
  } finally {
    setBusy(false);
  }
}

function wire() {
  const drop = $("drop"), input = $("file");
  input.addEventListener("change", () => setFile(input.files[0] || null));
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) setFile(f); });
  $("sample").addEventListener("click", async () => {
    try {
      const blob = await (await fetch("/samples/churn.csv")).blob();
      await setFile(new File([blob], "churn.csv", { type: "text/csv" }));
      $("target").value = "churned";
    } catch { showAlert("Could not load the sample dataset."); }
  });
  $("run").addEventListener("click", runAnalysis);
  $("demo").addEventListener("click", runDemo);
}
wire();
loadHealth();
