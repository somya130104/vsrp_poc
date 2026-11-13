/* ════════════════════════════════════════════════════════════════
   VSRP dashboard — mid-term layout on the Maersk TP8 / TP7 schedules
   1 Route · 2 Disruption · 3 MILP results · 4 ML + live predictor
   ════════════════════════════════════════════════════════════════ */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtInt = (n) => Math.round(n).toLocaleString('en-US');
const fmtM = (v) => `$${(v / 1e6).toFixed(2)}M`;
const isLarge = () => document.documentElement.getAttribute('data-size') === 'lg';

const S = { svc: 'TP8', overview: null, real: null, ml: null, charts: {}, base: null };

// Default departure / jammed port per service (the real bottlenecks)
const DEFAULTS = {
  TP8: { start: 'CNSHA', pStart: 'KRPUS', pCong: 'CNSHA' },
  TP7: { start: 'CNNGB', pStart: 'KRPUS', pCong: 'CNNGB' },
};

async function api(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), 4000);
}
function setStatus(msg, ready = false) {
  $('#statusText').textContent = msg;
  $('#statusPip').className = `status-pip ${ready ? 'ready' : 'loading'}`;
}
const pname = (code) => (S.overview ? S.overview.ports[code].name : code);

/* ── charts ──────────────────────────────────────────────────── */
const C = () => ({
  milp: css('--s-milp'), base: css('--s-base'), accent: css('--accent'), ml: css('--s-ml'),
  ink: css('--ink'), ink2: css('--ink-2'), ink3: css('--ink-3'), grid: css('--grid'), surface: css('--surface'),
});
function chartDefaults() {
  const c = C();
  Chart.defaults.color = c.ink2;
  Chart.defaults.borderColor = c.grid;
  Chart.defaults.font.family = "'IBM Plex Sans', system-ui, sans-serif";
  Chart.defaults.font.size = isLarge() ? 15 : 13;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.animation = { duration: 300 };
}
function axis(title, extra = {}) {
  const c = C();
  return { title: title ? { display: true, text: title, color: c.ink3, font: { weight: 500 } } : { display: false },
           grid: { color: c.grid }, ticks: { color: c.ink2 }, border: { color: c.grid }, ...extra };
}
function mk(id, cfg) {
  if (S.charts[id]) S.charts[id].destroy();
  S.charts[id] = new Chart(document.getElementById(id), cfg);
  return S.charts[id];
}

/* ── 1 · ROUTE ───────────────────────────────────────────────── */
function meanLate(svc, port) {
  const r = S.real.port_stats.find((p) => p.service === svc && p.port === port);
  return r ? r.mean_delay : null;
}
function drawRoute() {
  const sv = S.overview.services[S.svc];
  const pf = sv.proforma.ports;
  const track = $('#routeTrack');
  let html = '';
  pf.forEach((p, i) => {
    const late = meanLate(S.svc, p.port);
    const hot = late != null && late > 96;
    html += `<div class="port-node">
      <div class="port-pip ${hot ? 'congested' : ''}"></div>
      <div class="port-name">${esc(p.name)}</div>
      <div class="port-code">${p.port}</div>
      <div class="port-late ${hot ? 'hot' : ''}">${late == null ? '—' : `${Math.round(late)} h late`}</div>
    </div>`;
    if (i < pf.length - 1) {
      html += `<div class="leg-connector"><div class="leg-dist">${fmtInt(p.dist_nm)} nm</div><div class="leg-line"></div><div class="leg-time">${Math.round(p.transit_h / 24 * 10) / 10} d</div></div>`;
    }
  });
  const last = pf[pf.length - 1];
  html += `<div class="leg-connector back"><div class="leg-dist">${fmtInt(last.dist_nm)} nm</div><div class="leg-line"></div><div class="leg-time">back to ${esc(pf[0].name)}</div></div>`;
  track.innerHTML = html;

  const total = pf.reduce((s, p) => s + p.dist_nm, 0);
  const ships = S.overview.fleet.filter((f) => f.service === S.svc).length;
  const spd = pf.reduce((s, p) => s + p.dist_nm, 0) / pf.reduce((s, p) => s + p.transit_h, 0);
  const chips = [
    ['Loop distance', `${fmtInt(total)} nm`],
    ['Round trip', `${sv.proforma.loop_days} days`],
    ['Ships on service', `~${sv.fleet_estimate}`],
    ['Ships in data', ships],
    ['Ship size', `${fmtInt(sv.teu)} TEU`],
    ['Planned speed', `${spd.toFixed(1)} kn`],
  ];
  $('#routeStats').innerHTML = chips.map(([k, v]) => `<div class="stat-chip">${k} <span class="val">${v}</span></div>`).join('');
}

/* ── 2 · DISRUPTION controls ─────────────────────────────────── */
function fillSelect(sel, list, value) {
  sel.innerHTML = list.map((p) => `<option value="${p}">${esc(pname(p))}</option>`).join('');
  sel.value = value;
}
function setupControls() {
  const rot = S.overview.services[S.svc].rotation;
  const d = DEFAULTS[S.svc];
  fillSelect($('#startPort'), rot, d.start);
  fillSelect($('#pStart'), rot, d.pStart);
  fillPredCong(d.pCong);
  const h = S.overview.headline;
  $('#disruptionLede').innerHTML = `Choose the port the ship has just left and how late it is, then run the optimiser. In the Maersk schedules, ships reached Shanghai on average <b>${Math.round(h.shanghai_mean_delay)} hours</b> late, and up to <b>${Math.round(h.shanghai_max_delay)} hours</b>.`;
}
function fillPredCong(value) {
  const rot = S.overview.services[S.svc].rotation;
  const start = $('#pStart').value;
  const opts = rot.filter((p) => p !== start);
  fillSelect($('#pCong'), opts, opts.includes(value) ? value : opts[0]);
}

/* ── 3 · MILP ────────────────────────────────────────────────── */
async function runOptimizer() {
  const btn = $('#runBtn');
  const start = $('#startPort').value, delay = +$('#delay').value;
  btn.disabled = true; btn.textContent = 'Solving…';
  setStatus(`Running MILP optimiser: ${pname(start)}, ${delay} h late…`);
  try {
    const r = await api(`/api/optimize?service=${S.svc}&start=${start}&delay=${delay}`);
    S.opt = r;
    drawOptimizer();
    const m = r.plans.milp;
    setStatus(`Optimiser complete: ${m.saving_pct.toFixed(1)}% cost reduction · ${m.skipped.length} port${m.skipped.length === 1 ? '' : 's'} skipped · solved in ${Math.round(m.solve_ms)} ms`, true);
  } catch (e) {
    setStatus(`Optimiser error: ${e.message}`);
  } finally {
    btn.disabled = false; btn.textContent = 'Run optimiser';
  }
}

function drawOptimizer() {
  if (!S.opt) return;
  const c = C();
  const { plans, scenario } = S.opt;
  const b = plans.baseline, m = plans.milp;
  const keys = [['fuel', ['Fuel']], ['delay', ['Cargo', 'delay']], ['residual', ['Carried to', 'next loop']], ['skip', ['Rollover of', 'skipped cargo']], ['handling', ['Extra', 'handling']]];
  $('#costPh').hidden = true; $('#speedPh').hidden = true;
  mk('costChart', {
    type: 'bar',
    data: { labels: keys.map((k) => k[1]), datasets: [
      { label: 'No recovery', data: keys.map((k) => b.costs[k[0]] / 1e6), backgroundColor: c.base, borderRadius: 3, barPercentage: 0.8, categoryPercentage: 0.7 },
      { label: 'MILP recovery', data: keys.map((k) => m.costs[k[0]] / 1e6), backgroundColor: c.milp, borderRadius: 3, barPercentage: 0.8, categoryPercentage: 0.7 },
    ] },
    options: {
      scales: { y: axis('USD million', { beginAtZero: true }), x: axis(null, { grid: { display: false }, ticks: { color: c.ink2, maxRotation: 0, autoSkip: false, font: { size: isLarge() ? 13 : 11.5 } } }) },
      plugins: { tooltip: { callbacks: { title: (it) => it[0].label.join(' '), label: (it) => ` ${it.dataset.label}: $${it.raw.toFixed(2)}M` } } },
    },
  });

  // speeds on the legs actually sailed by the MILP plan
  const nodes = scenario.nodes;
  const vis = m.timeline.filter((t) => t.visited);
  const labels = [], opt = [], pln = [];
  for (let k = 1; k < vis.length; k++) {
    const a = vis[k - 1], t = vis[k];
    labels.push([a.name, `→ ${t.name}`]);
    opt.push(t.speed_in);
    const hrs = nodes[t.node].pln_arr - nodes[a.node].pln_dep;
    pln.push(hrs > 0 ? t.dist_in / hrs : null);
  }
  mk('speedChart', {
    type: 'bar',
    data: { labels, datasets: [
      { type: 'line', label: 'Planned speed', data: pln, borderColor: c.ink3, borderDash: [5, 4], borderWidth: 2, pointRadius: 3, pointBackgroundColor: c.ink3, fill: false, tension: 0 },
      { label: 'Optimal speed', data: opt, backgroundColor: opt.map((v) => (v >= 18 ? c.accent : c.milp)), borderRadius: 3, barPercentage: 0.62 },
    ] },
    options: {
      scales: { y: axis('Knots', { min: 0, max: 24 }), x: axis(null, { grid: { display: false }, ticks: { color: c.ink2, maxRotation: 0, autoSkip: false, font: { size: isLarge() ? 13 : 11.5 } } }) },
      plugins: {
        legend: { labels: { generateLabels: (ch) => Chart.defaults.plugins.legend.labels.generateLabels(ch)
          .map((l) => (l.datasetIndex === 1 ? { ...l, fillStyle: c.milp, strokeStyle: c.milp } : l)) } },
        tooltip: { callbacks: { title: (it) => it[0].label.join(' '), label: (it) => ` ${it.dataset.label}: ${it.raw == null ? '—' : it.raw.toFixed(1)} kn` } },
      },
    },
  });

  $('#savingsPanel').hidden = false;
  $('#savingsNum').textContent = `${m.saving_pct.toFixed(1)}%`;
  $('#savingsDetail').innerHTML = `<div><span>No recovery</span><b>${fmtM(b.total)}</b></div>
    <div><span>MILP recovery</span><b>${fmtM(m.total)}</b></div>
    <div><span>Ports skipped</span><b>${m.skipped.length}</b></div>
    <div><span>Fuel used</span><b>${fmtInt(m.fuel_t)} t</b></div>`;

  const tags = m.timeline.slice(1).map((t) => `<div class="skip-tag ${t.visited ? 'call' : 'skip'}">${t.visited ? 'Call' : 'Skip'} · ${esc(t.name)}${t.visited ? `<small>${t.late < 0.5 ? 'on time' : `${Math.round(t.late)} h late`}</small>` : ''}</div>`).join('');
  $('#skipDecisions').innerHTML = `<h3 class="decisions-h">Port call decisions</h3><div class="skip-row">${tags}</div>`;
}

/* ── formula ─────────────────────────────────────────────────── */
function drawFormula() {
  const tex = String.raw`\begin{aligned}
\min\;& \sum_{(i,j)\in A} F_{ij} \;+\; \sum_{j} \pi_j L_j \;+\; \rho\,L_N \;+\; \sum_{j} \omega_j\,(1-x_j) \;+\; \sum_{j} h\,c_j \\[4pt]
\text{s.t.}\;& \textstyle\sum_{j} y_{0j} = 1,\qquad \sum_{i} y_{ij} = x_j = \sum_{k} y_{jk} \\
& F_{ij} \ge 3f_k\,y_{ij} - \tfrac{2f_k}{T_k}\,T_{ij} \quad \forall k \qquad \big(F = \kappa D_{ij}^3/T^2\big) \\
& \tfrac{D_{ij}}{v_{\max}}\,y_{ij} \le T_{ij} \le \tfrac{D_{ij}}{v_{\min}}\,y_{ij} \\
& a_j \ge d_i + T_{ij} + \tau - M(1-y_{ij}) \\
& b_j \ge \max(a_j,\, o_j), \qquad d_j \ge b_j + s_j - c_j, \qquad c_j \le \bar c_j\,x_j \\
& L_j \ge b_j - p_j - M_j(1-x_j), \qquad x, y \in \{0,1\}
\end{aligned}`;
  try { katex.render(tex, $('#formula'), { displayMode: true, throwOnError: false }); } catch (e) { $('#formula').textContent = 'Formula requires KaTeX.'; }
  const defs = [
    ['y_{ij},\\;x_j', 'sail directly i → j; call port j (1) or skip it (0)'],
    ['T_{ij},\\;F_{ij}', 'sailing hours and fuel cost on the leg'],
    ['a_j,\\;b_j,\\;d_j', 'arrival, berth start and departure; o_j = berth opens'],
    ['L_j,\\;p_j', 'lateness at port j against its planned time p_j'],
    ['\\pi_j,\\;\\rho', 'cargo delay cost per hour; delay carried to next loop'],
    ['\\omega_j', 'rollover cost of the skipped cargo'],
    ['c_j,\\;h', 'berth hours saved by extra cranes, at $h per hour'],
  ];
  const kx = (t) => { try { return katex.renderToString(t, { throwOnError: false }); } catch (e) { return esc(t); } };
  $('#formulaDefs').innerHTML = defs.map(([a, b]) => `<dt>${kx(a)}</dt><dd>${esc(b)}</dd>`).join('');
}

/* ── 4 · ML ──────────────────────────────────────────────────── */
function drawML() {
  if (!S.ml) return;
  const c = C();
  const adv = S.ml.advisor, x = adv.xgboost, r = adv.regret;
  $('#metricsGrid').innerHTML = [
    [`${(100 * x.accuracy).toFixed(1)}%`, 'Accuracy'],
    [`${(100 * x.precision).toFixed(1)}%`, 'Precision'],
    [`${(100 * x.recall).toFixed(1)}%`, 'Recall'],
    [x.roc_auc.toFixed(3), 'ROC-AUC'],
  ].map(([v, l]) => `<div class="metric-tile"><div class="big">${v}</div><div class="lbl">${l}</div></div>`).join('');
  $('#regretTiles').innerHTML = [
    [`${r.mean_pct.toFixed(2)}%`, 'extra cost vs. the exact MILP answer', true],
    [`${r.rule_mean_pct.toFixed(0)}%`, 'extra cost of the simple rule of thumb'],
  ].map(([v, l, hi]) => `<div class="tile${hi ? ' hi' : ''}"><div class="tile-v">${v}</div><div class="tile-l">${esc(l)}</div></div>`).join('');
  $('#perfNote').textContent = `${fmtInt(adv.n_test_scenarios)} held-out situations (${fmtInt(adv.n_test_rows)} port decisions) the model never saw.`;

  const top = adv.importance.slice(0, 8);
  mk('featChart', {
    type: 'bar',
    data: { labels: top.map((f) => f.label), datasets: [{ label: 'Importance', data: top.map((f) => 100 * f.gain), backgroundColor: top.map((_, i) => (i === 0 ? c.accent : c.milp)), borderRadius: 3, barPercentage: 0.7 }] },
    options: {
      indexAxis: 'y',
      scales: { x: axis('% of total importance', { beginAtZero: true }), y: axis(null, { grid: { display: false }, ticks: { color: c.ink2 } }) },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (it) => ` ${it.raw.toFixed(1)}%` } } },
    },
  });

  const rows = adv.models || [];
  $('#modelTable').innerHTML = `<thead><tr><th>#</th><th>Model</th><th class="n">Accuracy</th><th class="n">F1</th><th class="n">ROC-AUC</th><th class="n">Extra cost</th><th class="n">Same skips as MILP</th></tr></thead><tbody>` +
    rows.map((m, i) => `<tr class="${m.key === 'xgboost' ? 'best' : ''}"><td>${i + 1}</td><td><b>${esc(m.name)}</b></td>
      <td class="n">${(100 * m.accuracy).toFixed(1)}%</td><td class="n">${m.f1.toFixed(3)}</td><td class="n">${m.roc_auc.toFixed(3)}</td>
      <td class="n">${m.regret.mean_pct.toFixed(2)}%</td><td class="n">${(100 * m.regret.exact_skip_match).toFixed(0)}%</td></tr>`).join('') +
    `<tr class="muted"><td></td><td>Rule of thumb (no ML)</td><td class="n">—</td><td class="n">—</td><td class="n">—</td><td class="n">${r.rule_mean_pct.toFixed(1)}%</td><td class="n">—</td></tr></tbody>`;
}

/* ── live predictor ──────────────────────────────────────────── */
function predParams() {
  return {
    start: $('#pStart').value, cong: $('#pCong').value,
    delay: +$('#pDelay').value, wait: +$('#pWait').value,
    vmax: +$('#pVmax').value, comp: +$('#pComp').value,
  };
}
function updatePredLabels() {
  const p = predParams();
  $('#pDelayVal').textContent = `${p.delay} h`;
  $('#pWaitVal').textContent = `${p.wait} h`;
  $('#pVmaxVal').textContent = `${p.vmax.toFixed(1)} kn`;
  $('#pCompVal').textContent = `${p.comp}%`;
}
function drawFixed() {
  const sv = S.overview.services[S.svc], P = S.overview.params;
  const items = [
    ['Ship size', `${fmtInt(sv.teu)} TEU`],
    ['Fuel price', `$${P.bunker_usd_t} /t`],
    ['Cargo delay cost', `$${P.delay_usd_teu_day} /TEU·day`],
    ['Rollover cost', `$${P.rollover_usd_teu} /TEU`],
  ];
  $('#fixedRow').innerHTML = `<span class="fixed-k">Fixed in the background</span>` +
    items.map(([k, v]) => `<span class="stat-chip">${k} <span class="val">${v}</span></span>`).join('');
}

const when = (h) => {
  const d = new Date(S.base.getTime() + h * 3600e3);
  return d.toLocaleString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
};

let _predTimer = null, _predSeq = 0;
function schedulePredict() {
  updatePredLabels();
  clearTimeout(_predTimer);
  _predTimer = setTimeout(runPredict, 220);
}
async function runPredict() {
  const p = predParams();
  const seq = ++_predSeq;
  $('#predStatus').textContent = 'Updating…';
  try {
    const r = await api(`/api/optimize?service=${S.svc}&start=${p.start}&delay=${p.delay}&cong_port=${p.cong}&cong_h=${p.wait}&vmax=${p.vmax}&compress=${p.comp / 100}&handling=${p.comp > 0 ? 1 : 0}`);
    if (seq !== _predSeq) return;
    drawPredict(r);
    const ms = r.plans.ml ? r.plans.ml.solve_ms : r.plans.milp.solve_ms;
    $('#predStatus').textContent = `Updated in ${Math.round(ms)} ms`;
  } catch (e) {
    $('#predStatus').textContent = `Error: ${e.message}`;
  }
}
function drawPredict(r) {
  const plan = r.plans.ml || r.plans.milp;
  const base = r.plans.baseline;
  const probs = Object.fromEntries(r.ml_probs.map((q) => [q.node, q.p_skip]));
  const bl = Object.fromEntries(base.timeline.map((t) => [t.node, t]));
  const rows = plan.timeline.slice(1);

  const lateCls = (h) => (h > 96 ? 'lt-hi' : h > 24 ? 'lt-mid' : 'lt-ok');
  const body = rows.map((t) => {
    const p = probs[t.node];
    const pr = p == null ? '<span class="muted">must call</span>'
      : `<span class="pbar"><i style="width:${Math.max(2, 100 * p)}%" class="${p >= 0.5 ? 'hi' : ''}"></i></span>${Math.round(100 * p)}%`;
    const b = bl[t.node];
    const bLate = b && b.visited ? Math.max(0, b.late) : null;
    if (!t.visited) {
      return `<tr class="skipped"><td><b>${esc(t.name)}</b></td><td>${when(t.pln_arr)}</td><td>—</td><td class="n">—</td><td class="n">${bLate == null ? '—' : `${Math.round(bLate)} h`}</td><td>${pr}</td><td><span class="dec skip">Skip</span></td></tr>`;
    }
    const late = Math.max(0, t.late);
    return `<tr><td><b>${esc(t.name)}</b></td><td>${when(t.pln_arr)}</td><td>${when(t.berth ?? t.arr)}</td>
      <td class="n"><span class="late ${lateCls(late)}">${late < 0.5 ? 'On time' : `${Math.round(late)} h`}</span></td>
      <td class="n">${bLate == null ? '—' : `${Math.round(bLate)} h`}</td><td>${pr}</td><td><span class="dec call">Call</span></td></tr>`;
  }).join('');
  $('#predTable').innerHTML = `<thead><tr><th>Port</th><th>Planned arrival</th><th>Expected berthing</th><th class="n">Late by</th><th class="n">If nothing is done</th><th>Skip chance (XGBoost)</th><th>Decision</th></tr></thead><tbody>${body}</tbody>`;

  const visited = rows.filter((t) => t.visited);
  const lax = visited.find((t) => t.port === 'USLAX');
  const worst = visited.reduce((a, t) => (Math.max(0, t.late) > Math.max(0, a.late) ? t : a), visited[0]);
  const skipped = rows.filter((t) => !t.visited).map((t) => t.name);
  const save = 100 * (base.total - plan.total) / base.total;
  $('#predSummary').innerHTML = [
    lax ? [lax.late < 0.5 ? 'On time' : `${Math.round(lax.late)} h late`, `at Los Angeles · ${when(lax.berth ?? lax.arr)}`] : null,
    [worst.late < 0.5 ? 'On time' : `${Math.round(worst.late)} h`, `worst lateness · ${worst.name}`],
    [skipped.length ? skipped.join(', ') : 'None', 'ports skipped'],
    [`${save.toFixed(0)}%`, `cheaper than doing nothing · ${fmtM(plan.total)}`],
  ].filter(Boolean).map(([v, l], i) => `<div class="tile${i === 0 ? ' hi' : ''}"><div class="tile-v">${esc(v)}</div><div class="tile-l">${esc(l)}</div></div>`).join('');
}

/* ── wiring ──────────────────────────────────────────────────── */
function setService(svc) {
  S.svc = svc;
  $$('#serviceSeg button').forEach((b) => b.classList.toggle('on', b.dataset.svc === svc));
  drawRoute();
  setupControls();
  drawFixed();
  runOptimizer();
  schedulePredict();
}
function redrawAll() {
  chartDefaults();
  if (S.opt) drawOptimizer();
  if (S.ml) drawML();
}
function setupTools() {
  const root = document.documentElement;
  const sz = $('#sizeToggle');
  sz.setAttribute('aria-pressed', root.getAttribute('data-size') === 'lg');
  sz.addEventListener('click', () => {
    const lg = root.getAttribute('data-size') !== 'lg';
    if (lg) root.setAttribute('data-size', 'lg'); else root.removeAttribute('data-size');
    sz.setAttribute('aria-pressed', lg);
    try { localStorage.setItem('vsrp-size', lg ? 'lg' : ''); } catch (e) {}
    redrawAll();
  });
  $('#themeToggle').addEventListener('click', () => {
    const dark = root.getAttribute('data-theme') !== 'dark';
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
    try { localStorage.setItem('vsrp-theme', dark ? 'dark' : 'light'); } catch (e) {}
    redrawAll();
  });
}

async function init() {
  chartDefaults();
  setupTools();
  const now = new Date(); now.setMinutes(0, 0, 0);
  S.base = now;
  try {
    const [ov, real] = await Promise.all([api('/api/overview'), api('/api/real')]);
    S.overview = ov; S.real = real;
  } catch (e) {
    setStatus(`Could not load data (${e.message}). Run python build.py, then python app.py.`);
    return;
  }
  drawFormula();
  $$('#serviceSeg button').forEach((b) => b.addEventListener('click', () => setService(b.dataset.svc)));
  $('#delay').addEventListener('input', (e) => { $('#delayVal').textContent = e.target.value; });
  $('#runBtn').addEventListener('click', runOptimizer);
  $('#startPort').addEventListener('change', runOptimizer);
  ['#pDelay', '#pWait', '#pVmax', '#pComp'].forEach((id) => $(id).addEventListener('input', schedulePredict));
  $('#pStart').addEventListener('change', () => { fillPredCong($('#pCong').value); schedulePredict(); });
  $('#pCong').addEventListener('change', schedulePredict);
  // paint the filled part of every slider up to its handle
  const fill = (el) => el.style.setProperty('--p', `${(100 * (el.value - el.min)) / (el.max - el.min)}%`);
  $$('input[type=range]').forEach((el) => { fill(el); el.addEventListener('input', () => fill(el)); });
  setService('TP8');
  api('/api/ml').then((r) => { S.ml = r; drawML(); }).catch((e) => toast(`ML results: ${e.message}`));
}
document.addEventListener('DOMContentLoaded', init);
