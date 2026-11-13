/* ════════════════════════════════════════════════════════════════
   VSRP dashboard — real Maersk TP8 / TP7 data
   ════════════════════════════════════════════════════════════════ */
(() => {
'use strict';

const S = {
  svc: 'TP8', overview: null, real: null, replays: null, ml: null,
  opt: null, pareto: null, plan: 'milp', caseId: null, land: null,
  charts: {}, ctl: null, req: 0,
};

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const isDark = () => document.documentElement.getAttribute('data-theme') === 'dark';
const isLarge = () => document.documentElement.getAttribute('data-size') === 'lg';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const fmtH = (h, d = 0) => (h == null || Number.isNaN(h) ? '—' : `${(+h).toFixed(d)} h`);
const fmtM = (usd, d = 2) => `$${(usd / 1e6).toFixed(d)}M`;
const fmtPct = (p, d = 1) => `${(+p).toFixed(d)}%`;
const fmtInt = (n) => Math.round(n).toLocaleString('en-US');
const fmtDate = (ms) => new Date(ms).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
const isoDate = (iso) => (iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—');

const SERIES = () => ({
  milp: css('--s-milp'), actual: css('--s-actual'), ml: css('--s-ml'), rule: css('--s-rule'),
  base: css('--s-base'), plan: css('--s-plan'), ink: css('--ink'), ink2: css('--ink-2'), ink3: css('--ink-3'),
  grid: css('--grid'), rule2: css('--rule'), surface: css('--surface'), accent: css('--accent'),
});
const CATEGORICAL = () => (isDark()
  ? ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#9085e9']
  : ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#6250d6']);

const PLAN_META = {
  milp:     { label: 'MILP · full recovery',   short: 'MILP full',      key: 'milp' },
  speed:    { label: 'MILP · speed + handling', short: 'Speed only',     key: 'milp', dash: [6, 4] },
  ml:       { label: 'ML-guided MILP',          short: 'ML-guided',      key: 'ml',   dash: [2, 3] },
  rule:     { label: 'Rule-based (Kowatari)',   short: 'Rule-based',     key: 'rule' },
  baseline: { label: 'No recovery',             short: 'No recovery',    key: 'base', dash: [4, 4] },
  actual:   { label: 'Maersk (actual)',         short: 'Maersk actual',  key: 'actual' },
};

/* ─────────────────────────── fetch ─────────────────────────── */
async function api(path, signal) {
  const r = await fetch(path, { signal });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).error || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return r.json();
}
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove('show'), 3200);
}

/* ─────────────────────────── charts ─────────────────────────── */
function chartDefaults() {
  const c = SERIES();
  Chart.defaults.font.family = '"IBM Plex Sans", system-ui, sans-serif';
  Chart.defaults.font.size = isLarge() ? 14 : 12;
  Chart.defaults.color = c.ink2;
  Chart.defaults.borderColor = c.grid;
  Chart.defaults.animation = false;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.responsive = true;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.legend.labels.boxHeight = 8;
  Chart.defaults.plugins.legend.labels.padding = 14;
  Chart.defaults.plugins.legend.labels.color = c.ink2;
  Chart.defaults.plugins.legend.position = 'bottom';
  Chart.defaults.plugins.legend.align = 'start';
  const tt = Chart.defaults.plugins.tooltip;
  tt.backgroundColor = c.ink; tt.titleColor = c.surface; tt.bodyColor = c.surface;
  tt.borderWidth = 0; tt.padding = 10; tt.cornerRadius = 6; tt.displayColors = true;
  tt.boxPadding = 4; tt.usePointStyle = true;
  tt.titleFont = { weight: '600' };
  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.point.radius = 4;
  Chart.defaults.elements.point.hoverRadius = 6;
  Chart.defaults.elements.bar.borderRadius = 4;
}
function axis(title, extra = {}) {
  const c = SERIES();
  return Object.assign({
    grid: { color: c.grid, drawTicks: false },
    border: { display: false },
    ticks: { color: c.ink3, padding: 8 },
    title: title ? { display: true, text: title, color: c.ink3, font: { size: isLarge() ? 13 : 11, weight: '500' } } : undefined,
  }, extra);
}
function mk(id, config) {
  if (S.charts[id]) S.charts[id].destroy();
  const el = document.getElementById(id);
  if (!el) return null;
  S.charts[id] = new Chart(el.getContext('2d'), config);
  return S.charts[id];
}

/* ─────────────────────────── theme & size ─────────────────────────── */
function setTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  try { localStorage.setItem('vsrp-theme', t); } catch (e) { /* ignore */ }
  redrawAll();
}
function setSize(s) {
  if (s) document.documentElement.setAttribute('data-size', s);
  else document.documentElement.removeAttribute('data-size');
  try { localStorage.setItem('vsrp-size', s || ''); } catch (e) { /* ignore */ }
  $('#sizeToggle').setAttribute('aria-pressed', s ? 'true' : 'false');
  setTimeout(redrawAll, 30);
}
function redrawAll() {
  chartDefaults();
  if (!S.overview) return;
  drawMap();
  drawVoyages();
  if (S.opt) drawRecover();
  if (S.pareto) drawPareto();
  if (S.replays) drawReplay();
  if (S.ml) { drawML(); drawModels(); }
}

/* ════════════════════════════ HERO ════════════════════════════ */
function svcCalls(svc = S.svc) {
  return S.real.calls.filter((c) => c.service === svc);
}
function svcPortStats(svc = S.svc) {
  return S.real.port_stats.filter((p) => p.service === svc);
}

function drawHero() {
  const cfg = S.overview.services[S.svc];
  const P = S.overview.ports;
  $('#rotationText').innerHTML = `<b>${esc(cfg.name)}</b> · ` +
    cfg.rotation.map((p) => esc(P[p].name)).join('<span class="arrow">→</span>') +
    `<span class="arrow">↺</span> · ${cfg.proforma.loop_days}-day loop · ≈${cfg.fleet_estimate} vessels`;

  const calls = svcCalls().filter((c) => c.arr_delay_h != null);
  const vessels = new Set(calls.map((c) => c.vessel));
  const worst = calls.reduce((a, c) => (c.arr_delay_h > (a?.arr_delay_h ?? -1e9) ? c : a), null);
  const east = S.real.legs.filter((l) => l.service === S.svc && l.dist_nm > 3000 && l.to === 'USLAX' && l.recovered_h != null);
  const rec = east.length ? east.reduce((s, l) => s + l.recovered_h, 0) / east.length : 0;
  const om = S.overview.omissions.filter((o) => o.service === S.svc);
  const k = [
    { v: calls.length, s: `calls · ${vessels.size} vessel${vessels.size > 1 ? 's' : ''}`, l: 'completed port calls in the real schedules' },
    { v: Math.round(worst.arr_delay_h), s: 'h', l: `worst arrival lateness, ${worst.port_name} (${worst.vessel})` },
    { v: Math.round(rec), s: 'h', l: `won back on average per eastbound Pacific crossing (${east.length} crossings)` },
    { v: om.length, s: om.length === 1 ? 'call' : 'calls', l: 'omitted from the rotation in reality' },
  ];
  $('#kpis').innerHTML = k.map((x) => `<div class="kpi"><div class="kpi-v">${x.v}<small>${esc(x.s)}</small></div><div class="kpi-l">${esc(x.l)}</div></div>`).join('');
  const h = S.overview.headline;
  $('#footMeta').textContent = `Data: Maersk vessel schedules, ${isoDate(h.period_start)} – ${isoDate(h.period_end)} · built ${h ? S.overview.generated : ''}`;
}

const LABEL_POS = {
  CNTXG: { dx: -12, dy: -10, a: 'end' }, CNTAO: { dx: -12, dy: 4, a: 'end' }, KRPUS: { dx: 12, dy: -10, a: 'start' },
  CNSHA: { dx: -12, dy: 6, a: 'end' }, CNNGB: { dx: -12, dy: 14, a: 'end' }, JPYOK: { dx: 12, dy: 4, a: 'start' },
  USLAX: { dx: 12, dy: 14, a: 'start' }, USOAK: { dx: 12, dy: -6, a: 'start' },
};

async function loadLand() {
  if (S.land !== null) return S.land;
  try {
    const topo = await api('https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/land-110m.json');
    S.land = topojson.feature(topo, topo.objects.land);
  } catch (e) {
    S.land = false;
  }
  return S.land;
}

function drawMap() {
  const el = $('#map');
  if (!el || !window.d3) return;
  const W = el.clientWidth || 760;
  const H = Math.max(el.clientHeight, 380);
  const cfg = S.overview.services[S.svc];
  const P = S.overview.ports;
  const pts = cfg.rotation.map((p) => [P[p].lon, P[p].lat]);
  const proj = d3.geoMercator().rotate([-178, 0]);
  proj.fitExtent([[70, 60], [W - 90, H - 50]], { type: 'MultiPoint', coordinates: pts });
  const path = d3.geoPath(proj);
  const svg = d3.create('svg').attr('viewBox', `0 0 ${W} ${H}`).attr('role', 'img')
    .attr('aria-label', `${cfg.name} rotation map`);
  svg.append('path').attr('class', 'grat').attr('d', path(d3.geoGraticule().step([10, 10])()));
  if (S.land) svg.append('path').attr('class', 'land').attr('d', path(S.land));

  S.overview.geometry[S.svc].forEach((g) => {
    svg.append('path').attr('class', 'route').attr('d', path({ type: 'LineString', coordinates: g.coords }));
  });

  const stats = Object.fromEntries(svcPortStats().map((p) => [p.port, p]));
  const omitted = new Set(S.overview.omissions.filter((o) => o.service === S.svc).flatMap((o) => o.skipped));
  const maxMean = Math.max(...Object.values(stats).map((p) => p.mean_delay || 0), 1);
  const r = d3.scaleSqrt().domain([0, maxMean]).range([0, 30]);
  let top = null;
  cfg.rotation.forEach((p) => {
    const [x, y] = proj([P[p].lon, P[p].lat]);
    const st = stats[p];
    if (st && st.mean_delay > 0) svg.append('circle').attr('class', 'port-halo').attr('cx', x).attr('cy', y).attr('r', Math.max(6, r(st.mean_delay)));
    if (st && (!top || st.mean_delay > top.st.mean_delay)) top = { p, x, y, st };
  });
  cfg.rotation.forEach((p) => {
    const [x, y] = proj([P[p].lon, P[p].lat]);
    const st = stats[p];
    const lp = LABEL_POS[p] || { dx: 10, dy: 4, a: 'start' };
    if (omitted.has(p)) svg.append('circle').attr('class', 'port-omit').attr('cx', x).attr('cy', y).attr('r', 10);
    svg.append('circle').attr('class', 'port-dot').attr('cx', x).attr('cy', y).attr('r', 5);
    svg.append('text').attr('class', 'port-label').attr('x', x + lp.dx).attr('y', y + lp.dy).attr('text-anchor', lp.a).text(P[p].name);
    if (st) svg.append('text').attr('class', 'port-sub').attr('x', x + lp.dx).attr('y', y + lp.dy + 14).attr('text-anchor', lp.a)
      .text(`${Math.round(st.mean_delay)} h avg`);
  });
  if (top && top.st.mean_delay > 20) {
    const cx = Math.min(W - 260, top.x + 150), cy = Math.min(H - 70, top.y + 120);
    svg.append('line').attr('class', 'callout-line').attr('x1', top.x).attr('y1', top.y - Math.max(6, r(top.st.mean_delay)))
      .attr('x2', cx - 6).attr('y2', cy - 26);
    svg.append('text').attr('class', 'callout-num').attr('x', cx).attr('y', cy).text(`${Math.round(top.st.max_delay)} h`);
    svg.append('text').attr('class', 'callout-txt').attr('x', cx).attr('y', cy + 20)
      .text(`worst lateness at ${top.st.name} · mean ${Math.round(top.st.mean_delay)} h over ${top.st.n} calls`);
  }
  el.replaceChildren(svg.node());
}

/* ════════════════════════════ 01 VOYAGES ════════════════════════════ */
function drawVoyages() {
  const c = SERIES();
  const cfg = S.overview.services[S.svc];
  const calls = svcCalls().filter((x) => x.arr_delay_h != null && x.t_act_arr != null);
  const vessels = [...new Set(calls.map((x) => x.vessel))].sort();
  const pal = CATEGORICAL();

  mk('cLateness', {
    type: 'line',
    data: {
      datasets: vessels.map((v, i) => ({
        label: v,
        data: calls.filter((x) => x.vessel === v).sort((a, b) => a.t_act_arr - b.t_act_arr)
          .map((x) => ({ x: x.t_act_arr * 3600e3, y: x.arr_delay_h, port: x.port_name, voy: x.voyage_in })),
        borderColor: pal[i % pal.length], backgroundColor: pal[i % pal.length],
        pointBorderColor: c.surface, pointBorderWidth: 2, pointRadius: 5, tension: 0,
      })),
    },
    options: {
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: axis(null, { type: 'linear', ticks: { color: c.ink3, padding: 8, maxTicksLimit: 8, callback: (v) => fmtDate(v) } }),
        y: axis('Hours late at arrival', { beginAtZero: true }),
      },
      plugins: {
        tooltip: { callbacks: {
          title: (it) => `${it[0].raw.port} · ${fmtDate(it[0].raw.x)}`,
          label: (it) => ` ${it.dataset.label} (${it.raw.voy}): ${Math.round(it.raw.y)} h late`,
        } },
      },
    },
  });

  const stats = Object.fromEntries(svcPortStats().map((p) => [p.port, p]));
  const labels = cfg.rotation.map((p) => S.overview.ports[p].name);
  const dots = [];
  cfg.rotation.forEach((p, i) => (stats[p]?.arr_delays || []).forEach((d) => dots.push({ x: labels[i], y: d })));
  mk('cPorts', {
    data: {
      labels,
      datasets: [
        { type: 'bar', label: 'Mean lateness', data: cfg.rotation.map((p) => stats[p]?.mean_delay ?? 0),
          backgroundColor: c.milp, borderRadius: 4, barPercentage: 0.62, order: 2 },
        { type: 'scatter', label: 'Single call', data: dots, backgroundColor: c.ink, borderColor: c.surface,
          borderWidth: 1.5, pointRadius: 4, order: 1 },
      ],
    },
    options: {
      scales: { x: axis(null, { grid: { display: false }, ticks: { color: c.ink2, padding: 6, maxRotation: 0, autoSkip: false } }), y: axis('Hours late', { beginAtZero: true }) },
      plugins: { tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: ${Math.round(it.parsed.y)} h` } } },
    },
  });

  const pac = S.real.legs.filter((l) => l.service === S.svc && l.dist_nm > 3000 && l.to === 'USLAX' && l.pln_h)
    .sort((a, b) => a.t_dep - b.t_dep);
  mk('cPacific', {
    type: 'bar',
    data: {
      labels: pac.map((l) => [l.vessel.replace('Maersk ', '').replace(' Maersk', ''), l.voyage]),
      datasets: [
        { label: 'Planned hours', data: pac.map((l) => l.pln_h), backgroundColor: c.base, barPercentage: 0.8, categoryPercentage: 0.7 },
        { label: 'Actual hours', data: pac.map((l) => l.act_h), backgroundColor: c.milp, barPercentage: 0.8, categoryPercentage: 0.7 },
      ],
    },
    options: {
      scales: { x: axis(null, { grid: { display: false }, ticks: { color: c.ink2 } }), y: axis('Hours, departure to arrival', { min: 0 }) },
      plugins: { tooltip: { callbacks: {
        afterBody: (it) => { const l = pac[it[0].dataIndex]; return `Won back ${Math.round(l.recovered_h)} h · avg ${l.avg_speed_kn} kn vs plan ${l.pln_speed_kn} kn`; },
      } } },
    },
  });

  drawOmission();
  drawCallTable();
}

function drawOmission() {
  const el = $('#omissionStory');
  const om = S.overview.omissions.filter((o) => o.service === S.svc);
  if (!om.length) { el.innerHTML = '<p>No omitted calls on this service.</p>'; return; }
  const vessel = om[0].vessel;
  const vo = om.filter((o) => o.vessel === vessel);
  const blk = S.real.calls.filter((c) => c.vessel === vessel && c.service === S.svc).sort((a, b) => a.t_pln_arr - b.t_pln_arr);
  const iFrom = blk.findIndex((c) => c.port === vo[0].from && c.voyage_out === vo[0].voyage);
  const nodes = [];
  let i = iFrom;
  vo.forEach((o) => {
    const a = blk[i];
    if (!nodes.length) nodes.push({ p: a.port_name, d: `left ${Math.round(a.dep_delay_h)} h late` });
    o.skipped_names.forEach((n) => nodes.push({ p: n, d: 'omitted', skip: true }));
    const j = blk.findIndex((c, k) => k > i && c.port === o.to);
    i = j;
    nodes.push({ p: blk[j].port_name, d: `${Math.round(blk[j].arr_delay_h)} h late` });
  });
  const dedup = nodes.filter((n, k) => !(k > 0 && n.p === nodes[k - 1].p && !n.skip));
  const first = vo[0], last = vo[vo.length - 1];
  const isTP8 = S.svc === 'TP8';
  const text = isTP8
    ? `Leaving ${first.from === 'USOAK' ? 'Oakland' : first.from} ${Math.round(first.from_dep_delay_h)} h late, ${esc(vessel)} omitted ${vo.flatMap((o) => o.skipped_names).join(' and ')}. That cut its lateness to ${Math.round(first.to_arr_delay_h)} h at Qingdao. It then waited off Shanghai anyway and berthed ${Math.round(last.to_arr_delay_h)} h late. Omitting calls <b>before</b> a congested port buys little time, and the MILP finds the same.`
    : `${esc(vessel)} sailed Los Angeles → Yokohama without calling Oakland, recovering from ${Math.round(first.from_dep_delay_h)} h to ${Math.round(first.to_arr_delay_h)} h late. Maersk announced a direct Oakland call on TP7 from August 2026, so this may be a rotation change rather than a recovery decision.`;
  el.innerHTML = `
    <span class="story-k">Real omitted call${vo.length > 1 ? 's' : ''} · ${esc(vessel)} · voyage ${esc(first.voyage)}</span>
    <h3>${isTP8 ? 'Two ports skipped, and still stuck at Shanghai' : 'Oakland left out of the loop'}</h3>
    <div class="story-track">${dedup.map((n) => `<div class="st-node${n.skip ? ' skip' : ''}"><div class="p">${esc(n.p)}</div><div class="d">${esc(n.d)}</div></div>`).join('')}</div>
    <p>${text}</p>`;
}

function drawCallTable() {
  const calls = svcCalls();
  const sel = $('#callVessel');
  const vessels = [...new Set(calls.map((c) => c.vessel))].sort();
  const cur = sel.value && vessels.includes(sel.value) ? sel.value : '';
  sel.innerHTML = `<option value="">All vessels</option>` + vessels.map((v) => `<option${v === cur ? ' selected' : ''}>${esc(v)}</option>`).join('');
  const rows = calls.filter((c) => !cur || c.vessel === cur).sort((a, b) => a.t_pln_arr - b.t_pln_arr);
  $('#callCount').textContent = rows.length;
  const d = (iso) => (iso ? iso.slice(5, 16).replace('T', ' ') : '—');
  const lat = (h) => (h == null ? '<td class="n">—</td>' : `<td class="n${h > 48 ? ' late-hi' : ''}">${Math.round(h)}</td>`);
  $('#callTable').innerHTML = `<thead><tr><th>Vessel</th><th>Voyage</th><th>Port</th><th>Planned arrival (UTC)</th><th>Actual arrival</th><th class="n">Late at arrival, h</th><th class="n">Late at departure, h</th><th class="n">Berth h (plan / actual)</th><th>Status</th></tr></thead><tbody>` +
    rows.map((c) => `<tr><td>${esc(c.vessel)}</td><td class="mono">${esc(c.voyage_in)}/${esc(c.voyage_out)}</td><td>${esc(c.port_name)}</td><td class="mono">${d(c.pln_arr)}</td><td class="mono">${d(c.act_arr)}</td>${lat(c.arr_delay_h)}${lat(c.dep_delay_h)}<td class="n">${c.stay_pln_h ?? '—'} / ${c.stay_act_h ?? '—'}</td><td>${c.status.replace('_', ' ')}</td></tr>`).join('') + '</tbody>';
}

/* ════════════════════════════ 02 RECOVER ════════════════════════════ */
const PRESETS = {
  TP8: [
    { t: 'Georg Maersk leaves Shanghai 143 h late', start: 'CNSHA', delay: 143, cong: '', h: 0 },
    { t: 'Busan on time, Shanghai berth +300 h', start: 'KRPUS', delay: 12, cong: 'CNSHA', h: 300 },
    { t: 'Gustav Maersk leaves Oakland 171 h late', start: 'USOAK', delay: 171, cong: 'CNSHA', h: 326 },
    { t: 'Small delay, no congestion', start: 'CNTXG', delay: 24, cong: '', h: 0 },
  ],
  TP7: [
    { t: 'Maersk Kinloss leaves Los Angeles 95 h late', start: 'USLAX', delay: 95, cong: '', h: 0 },
    { t: 'Kinloss leaves Busan 87 h late', start: 'KRPUS', delay: 87, cong: 'CNNGB', h: 68 },
    { t: 'Small delay, no congestion', start: 'JPYOK', delay: 24, cong: '', h: 0 },
  ],
};

function ctlState() {
  return {
    service: S.svc,
    start: $('#startPort').value,
    delay: +$('#delay').value,
    cong_port: $('#congPort').value,
    cong_h: +$('#congH').value,
    bunker: +$('#bunker').value,
    delay_cost: +$('#delayCost').value,
    rollover: +$('#rollover').value,
    skip: $('#allowSkip').checked ? 1 : 0,
    handling: $('#allowHandling').checked ? 1 : 0,
    mandatory: $$('#mandatory .chip.on').map((b) => b.dataset.p).join(','),
  };
}
function paintRange(el) {
  const p = ((el.value - el.min) / (el.max - el.min)) * 100;
  el.style.setProperty('--p', `${p}%`);
}
function syncOutputs() {
  $('#delayOut').textContent = `${$('#delay').value} h`;
  $('#congOut').textContent = $('#congPort').value ? `${$('#congH').value} h` : 'none';
  $('#congH').disabled = !$('#congPort').value;
  $('#bunkerOut').textContent = `$${$('#bunker').value} /t`;
  $('#delayCostOut').textContent = `$${$('#delayCost').value} /TEU·day`;
  $('#rolloverOut').textContent = `$${$('#rollover').value} /TEU`;
  $$('#controls input[type=range]').forEach(paintRange);
}

function setupControls() {
  const cfg = S.overview.services[S.svc];
  const P = S.overview.ports;
  const start = $('#startPort');
  const prevStart = start.value;
  start.innerHTML = cfg.rotation.map((p) => `<option value="${p}">${esc(P[p].name)}</option>`).join('');
  start.value = cfg.rotation.includes(prevStart) ? prevStart : PRESETS[S.svc][0].start;
  buildPortDependents();
  $('#presets').innerHTML = PRESETS[S.svc].map((p, i) => `<button class="chip" data-i="${i}">${esc(p.t)}</button>`).join('');
  $$('#presets .chip').forEach((b) => b.addEventListener('click', () => applyPreset(PRESETS[S.svc][+b.dataset.i], b)));
}
function buildPortDependents(keepCong) {
  const cfg = S.overview.services[S.svc];
  const P = S.overview.ports;
  const startP = $('#startPort').value;
  const cong = $('#congPort');
  const prev = keepCong ?? cong.value;
  const others = cfg.rotation.filter((p) => p !== startP);
  cong.innerHTML = '<option value="">None</option>' + others.map((p) => `<option value="${p}">${esc(P[p].name)}</option>`).join('');
  cong.value = others.includes(prev) ? prev : '';
  const mand = cfg.mandatory;
  $('#mandatory').innerHTML = others.map((p) => `<button class="chip${mand.includes(p) ? ' on' : ''}" data-p="${p}" aria-pressed="${mand.includes(p)}">${esc(P[p].name)}</button>`).join('');
  $$('#mandatory .chip').forEach((b) => b.addEventListener('click', () => {
    b.classList.toggle('on'); b.setAttribute('aria-pressed', b.classList.contains('on')); queueOptimize();
  }));
  syncOutputs();
}
function applyPreset(p, btn) {
  $('#startPort').value = p.start;
  buildPortDependents(p.cong);
  $('#delay').value = p.delay;
  $('#congH').value = p.h;
  $$('#presets .chip').forEach((b) => b.classList.toggle('on', b === btn));
  syncOutputs();
  queueOptimize(0);
}

let optTimer = null;
function queueOptimize(wait = 220) {
  syncOutputs();
  clearTimeout(optTimer);
  optTimer = setTimeout(runOptimize, wait);
}
async function runOptimize() {
  const st = ctlState();
  const qs = new URLSearchParams(st).toString();
  const id = ++S.req;
  S.ctl?.abort();
  S.ctl = new AbortController();
  $('.results').classList.add('loading');
  try {
    const [opt, par] = await Promise.all([api(`/api/optimize?${qs}`, S.ctl.signal), api(`/api/pareto?${qs}`, S.ctl.signal)]);
    if (id !== S.req) return;
    S.opt = opt; S.pareto = par.points;
    if (!S.opt.plans[S.plan]) S.plan = 'milp';
    drawRecover();
    drawPareto();
  } catch (e) {
    if (e.name !== 'AbortError') toast(`Optimiser unavailable: ${e.message}`);
  } finally {
    if (id === S.req) $('.results').classList.remove('loading');
  }
}

function planSentence(plan, scn) {
  const tl = plan.timeline.slice(1);
  const parts = [];
  const fast = tl.filter((t) => t.visited && t.speed_in && t.speed_in >= 17).sort((a, b) => b.dist_in - a.dist_in)[0];
  if (fast) parts.push(`sail to ${fast.name} at ${fast.speed_in.toFixed(1)} kn`);
  const skipped = tl.filter((t) => !t.visited).map((t) => t.name);
  if (skipped.length) parts.push(`omit ${skipped.join(' and ')}`);
  const comp = tl.filter((t) => t.compress > 0.5);
  if (comp.length) parts.push(`buy back ${Math.round(comp.reduce((s, t) => s + t.compress, 0))} berth hours at ${comp.map((t) => t.name).join(', ')}`);
  const slow = tl.filter((t) => t.visited && t.speed_in && t.speed_in <= 12.5 && t.dist_in > 2000);
  if (slow.length) parts.push(`slow-steam to ${slow[0].name}`);
  const last = tl[tl.length - 1];
  const end = plan.residual_h < 1 ? `back on schedule by ${last.name}` : `still ${Math.round(plan.residual_h)} h late at ${last.name}`;
  if (!parts.length) return `Keep the planned schedule. The ship ends ${end}.`;
  const s = parts.join(', ');
  return `${s.charAt(0).toUpperCase()}${s.slice(1)}. The ship ends ${end}.`;
}

function drawRecover() {
  const { plans, scenario } = S.opt;
  const m = plans.milp, b = plans.baseline;
  const savingUSD = b.total - m.total;
  $('#verdict').innerHTML = `
    <div>
      <div class="verdict-big num">${Math.max(0, m.saving_pct).toFixed(1)}<span class="pct">%</span></div>
      <div class="verdict-cap">lower cost than doing nothing</div>
    </div>
    <div class="verdict-body">
      <h4>${esc(planSentence(m, scenario))}</h4>
      <p>Optimal recovery costs <b>${fmtM(m.total)}</b> against <b>${fmtM(b.total)}</b> for no recovery, saving ${fmtM(savingUSD)}.
      The Kowatari rule saves ${fmtPct(plans.rule.saving_pct)}${plans.ml ? ` and the ML-guided plan ${fmtPct(plans.ml.saving_pct)}` : ''}.</p>
      <div class="verdict-facts">
        <div><div class="fact-v">${fmtH(m.residual_h)}</div><div class="fact-l">late at end of loop (from ${fmtH(scenario.d0)})</div></div>
        <div><div class="fact-v">${fmtInt(m.fuel_t)} t</div><div class="fact-l">fuel · ${fmtInt(m.co2_t)} t CO₂</div></div>
        <div><div class="fact-v">${m.skipped.length ? m.skipped.map((p) => S.overview.ports[p].name).join(', ') : 'None'}</div><div class="fact-l">ports omitted</div></div>
        <div><div class="fact-v">${m.solve_ms} ms</div><div class="fact-l">exact MILP solve</div></div>
      </div>
    </div>`;

  const order = ['milp', 'speed', 'ml', 'rule', 'baseline'].filter((k) => plans[k]);
  $('#planSeg').innerHTML = order.map((k) => `<button data-k="${k}" class="${k === S.plan ? 'on' : ''}">${PLAN_META[k].short}</button>`).join('');
  $$('#planSeg button').forEach((bt) => bt.addEventListener('click', () => { S.plan = bt.dataset.k; drawRecover(); }));
  drawStrip(plans[S.plan]);

  const c = SERIES();
  const nodes = scenario.nodes.slice(1);
  const labels = nodes.map((n) => S.overview.ports[n.port].name);
  const ds = order.slice().reverse().map((k) => {
    const meta = PLAN_META[k];
    const col = c[meta.key];
    return {
      label: meta.label, data: plans[k].timeline.slice(1).map((t) => (t.visited ? t.late : null)),
      borderColor: col, backgroundColor: col, borderDash: meta.dash || [], spanGaps: true,
      pointRadius: k === 'milp' ? 5 : 3.5, borderWidth: k === 'milp' ? 3 : 2, pointBorderColor: c.surface, pointBorderWidth: 1.5,
    };
  });
  mk('cPlanLate', {
    type: 'line', data: { labels, datasets: ds },
    options: {
      interaction: { mode: 'index', intersect: false },
      scales: { x: axis(null, { grid: { display: false }, ticks: { color: c.ink2, maxRotation: 0, autoSkip: false } }), y: axis('Hours behind plan', { beginAtZero: true }) },
      plugins: { tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: ${it.raw == null ? 'omitted' : `${Math.round(it.raw)} h`}` } } },
    },
  });

  const comps = [['fuel', 'Fuel'], ['delay', 'Cargo delay'], ['skip', 'Rollover'], ['handling', 'Extra handling'], ['residual', 'Carry-over delay']];
  const pal = CATEGORICAL();
  mk('cPlanCost', {
    type: 'bar',
    data: {
      labels: order.map((k) => PLAN_META[k].short),
      datasets: comps.map(([k, l], i) => ({
        label: l, data: order.map((p) => plans[p].costs[k] / 1e6), backgroundColor: pal[i],
        borderColor: c.surface, borderWidth: { left: 0, right: 2, top: 0, bottom: 0 }, borderSkipped: false, borderRadius: 0,
        barPercentage: 0.7,
      })),
    },
    options: {
      indexAxis: 'y',
      scales: {
        x: axis('USD million', { stacked: true, beginAtZero: true }),
        y: axis(null, { stacked: true, grid: { display: false }, ticks: { color: c.ink2, font: { weight: '600' } } }),
      },
      plugins: { tooltip: { mode: 'index', callbacks: {
        label: (it) => ` ${it.dataset.label}: $${it.raw.toFixed(2)}M`,
        footer: (it) => `Total ${fmtM(plans[order[it[0].dataIndex]].total)}`,
      } } },
    },
  });

  const probs = S.opt.ml_probs || [];
  const visitedMilp = new Set(m.timeline.filter((t) => t.visited).map((t) => t.node));
  $('#mlProbs').innerHTML = probs.length ? probs.map((p) => {
    const skipM = !visitedMilp.has(p.node);
    const skipML = p.p_skip >= 0.5;
    return `<div class="prob-row"><span class="nm">${esc(p.name)}</span>
      <span class="prob-bar" role="img" aria-label="${Math.round(p.p_skip * 100)} percent"><i style="width:${(p.p_skip * 100).toFixed(1)}%"></i></span>
      <span class="v">${(p.p_skip * 100).toFixed(p.p_skip > 0.01 && p.p_skip < 0.99 ? 0 : 1)}%</span>
      <span class="dec ${skipM === skipML ? 'agree' : 'disagree'}">MILP: ${skipM ? 'omit' : 'call'}</span></div>`;
  }).join('') : '<p class="empty">Port omission is switched off, so there is nothing to predict.</p>';

  const ms = plans.ml ? ` · ML-guided ${plans.ml.solve_ms} ms` : '';
  $('#solverLine').textContent = `HiGHS (SciPy) · ${m.n_vars} variables, ${m.n_binary} binary · ${m.n_cons} constraints · solved in ${m.solve_ms} ms${ms} · fuel curve linearised with 21 tangent cuts`;
}

function drawStrip(plan) {
  const P = S.overview.ports;
  const tl = plan.timeline;
  const el = $('#strip');
  el.style.setProperty('--n', tl.length);
  let html = `<div class="vs-port start"><div class="vs-in none">departed</div><span class="nm">${esc(P[tl[0].port].name)}</span><span class="cd">${tl[0].port}</span>
    <span class="lt bad">+${Math.round(S.opt.scenario.d0)} h</span><span class="st">departure delay</span></div>`;
  tl.slice(1).forEach((t) => {
    if (!t.visited) {
      html += `<div class="vs-port skip"><div class="vs-in none">bypassed</div><span class="nm">${esc(P[t.port].name)}</span><span class="cd">${t.port}</span><span class="lt">Omitted</span><span class="st">cargo rolled over</span></div>`;
      return;
    }
    const late = t.late;
    const extra = t.wait > 1 ? `waits ${Math.round(t.wait)} h for berth` : (t.compress > 0.5 ? `${Math.round(t.compress)} berth h saved` : 'straight to berth');
    html += `<div class="vs-port"><div class="vs-in"><b class="${t.speed_in >= 17 ? 'fast' : ''}">${t.speed_in.toFixed(1)} kn</b><span>${fmtInt(t.dist_in)} nm</span></div>
      <span class="nm">${esc(P[t.port].name)}</span><span class="cd">${t.port}</span>
      <span class="lt ${late < 1 ? 'ok' : 'bad'}">${late < 1 ? 'On time' : `+${Math.round(late)} h`}</span><span class="st">${esc(extra)}</span></div>`;
  });
  el.innerHTML = html;
}

function drawPareto() {
  const c = SERIES();
  const pts = (S.pareto || []).slice().sort((a, b) => a.fuel_t - b.fuel_t);
  const plans = S.opt?.plans;
  const extra = [];
  if (plans) {
    extra.push({ label: 'No recovery', data: [{ x: plans.baseline.fuel_t, y: plans.baseline.lateness_sum_h }], backgroundColor: c.base, pointStyle: 'rectRot', pointRadius: 7 });
    extra.push({ label: 'Rule-based', data: [{ x: plans.rule.fuel_t, y: plans.rule.lateness_sum_h }], backgroundColor: c.rule, pointStyle: 'triangle', pointRadius: 7 });
    extra.push({ label: 'Chosen MILP plan', data: [{ x: plans.milp.fuel_t, y: plans.milp.lateness_sum_h }], backgroundColor: c.milp, borderColor: c.ink, borderWidth: 2, pointRadius: 8 });
  }
  mk('cPareto', {
    type: 'scatter',
    data: { datasets: [
      { label: 'MILP frontier', data: pts.map((p) => ({ x: p.fuel_t, y: p.lateness_h, w: p.weight, s: p.skipped })), showLine: true,
        borderColor: c.milp, backgroundColor: c.surface, pointBorderColor: c.milp, pointBorderWidth: 2, pointRadius: 4, borderWidth: 2 },
      ...extra,
    ] },
    options: {
      scales: { x: axis('Fuel over the loop (t)'), y: axis('Total lateness across calls (h)', { beginAtZero: true }) },
      plugins: { tooltip: { callbacks: { label: (it) => {
        const r = it.raw;
        const w = r.w != null ? ` · delay weight ×${r.w}` : '';
        const s = r.s && r.s.length ? ` · omits ${r.s.map((p) => S.overview.ports[p].name).join(', ')}` : '';
        return ` ${it.dataset.label}: ${fmtInt(r.x)} t, ${fmtInt(r.y)} h${w}${s}`;
      } } } },
    },
  });
}

/* ════════════════════════════ 03 REPLAY ════════════════════════════ */
function drawReplay() {
  const cases = S.replays.filter((c) => c.service === S.svc);
  if (!cases.find((c) => c.id === S.caseId)) {
    const pick = cases.find((c) => c.real_skips.length) || cases.slice().sort((a, b) => b.d0 - a.d0)[0];
    S.caseId = pick?.id;
  }
  const all = S.replays;
  const avg = (arr) => arr.reduce((s, x) => s + x, 0) / Math.max(1, arr.length);
  const sv = (k) => avg(all.map((c) => c.plans[k].saving_pct));
  const gap = avg(all.map((c) => 100 * (c.plans.actual.total - c.plans.speed.total) / c.plans.actual.total));
  const pacificDiff = [];
  all.forEach((c) => {
    c.plans.actual.timeline.forEach((t, i) => {
      const s = c.plans.speed.timeline[i];
      if (t.visited && s?.visited && t.dist_in > 3000 && t.speed_in && s.speed_in) pacificDiff.push(Math.abs(t.speed_in - s.speed_in));
    });
  });
  $('#replaySummary').innerHTML = [
    { v: all.length, l: `real decision points (${all.filter((c) => c.service === 'TP8').length} TP8 · ${all.filter((c) => c.service === 'TP7').length} TP7)` },
    { v: fmtPct(sv('actual'), 0), l: 'cost Maersk saved versus doing nothing' },
    { v: fmtPct(sv('speed'), 0), l: `MILP with speed only · ${gap >= 0 ? `${gap.toFixed(1)}% cheaper` : `${(-gap).toFixed(1)}% dearer`} than Maersk` },
    { v: `${avg(pacificDiff).toFixed(1)} kn`, l: `mean speed gap to Maersk on ocean crossings (${pacificDiff.length} legs)` },
  ].map((x) => `<div class="rs"><div class="rs-v">${x.v}</div><div class="rs-l">${esc(x.l)}</div></div>`).join('');

  const byV = {};
  cases.forEach((c) => (byV[c.vessel] = byV[c.vessel] || []).push(c));
  $('#caseList').innerHTML = Object.keys(byV).sort().map((v) => `<div class="case-group">${esc(v)}</div>` +
    byV[v].map((c) => `<button class="case${c.id === S.caseId ? ' on' : ''}" data-id="${c.id}">
      <span class="c1">Leaving ${esc(c.port_name)} · ${esc(c.voyage)}</span>
      <span class="c2">${isoDate(c.date)}${c.real_skips.length ? ' · real omission' : ''}</span>
      <span class="c3">+${Math.round(c.d0)} h</span></button>`).join('')).join('');
  $$('#caseList .case').forEach((b) => b.addEventListener('click', () => { S.caseId = b.dataset.id; drawReplay(); }));
  drawCase(cases.find((c) => c.id === S.caseId));
}

function drawCase(cs) {
  if (!cs) return;
  const P = S.overview.ports;
  const c = SERIES();
  const pl = cs.plans;
  const name = (p) => P[p].name;
  $('#caseTitle').innerHTML = `<h3>${esc(cs.vessel)} leaves ${esc(cs.port_name)} ${Math.round(cs.d0)} h late</h3>
    <p>Voyage ${esc(cs.voyage)} · ${isoDate(cs.date)} · next ${cs.horizon.length} calls: ${cs.horizon.map(name).join(' → ')}</p>
    <div class="flags">${cs.congested.length ? `<span class="tag tag-derived">Berth waiting observed: ${cs.congested.map(name).join(', ')}</span>` : ''}
    ${cs.real_skips.length ? `<span class="tag tag-assumed">Maersk omitted: ${cs.real_skips.map(name).join(', ')}</span>` : ''}
    <span class="tag tag-real">Real planned timetable</span></div>`;

  const labels = [`Start`, ...cs.horizon.map(name)];
  const order = ['baseline', 'actual', 'speed', 'milp'];
  const series = (k) => [pl[k].timeline[0].late, ...pl[k].timeline.slice(1).map((t) => (t.visited ? t.late : null))];
  mk('cCaseLate', {
    type: 'line',
    data: { labels, datasets: order.map((k) => ({
      label: PLAN_META[k].label, data: series(k), borderColor: c[PLAN_META[k].key], backgroundColor: c[PLAN_META[k].key],
      borderDash: PLAN_META[k].dash || [], spanGaps: true, borderWidth: k === 'actual' ? 3 : 2,
      pointRadius: k === 'actual' ? 5 : 3.5, pointBorderColor: c.surface, pointBorderWidth: 1.5,
    })) },
    options: {
      interaction: { mode: 'index', intersect: false },
      scales: { x: axis(null, { grid: { display: false }, ticks: { color: c.ink2, maxRotation: 0, autoSkip: false } }), y: axis('Hours behind plan', { beginAtZero: true }) },
      plugins: { tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: ${it.raw == null ? 'omitted' : `${Math.round(it.raw)} h`}` } } },
    },
  });

  mk('cCaseSpeed', {
    type: 'bar',
    data: { labels: cs.horizon.map((p) => `→ ${name(p)}`), datasets: ['actual', 'speed', 'milp'].map((k) => ({
      label: PLAN_META[k].label, data: pl[k].timeline.slice(1).map((t) => (t.visited ? t.speed_in : null)),
      backgroundColor: k === 'milp' ? c.milp : (k === 'speed' ? `${c.milp}88` : c.actual), barPercentage: 0.85, categoryPercentage: 0.7,
    })) },
    options: {
      scales: { x: axis(null, { grid: { display: false }, ticks: { color: c.ink2, maxRotation: 0, autoSkip: false } }), y: axis('Knots', { min: 0, max: 24 }) },
      plugins: { tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: ${it.raw == null ? 'omitted' : `${it.raw.toFixed(1)} kn`}` } } },
    },
  });

  const rows = ['actual', 'baseline', 'speed', 'milp'];
  const best = rows.reduce((a, k) => (pl[k].total < pl[a].total ? k : a), 'actual');
  $('#caseTable').innerHTML = `<thead><tr><th>Course of action</th><th class="n">Total</th><th class="n">Fuel</th><th class="n">Cargo delay</th><th class="n">Rollover</th><th class="n">Late at end</th><th class="n">vs. no recovery</th><th>Omitted</th></tr></thead><tbody>` +
    rows.map((k) => {
      const p = pl[k];
      return `<tr class="${k === best ? 'best' : ''}"><td><span class="sw" style="background:${k === 'speed' ? `${c.milp}88` : c[PLAN_META[k].key]}"></span>${PLAN_META[k].label}</td>
      <td class="n">${fmtM(p.total)}</td><td class="n">${fmtM(p.costs.fuel)}</td><td class="n">${fmtM(p.costs.delay + p.costs.residual)}</td>
      <td class="n">${fmtM(p.costs.skip)}</td><td class="n">${fmtH(p.residual_h)}</td>
      <td class="n ${p.saving_pct > 0 ? 'pos' : ''}">${k === 'baseline' ? '—' : `${p.saving_pct > 0 ? '−' : '+'}${Math.abs(p.saving_pct).toFixed(1)}%`}</td>
      <td>${p.skipped.length ? p.skipped.map(name).join(', ') : '—'}</td></tr>`;
    }).join('') + '</tbody>';

  const a = pl.actual, s = pl.speed, m = pl.milp;
  const d = 100 * (a.total - s.total) / a.total;
  let txt = Math.abs(d) < 3
    ? `Maersk's real recovery is within ${Math.abs(d).toFixed(1)}% of the speed-only optimum. Its speed choices are close to what the MILP would pick.`
    : d > 0
      ? `The speed-only MILP is ${d.toFixed(1)}% cheaper than what happened, mainly by ${s.fuel_t < a.fuel_t ? 'using less fuel where the berth was not ready anyway' : 'sailing faster earlier to cut cargo lateness'}.`
      : `Maersk did ${(-d).toFixed(1)}% better than the speed-only model. Real handling time was shorter than planned, which the model cannot use.`;
  if (m.skipped.length && m.total < s.total * 0.97) {
    txt += ` With hindsight, the full MILP would also omit ${m.skipped.map(name).join(' and ')}, cutting cost by another ${fmtPct(100 * (s.total - m.total) / s.total, 0)}. Maersk kept those calls, which suggests their cargo is worth more than the assumed $${S.opt?.scenario?.params?.rollover_usd_teu ?? 400}/TEU rollover cost.`;
  }
  if (cs.real_skips.length) {
    txt += ` In reality Maersk omitted ${cs.real_skips.map(name).join(' and ')}.`;
  }
  $('#caseInsight').textContent = txt;
}

/* ════════════════════════════ 04 ML ════════════════════════════ */
function drawML() {
  const c = SERIES();
  const dm = S.ml.delay_model;
  const adv = S.ml.advisor;
  const imp = 100 * (dm.mae_naive - dm.mae_model) / dm.mae_naive;
  $('#dmTiles').innerHTML = [
    { v: fmtH(dm.mae_naive), l: 'mean error, naive "delay carries over"' },
    { v: fmtH(dm.mae_model), l: `mean error, ${dm.best ? dm.best.name.toLowerCase() : 'ML-1'} (held-out real ship)`, hi: true },
    { v: `${imp.toFixed(0)}%`, l: `smaller error · ${dm.n} legs, ${dm.n_vessels} vessels` },
  ].map((t) => `<div class="tile${t.hi ? ' hi' : ''}"><div class="tile-v">${t.v}</div><div class="tile-l">${esc(t.l)}</div></div>`).join('');

  const pts = dm.points;
  const max = Math.ceil(Math.max(...pts.map((p) => Math.max(p.actual, p.model, p.naive))) / 50) * 50;
  mk('cDelayModel', {
    type: 'scatter',
    data: { datasets: [
      { label: 'Perfect forecast', data: [{ x: 0, y: 0 }, { x: max, y: max }], showLine: true, borderColor: c.ink3, borderDash: [4, 4], pointRadius: 0, borderWidth: 1.5 },
      { label: 'Naive forecast', data: pts.map((p) => ({ x: p.actual, y: p.naive, p })), backgroundColor: c.base, borderColor: c.surface, borderWidth: 1, pointRadius: 4.5 },
      { label: `ML-1 forecast (${dm.best ? dm.best.name : 'ridge'})`, data: pts.map((p) => ({ x: p.actual, y: p.model, p })), backgroundColor: c.milp, borderColor: c.surface, borderWidth: 1, pointRadius: 5 },
    ] },
    options: {
      scales: { x: axis('Actual arrival lateness (h)', { min: -50, max }), y: axis('Forecast (h)', { min: -50, max }) },
      plugins: { tooltip: { filter: (it) => it.datasetIndex > 0, callbacks: {
        label: (it) => { const p = it.raw.p; return ` ${p.vessel} → ${p.to_name}: actual ${Math.round(p.actual)} h, ${it.dataset.label.toLowerCase()} ${Math.round(it.raw.y)} h`; },
      } } },
    },
  });

  const x = adv.xgboost, r = adv.regret;
  $('#advTiles').innerHTML = [
    { v: fmtPct(100 * x.accuracy), l: 'accuracy on held-out calls' },
    { v: x.f1.toFixed(3), l: `F1 for "omit" (skip rate ${fmtPct(100 * adv.skip_rate, 0)})` },
    { v: x.roc_auc.toFixed(3), l: 'ROC AUC' },
    { v: fmtPct(r.mean_pct, 2), l: 'mean extra cost vs exact MILP', hi: true },
    { v: fmtPct(100 * r.exact_skip_match, 0), l: 'plans with exactly the MILP skips' },
    { v: fmtPct(r.rule_mean_pct, 0), l: 'mean extra cost, rule-based heuristic' },
  ].map((t) => `<div class="tile${t.hi ? ' hi' : ''}"><div class="tile-v">${t.v}</div><div class="tile-l">${esc(t.l)}</div></div>`).join('');

  const e = r.hist_edges;
  const binLabels = e.slice(0, -1).map((lo, i) => (i === 0 ? `<${e[1]}` : (i === e.length - 2 ? `≥${lo}` : `${lo}–${e[i + 1]}`)));
  const share = (h) => { const n = h.reduce((s, v) => s + v, 0) || 1; return h.map((v) => (100 * v) / n); };
  mk('cRegret', {
    type: 'bar',
    data: { labels: binLabels, datasets: [
      { label: 'ML-guided MILP', data: share(r.hist), backgroundColor: c.ml, barPercentage: 0.85, categoryPercentage: 0.75 },
      { label: 'Rule-based', data: share(r.rule_hist), backgroundColor: c.rule, barPercentage: 0.85, categoryPercentage: 0.75 },
    ] },
    options: {
      scales: { x: axis('Extra cost vs optimum (%)', { grid: { display: false }, ticks: { color: c.ink3, maxRotation: 0, autoSkip: false, font: { size: isLarge() ? 12 : 10 } } }), y: axis('Share of scenarios (%)', { beginAtZero: true, max: 100 }) },
      plugins: { tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: ${it.raw.toFixed(1)}% of scenarios` } } },
    },
  });

  const top = adv.importance.slice(0, 8);
  mk('cImportance', {
    type: 'bar',
    data: { labels: top.map((f) => f.label), datasets: [{ label: 'Gain share', data: top.map((f) => 100 * f.gain), backgroundColor: c.milp, barPercentage: 0.7 }] },
    options: {
      indexAxis: 'y',
      scales: { x: axis('% of total gain', { beginAtZero: true }), y: axis(null, { grid: { display: false }, ticks: { color: c.ink2 } }) },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (it) => ` ${it.raw.toFixed(1)}% of gain` } } },
    },
  });

  const cm = x.confusion;
  $('#confusion').innerHTML = `<span></span><span class="h c">Predicted call</span><span class="h c">Predicted omit</span>
    <span class="h">MILP call</span><div class="cell tn">${cm[0][0]}<small>correct</small></div><div class="cell fp">${cm[0][1]}<small>false omit</small></div>
    <span class="h">MILP omit</span><div class="cell fn">${cm[1][0]}<small>missed omit</small></div><div class="cell tp">${cm[1][1]}<small>correct</small></div>`;
  $('#modelCompare').innerHTML = '';
}


/* ── synthetic data + model leaderboards ───────────────────────── */
function drawModels() {
  const c = SERIES();
  const adv = S.ml.advisor, syn = S.ml.synthetic;
  const rows = adv.models || [];
  if (rows.length && $('#leaderboard')) {
    const maxR = Math.max(...rows.map((m) => m.regret.mean_pct), adv.rule ? 0 : 1) || 1;
    const scale = (v) => `${Math.max(2, 70 * Math.sqrt(v / maxR))}px`;
    const body = rows.map((m, i) => `<tr class="${m.key === adv.best_model ? 'best' : ''}">
      <td>${i + 1}</td><td>${esc(m.name)}</td>
      <td>${(100 * m.accuracy).toFixed(1)}%</td><td>${m.f1.toFixed(3)}</td><td>${m.roc_auc.toFixed(3)}</td>
      <td><span class="bar" style="width:${scale(m.regret.mean_pct)}"></span>${m.regret.mean_pct.toFixed(2)}%</td>
      <td>${(100 * m.regret.exact_skip_match).toFixed(0)}%</td>
      <td>${m.train_s < 0.1 ? '<0.1' : m.train_s.toFixed(1)} s</td></tr>`).join('');
    const rule = adv.rule ? `<tr class="rule"><td></td><td>Rule of thumb (no ML)</td><td>—</td><td>—</td><td>—</td>
      <td>${adv.rule.mean_pct.toFixed(1)}%</td><td>—</td><td>—</td></tr>` : '';
    $('#leaderboard').innerHTML = `<table><thead><tr><th>#</th><th>Model</th><th>Accuracy</th><th>F1</th><th>AUC</th>
      <th>Extra cost</th><th>Exact skips</th><th>Train</th></tr></thead><tbody>${body}${rule}</tbody></table>
      <p class="note" style="margin:12px 0 0">${fmtInt(adv.n_scenarios)} scenarios · ${fmtInt(adv.n_rows)} call decisions · ${adv.n_test_scenarios} held-out scenarios. "Exact skips" is the share of plans with exactly the MILP's omissions.</p>`;
  }

  if (syn && $('#synTiles')) {
    const d = adv.data || {};
    const v = syn.validation;
    $('#synTiles').innerHTML = [
      { v: fmtInt(d.real_calls || 58), l: `real calls · ${d.real_vessels || 6} ships` },
      { v: fmtInt(syn.n_calls), l: `synthetic calls · ${syn.n_vessels} ships · 12 months`, hi: true },
      { v: `${v.pacific_recovered_real} / ${v.pacific_recovered_syn} h`, l: 'Pacific delay won back, real / synthetic' },
    ].map((t) => `<div class="tile${t.hi ? ' hi' : ''}"><div class="tile-v">${t.v}</div><div class="tile-l">${esc(t.l)}</div></div>`).join('');
    const ports = v.ports.filter((p) => p.service === S.svc);
    mk('cSynth', {
      type: 'bar',
      data: { labels: ports.map((p) => p.name), datasets: [
        { label: 'Real (Jun–Oct)', data: ports.map((p) => p.mean_real), backgroundColor: c.actual, barPercentage: 0.85, categoryPercentage: 0.7 },
        { label: 'Synthetic, same months', data: ports.map((p) => p.mean_syn_peak), backgroundColor: c.ml, barPercentage: 0.85, categoryPercentage: 0.7 },
        { label: 'Synthetic, full year', data: ports.map((p) => p.mean_syn), backgroundColor: c.base, barPercentage: 0.85, categoryPercentage: 0.7 },
      ] },
      options: {
        scales: { x: axis(null, { grid: { display: false }, ticks: { color: c.ink2, maxRotation: 0, autoSkip: false } }),
                  y: axis('Mean arrival lateness (h)', { beginAtZero: true }) },
        plugins: { tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: ${Math.round(it.raw)} h` } } },
      },
    });
    $('#synNote').textContent = `${S.svc} shown; switch service at the top. Off-peak months (Nov–May) are simulated with less congestion, so the full-year bars sit lower.`;
  }

  const dm = S.ml.delay_model;
  if (dm && dm.models && $('#dmBoard')) {
    const best = dm.models[0].mae;
    $('#dmBoard').innerHTML = `<table><thead><tr><th>Model</th><th>Trained on</th><th>Error</th><th>Shanghai</th><th>Other ports</th></tr></thead><tbody>
      ${dm.models.map((m) => `<tr${m.mae === best ? ' class="best"' : ''}><td>${esc(m.name)}</td><td class="regime">${esc(m.regime_name)}</td>
      <td>${m.mae.toFixed(1)} h</td><td>${m.mae_shanghai.toFixed(1)} h</td><td>${m.mae_other.toFixed(1)} h</td></tr>`).join('')}
      <tr class="rule"><td>Naive: delay carries over</td><td class="regime">—</td><td>${dm.mae_naive.toFixed(1)} h</td><td>${dm.mae_naive_shanghai.toFixed(1)} h</td><td>${dm.mae_naive_other.toFixed(1)} h</td></tr>
      </tbody></table>`;
  }
}

/* ════════════════════════════ 05 METHOD ════════════════════════════ */
function drawMethod() {
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
    ['y_{ij},\\;x_j', 'sail directly i → j (omitting calls between them); call at port j'],
    ['T_{ij},\\;F_{ij}', 'sailing hours and fuel cost on the arc; κ = δ·√(10·TEU)·price/24'],
    ['a_j,\\;b_j,\\;d_j', 'arrival, berth start and departure times; o_j = berth opens'],
    ['L_j,\\;p_j', 'lateness at port j against planned arrival p_j'],
    ['\\pi_j,\\;\\rho', 'cargo delay cost (TEU at port × $/TEU·h); carry-over into next loop'],
    ['\\omega_j', 'rollover cost of omitted cargo minus the port fee saved'],
    ['c_j,\\;h', 'berth hours bought back with extra gangs, at $h per hour'],
  ];
  const kx = (t) => { try { return katex.renderToString(t, { throwOnError: false }); } catch (e) { return esc(t); } };
  $('#formulaDefs').innerHTML = defs.map(([a, b]) => `<dt>${kx(a)}</dt><dd>${esc(b)}</dd>`).join('');

  const tag = (t) => `<span class="tag tag-${t.toLowerCase()}">${t.charAt(0) + t.slice(1).toLowerCase()}</span>`;
  if ($('#provTable')) $('#provTable').innerHTML = `<thead><tr><th>Input</th><th>Status</th><th>Source</th></tr></thead><tbody>` +
    S.overview.provenance.map((p) => `<tr><td>${esc(p.item)}</td><td>${tag(p.tag)}</td><td>${esc(p.source)}</td></tr>`).join('') + '</tbody>';

  if (!$('#routeCompare')) return;
  const P = S.overview.ports;
  const paper = S.overview.paper_route;
  const tp8 = S.overview.services.TP8.rotation.map((p) => P[p].name);
  const tp7 = S.overview.services.TP7.rotation.map((p) => P[p].name);
  const mark = (list) => list.map((n) => (paper.includes(n) ? `<span class="hit">${esc(n)}</span>` : esc(n))).join(' → ');
  const shared = (list) => list.filter((n) => paper.includes(n)).length;
  $('#routeCompare').innerHTML = `<thead><tr><th>Service</th><th>Rotation</th><th class="n">Shared with FP1</th></tr></thead><tbody>
    <tr><td><b>FP1</b> (papers)</td><td>${paper.map(esc).join(' → ')}</td><td class="n">—</td></tr>
    <tr><td><b>TP8</b></td><td>${mark(tp8)}</td><td class="n">${shared(tp8)} of ${tp8.length}</td></tr>
    <tr><td><b>TP7</b></td><td>${mark(tp7)}</td><td class="n">${shared(tp7)} of ${tp7.length}</td></tr></tbody>`;
}

/* ════════════════════════════ wiring ════════════════════════════ */
function setService(svc) {
  S.svc = svc;
  $$('#serviceSeg button').forEach((b) => b.classList.toggle('on', b.dataset.svc === svc));
  drawHero();
  drawMap();
  drawVoyages();
  setupControls();
  applyPreset(PRESETS[svc][0], $('#presets .chip'));
  if (S.ml) drawModels();
  if (S.replays) drawReplay();
}

function observeNav() {
  const links = $$('#nav a');
  const map = new Map(links.map((a) => [a.getAttribute('href').slice(1), a]));
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) { links.forEach((a) => a.classList.remove('active')); map.get(e.target.id)?.classList.add('active'); }
    });
  }, { rootMargin: '-45% 0px -50% 0px' });
  map.forEach((_, id) => { const s = document.getElementById(id); if (s) io.observe(s); });
}

async function init() {
  chartDefaults();
  $('#sizeToggle').setAttribute('aria-pressed', isLarge() ? 'true' : 'false');
  $('#themeToggle').addEventListener('click', () => setTheme(isDark() ? 'light' : 'dark'));
  $('#sizeToggle').addEventListener('click', () => setSize(isLarge() ? '' : 'lg'));
  $$('#serviceSeg button').forEach((b) => b.addEventListener('click', () => setService(b.dataset.svc)));
  $('#callVessel').addEventListener('change', drawCallTable);
  $('#startPort').addEventListener('change', () => { buildPortDependents(); $$('#presets .chip').forEach((b) => b.classList.remove('on')); queueOptimize(); });
  $('#congPort').addEventListener('change', () => { if ($('#congPort').value && +$('#congH').value === 0) $('#congH').value = 150; queueOptimize(); });
  ['delay', 'congH', 'bunker', 'delayCost', 'rollover'].forEach((id) => $(`#${id}`).addEventListener('input', () => {
    if (id === 'delay' || id === 'congH') $$('#presets .chip').forEach((b) => b.classList.remove('on'));
    queueOptimize();
  }));
  ['allowSkip', 'allowHandling'].forEach((id) => $(`#${id}`).addEventListener('change', () => queueOptimize(0)));
  observeNav();
  let rz;
  window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(drawMap, 150); });

  try {
    const [ov, real] = await Promise.all([api('/api/overview'), api('/api/real')]);
    S.overview = ov; S.real = real;
  } catch (e) {
    toast(`Could not load data (${e.message}). Run python build.py, then python app.py.`);
    return;
  }
  drawMethod();
  setService('TP8');
  loadLand().then(() => drawMap());
  api('/api/replays').then((r) => { S.replays = r; drawReplay(); }).catch((e) => toast(`Replays: ${e.message}`));
  api('/api/ml').then((r) => { S.ml = r; drawML(); drawModels(); }).catch((e) => toast(`ML results: ${e.message}`));
}

document.addEventListener('DOMContentLoaded', init);
})();
