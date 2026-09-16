/* ─────────────────────────────────────────────────────────────────────────────
   app.js — VSRP Dashboard
   Chart colors are resolved from CSS custom properties at render time so they
   work correctly in both dark mode and light mode.
   ───────────────────────────────────────────────────────────────────────────── */

// ── Dynamic theme palette ─────────────────────────────────────────────────────
// Reads the *computed* CSS variable values so dark/light mode is automatic.
function getT() {
  const s = getComputedStyle(document.documentElement);
  const v = (name) => s.getPropertyValue(name).trim();
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';

  return {
    // Accent colours — same hue, slightly adjusted for readability per mode
    accent  : isDark ? '#d4644a' : '#c2472a',
    accentD : isDark ? 'rgba(212,100,74,0.22)' : 'rgba(194,71,42,0.14)',
    gold    : isDark ? '#d4a84b' : '#b8851e',
    goldD   : isDark ? 'rgba(212,168,75,0.20)' : 'rgba(184,133,30,0.14)',
    jade    : isDark ? '#52a67e' : '#3a8560',
    jadeD   : isDark ? 'rgba(82,166,126,0.18)' : 'rgba(58,133,96,0.12)',
    steel   : isDark ? 'rgba(160,192,220,0.78)' : 'rgba(50,90,160,0.72)',
    steelD  : isDark ? 'rgba(160,192,220,0.16)' : 'rgba(50,90,160,0.10)',
    // Text colours — derived from CSS vars, fully correct per mode
    text    : isDark ? 'rgba(240,232,216,0.82)' : 'rgba(26,20,16,0.80)',
    muted   : isDark ? 'rgba(240,232,216,0.46)' : 'rgba(26,20,16,0.50)',
    grid    : isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.07)',
    border  : isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.10)',
  };
}

// ── Apply Chart.js globals for current theme ──────────────────────────────────
function applyChartDefaults() {
  const T = getT();
  Chart.defaults.color       = T.muted;
  Chart.defaults.borderColor = T.grid;
  Chart.defaults.font.family = "'Space Grotesk', system-ui, sans-serif";
  Chart.defaults.font.size   = 11;
}
applyChartDefaults();


// ── State ─────────────────────────────────────────────────────────────────────
let routeMeta       = null;
let costChartInst   = null;
let speedChartInst  = null;
let paretoChartInst = null;
let skipThChartInst = null;
let featChartInst   = null;

// ── Helpers ───────────────────────────────────────────────────────────────────
function setStatus(msg, ready = false) {
  document.getElementById('statusText').textContent = msg;
  const pip = document.getElementById('statusPip');
  pip.className = 'status-pip ' + (ready ? 'ready' : 'loading');
}

async function api(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status} — ${url}`);
  return r.json();
}

function show(id) { const el = document.getElementById(id); if (el) el.style.display = ''; }
function hide(id) { const el = document.getElementById(id); if (el) el.style.display = 'none'; }
function setText(id, t) { const el = document.getElementById(id); if (el) el.textContent = t; }

// ── SLIDER ────────────────────────────────────────────────────────────────────
document.getElementById('disruptionSlider').addEventListener('input', function () {
  setText('disruptionVal', this.value);
});

// ── ROUTE MAP ─────────────────────────────────────────────────────────────────
function buildRouteMap(meta) {
  const track  = document.getElementById('routeTrack');
  const stats  = document.getElementById('routeStats');
  const ports  = meta.ports;
  const dists  = meta.leg_distances;
  const usCode = new Set(['USLAX','USLGB','USOAK']);

  track.innerHTML = '';

  ports.forEach((p, i) => {
    // Port node
    const node = document.createElement('div');
    node.className = 'port-node';
    const congested = usCode.has(p.code);

    node.innerHTML = `
      <div class="port-pip ${congested ? 'congested' : 'normal'}"></div>
      <div class="port-name">${p.name}</div>
      <div class="port-code">${p.code}</div>
    `;
    track.appendChild(node);

    // Connector
    if (i < ports.length - 1) {
      const conn = document.createElement('div');
      conn.className = 'leg-connector';
      conn.innerHTML = `
        <div class="leg-dist">${dists[i].toLocaleString()} nm</div>
        <div class="leg-line"></div>
      `;
      track.appendChild(conn);
    }
  });

  // Stats
  stats.innerHTML = `
    <div class="stat-chip">Total distance <span class="val">${meta.leg_distances.reduce((a,b)=>a+b,0).toLocaleString()} nm</span></div>
    <div class="stat-chip">Vessels deployed <span class="val">${meta.n_vessels}</span></div>
    <div class="stat-chip">TEU capacity <span class="val">${meta.teu_capacity.toLocaleString()}</span></div>
    <div class="stat-chip">Design speed <span class="val">${meta.v_default} kn</span></div>
    <div class="stat-chip">Berth time/port <span class="val">${meta.t_berth_base} hrs</span></div>
    <div class="stat-chip">Delay penalty <span class="val">$${meta.delay_penalty_usd_hr.toLocaleString()}/hr</span></div>
    <div class="stat-chip">Skip penalty <span class="val">$${meta.skip_penalty_usd.toLocaleString()}/port</span></div>
  `;
}

// ── OPTIMIZER ─────────────────────────────────────────────────────────────────
async function runOptimizer() {
  const delay = document.getElementById('disruptionSlider').value;
  const btn   = document.getElementById('runOptBtn');
  btn.disabled = true;
  btn.textContent = 'Solving…';
  setStatus(`Running MILP optimiser — disruption delay ${delay} hrs…`);

  hide('savingsPanel');
  document.getElementById('skipDecisions').innerHTML = '';

  // Reset charts to placeholder
  hide('costChart'); show('costPlaceholder');
  hide('speedChart'); show('speedPlaceholder');
  setText('costPlaceholder', 'Solving…');
  setText('speedPlaceholder', 'Solving…');

  try {
    const r = await api(`/api/optimize?delay=${delay}`);
    renderOptimizer(r);
    setStatus(`Optimiser complete — ${r.savings_pct}% cost reduction · ${r.n_skips} port${r.n_skips !== 1 ? 's' : ''} skipped`, true);
  } catch (e) {
    setStatus('Optimiser error: ' + e.message);
    setText('costPlaceholder', 'Error loading data');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Run Optimiser';
  }
}

function renderOptimizer(r) {
  const costs = r.costs;
  const T = getT();

  // ── Cost chart ──────────────────────────────────────────────────────────────
  hide('costPlaceholder'); show('costChart');
  if (costChartInst) costChartInst.destroy();

  costChartInst = new Chart(document.getElementById('costChart'), {
    type: 'bar',
    data: {
      labels: ['Fuel', 'Port Dues', 'Delay Cost', 'Skip Penalty'],
      datasets: [
        {
          label: 'No Recovery',
          data: [costs.baseline.fuel, costs.baseline.port_fees, costs.baseline.delay, costs.baseline.skip_penalty],
          backgroundColor: T.accentD,
          borderColor: T.accent,
          borderWidth: 1,
          borderRadius: 3,
        },
        {
          label: 'MILP Recovery',
          data: [costs.optimized.fuel, costs.optimized.port_fees, costs.optimized.delay, costs.optimized.skip_penalty],
          backgroundColor: T.steelD,
          borderColor: T.steel,
          borderWidth: 1,
          borderRadius: 3,
        }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'top', labels: { color: T.muted, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => ` $${(ctx.raw/1000).toFixed(0)}K` } }
      },
      scales: {
        y: { ticks: { callback: v => `$${(v/1000).toFixed(0)}K`, color: T.text }, grid: { color: T.grid } },
        x: { ticks: { color: T.text }, grid: { display: false } }
      }
    }
  });

  // ── Speed chart ─────────────────────────────────────────────────────────────
  hide('speedPlaceholder'); show('speedChart');
  if (speedChartInst) speedChartInst.destroy();

  const legLabels = routeMeta
    ? routeMeta.ports.slice(0, -1).map((_, i) =>
        `${routeMeta.ports[i].name.split(' ')[0]} → ${routeMeta.ports[i+1].name.split(' ')[0]}`)
    : r.optimal_speeds.map((_, i) => `Leg ${i + 1}`);

  const defSpeed = routeMeta ? routeMeta.v_default : 18.3;

  speedChartInst = new Chart(document.getElementById('speedChart'), {
    type: 'bar',
    data: {
      labels: legLabels,
      datasets: [
        {
          label: 'Design Speed',
          data: r.optimal_speeds.map(() => defSpeed),
          type: 'line',
          borderColor: T.muted,
          borderDash: [4, 3],
          borderWidth: 1.5,
          pointRadius: 0,
          fill: false,
          tension: 0,
        },
        {
          label: 'Optimal Speed',
          data: r.optimal_speeds,
          backgroundColor: r.optimal_speeds.map(v => v > defSpeed + 1 ? T.accentD : T.goldD),
          borderColor: r.optimal_speeds.map(v => v > defSpeed + 1 ? T.accent : T.gold),
          borderWidth: 1,
          borderRadius: 3,
        }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { position: 'top', labels: { color: T.muted, font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => ` ${ctx.raw.toFixed(1)} kn` } }
      },
      scales: {
        y: { min: 8, max: 26, ticks: { callback: v => `${v}`, color: T.text }, grid: { color: T.grid } },
        x: { ticks: { color: T.text, maxRotation: 30, font: { size: 9 } }, grid: { display: false } }
      }
    }
  });

  // ── Savings panel ────────────────────────────────────────────────────────────
  show('savingsPanel');
  setText('savingsNum', `${r.savings_pct}%`);
  setText('savingsDetail',
    `Baseline: $${(costs.baseline.total/1000).toFixed(0)}K  →  Optimised: $${(costs.optimized.total/1000).toFixed(0)}K` +
    ` · Ports skipped: ${r.n_skips}`
  );

  // ── Skip decisions ──────────────────────────────────────────────────────────
  const skipsEl = document.getElementById('skipDecisions');
  if (routeMeta) {
    const header = document.createElement('div');
    header.innerHTML = `<div class="label-mono" style="margin:24px 0 10px">Port Call Decisions</div>`;
    const row = document.createElement('div');
    row.className = 'skip-row';

    routeMeta.ports.forEach((p, i) => {
      if (i === 0 || i === routeMeta.ports.length - 1) return;
      const skipped = r.skip_decisions[p.name] === 1;
      const tag = document.createElement('div');
      tag.className = `skip-tag ${skipped ? 'skip' : 'call'}`;
      tag.textContent = (skipped ? 'Skip · ' : 'Call · ') + p.name;
      row.appendChild(tag);
    });
    skipsEl.appendChild(header);
    skipsEl.appendChild(row);
  }
}


// ── ML RESULTS ────────────────────────────────────────────────────────────────
async function loadMLResults() {
  try {
    const data = await api('/api/ml_results');
    renderML(data);
  } catch (e) {
    setText('mlMetricPlaceholder', 'Error loading ML results');
  }
}

function renderML(data) {
  const m = data.metrics;

  // Metrics tiles
  hide('mlMetricPlaceholder'); show('mlMetrics');
  document.getElementById('metricsGrid').innerHTML = `
    <div class="metric-tile">
      <div class="big">${(m.accuracy * 100).toFixed(1)}%</div>
      <div class="lbl">Accuracy</div>
    </div>
    <div class="metric-tile">
      <div class="big">${(m.precision * 100).toFixed(1)}%</div>
      <div class="lbl">Precision</div>
    </div>
    <div class="metric-tile">
      <div class="big">${(m.recall * 100).toFixed(1)}%</div>
      <div class="lbl">Recall</div>
    </div>
    <div class="metric-tile">
      <div class="big">${m.roc_auc.toFixed(3)}</div>
      <div class="lbl">ROC-AUC</div>
    </div>
  `;

  // Feature importance bar chart
  hide('featPlaceholder'); show('featChart');
  if (featChartInst) featChartInst.destroy();
  const T = getT();

  const feats  = Object.keys(data.feature_importances);
  const values = Object.values(data.feature_importances);

  featChartInst = new Chart(document.getElementById('featChart'), {
    type: 'bar',
    data: {
      labels: feats.map(f => f.replace(/_/g, ' ')),
      datasets: [{
        label: 'Importance',
        data: values,
        backgroundColor: values.map((v, i) =>
          i === 0 ? T.accentD : i === 1 ? T.goldD : T.steelD
        ),
        borderColor: values.map((v, i) =>
          i === 0 ? T.accent : i === 1 ? T.gold : T.steel
        ),
        borderWidth: 1,
        borderRadius: 3,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: T.text }, grid: { color: T.grid } },
        y: { ticks: { color: T.text, font: { size: 10 } }, grid: { display: false } }
      }
    }
  });
}

// ── LIVE PREDICTOR ────────────────────────────────────────────────────────────
let _predTimer = null;

function updatePredictor() {
  const delay = document.getElementById('p_delay').value;
  const queue = document.getElementById('p_queue').value;
  const heff  = document.getElementById('p_heff').value;
  const days  = document.getElementById('p_days').value;

  setText('p_delay_val', delay);
  setText('p_queue_val', queue);
  setText('p_heff_val',  parseFloat(heff).toFixed(2));
  setText('p_days_val',  days);

  clearTimeout(_predTimer);
  _predTimer = setTimeout(async () => {
    try {
      const d = await api(
        `/api/predict_skip?delay=${delay}&queue=${queue}&h_eff=${heff}&days=${days}&teu=14000&dist=386&skip_hist=0.15&fuel_p=650`
      );
      const prob = d.skip_probability;
      const pct  = Math.round(prob * 100);

      document.getElementById('probBar').style.width = pct + '%';
      setText('probValue', pct + '%');

      const probEl = document.getElementById('probValue');
      const T2 = getT();
      probEl.style.color = prob > 0.7 ? T2.accent : prob > 0.4 ? T2.gold : T2.jade;

      let verdict = 'Low risk — vessel likely to call port';
      if (prob > 0.7) verdict = 'High risk — vessel likely to skip port';
      else if (prob > 0.4) verdict = 'Moderate risk — outcome uncertain';
      setText('probVerdict', verdict);

    } catch (_) { /* predictor warming up */ }
  }, 400);
}

// ── INIT ──────────────────────────────────────────────────────────────────────
async function init() {
  setStatus('Loading route metadata…');
  try {
    routeMeta = await api('/api/route_meta');
    buildRouteMap(routeMeta);
  } catch (e) {
    setStatus('Cannot load route data. Ensure run_poc.py has completed.');
    return;
  }

  // Run default optimisation
  setStatus('Running default optimisation (140 h disruption)…');
  try {
    const r = await api('/api/optimize?delay=140');
    renderOptimizer(r);
    setStatus(`Optimiser ready — ${r.savings_pct}% cost reduction at 140 h disruption`, true);
  } catch (e) {
    setStatus('Optimiser not ready: ' + e.message);
  }

  // Load async panels
  loadMLResults();
  updatePredictor();
}

document.addEventListener('DOMContentLoaded', init);
