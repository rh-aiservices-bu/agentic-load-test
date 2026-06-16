// Dashboard logic: load config + scenarios, drive start/stop, and render live
// metrics from the /ws WebSocket into stat cards, charts and tables.

const $ = (id) => document.getElementById(id);
const fmt = (n) => (n ?? 0).toLocaleString();

// Map DOM field id -> path in the RunConfig object.
const FIELDS = {
  base_url: "llm.base_url", api_key: "llm.api_key", model: "llm.model",
  temperature: "llm.temperature", max_tokens: "llm.max_tokens",
  num_users: "num_users", ramp_up_s: "ramp_up_s", duration_s: "duration_s",
  iterations_per_user: "iterations_per_user", max_concurrent_requests: "max_concurrent_requests",
  think_time_min_ms: "think_time_min_ms", think_time_max_ms: "think_time_max_ms",
  use_llm_fallback: "tool_sim.use_llm_fallback",
  min_latency_ms: "tool_sim.min_latency_ms", max_latency_ms: "tool_sim.max_latency_ms",
  sp_preamble: "system_prompt.preamble", sp_position: "system_prompt.position",
  num_unique_prompts: "prompt_pool.num_unique_prompts",
  prompt_tokens_target: "prompt_pool.prompt_tokens_target",
  vm_enabled: "vllm_metrics.enabled", vm_poll: "vllm_metrics.poll_interval_s",
  vm_expand: "vllm_metrics.expand_dns",
};
const NUMERIC = new Set(["temperature", "max_tokens", "num_users", "ramp_up_s", "duration_s",
  "iterations_per_user", "max_concurrent_requests", "think_time_min_ms", "think_time_max_ms",
  "min_latency_ms", "max_latency_ms", "num_unique_prompts", "prompt_tokens_target",
  "vm_poll"]);

function getPath(obj, path) { return path.split(".").reduce((o, k) => o?.[k], obj); }
function setPath(obj, path, val) {
  const keys = path.split("."); let o = obj;
  for (let i = 0; i < keys.length - 1; i++) o = (o[keys[i]] ??= {});
  o[keys[keys.length - 1]] = val;
}

function populate(cfg) {
  for (const [id, path] of Object.entries(FIELDS)) {
    const el = $(id); const v = getPath(cfg, path);
    if (v === undefined) continue;
    if (el.type === "checkbox") el.checked = !!v; else el.value = v;
  }
  // Endpoints list -> textarea, one per line.
  $("vm_endpoints").value = (getPath(cfg, "vllm_metrics.endpoints") || []).join("\n");
}

function collect() {
  const cfg = {};
  for (const [id, path] of Object.entries(FIELDS)) {
    const el = $(id);
    let v = el.type === "checkbox" ? el.checked : el.value;
    if (NUMERIC.has(id)) v = Number(v);
    setPath(cfg, path, v);
  }
  // Scenario weights
  const weights = {};
  document.querySelectorAll("[data-scn]").forEach((el) => {
    const w = Number(el.value);
    if (!Number.isNaN(w)) weights[el.dataset.scn] = w;
  });
  cfg.scenario_weights = weights;
  // Endpoints textarea -> array (split on newlines/commas, trim, drop blanks).
  setPath(cfg, "vllm_metrics.endpoints",
    $("vm_endpoints").value.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean));
  return cfg;
}

function updatePromptTokens() {
  const chars = $("sp_preamble").value.length;
  $("sp_tokens").textContent = `~${Math.max(0, Math.round(chars / 4)).toLocaleString()} tok · ${chars.toLocaleString()} chars`;
}
$("sp_preamble").addEventListener("input", updatePromptTokens);

function updatePoolInfo(live) {
  const el = $("pool-info");
  if (live && live.count > 0) {
    el.innerHTML = `<b style="color:var(--good)">${live.count} distinct large prompts</b> in use, ` +
      `~${live.avg_tokens.toLocaleString()} tok each, ~${live.users_per_prompt} users/prompt sharing each prefix. ` +
      `Watch the KV cache hit rate climb — that's prefix reuse llm-d can route on.`;
    return;
  }
  const n = Number($("num_unique_prompts").value) || 0;
  const t = Number($("prompt_tokens_target").value) || 0;
  const u = Number($("num_users").value) || 0;
  if (n <= 0) {
    el.innerHTML = "Disabled — using the single preamble above. Set &gt; 0 to drive a pool of distinct large prompts.";
  } else {
    const per = u ? (u / n).toFixed(1) : "—";
    el.innerHTML = `<b>${n} distinct large prompts</b>, ~${t.toLocaleString()} tok each, ~${per} users sharing each. ` +
      `Many users → same large prefix → llm-d prefix-aware routing reuses the KV cache. Overrides the single preamble.`;
  }
}
["num_unique_prompts", "prompt_tokens_target", "num_users"].forEach((id) =>
  $(id).addEventListener("input", () => updatePoolInfo()));

async function loadPresets() {
  const { prompts } = await (await fetch("/api/prompts")).json();
  const sel = $("preset");
  prompts.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.name;
    o.textContent = `${p.name} (~${p.tokens_est.toLocaleString()} tok)`;
    sel.append(o);
  });
}
$("preset").addEventListener("change", async (e) => {
  const name = e.target.value;
  if (!name) return;
  const { content } = await (await fetch(`/api/prompts/${encodeURIComponent(name)}`)).json();
  $("sp_preamble").value = content || "";
  updatePromptTokens();
});

async function loadScenarios() {
  const { scenarios } = await (await fetch("/api/scenarios")).json();
  const box = $("scn-weights");
  box.innerHTML = "";
  scenarios.forEach((s) => {
    const label = document.createElement("span");
    label.textContent = s.name;
    label.title = s.description;
    const input = document.createElement("input");
    input.type = "number"; input.step = "0.5"; input.value = s.weight;
    input.dataset.scn = s.name;
    box.append(label, input);
  });
}

// ───────── charts ─────────
const charts = {};
function mkChart(id, datasets, opts = {}) {
  return new Chart($(id), {
    type: "line",
    data: { labels: [], datasets },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      scales: { x: { ticks: { color: "#8b98a5", maxTicksLimit: 8 }, grid: { color: "#222c38" } },
                y: { beginAtZero: true, ticks: { color: "#8b98a5" }, grid: { color: "#222c38" }, ...(opts.y || {}) },
                ...(opts.y1 ? { y1: opts.y1 } : {}) },
      plugins: { legend: { labels: { color: "#e6edf3", boxWidth: 12 } } },
    },
  });
}
const C = (c) => ({ borderColor: c, backgroundColor: c + "33", borderWidth: 2, pointRadius: 0, tension: .25, fill: false });

function initCharts() {
  charts.tokens = mkChart("chart-tokens", [
    { label: "prompt", ...C("#4493f8") }, { label: "completion", ...C("#3fb950") },
    { label: "total", ...C("#d29922") }]);
  charts.rate = mkChart("chart-rate", [
    { label: "tokens/s", ...C("#4493f8") },
    { label: "reqs/s", ...C("#f778ba"), yAxisID: "y1" }],
    { y1: { position: "right", beginAtZero: true, ticks: { color: "#f778ba" }, grid: { drawOnChartArea: false } } });
  charts.ttft = mkChart("chart-ttft", [
    { label: "p50", ...C("#3fb950") }, { label: "p95", ...C("#f85149") }]);
  charts.cache = mkChart("chart-cache", [
    { label: "cumulative", ...C("#3fb950"), fill: true },
    { label: "interval", ...C("#d29922") }],
    { y: { beginAtZero: true, max: 1, ticks: { color: "#8b98a5" }, grid: { color: "#222c38" } } });
}

let serverCacheActive = false;
const MAX_POINTS = 600;
function pushPoint(p) {
  const t = p.t + "s";
  const push = (ch, vals) => {
    ch.data.labels.push(t);
    vals.forEach((v, i) => ch.data.datasets[i].data.push(v));
    if (ch.data.labels.length > MAX_POINTS) {
      ch.data.labels.shift(); ch.data.datasets.forEach((d) => d.data.shift());
    }
    ch.update("none");
  };
  push(charts.tokens, [p.prompt_tokens, p.completion_tokens, p.total_tokens]);
  push(charts.rate, [p.tokens_per_sec, p.requests_per_sec]);
  push(charts.ttft, [p.ttft_p50, p.ttft_p95]);
  const cum = serverCacheActive ? (p.server_cache_hit_rate ?? 0) : (p.cache_hit_rate ?? 0);
  const intv = serverCacheActive ? (p.server_cache_hit_rate_interval ?? 0) : (p.cache_hit_rate_interval ?? 0);
  push(charts.cache, [cum, intv]);
}

function renderSnapshot(m, state) {
  $("c-users").textContent = fmt(m.active_users);
  $("c-tokens").textContent = fmt(m.total_tokens);
  $("c-aptok").textContent = fmt(m.avg_prompt_tokens);
  $("c-tps").textContent = fmt(m.avg_tokens_per_sec);
  $("c-ttft").textContent = m.ttft.p95;
  $("c-reqs").textContent = fmt(m.requests_ok);
  $("c-fail").textContent = fmt(m.requests_failed);
  $("c-tools").textContent = fmt(m.tool_calls);
  $("c-lat").textContent = m.latency.p95;
  // Prefer the server-scraped prefix-cache hit rate when available; fall back to
  // the per-request cached_tokens metric otherwise.
  serverCacheActive = !!m.server_cache;
  const cacheRate = serverCacheActive ? m.server_cache.hit_rate : (m.cache_hit_rate ?? 0);
  $("c-cache").textContent = (cacheRate * 100).toFixed(1) + "%";
  if (serverCacheActive) {
    $("c-cached-l").textContent = `Cache hits / queries (${m.server_cache.targets} pods)`;
    $("c-cached").textContent = `${fmt(m.server_cache.hits)} / ${fmt(m.server_cache.queries)}`;
  } else {
    $("c-cached-l").textContent = "Cached tokens";
    $("c-cached").textContent = fmt(m.cached_tokens);
  }
  $("elapsed").textContent = Math.round(m.elapsed_s) + "s";

  const sb = document.querySelector("#scn-table tbody"); sb.innerHTML = "";
  for (const [name, s] of Object.entries(m.scenarios)) {
    sb.insertAdjacentHTML("beforeend",
      `<tr><td>${name}</td><td class="num">${fmt(s.started)}</td><td class="num">${fmt(s.completed)}</td>
       <td class="num">${fmt(s.failed)}</td><td class="num">${fmt(s.tool_calls)}</td>
       <td class="num">${fmt(s.total_tokens)}</td></tr>`);
  }
  const tb = document.querySelector("#tool-table tbody"); tb.innerHTML = "";
  Object.entries(m.tool_call_counts).sort((a, b) => b[1] - a[1]).forEach(([t, c]) =>
    tb.insertAdjacentHTML("beforeend", `<tr><td>${t}</td><td class="num">${fmt(c)}</td></tr>`));
}

function setState(state, running) {
  const el = $("state");
  el.textContent = state;
  el.className = "pill " + state;
  $("btn-start").disabled = running;
  $("btn-stop").disabled = !running;
  $("btn-reset").disabled = running;  // can only reset between runs
}

// Reset the dashboard view to zero: charts, stat cards, and breakdown tables.
function clearDashboard() {
  Object.values(charts).forEach((c) => {
    c.data.labels = []; c.data.datasets.forEach((d) => (d.data = [])); c.update();
  });
  ["c-users", "c-tokens", "c-aptok", "c-tps", "c-ttft", "c-reqs", "c-fail",
   "c-tools", "c-lat", "c-cached"].forEach((id) => ($(id).textContent = "0"));
  $("c-cache").textContent = "0%";
  serverCacheActive = false;
  $("c-cached-l").textContent = "Cached tokens";
  $("elapsed").textContent = "0s";
  document.querySelector("#scn-table tbody").innerHTML = "";
  document.querySelector("#tool-table tbody").innerHTML = "";
  updatePoolInfo();
  lastT = -1;
}

// ───────── websocket ─────────
let lastT = -1;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    setState(d.state, d.running);
    if (d.metrics) renderSnapshot(d.metrics, d.state);
    if (d.running && d.prompt_pool) updatePoolInfo(d.prompt_pool);
    if (d.point && d.point.t !== lastT) { lastT = d.point.t; pushPoint(d.point); }
  };
  ws.onclose = () => setTimeout(connectWS, 2000);
}

// Replay existing timeline (e.g. after a page reload mid-run).
async function replayTimeline() {
  const { timeline } = await (await fetch("/api/timeline")).json();
  timeline.forEach((p) => { lastT = p.t; pushPoint(p); });
}

// ───────── actions ─────────
$("btn-start").onclick = async () => {
  $("err").textContent = "";
  clearDashboard();
  const res = await fetch("/api/start", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collect()),
  });
  if (!res.ok) $("err").textContent = (await res.json()).error || "Failed to start";
};
$("btn-stop").onclick = () => fetch("/api/stop", { method: "POST" });
$("btn-reset").onclick = async () => {
  $("err").textContent = "";
  const res = await fetch("/api/reset", { method: "POST" });
  if (res.ok) clearDashboard();
  else $("err").textContent = (await res.json()).error || "Failed to reset";
};

(async function init() {
  initCharts();
  await loadPresets();
  populate(await (await fetch("/api/config")).json());
  updatePromptTokens();
  updatePoolInfo();
  await loadScenarios();
  const st = await (await fetch("/api/status")).json();
  setState(st.state, st.running);
  if (st.metrics) renderSnapshot(st.metrics, st.state);
  await replayTimeline();
  connectWS();
})();
