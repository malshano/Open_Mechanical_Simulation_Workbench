// CFD-coupled flutter: result charts and summary pieces.
//
// Pure functions of plain data (no global state, no DOM access except the canvas that is passed in), so that they can
// be ported to the Qt app. Damping ratios (zeta, sigma) are RATIOS (0.01 = 1 %); the strip-theory report keeps its own
// damping_percent in percent. Charts show percent.
//
// Exports: drawModalSeries, drawDampingVsSpeed, pickStripCurve, flutterCfdSummaryHtml, flutterCfdProgressHtml.

const COLORS = ['#2563eb', '#e4572e', '#2a9d8f', '#8338ec', '#ff9f1c', '#06aed5', '#d62828', '#6a994e'];
const GRID = 'rgba(127,127,127,.25)', TEXT = 'rgba(127,127,127,.9)', FLUTTER = '#e4572e';

const num = v => typeof v === 'number' && Number.isFinite(v);
const defEsc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const defFmt = (v, d = 3) => {
  if (!num(Number(v)) || v == null) return '–';
  v = Number(v);
  if (v !== 0 && (Math.abs(v) >= 1e6 || Math.abs(v) < 1e-3)) return v.toExponential(2);
  if (Math.abs(v) >= 10 ** d) return String(Math.round(v));
  const t = v.toPrecision(d);
  return t.includes('e') ? String(Number(t)) : t.includes('.') ? t.replace(/\.?0+$/, '') : t;
};

// a "nice" step for an axis with about n ticks
function niceStep(range, n) {
  const raw = range / Math.max(n, 1), p = 10 ** Math.floor(Math.log10(raw)), m = raw / p;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
}
function niceTicks(lo, hi, n = 4) {
  if (!(hi > lo)) return [lo];
  const s = niceStep(hi - lo, n), out = [];
  for (let v = Math.ceil(lo / s - 1e-9) * s; v <= hi + 1e-9 * s; v += s) out.push(Math.abs(v) < s * 1e-9 ? 0 : v);
  return out;
}
const tickLabel = v => { const a = Math.abs(v); return a !== 0 && (a >= 1e5 || a < 1e-3) ? v.toExponential(1) : String(Number(v.toPrecision(3))); };

// canvas set-up: device-pixel scaling the way drawFlutter does it; returns null when there is nothing to draw on
function setup(c, opts) {
  if (!c || typeof c.getContext !== 'function') return null;
  const dp = opts?.dpr ?? globalThis.devicePixelRatio ?? 1;
  const W = c.width = Math.round((c.clientWidth || opts?.width || 300) * dp), H = c.height = Math.round((c.clientHeight || opts?.height || 150) * dp);
  const g = c.getContext('2d');
  if (!g) return null;
  g.clearRect?.(0, 0, W, H);
  g.lineWidth = dp; g.font = `${9 * dp}px sans-serif`;
  return { g, dp, W, H };
}
function message(ctx, text) {
  const { g, dp, W, H } = ctx;
  g.fillStyle = TEXT; g.font = `${11 * dp}px sans-serif`;
  g.fillText(text, Math.max(4, (W - (g.measureText?.(text)?.width ?? 0)) / 2), H / 2);
}
// legend: a row of colour swatches and labels at the top left of the plot area; wraps to a second row when needed
function legend(ctx, items, x, y, maxW) {
  const { g, dp } = ctx;
  let cx = x, cy = y;
  g.font = `${9 * dp}px sans-serif`; g.lineWidth = 1.6 * dp;
  for (const it of items) {
    const w = (g.measureText?.(it.label)?.width ?? it.label.length * 5 * dp) + 26 * dp;
    if (cx + w > x + maxW && cx > x) { cx = x; cy += 11 * dp; }
    g.strokeStyle = it.color; g.setLineDash?.(it.dash ? [4 * dp, 3 * dp] : []);
    g.beginPath(); g.moveTo(cx, cy - 3 * dp); g.lineTo(cx + 14 * dp, cy - 3 * dp); g.stroke(); g.setLineDash?.([]);
    g.fillStyle = TEXT; g.fillText(it.label, cx + 18 * dp, cy);
    cx += w;
  }
}

/**
 * Modal coordinates against time, one line per mode.
 * series = {t:[s], q:[[per mode, same length as t]], labels:[...]}.
 * opts = {dpr, colors, unit ('m' default), tRelease (s: dashed vertical marker "structure released"),
 *         envelope: {a0, rate, label} (dashed +-a0*exp(rate*(t - t0)) hint, rate in 1/s = -zeta*omega, t0 = tRelease or
 *         the first time), title}.
 */
export function drawModalSeries(canvas, series, opts = {}) {
  const ctx = setup(canvas, opts);
  if (!ctx) return;
  const { g, dp, W, H } = ctx;
  const t = series?.t || [], q = (series?.q || []).filter(a => Array.isArray(a) && a.length);
  if (t.length < 2 || !q.length) { message(ctx, 'No motion recorded yet'); return; }
  const colors = opts.colors || COLORS, labels = series.labels || [];
  const t0 = t[0], t1 = t[t.length - 1];
  let amp = 0;
  for (const a of q) for (const v of a) if (num(v)) amp = Math.max(amp, Math.abs(v));
  if (opts.envelope && num(opts.envelope.a0)) amp = Math.max(amp, Math.abs(opts.envelope.a0));
  if (!(amp > 0)) amp = 1;
  const lo = -amp * 1.1, hi = amp * 1.1;
  const padL = 46 * dp, padB = 14 * dp, padT = 18 * dp;
  const X = v => padL + (W - padL - 6) * (v - t0) / Math.max(t1 - t0, 1e-12), Y = v => padT + (H - padB - padT) * (hi - v) / (hi - lo);
  g.strokeStyle = GRID; g.fillStyle = TEXT; g.lineWidth = dp;
  for (const v of niceTicks(lo, hi, 4)) { const yy = Y(v); g.beginPath(); g.moveTo(padL, yy); g.lineTo(W, yy); g.stroke(); g.fillText(tickLabel(v), 2, yy + 3); }
  g.strokeStyle = TEXT; g.beginPath(); g.moveTo(padL, Y(0)); g.lineTo(W, Y(0)); g.stroke();
  g.fillStyle = TEXT;
  g.fillText(`${tickLabel(t0)} s`, padL, H - 2);
  const end = `${tickLabel(t1)} s`; g.fillText(end, W - (g.measureText?.(end)?.width ?? 30 * dp) - 2, H - 2);
  if (opts.unit !== '') g.fillText(opts.unit || 'm', 2, padT - 6 * dp);
  const tr = opts.tRelease;
  if (num(tr) && tr > t0 && tr < t1) {
    g.strokeStyle = FLUTTER; g.setLineDash?.([4 * dp, 3 * dp]);
    g.beginPath(); g.moveTo(X(tr), padT); g.lineTo(X(tr), H - padB); g.stroke(); g.setLineDash?.([]);
    g.fillStyle = FLUTTER; g.fillText('released', Math.min(X(tr) + 3, W - 50 * dp), H - padB - 3 * dp);
  }
  const items = [];
  g.lineWidth = 1.4 * dp;
  q.forEach((a, i) => {
    g.strokeStyle = colors[i % colors.length]; g.beginPath();
    let started = false;
    a.forEach((v, k) => {
      if (!num(v) || k >= t.length) return;
      started ? g.lineTo(X(t[k]), Y(v)) : g.moveTo(X(t[k]), Y(v)); started = true;
    });
    g.stroke();
    items.push({ color: colors[i % colors.length], label: labels[i] ?? `mode ${i + 1}` });
  });
  const env = opts.envelope;
  if (env && num(env.a0) && num(env.rate)) {
    const ts = num(tr) ? tr : t0;
    g.strokeStyle = TEXT; g.setLineDash?.([3 * dp, 3 * dp]);
    for (const sgn of [1, -1]) {
      g.beginPath();
      for (let k = 0; k <= 40; k++) { const tt = ts + (t1 - ts) * k / 40, v = sgn * env.a0 * Math.exp(env.rate * (tt - ts)); k ? g.lineTo(X(tt), Y(v)) : g.moveTo(X(tt), Y(v)); }
      g.stroke();
    }
    g.setLineDash?.([]);
    items.push({ color: TEXT, label: env.label || 'envelope', dash: true });
  }
  legend(ctx, items, padL + 2 * dp, 9 * dp, W - padL - 8 * dp);
}

/**
 * The strip-theory damping curve to compare with the CFD points. strip = report.sweep {speed_m_s:[...], roots:[{label,
 * frequency_Hz:[...], damping_percent:[...]}]}. Per CFD point the root whose frequency at that speed is nearest to the
 * CFD frequency is followed; points that carry strip_zeta win. Returns [{U, zeta (ratio)}] sorted by U (points of the
 * strip sweep, one per strip speed, using the CFD frequency interpolated at that speed).
 */
export function pickStripCurve(strip, curve) {
  const V = strip?.speed_m_s, roots = strip?.roots;
  const pts = (curve || []).filter(p => num(p?.U));
  const given = pts.filter(p => num(p.strip_zeta)).map(p => ({ U: p.U, zeta: p.strip_zeta })).sort((a, b) => a.U - b.U);
  if (given.length >= 2 || !V?.length || !roots?.length) return given;
  const fp = pts.filter(p => num(p.f)).sort((a, b) => a.U - b.U);
  const fAt = u => {
    if (!fp.length) return null;
    if (u <= fp[0].U) return fp[0].f;
    for (let i = 1; i < fp.length; i++) if (u <= fp[i].U) { const w = (u - fp[i - 1].U) / Math.max(fp[i].U - fp[i - 1].U, 1e-12); return fp[i - 1].f + w * (fp[i].f - fp[i - 1].f); }
    return fp[fp.length - 1].f;
  };
  const out = [];
  V.forEach((u, k) => {
    const f = fAt(u);
    let best = null;
    roots.forEach(r => {
      const rf = r.frequency_Hz?.[k], rz = r.damping_percent?.[k];
      if (!num(rf) || !num(rz)) return;
      // nearest in frequency; without a CFD frequency the least damped root is the one that matters
      const score = f == null ? rz : Math.abs(rf - f);
      if (best == null || score < best.score) best = { score, zeta: rz / 100 };
    });
    if (best) out.push({ U: u, zeta: best.zeta });
  });
  return out;
}

/**
 * Damping ratio (percent) against air speed.
 * curve = [{U, zeta, sigma, strip_zeta, f, strip_f, ok}] (zeta, sigma, strip_zeta are ratios); points with ok === false
 * are hollow. strip = the strip-theory report.sweep (dashed line). flutter = {speed_m_s, sigma_m_s, basis,
 * frequency_Hz} | null (vertical marker with its +-sigma band). opts = {dpr, colors}.
 */
export function drawDampingVsSpeed(canvas, curve, strip, flutter, opts = {}) {
  const ctx = setup(canvas, opts);
  if (!ctx) return;
  const { g, dp, W, H } = ctx;
  const pts = (curve || []).filter(p => num(p?.U) && num(p?.zeta)).sort((a, b) => a.U - b.U);
  if (!pts.length) { message(ctx, 'No damping measured yet'); return; }
  const colors = opts.colors || COLORS;
  const fs = num(flutter?.speed_m_s) ? flutter.speed_m_s : null, fsig = num(flutter?.sigma_m_s) ? Math.abs(flutter.sigma_m_s) : 0;
  let x0 = pts[0].U, x1 = pts[pts.length - 1].U;
  if (fs != null) { x0 = Math.min(x0, fs - fsig); x1 = Math.max(x1, fs + fsig); }
  const mx = Math.max((x1 - x0) * 0.08, x1 * 0.02, 1e-9); x0 -= mx; x1 += mx;
  const stripPts = pickStripCurve(strip, curve).filter(p => num(p.zeta));
  // y range (percent): the points with their error bars, the strip curve inside the x range, the zero line
  const ys = [0];
  for (const p of pts) { const s = num(p.sigma) ? p.sigma : 0; ys.push((p.zeta - s) * 100, (p.zeta + s) * 100); }
  for (const p of stripPts) if (p.U >= x0 && p.U <= x1) ys.push(p.zeta * 100);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const padY = Math.max((hi - lo) * 0.1, 0.05); lo -= padY; hi += padY;
  const padL = 40 * dp, padB = 14 * dp, padT = 18 * dp;
  const X = v => padL + (W - padL - 6) * (v - x0) / Math.max(x1 - x0, 1e-12), Y = v => padT + (H - padB - padT) * (hi - v) / (hi - lo);
  g.strokeStyle = GRID; g.fillStyle = TEXT; g.lineWidth = dp;
  for (const v of niceTicks(lo, hi, 4)) { const yy = Y(v); g.beginPath(); g.moveTo(padL, yy); g.lineTo(W, yy); g.stroke(); g.fillText(tickLabel(v), 2, yy + 3); }
  g.fillText('%', 2, padT - 6 * dp);
  g.fillText(`${tickLabel(x0)}`, padL, H - 2); const end = `${tickLabel(x1)} m/s`; g.fillText(end, W - (g.measureText?.(end)?.width ?? 40 * dp) - 2, H - 2);
  g.strokeStyle = TEXT; g.beginPath(); g.moveTo(padL, Y(0)); g.lineTo(W, Y(0)); g.stroke();
  // flutter speed: band and line
  if (fs != null) {
    if (fsig > 0) { g.fillStyle = 'rgba(228,87,46,.15)'; g.fillRect(X(fs - fsig), padT, Math.max(X(fs + fsig) - X(fs - fsig), 1), H - padB - padT); }
    g.strokeStyle = FLUTTER; g.setLineDash?.([4 * dp, 3 * dp]); g.beginPath(); g.moveTo(X(fs), padT); g.lineTo(X(fs), H - padB); g.stroke(); g.setLineDash?.([]);
    const lbl = `${tickLabel(fs)} m/s${flutter.basis === 'extrapolated' ? ' (extrapolated)' : ''}`;
    g.fillStyle = FLUTTER; g.fillText(lbl, Math.min(X(fs) + 3, W - (g.measureText?.(lbl)?.width ?? 80 * dp) - 2), H - padB - 3 * dp);
  }
  // strip theory: dashed, clipped to the plot area
  const items = [];
  if (stripPts.length) {
    g.save?.(); g.beginPath(); g.rect?.(padL, padT, W - padL, H - padB - padT); g.clip?.();
    g.strokeStyle = colors[1 % colors.length]; g.lineWidth = 1.4 * dp; g.setLineDash?.([5 * dp, 3 * dp]); g.beginPath();
    stripPts.forEach((p, i) => i ? g.lineTo(X(p.U), Y(p.zeta * 100)) : g.moveTo(X(p.U), Y(p.zeta * 100)));
    g.stroke(); g.setLineDash?.([]); g.restore?.();
    items.push({ color: colors[1 % colors.length], label: 'strip theory', dash: true });
  }
  // CFD points: line through them, error bars, filled (reliable) or hollow (ok === false) markers
  const c0 = colors[0];
  g.strokeStyle = c0; g.lineWidth = 1.2 * dp; g.beginPath();
  pts.forEach((p, i) => i ? g.lineTo(X(p.U), Y(p.zeta * 100)) : g.moveTo(X(p.U), Y(p.zeta * 100)));
  g.stroke();
  for (const p of pts) {
    const x = X(p.U), y = Y(p.zeta * 100);
    if (num(p.sigma) && p.sigma > 0) {
      const a = Y((p.zeta + p.sigma) * 100), b = Y((p.zeta - p.sigma) * 100), cap = 3 * dp;
      g.strokeStyle = c0; g.lineWidth = dp;
      g.beginPath(); g.moveTo(x, a); g.lineTo(x, b); g.moveTo(x - cap, a); g.lineTo(x + cap, a); g.moveTo(x - cap, b); g.lineTo(x + cap, b); g.stroke();
    }
    g.beginPath(); g.arc(x, y, 3.2 * dp, 0, Math.PI * 2);
    if (p.ok === false) { g.strokeStyle = c0; g.lineWidth = 1.4 * dp; g.stroke(); } else { g.fillStyle = c0; g.fill(); }
  }
  items.unshift({ color: c0, label: pts.some(p => p.ok === false) ? 'CFD (hollow = unreliable)' : 'CFD' });
  legend(ctx, items, padL + 2 * dp, 9 * dp, W - padL - 8 * dp);
}

const VERDICTS = {
  decaying: ['ok', 'decaying', 'The motion dies out: stable at this speed'],
  neutral: ['warn', 'neutral', 'The motion neither grows nor decays: close to the flutter speed'],
  growing: ['bad', 'growing', 'The motion grows: flutter at this speed'],
  lco: ['bad', 'limit cycle', 'The motion grows and then settles at a constant amplitude (limit-cycle oscillation)'],
  unreliable: ['warn', 'unreliable', 'The fit is not trustworthy (too short, noisy or modes unresolved): run longer'],
};
function badge(verdict, esc) {
  const v = VERDICTS[verdict];
  return v ? `<span class="chip ${v[0]}" title="${esc(v[2])}">${esc(v[1])}</span>` : `<span class="chip">${esc(verdict ?? '–')}</span>`;
}
const pct = (z, fmt, d = 3) => num(z) ? fmt(z * 100, d) : '–';
function kpiHtml(lbl, val, unit, esc, cls = '', hint = '') {
  return `<div class="kpi ${cls}"><div class="lbl">${esc(lbl)}</div><div class="val">${val}<span class="small muted"> ${esc(unit)}</span></div>${hint ? `<div class="hint">${esc(hint)}</div>` : ''}</div>`;
}
const list = (items, esc) => items?.length ? `<ul class="small">${items.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : '';

/**
 * Summary of a finished (or partial) CFD-coupled flutter study as an HTML string. summary: see docs (kind "flutter_cfd").
 * esc escapes text, fmt(v, digits) formats numbers (the app's helpers; plain fallbacks are used when omitted).
 */
export function flutterCfdSummaryHtml(summary, esc = defEsc, fmt = defFmt) {
  const s = summary || {}, fl = s.flutter, st = s.strip || {};
  const speeds = Array.isArray(s.speeds) ? s.speeds : [];
  let h = '';
  const partial = s.status === 'partial';
  const vsStrip = fl && num(fl.vs_strip_percent) ? `${fl.vs_strip_percent > 0 ? '+' : ''}${fmt(fl.vs_strip_percent, 3)} %` : '–';
  if (fl && num(fl.speed_m_s)) {
    const sig = num(fl.sigma_m_s) ? ` ± ${fmt(fl.sigma_m_s, 2)}` : '';
    h += `<div class="kpis">${kpiHtml('Flutter speed (CFD)', `${fmt(fl.speed_m_s)}${sig}`, 'm/s', esc, fl.basis === 'extrapolated' ? 'warn' : 'ok',
      fl.basis === 'extrapolated' ? 'extrapolated: the speeds do not bracket it' : fl.basis === 'interpolated' ? 'interpolated between the speeds run' : '')}
      ${kpiHtml('Flutter frequency', fmt(fl.frequency_Hz), 'Hz', esc)}
      ${kpiHtml('Strip theory', fmt(st.flutter_speed_m_s), 'm/s', esc, '', num(st.frequency_Hz) ? `${fmt(st.frequency_Hz)} Hz` : '')}
      ${kpiHtml('CFD against strip theory', vsStrip, '', esc)}
      ${kpiHtml('Status', esc(partial ? 'partial' : 'complete'), '', esc, partial ? 'warn' : 'ok', partial ? 'some speeds are missing or unreliable' : '')}
      ${kpiHtml('Mesh', s.mesh?.cells != null ? fmt(s.mesh.cells) : '–', 'cells', esc)}</div>`;
  } else {
    h += `<div class="note warn small">No flutter speed found${partial ? ' (partial run)' : ''}: ${fl === null || fl?.basis === 'none' ? 'the damping does not cross zero in the speeds run' : 'too few reliable speeds'}. ${num(st.flutter_speed_m_s) ? `Strip theory: ${esc(fmt(st.flutter_speed_m_s))} m/s.` : ''}</div>
      <div class="kpis">${kpiHtml('Status', esc(partial ? 'partial' : 'complete'), '', esc, partial ? 'warn' : '')}
      ${kpiHtml('Strip theory', fmt(st.flutter_speed_m_s), 'm/s', esc)}</div>`;
  }
  if (speeds.length) {
    h += `<h3>Air speeds</h3><table class="summary"><thead><tr class="muted small"><td>U (m/s)</td><td>Verdict</td><td>ζ ± σ (%)</td><td>f (Hz)</td><td>Periods</td><td>Time (s)</td></tr></thead><tbody>`;
    for (const sp of speeds) {
      const c = sp.critical || (sp.poles || []).find(p => p.ok) || (sp.poles || [])[0] || null;
      const f = c?.f_Hz ?? c?.f;
      const sg = c && num(c.zeta_sigma) ? ` ± ${pct(c.zeta_sigma, fmt, 2)}` : '';
      const periods = num(sp.n_periods) ? sp.n_periods : (num(f) && num(sp.duration_s) ? f * sp.duration_s : null);
      h += `<tr><td>${esc(fmt(sp.U))}${num(sp.mach) ? ` <span class="muted">M ${esc(fmt(sp.mach, 2))}</span>` : ''}</td><td>${badge(sp.verdict, esc)}</td>
        <td>${c ? `${esc(pct(c.zeta, fmt))}${esc(sg)}` : '–'}</td><td>${esc(fmt(f))}</td><td>${periods != null ? esc(fmt(periods, 2)) : '–'}</td>
        <td>${esc(fmt(sp.duration_s))}${num(sp.wall_s) ? ` <span class="muted">(${esc(fmt(sp.wall_s / 60, 2))} min wall)</span>` : ''}</td></tr>`;
    }
    h += `</tbody></table><p class="small muted">ζ is the damping ratio of the least damped mode, negative = growing; σ its uncertainty from the fit. Periods = how many oscillations the fit used (about).</p>`;
  }
  if (Array.isArray(s.modes) && s.modes.length) {
    h += `<h3>Structure modes used</h3><table class="summary modes"><tbody>${s.modes.map(m =>
      `<tr><td>Mode ${esc(m.index)}</td><td>${esc(fmt(m.frequency_Hz))} Hz</td><td>${esc(fmt(m.generalized_mass_kg, 3))} kg</td></tr>`).join('')}</tbody></table>`;
  }
  if (s.warnings?.length) h += `<div class="note warn small"><b>Warnings</b>${list(s.warnings, esc)}</div>`;
  if (s.assumptions?.length) h += `<h3>Assumptions</h3>${list(s.assumptions, esc)}`;
  if (num(s.seconds)) h += `<p class="small muted">Total time ${esc(fmt(s.seconds / 3600, 3))} h.</p>`;
  return h;
}

const dur = (t, fmt) => !num(t) ? '–' : t >= 3600 ? `${fmt(t / 3600, 3)} h` : t >= 120 ? `${fmt(t / 60, 3)} min` : `${fmt(t, 3)} s`;
const STATE_LABEL = { queued: 'waiting', running: 'running', done: 'done', failed: 'failed', stalled: 'stalled', cancelled: 'cancelled', skipped: 'skipped' };

/**
 * Per-speed progress of a running study. status = {state, speeds:[{U, state, t, t_end, exchanges, eta_s, elapsed_s}],
 * total_eta_s, cost_estimate_h}. Progress bars use the existing .note .bar / .fill classes.
 */
export function flutterCfdProgressHtml(status, esc = defEsc, fmt = defFmt) {
  const sp = status?.speeds;
  if (!Array.isArray(sp) || !sp.length) return `<div class="note small">${esc(status?.state ? `Study ${status.state}` : 'Waiting for the first air speed')}</div>`;
  let h = `<table class="summary"><thead><tr class="muted small"><td>U (m/s)</td><td>State</td><td>Progress</td><td>Exchanges</td><td>Remaining</td></tr></thead><tbody>`;
  for (const r of sp) {
    const f = num(r.t) && num(r.t_end) && r.t_end > 0 ? Math.min(Math.max(r.t / r.t_end, 0), 1) : r.state === 'done' ? 1 : 0;
    const bad = r.state === 'failed' || r.state === 'stalled';
    h += `<tr><td>${esc(fmt(r.U))}</td><td><span class="chip ${r.state === 'done' ? 'ok' : bad ? 'bad' : r.state === 'running' ? 'warn' : ''}">${esc(STATE_LABEL[r.state] ?? r.state ?? '–')}</span></td>
      <td style="min-width:90px"><div class="note" style="padding:2px 4px"><div class="bar"><div class="fill" style="width:${(f * 100).toFixed(1)}%${bad ? ';background:var(--err)' : ''}"></div></div></div>
      <span class="small muted">${num(r.t) ? esc(fmt(r.t, 3)) : '–'} / ${num(r.t_end) ? esc(fmt(r.t_end, 3)) : '–'} s</span></td>
      <td>${num(r.exchanges) ? esc(fmt(r.exchanges)) : '–'}</td>
      <td>${r.state === 'done' ? esc(dur(r.elapsed_s, fmt)) : r.state === 'running' ? esc(dur(r.eta_s, fmt)) : '–'}</td></tr>`;
  }
  h += `</tbody></table>`;
  const parts = [];
  if (num(status.total_eta_s)) parts.push(`about ${dur(status.total_eta_s, fmt)} left`);
  if (num(status.cost_estimate_h)) parts.push(`estimated ${fmt(status.cost_estimate_h, 3)} h of computing in total`);
  if (parts.length) h += `<p class="small muted">${esc(parts.join('; '))}.</p>`;
  return h;
}
