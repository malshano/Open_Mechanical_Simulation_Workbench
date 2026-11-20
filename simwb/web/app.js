// Mechanical Simulation Workbench - GUI
import { Viewer, turbo } from '/static/viewer.js';
import { drawModalSeries, drawDampingVsSpeed, flutterCfdSummaryHtml, flutterCfdProgressHtml } from '/static/flutter_cfd.js';

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (v, d = 3) => {
  if (v == null || !Number.isFinite(Number(v))) return '–';
  v = Number(v);
  if (v !== 0 && (Math.abs(v) >= 1e6 || Math.abs(v) < 1e-3)) return v.toExponential(2);
  if (Math.abs(v) >= 10 ** d) return Math.round(v).toLocaleString('en-US');
  const t = v.toPrecision(d);
  return t.includes('e') ? String(Number(t)) : t.includes('.') ? t.replace(/\.?0+$/, '') : t;
};
const fmt1 = (v, d = 1) => v == null || Number.isNaN(v) ? '–' : Number(v).toFixed(d);
const hex2rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255);

const STEPS = [
  ['geometry', 'Geometry'], ['setup', 'Setup'], ['mesh', 'Mesh'], ['solve', 'Solve'], ['results', 'Results'],
];
const BODY_COLORS = ['#9aa5b8', '#d9a066', '#7fb3a6', '#b58fc9', '#c9b458', '#8fb2d9', '#d98f8f'];

const S = {
  meta: null, projects: [], pid: localStorage.getItem('simwb.pid') || null, p: null, faces: [],
  step: 'geometry', activeBc: null, jobs: {}, results: null, field: null, range: null, clip: { axis: '', pos: 500 },
  hideAir: true, chatBusy: false, foam: null, faceVersion: null, solveLog: null, autoRange: true,
  hiddenBodies: new Set(), hiddenParts: new Set(), fitPending: false,
};

// Simple / Expert: Simple hides the tools and settings marked "expert" and shows a hint for the next step. A
// browser that has used the workbench before starts in Expert, so nothing a user relies on disappears.
if (!localStorage.getItem('simwb.mode')) localStorage.setItem('simwb.mode', localStorage.getItem('simwb.pid') ? 'expert' : 'simple');
S.simple = localStorage.getItem('simwb.mode') === 'simple';
function applyMode() {
  document.body.classList.toggle('simple', S.simple);
  document.querySelectorAll('#modeBox [data-mode]').forEach(b => b.classList.toggle('active', (b.dataset.mode === 'simple') === S.simple));
}
document.querySelectorAll('#modeBox [data-mode]').forEach(b => b.onclick = () => {
  S.simple = b.dataset.mode === 'simple'; localStorage.setItem('simwb.mode', b.dataset.mode);
  applyMode(); if (S.meta) renderAll();
});
applyMode();

// ------------------------------------------------------------------ api
async function api(path, opts = {}) {
  const o = { ...opts };
  o.headers = { ...(o.headers || {}), 'X-SimWB': '1' };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers['Content-Type'] = 'application/json'; o.method ||= 'POST'; delete o.json; }
  const r = await fetch(path, o);
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data.error || data.detail || r.statusText);
  return data;
}
const P = (path) => `/api/projects/${S.pid}${path}`;
async function download(path, name, opts = {}) {
  const o = { ...opts, headers: { ...(opts.headers || {}), 'X-SimWB': '1' } };
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers['Content-Type'] = 'application/json'; o.method = 'POST'; delete o.json; }
  const r = await fetch(path, o);
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.error || (r.status === 404 || r.status === 405 ? 'Not available on this running server yet: restart the workbench to use it.' : e.detail || r.statusText));
  }
  saveBlob(await r.blob(), name);
}
function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

function toast(msg, err = false, ms = 4500) {
  const t = document.createElement('div');
  t.className = 't' + (err ? ' err' : ''); t.textContent = msg;
  $('#toast').appendChild(t); setTimeout(() => t.remove(), err ? ms * 1.6 : ms);
}
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg); return r; } catch (e) { toast(e.message, true); throw e; }
}

// ------------------------------------------------------------------ viewer
const viewer = new Viewer($('#three'), {
  onPick: (f, ev, ok, pt) => onPick(f, ev, ok, pt),
  onHover: (f, ev) => onHover(f, ev),
});

function studySpec() { return S.p?.study_spec; }
function bcOfFace() {
  const m = new Map();
  for (const bc of S.p?.bcs || []) for (const f of bc.faces) m.set(f, bc);
  return m;
}
function faceColorFn() {
  const spec = studySpec();
  const map = bcOfFace();
  const active = S.p?.bcs.find(b => b.id === S.activeBc);
  const bodyIdx = new Map((S.p?.body_list || []).map((b, i) => [b.id, i]));
  const byBody = S.step === 'geometry';
  return f => {
    if (!f) return [0.7, 0.7, 0.7];
    if (S.align && S.step === 'geometry' && f.id === S.align.face) return [1, 0.83, 0.23];  // the face being aligned
    if (S.align?.wall && S.step === 'geometry' && f.id === S.align.wall.face) return [1, 0.5, 0.15];  // the tunnel wall it goes against
    if (S.step === 'results') return f.kind === 'enclosure' || f.kind === 'farfield' ? [0.8, 0.84, 0.9] : [0.72, 0.74, 0.78];
    if (byBody && f.kind !== 'enclosure' && f.kind !== 'farfield') {
      const solidVol = f.volumes.find(v => (S.p.body_list.find(b => b.id === v) || {}).role === 'solid');
      const i = bodyIdx.get(solidVol ?? f.volumes[0]) ?? 0;
      return hex2rgb(BODY_COLORS[i % BODY_COLORS.length]);
    }
    const bc = map.get(f.id);
    if (bc) {
      const c = hex2rgb(spec.face_bc_types[bc.type]?.color || '#888888');
      if (active && bc.id !== active.id) return c.map(x => x * 0.55 + 0.35);
      return c;
    }
    if (f.kind === 'enclosure' || f.kind === 'farfield') return [0.62, 0.74, 0.95];
    if (f.kind === 'interface') return [0.66, 0.68, 0.72];
    return [0.78, 0.8, 0.84];
  };
}
function refreshViewerColors() { viewer.setColorFn(faceColorFn()); viewer.setBcGlyphs(bcGlyphs()); }

// every condition drawn on its faces, in its colour, with a label: arrows for loads and flows, blocks for fixed
// supports, rollers for sliding supports, plates for slip / symmetry, outward arrows for heat lost to the
// surroundings; plus the unassigned faces of the air volume (free stream / open sides) and heat sources in bodies
const AXIS_VEC = { '+x': [1, 0, 0], '-x': [-1, 0, 0], '+y': [0, 1, 0], '-y': [0, -1, 0], '+z': [0, 0, 1], '-z': [0, 0, -1] };
function bcLabel(bc, t) {
  const q = bc.params || {}, parts = [];
  const unitVal = (v, u) => `${fmt(v)}${u ? ' ' + u : ''}`;
  if (bc.type === 'force') parts.push(unitVal(Math.hypot(+q.fx_N || 0, +q.fy_N || 0, +q.fz_N || 0), 'N'));
  else if (bc.type === 'displacement') parts.push(['x', 'y', 'z'].map(a => q[`u${a}_mm`] == null || q[`u${a}_mm`] === '' ? null : `${a} ${fmt(q[`u${a}_mm`])}`).filter(Boolean).join(', ') + ' mm');
  else if (bc.type === 'velocity_inlet') parts.push(q.flow_rate > 0 ? unitVal(q.flow_rate, 'm³/h') : unitVal(q.velocity, 'm/s'), unitVal(q.T, '°C'));
  else for (const p of t?.params || []) {
    if (p.options || q[p.key] == null || q[p.key] === '' || parts.length >= 2 || (parts.length && !+q[p.key])) continue;
    parts.push(unitVal(q[p.key], p.unit));
  }
  return `${bc.name}${parts.length ? ': ' + parts.join(', ') : ''}`;
}
function bcGlyphs() {
  if (!S.p || !['geometry', 'setup', 'solve'].includes(S.step)) return [];  // (not over the mesh or the results)
  const spec = studySpec(), out = [];
  const num = v => (v === null || v === undefined || v === '' ? 0 : Number(v) || 0);
  for (const bc of S.p.bcs) {
    if (!bc.faces.length) continue;
    const t = spec.face_bc_types[bc.type];
    const q = bc.params || {}, g = { faces: bc.faces, color: t?.color || '#888888', label: bcLabel(bc, t) };
    const vec = keys => keys.map(k => num(q[k]));
    const sign = v => (v > 0 ? 'in' : v < 0 ? 'out' : null);
    let d = null, n = null;
    if (bc.type === 'force') d = vec(['fx_N', 'fy_N', 'fz_N']);
    else if (bc.type === 'displacement') d = vec(['ux_mm', 'uy_mm', 'uz_mm']);
    else if (bc.type === 'pressure') n = sign(num(q.pressure_Pa));
    else if (bc.type === 'heat_flux') n = sign(num(q.q));
    else if (bc.type === 'heat_load') n = sign(num(q.Q));
    else if (bc.type === 'velocity_inlet') {
      const ax = AXIS_VEC[q.direction];
      if (ax) d = ax; else n = 'in';  // (along an axis: uniform flow, e.g. a curved inlet)
    }
    else if (bc.type === 'pressure_outlet') n = 'out';
    else if (bc.type === 'moving_wall') d = AXIS_VEC[q.axis];
    else if (['convection', 'radiation_convection', 'wall_convection', 'natural_convection'].includes(bc.type)) g.symbol = 'exchange';
    if (bc.type === 'displacement' && !(d && d.some(x => x))) g.symbol = 'fixed';  // held in place
    if (d && d.some(x => x)) out.push({ ...g, dir: d });
    else if (n) out.push({ ...g, normal: n });
    else out.push({ ...g, symbol: g.symbol || { fixed_support: 'fixed', frictionless_support: 'roller', slip_wall: 'plate',
      added_mass: 'mass', symmetry: 'plate' }[bc.type] || 'marker' });
  }
  // unassigned faces of the air volume (the control surface)
  const used = new Set(S.p.bcs.flatMap(b => b.faces));
  const free = S.faces.filter(f => !used.has(f.id) && (f.kind === 'farfield' || f.kind === 'enclosure'));
  if (S.p.study === 'external_aero' && free.length) {
    const fd = S.p.flow_dir || [1, 0, 0], V = S.p.settings.speed;
    // (air volumes built before the faces were tagged: tell inlet / outlet from where the face sits along the flow)
    const bb = S.p.geometry.info.bbox, c = [0, 1, 2].map(i => (bb[i] + bb[i + 3]) / 2);
    const roleOf = f => f.domain_role || (Math.abs(f.normal.reduce((s, x, i) => s + x * fd[i], 0)) < 0.7 ? 'side'
      : f.centroid.reduce((s, x, i) => s + (x - c[i]) * fd[i], 0) < 0 ? 'inlet' : 'outlet');
    const role = r => free.filter(f => roleOf(f) === r).map(f => f.id);
    if (role('inlet').length) out.push({ faces: role('inlet'), color: '#3a86ff', dir: fd, far: true, label: `Free stream in: ${fmt(V)} m/s` });
    if (role('outlet').length) out.push({ faces: role('outlet'), color: '#06d6a0', dir: fd, far: true, label: 'Free stream out' });
    if (role('side').length) out.push({ faces: role('side'), color: '#9aa4b2', symbol: 'plate', far: true, label: 'Far field (free stream)' });
  }
  if (S.p.study === 'external_aero') {
    // the oncoming air in front of the body: follows speed, pitch and sideslip (the body itself is not rotated)
    const fd = S.p.flow_dir || [1, 0, 0], pb = S.p.geometry?.info?.parts_bbox, st = S.p.settings;
    if (pb && Math.hypot(...fd) > 1e-9) {
      const c = [0, 1, 2].map(i => (pb[i] + pb[i + 3]) / 2), r = 0.5 * Math.hypot(pb[3] - pb[0], pb[4] - pb[1], pb[5] - pb[2]);
      out.push({ point: c.map((x, i) => x - fd[i] * 1.05 * r), dir: fd, end: true, scale: 2.5, color: '#3a86ff',
        label: `Air ${fmt(st.speed)} m/s · pitch ${fmt(st.pitch_deg)}° · sideslip ${fmt(st.yaw_deg)}°` });
    }
  } else if (S.p.study === 'cht_enclosure' && free.length) {
    const open = S.p.domain.enclosure_mode === 'auto_box', nat = S.p.settings.air_flow === 'natural';
    if (open && nat) out.push({ faces: free.map(f => f.id), color: '#06d6a0', symbol: 'exchange',
      label: `Open to the room (${fmt(S.p.settings.T_room)} °C, air in / out)` });
    else out.push({ faces: free.map(f => f.id), color: open ? '#ced4da' : '#9aa4b2', symbol: open ? 'plate' : 'marker',
      label: open ? 'Open sides (slip, adiabatic)' : 'Walls (no-slip, adiabatic)' });
  }
  // moving zones: label at the rim
  for (const z of S.p.moving_zones || []) {
    const a = AXIS_VEC[z.axis], perp = Math.abs(a[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
    out.push({ point: z.center.map((x, k) => x + a[k] * z.length / 2 + perp[k] * z.radius), color: '#9b6cff', symbol: 'marker',
      label: `${z.name}: ${fmt(z.rpm)} rpm (${z.method === 'sliding' ? 'sliding mesh' : 'MRF'})` });
  }
  // heat generated inside bodies
  for (const b of S.p.body_list || []) if (b.heat_W > 0)
    out.push({ point: b.centroid, color: '#fb5607', symbol: 'source', label: `${b.name}: ${fmt(b.heat_W)} W` });
  // gravity (own weight / buoyancy): one arrow at the centre of the parts
  const gdir = AXIS_VEC[S.p.settings.gravity], pb = S.p.geometry?.info?.bbox;
  // (outside a corner of the model, starting level with its top, so it does not cover the part labels)
  if (gdir && pb) out.push({ point: [0, 1, 2].map(i => gdir[i] ? (gdir[i] < 0 ? pb[i + 3] : pb[i]) : pb[i] - 0.12 * (pb[i + 3] - pb[i])),
    dir: gdir, color: '#6c757d', label: 'Gravity 9.81 m/s²' });
  return out;
}

function onHover(f, ev) {
  const tip = $('#hoverTip');
  if (!f || !ev) { tip.classList.add('hidden'); return; }
  const bc = bcOfFace().get(f.id);
  const kindLbl = { body: 'body surface', farfield: 'far-field', enclosure: 'air boundary', solid_outer: 'part outer face',
    interface: 'contact between bodies', boundary: 'outer face' }[f.kind] || f.kind;
  const owners = f.part || f.volumes.map(v => (S.p.body_list.find(b => b.id === v) || {}).name).filter(Boolean).join(' / ');
  tip.innerHTML = `<b>Face ${f.id}</b> · ${esc(kindLbl)}<br><span style="opacity:.8">${esc(owners)}</span><br>${fmt(f.area * 1e6)} mm² · ${esc(f.surface_type)}<br>` +
    (bc ? `→ <b>${esc(bc.name)}</b>` : `<span style="opacity:.75">${esc(studySpec()?.default_face_bc.label || '')}</span>`);
  const r = $('#viewport').getBoundingClientRect();
  tip.style.left = (ev.clientX - r.left + 14) + 'px'; tip.style.top = (ev.clientY - r.top + 14) + 'px';
  tip.classList.remove('hidden');
}

function similarFaces(f, allowed) {
  // same surface type, parallel normal (either direction) and similar area: e.g. all fin side faces
  const n0 = f.normal, a0 = f.area;
  return S.faces.filter(g => allowed(g) && g.surface_type === f.surface_type &&
    Math.abs(g.normal[0] * n0[0] + g.normal[1] * n0[1] + g.normal[2] * n0[2]) > 0.995 &&
    Math.abs(g.area - a0) <= 0.15 * a0).map(g => g.id);
}
function bodyFaces(f, allowed) {
  // "whole part": same STEP part (drone), same solid (heatsink / conduction); never mixes in air-box faces
  if (f.kind === 'enclosure' || f.kind === 'farfield') return S.faces.filter(g => g.kind === f.kind && allowed(g)).map(g => g.id);
  if (f.part) return S.faces.filter(g => g.part === f.part && allowed(g)).map(g => g.id);
  const solid = f.volumes.find(v => (S.p.body_list.find(b => b.id === v) || {}).role === 'solid') ?? f.volumes[0];
  return S.faces.filter(g => g.volumes.includes(solid) && g.kind !== 'farfield' && g.kind !== 'enclosure' && allowed(g)).map(g => g.id);
}

function pickTip(ev, html) {
  const tip = $('#hoverTip');
  const r = $('#viewport').getBoundingClientRect();
  tip.innerHTML = html; tip.classList.remove('hidden');
  tip.style.left = (ev.clientX - r.left + 14) + 'px'; tip.style.top = (ev.clientY - r.top + 14) + 'px';
}

async function onPick(f, ev, allowed = true, point = null) {
  if (!S.p) return;
  if (S.axisPick && S.step === 'setup') {  // a cylindrical face or a circular edge: the centre of rotation
    if (!f) return;
    try {
      const q = point ? `&x=${point[0]}&y=${point[1]}&z=${point[2]}` : '';
      await axisPicked(await api(P(`/axis_info?face=${f.id}${q}`)));
    } catch (e) { pickTip(ev, `<b>No round feature here</b><br>${esc(e.message)}`); }
    return;
  }
  if (S.alignWallPick && S.step === 'geometry') {  // a wall of the tunnel to lay the face against
    if (!f) return;
    if (f.id === S.align?.face) { pickTip(ev, '<b>That is the face you are aligning</b><br>Click a different flat face or a tunnel wall.'); return; }
    try {
      const w = await api(P(`/align_info?face=${f.id}&ref=1`));
      S.alignWallPick = false; S.align.wall = w; updateSelectHint(); alignToWall(false); refreshViewerColors();
    } catch (e) { pickTip(ev, `<b>Cannot use this face</b><br>${esc(e.message)}`); }
    return;
  }
  if (S.alignPick && S.step === 'geometry') {  // a flat face to lay on a coordinate plane
    if (!f) return;
    S.alignPick = false; updateSelectHint();
    try {
      const info = await api(P(`/align_info?face=${f.id}`));
      S.align = { ...info, base: S.rotPending ? S.rotPending.map(r => [...r]) : [[1, 0, 0], [0, 1, 0], [0, 0, 1]], target: null };
      refreshViewerColors(); updateAlignMarker();
      startRefPick();  // the next click chooses the plane to align it to
      renderPanel();
    } catch (e) { pickTip(ev, `<b>Face ${f.id} can't be aligned</b><br>${esc(e.message)}`); }
    return;
  }
  if (S.symPick && S.step === 'setup') {  // a symmetry plane through a flat face
    if (!f) return;
    if (!String(f.surface_type).includes('Plane')) { pickTip(ev, `<b>Face ${f.id} is not flat</b><br>A symmetry plane goes through a flat face.`); return; }
    S.symPick = false; updateSelectHint();
    await symApply({ action: 'add', faces: [f.id] });
    return;
  }
  if (S.step === 'setup' && S.activeBc) {
    if (!f) return;
    const bc = S.p.bcs.find(b => b.id === S.activeBc);
    const t = studySpec().face_bc_types[bc.type];
    if (!allowed) {
      const why = f.kind === 'interface' ? 'it is a contact face between two bodies (coupled automatically)'
        : `“${t.label}” can only go on ${(t.face_kinds || []).map(k => ({ enclosure: 'air-volume walls', solid_outer: 'outer faces of parts', body: 'vehicle surfaces', farfield: 'air-box faces' }[k] || k)).join(' or ')}`;
      pickTip(ev, `<b>Face ${f.id} can't be used</b><br>${esc(why)}`);
      return;
    }
    const filt = pickFilter();
    let group = ev.shiftKey ? bodyFaces(f, filt) : (ev.ctrlKey || ev.altKey || ev.metaKey) ? similarFaces(f, filt) : [f.id];
    const has = bc.faces.includes(f.id);
    // whole-part / similar-face selection never takes faces from another condition (a single click still moves one)
    const owner = bcOfFace(), skipped = new Set();
    if (group.length > 1 && !has) group = group.filter(id => {
      const o = owner.get(id);
      if (o && o.id !== bc.id && id !== f.id) { skipped.add(o.name); return false; }
      return true;
    });
    const changed = group.filter(id => bc.faces.includes(id) === has).length;
    await act(async () => {
      S.p = await api(P(`/bcs/${bc.id}`), { method: 'PATCH', json: has ? { remove_faces: group } : { add_faces: group } });
      renderAll();
    });
    if (group.length > 1 || skipped.size) toast(`${has ? 'Removed' : 'Added'} ${changed} face${changed === 1 ? '' : 's'}` +
      (skipped.size ? ` (kept faces that belong to ${[...skipped].join(', ')}; click a face on its own to move it)` : ''));
  } else if (f && S.step === 'setup') {
    const bc = bcOfFace().get(f.id);
    if (bc) { S.activeBc = bc.id; renderPanel(); refreshViewerColors(); updateSelectHint(); }
  }
}

function pickFilter() {
  const bc = S.p?.bcs.find(b => b.id === S.activeBc);
  if (!bc || S.step !== 'setup') return f => f.kind !== 'interface' || S.step !== 'setup';
  const kinds = studySpec().face_bc_types[bc.type]?.face_kinds;
  return f => f.kind !== 'interface' && (!kinds || kinds.includes(f.kind));
}

function updateSelectHint() {
  const h = $('#selectHint');
  const bc = S.p?.bcs.find(b => b.id === S.activeBc);
  viewer.pickFilter = pickFilter();
  if (S.axisPick && S.step === 'setup') {
    viewer.pickFilter = f => !['farfield', 'enclosure', 'interface'].includes(f.kind);
    h.innerHTML = `Click a <b>circular edge</b> (the edges light up under the pointer) or a <b>cylindrical face</b>: the centre of rotation is put there<button class="small" id="hintDone">Cancel</button>`;
    h.classList.remove('hidden'); $('#three').style.cursor = 'crosshair';
    $('#hintDone').onclick = () => { S.axisPick = null; viewer.setEdgePick(null); updateSelectHint(); renderPanel(); };
    return;
  }
  if (S.alignWallPick && S.step === 'geometry') {
    viewer.pickFilter = f => f.id !== S.align?.face && f.kind !== 'interface' && String(f.surface_type).includes('Plane');
    h.innerHTML = `Now click the plane to align it to: a wall of the tunnel or another flat face<span style="opacity:.85"> · or choose a coordinate plane in the panel</span><button class="small" id="hintDone">Skip</button>`;
    h.classList.remove('hidden'); $('#three').style.cursor = 'crosshair';
    $('#hintDone').onclick = () => { S.alignWallPick = false; updateSelectHint(); renderPanel(); };
    return;
  }
  if (S.alignPick && S.step === 'geometry') {
    viewer.pickFilter = f => !['farfield', 'enclosure', 'interface'].includes(f.kind);
    h.innerHTML = `Click a flat face of the model to lay it on a coordinate plane<button class="small" id="hintDone">Cancel</button>`;
    h.classList.remove('hidden'); $('#three').style.cursor = 'crosshair';
    $('#hintDone').onclick = () => { S.alignPick = false; updateSelectHint(); };
    return;
  }
  if (S.symPick && S.step === 'setup') {
    viewer.pickFilter = f => f.kind !== 'interface';
    h.innerHTML = `Click a flat face: the symmetry plane goes through it<button class="small" id="hintDone">Cancel</button>`;
    h.classList.remove('hidden'); $('#three').style.cursor = 'crosshair';
    $('#hintDone').onclick = () => { S.symPick = false; updateSelectHint(); };
    return;
  }
  if (S.step === 'setup' && bc) {
    h.innerHTML = `Click faces to add/remove them from <b>${esc(bc.name)}</b> (${bc.faces.length} selected)` +
      `<span style="opacity:.85"> · Shift+click: whole part · Ctrl+click: all similar faces</span>` +
      `<button class="small" id="hintClear">Clear</button><button class="small" id="hintDone">Done</button>`;
    $('#hintClear').onclick = () => act(async () => {
      S.p = await api(P(`/bcs/${bc.id}`), { method: 'PATCH', json: { faces: [] } }); renderAll();
    });
    h.classList.remove('hidden');
    $('#hintDone').onclick = () => { S.activeBc = null; renderPanel(); refreshViewerColors(); updateSelectHint(); };
    $('#three').style.cursor = 'crosshair';
  } else { h.classList.add('hidden'); $('#three').style.cursor = 'default'; }
}

// ------------------------------------------------------------------ data loading
async function loadMeta() { S.meta = await api('/api/meta'); }
async function loadProjects() { S.projects = await api('/api/projects'); renderProjectBox(); }

async function openProject(pid) {
  S.pid = pid; S.rotPending = null; S.contours = []; resetVis(); if (S.iso) { S.iso.gap = null; S.iso.k = null; S.iso.on = null; } localStorage.setItem('simwb.pid', pid || '');
  S.results = null; S.activeBc = null; S.faceVersion = null; S.field = null; S.range = null; S.autoRange = true;
  S.clip = { axis: '', pos: 500 }; $('#clipAxis').value = ''; $('#clipPos').value = 500; S.hiddenBodies = new Set(); S.hiddenParts = new Set();
  S.jobs = {}; S.fitPending = true; S.showAir = null; S.symDraft = null; S.symPick = false; S.align = null; S.alignPick = false; S.alignWallPick = false; S.showAirSaved = null; S.axisPick = null; S.axisOblique = null;
  $('#chatCode')?.classList.toggle('hidden', !pid);   // (Claude Code in the study's folder)
  if (!pid) { S.p = null; renderAll(); return; }
  try { S.p = await api(P('')); } catch (e) { S.pid = null; S.p = null; localStorage.removeItem('simwb.pid'); renderAll(); return; }
  try {  // a job that is already running (page reloaded, study reopened): show its progress, not a Run button
    for (const j of (await api('/api/jobs')).jobs || []) if (j.project === pid && j.status === 'running') S.jobs[j.kind] = j;
  } catch (e) { /* the job events will fill it in */ }
  S.step = S.p.geometry.status !== 'ready' ? 'geometry' : S.p.solve.status === 'done' ? 'results'
    : S.p.mesh.status === 'ready' ? 'solve' : 'setup';
  await loadFaces();
  renderAll();
  chatRender();
}

async function loadFaces() {
  if (S.p?.geometry.status !== 'ready') { viewer.clearGeometry(); S.faces = []; return; }
  const key = S.p.geometry.info?.n_faces + ':' + JSON.stringify(S.p.geometry.info?.bbox) + ':' + S.p.study;
  if (key === S.faceVersion) return;
  const d = await api(P('/faces'));
  S.faces = d.faces; S.faceVersion = key;
  applyGeometry();
  if (S.fitPending) { viewer.fit([1, -1.4, 0.9]); S.fitPending = false; }
}

function applyGeometry() {
  const st = S.p.study;
  const hidden = S.hiddenBodies;
  let shown = hidden.size ? S.faces.filter(f => !f.volumes.every(v => hidden.has(v) ||
    (S.p.body_list.find(b => b.id === v) || {}).role === 'fluid') || f.kind === 'enclosure' || f.kind === 'farfield') : S.faces;
  if (S.hiddenParts.size) shown = shown.filter(f => !S.hiddenParts.has(f.part));
  if (S.showAir == null) S.showAir = st === 'cht_enclosure';  // (aero: the far field is big - off at first)
  $('#optAir').checked = S.showAir; viewer.setShowAir(S.showAir);
  viewer.setGeometry(shown, {
    airKinds: st === 'cht_enclosure' ? ['enclosure'] : [],
    hiddenKinds: st === 'external_aero' ? ['farfield'] : [],
  });
  refreshViewerColors();
}

let refreshT = null;
function scheduleRefresh() {
  clearTimeout(refreshT);
  refreshT = setTimeout(async () => {
    if (!S.pid) return;
    try {
      const prevSolve = S.p?.solve.status;
      S.p = await api(P(''));
      await loadFaces();
      if (S.p.solve.status === 'done' && prevSolve !== 'done') { S.results = null; }
      renderAll();
    } catch (e) { /* project deleted */ }
  }, 150);
}

// ------------------------------------------------------------------ websocket
let ws;
function connectWs() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => loadAllJobs();
  if (!S.jobsMenuInit) {
    S.jobsMenuInit = true;
    $('#jobsBtn').onclick = ev => { ev.stopPropagation(); loadAllJobs(); $('#jobsList').classList.toggle('hidden'); };
    document.addEventListener('click', ev => { if (!ev.target.closest('#jobsList')) $('#jobsList').classList.add('hidden'); });
  }
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.type === 'job') onJob(m);
    else if (m.type === 'project_changed' && m.project === S.pid) scheduleRefresh();
    else if (m.type === 'highlight' && m.project === S.pid) { viewer.flash(m.faces); }
    else if (m.type?.startsWith('chat') && m.project === S.pid) onChatEvent(m);
    else if (m.type?.startsWith('term_') && m.project === S.pid) onTermEvent(m);
  };
  ws.onclose = () => { if (!S.closed) setTimeout(connectWs, 1500); };
}

// all running jobs (every project): the Jobs menu in the top bar
S.allJobs = {};
function renderJobsMenu() {
  const js = Object.values(S.allJobs).filter(j => j.status === 'running');
  $('#jobsCount').textContent = js.length ? `(${js.length})` : '';
  $('#jobsBtn').classList.toggle('busy', js.length > 0);
  const lbl = { prepare: 'Geometry import', mesh: 'Meshing', solve: 'Solving', check: 'Mesh check', install: 'Setting up OpenFOAM' };
  const names = Object.fromEntries((S.projects || []).map(p => [p.id, p.name]));
  $('#jobsList').innerHTML = js.length ? js.map(j => `<div class="mi"><span><b>${esc(lbl[j.kind] || j.kind)}</b>
      <span class="muted small">${esc(j.project === '_openfoam' ? 'OpenFOAM' : names[j.project] || j.project)} · ${Math.round(j.progress * 100)}% · ${Math.round(j.elapsed)} s</span><br>
      <span class="small muted">${esc(j.message || '')}</span></span>
      <button class="danger small" data-stopjob="${esc(j.id)}" title="Stop this job">Stop</button></div>`).join('')
    : '<div class="mi"><span class="muted">No jobs running</span></div>';
  $('#jobsList').querySelectorAll('[data-stopjob]').forEach(b => b.onclick = ev => {
    ev.stopPropagation(); b.disabled = true; b.textContent = 'Stopping…';
    api(`/api/jobs/${b.dataset.stopjob}/cancel`, { method: 'POST' }).catch(e => toast(e.message, true));
  });
}
async function loadAllJobs() {
  try { S.allJobs = Object.fromEntries((await api('/api/jobs')).jobs.map(j => [j.id, j])); } catch (e) { /* server restarting */ }
  renderJobsMenu();
}

function onJob(m) {
  const j = m.job;
  S.allJobs[j.id] = j; if (m.final) delete S.allJobs[j.id];
  renderJobsMenu();
  if (j.project === '_openfoam') {  // built-in OpenFOAM install: global, shown in the job bar on every page
    S.foamJob = j; renderJobBar();
    if (m.final) { S.foam = null; if (S.step === 'solve') renderPanel(); }
    return;
  }
  if (j.project !== S.pid) return;
  const was = S.jobs[j.kind]?.status;
  S.jobs[j.kind] = j;
  if (was !== j.status && !m.final) renderPanel();  // started: buttons switch to their busy state
  renderJobBar();
  if (m.final) {
    if (j.status === 'failed') toast(`${j.kind} failed: ${j.error}`, true, 8000);
    if (j.status === 'done') {
      if (j.kind === 'prepare') { S.faceVersion = null; S.fitPending = true; toast('Geometry ready'); }
      if (j.kind === 'mesh') { toast('Mesh ready'); if (S.step === 'mesh') showMesh(); }
      if (j.kind === 'solve') { toast('Solution finished'); S.results = null; S.step = 'results'; }
      if (j.kind === 'check') { toast(`${(j.extra?.check_kind || 'check').replace(/^./, c => c.toUpperCase())} finished`); S.results = null; }
    }
    scheduleRefresh();
  } else if (S.step === 'solve' && j.kind === 'solve') renderSolveLive();
}

function runningJob() {
  return Object.values(S.jobs).find(j => j.status === 'running');
}
function renderStatus() {
  const p = S.p;
  $('#stStudy').innerHTML = p ? `<b>${esc(p.name)}</b> · ${esc(studySpec()?.label || p.study)}` : 'Ready';
  const ms = p?.mesh?.stats;
  $('#stMesh').innerHTML = p ? (p.mesh.status === 'ready' || p.mesh.status === 'stale'
    ? `Mesh: <b>${ms ? ms.cells.toLocaleString() : ''}</b> cells${p.mesh.status === 'stale' ? ' (out of date)' : ''}` : 'Mesh: none') : '';
  const sv = { done: 'solved', running: 'running', failed: 'failed', stale: 'out of date', interrupted: 'interrupted' }[p?.solve?.status];
  $('#stSolve').innerHTML = p ? `Solution: <b>${sv || 'none'}</b>` : '';
}

function renderJobBar() {
  renderStatus();
  const j = runningJob() || (S.foamJob?.status === 'running' ? S.foamJob : null);
  const bar = $('#jobBar');
  if (!j) { bar.classList.add('hidden'); return; }
  bar.classList.remove('hidden');
  bar.querySelector('.fill').style.width = (j.progress * 100).toFixed(1) + '%';
  const lbl = { prepare: 'Geometry', mesh: 'Meshing', solve: 'Solving', check: 'Mesh check', install: 'Setting up OpenFOAM' }[j.kind] || j.kind;
  bar.querySelector('.txt').textContent = `${lbl}: ${j.message}  (${Math.round(j.progress * 100)}%, ${j.elapsed}s)`;
  $('#jobCancel').onclick = () => api(`/api/jobs/${j.id}/cancel`, { method: 'POST' });
}

// ------------------------------------------------------------------ top bar
function renderProjectBox() {
  const box = $('#projectBox');
  const st = S.meta?.studies || {};
  box.innerHTML = `<div class="projMenu"><button class="ghost" id="projBtn" title="Open or delete a project">${esc(S.p?.name || '— Start page —')} ▾</button>
    <div id="projList" class="menu hidden"><div class="mi" data-open=""><span>— Start page —</span></div>
    ${S.projects.map(p => `<div class="mi ${p.id === S.pid ? 'sel' : ''}" data-open="${esc(p.id)}"><span><b>${esc(p.name)}</b> <span class="muted small">${esc(st[p.study]?.label || p.study || '')}</span></span>
      <button class="ghost small" data-del="${esc(p.id)}" title="Delete this project and all its files">🗑</button></div>`).join('')}</div></div>
    <button class="ghost small" id="newProj" title="New project">＋ New</button>
    ${S.pid ? `<button class="ghost small" id="renProj" title="Rename project">✎</button>
    <button class="ghost small" id="dupProj" title="Duplicate: a copy of this study with its geometry, setup and mesh (not the results) - e.g. for another speed or a transient run">⧉</button>
    <button class="ghost small" id="delProj" title="Delete project">🗑</button>` : ''}`;
  const list = $('#projList');
  $('#projBtn').onclick = ev => { ev.stopPropagation(); list.classList.toggle('hidden'); };
  list.querySelectorAll('[data-open]').forEach(d => d.onclick = () => { list.classList.add('hidden'); openProject(d.dataset.open || null); });
  list.querySelectorAll('[data-del]').forEach(b => b.onclick = ev => { ev.stopPropagation(); list.classList.add('hidden'); deleteProject(b.dataset.del); });
  $('#newProj').onclick = () => openProject(null);
  if (S.pid) {
    $('#renProj').onclick = async () => {
      const n = prompt('Project name', S.p?.name || ''); if (!n) return;
      await act(() => api(P('/rename'), { json: { name: n } })); await loadProjects();
    };
    $('#delProj').onclick = () => deleteProject(S.pid);
    $('#dupProj').onclick = async () => {
      const n = prompt('Name of the copy', `${S.p?.name || ''} (copy)`); if (!n) return;
      const st = await act(() => api(P('/duplicate'), { json: { name: n } }), 'Study duplicated');
      if (st?.id) { await loadProjects(); openProject(st.id); }
    };
  }
}
document.addEventListener('click', ev => { if (!ev.target.closest?.('.projMenu')) $('#projList')?.classList.add('hidden'); });

async function deleteProject(id) {
  const p = S.projects.find(x => x.id === id);
  const running = id === S.pid && Object.values(S.jobs).some(j => j?.status === 'running');
  if (!await confirmBox('Delete project', `Delete “${esc(p?.name || id)}” and all its files (geometry, mesh, results)? This cannot be undone.`
    + (running ? '<br><br>Something is still running in it: stop that first.' : ''), 'Delete')) return;
  await act(() => api(`/api/projects/${encodeURIComponent(id)}`, { method: 'DELETE' }), 'Project deleted');
  await loadProjects();
  if (id === S.pid) openProject(null); else if (!S.p) renderWelcome();
}

// confirmation window (in the page): resolves true for OK
function confirmBox(title, html, okLabel = 'OK') {
  return new Promise(res => {
    const d = document.createElement('div');
    d.className = 'modalBack';
    const danger = okLabel === 'Delete' || okLabel.startsWith('Close');
    d.innerHTML = `<div class="modal"><h3>${esc(title)}</h3><p>${html}</p><div class="row" style="justify-content:flex-end">
      <button class="ghost" data-r="0">Cancel</button><button class="${danger ? 'danger' : ''}" data-r="1">${esc(okLabel)}</button></div></div>`;
    const key = e => { if (e.key === 'Escape') done(false); if (e.key === 'Enter') done(true); };
    const done = v => { d.remove(); document.removeEventListener('keydown', key); res(v); };
    d.onclick = e => { if (e.target === d) done(false); };
    d.querySelectorAll('[data-r]').forEach(b => b.onclick = () => done(b.dataset.r === '1'));
    document.addEventListener('keydown', key);
    document.body.appendChild(d);
    d.querySelector('[data-r="1"]').focus();
  });
}

function stepState(step) {
  const p = S.p;
  if (!p) return '';
  const errs = (p.checks || []).filter(c => c.level === 'error' && c.step === step);
  const warns = (p.checks || []).filter(c => c.level === 'warning' && c.step === step);
  if (step === 'geometry') return p.geometry.status === 'ready' ? 'done' : '';
  if (step === 'setup') return p.geometry.status !== 'ready' ? '' : errs.length ? '' : warns.length ? 'warn' : 'done';
  if (step === 'mesh') return p.mesh.status === 'ready' ? 'done' : p.mesh.status === 'stale' ? 'warn' : '';
  if (step === 'solve') return p.solve.status === 'done' ? 'done' : p.solve.status === 'stale' ? 'warn' : '';
  if (step === 'results') return p.solve.status === 'done' ? 'done' : p.solve.status === 'stale' ? 'warn' : '';
  return '';
}

function renderSteps() {
  const nav = $('#steps');
  if (!S.p) { nav.innerHTML = ''; return; }
  nav.innerHTML = STEPS.map(([k, l], i) =>
    `<button data-step="${k}" class="${S.step === k ? 'active' : ''} ${stepState(k)}"><span class="num">${stepState(k) === 'done' ? '✓' : i + 1}</span>${l}</button>`).join('');
  nav.querySelectorAll('button').forEach(b => b.onclick = () => goStep(b.dataset.step));
}

function goStep(step) {
  if (S.step === 'geometry' && step !== 'geometry' && S.rotPending) {  // a turn that was only previewed is applied now
    applyOrientation(step); return;
  }
  S.step = step;
  if (step !== 'setup') S.activeBc = null;
  renderAll();
}

// ------------------------------------------------------------------ panels
function renderAll() {
  renderProjectBox(); renderSteps(); renderPanel(); renderViewportMode(); renderJobBar(); updateSelectHint();
  $('#welcome').classList.toggle('hidden', !!S.p);
  $('#viewTools').classList.toggle('hidden', !S.p);
  if (!S.p) renderWelcome();
  chatSuggestions();
}

let panelDirty = false;
function renderPanel() {
  const el = $('#panel');
  if (el.contains(document.activeElement) && document.activeElement.matches('input[type=text],input[type=number],textarea')) {
    panelDirty = true; return;
  }
  panelDirty = false;
  renderTree();
  if (!S.p) { el.innerHTML = welcomePanel(); return; }
  const fn = { geometry: panelGeometry, setup: panelSetup, mesh: panelMesh, solve: panelSolve, results: panelResults }[S.step];
  el.innerHTML = (S.simple ? guideHtml() : '') + fn();
  bindPanel(el);
  if (S.step === 'mesh') drawHistogram();
  if (S.step === 'solve') renderSolveLive();
  if (S.step === 'results') loadResultsPanel();
  el.querySelector('[data-about]')?.addEventListener('toggle', e => { (S.aboutOpen ||= {})[S.p.study] = e.target.open; });
  renderDock();
}

// Simple mode: what to do next, from the same checks as the Messages list
function guideHtml() {
  const p = S.p, run = runningJob(), err = (p.checks || []).find(c => c.level === 'error');
  let txt, step = null;
  if (run) txt = `${{ mesh: 'Meshing', solve: 'Solving', prepare: 'Reading the geometry', check: 'Checking' }[run.kind] || 'Working'}… The progress is shown at the bottom of the window.`;
  else if (p.geometry.status !== 'ready') { txt = 'Import your part: a STEP file from your CAD program (or STL / OBJ).'; step = 'geometry'; }
  else if (err) { txt = err.message || err.text || ''; step = err.step === 'mesh' && p.mesh.status === 'ready' ? 'mesh' : err.step; }
  else if (p.solve.status === 'stale') { txt = 'The setup changed after the last run: run the simulation again.'; step = 'solve'; }
  else if (p.solve.status !== 'done') { txt = 'Everything needed is set: run the simulation.'; step = 'solve'; }
  else { txt = 'The results are ready. Read the warnings under the numbers before you rely on them.'; step = 'results'; }
  const names = Object.fromEntries(STEPS);
  return `<div class="guide"><b>Next step</b><span>${esc(txt)}</span>${step && names[step] && step !== S.step ? `<button class="small" data-goto="${step}">Go to ${names[step]} →</button>` : ''}</div>`;
}

// ------------------------------------------------------------------ outline (model tree)
const TREE_OPEN = {};
function treeNode(d, icon, label, { sub = '', st = '', id = '', go = '', sel = false, kids = null, sw = '' } = {}) {
  const open = kids && TREE_OPEN[id] !== false;
  const stx = { ok: '✓', warn: '⚠', err: '✕' }[st] || '';
  return `<div class="tn ${sel ? 'sel' : ''}" style="--d:${d}" data-tid="${esc(id)}" data-go="${esc(go)}">
    <span class="tw">${kids ? (open ? '▾' : '▸') : ''}</span>${sw ? `<span class="sw" style="background:${sw}"></span>` : `<span class="ic">${icon}</span>`}
    <span class="lb">${esc(label)}</span>${sub ? `<span class="sub">${esc(sub)}</span>` : ''}${stx ? `<span class="st ${st}">${stx}</span>` : ''}</div>`
    + (open ? kids.join('') : '');
}
function stepSt(step) {
  const c = (S.p.checks || []).filter(x => x.step === step);
  return c.some(x => x.level === 'error') ? 'err' : c.some(x => x.level === 'warning') ? 'warn' : stepState(step) === 'done' ? 'ok' : '';
}
function renderTree() {
  const el = $('#tree');
  $('#treeStudy').textContent = S.p ? (studySpec()?.label || '') : '';
  if (!S.p) { el.innerHTML = ''; return; }
  const p = S.p, spec = studySpec(), mats = S.meta.materials.solids;
  const solids = p.body_list.filter(b => b.role === 'solid');
  const n = [];
  const geoKids = p.geometry.status === 'ready' ? p.body_list.map((b, i) => treeNode(2, b.role === 'fluid' ? '≋' : '■', b.name,
    { sub: b.role === 'fluid' ? 'air' : '', id: 'body' + b.id, go: `geometry|[data-body="${b.id}"]`, sw: b.role === 'fluid' ? '' : BODY_COLORS[i % BODY_COLORS.length] })) : [];
  if (airSpec()) geoKids.push(treeNode(2, '⬚', 'Air volume', { sub: (AIR_SHAPES[airSpec().shape] || '').split(' ')[0], id: 'air', go: 'geometry|[data-air="shape"]' }));
  n.push(treeNode(1, '◆', 'Geometry', { sub: p.geometry.status === 'ready' ? `${p.body_list.length} bodies` : 'not imported', st: stepSt('geometry'), id: 'geo', go: 'geometry', kids: geoKids }));
  if (solids.length) n.push(treeNode(1, '▤', 'Materials', { id: 'mats', go: 'setup', kids: solids.map(b => treeNode(2, '', b.name,
    { sub: mats[b.material]?.label || b.material || '-', id: 'mat' + b.id, go: `setup|[data-body="${b.id}"]`, sw: '#8d96a3' })) }));
  const bcKids = p.bcs.map(bc => treeNode(2, '', bc.name, { sub: `${spec.face_bc_types[bc.type]?.label || bc.type} · ${bc.faces.length} faces`,
    id: 'bc' + bc.id, go: `setup|[data-bc="${bc.id}"]|bc:${bc.id}`, sw: spec.face_bc_types[bc.type]?.color, sel: bc.id === S.activeBc, st: bc.faces.length ? '' : 'warn' }));
  bcKids.push(treeNode(2, '·', 'Other faces', { sub: spec.default_face_bc.label, id: 'bcdef', go: 'setup' }));
  for (const b of p.body_list) if (b.heat_W > 0) bcKids.push(treeNode(2, '✺', `${b.name}: ${fmt(b.heat_W)} W`, { sub: 'heat generated', id: 'heat' + b.id, go: `setup|[data-body="${b.id}"]` }));
  if (p.settings.gravity && p.settings.gravity !== 'off') bcKids.push(treeNode(2, '↓', 'Gravity', { sub: p.settings.gravity, id: 'grav', go: 'setup|[data-setting="gravity"]' }));
  n.push(treeNode(1, '⇥', spec.solver === 'builtin_fem' && p.study !== 'heat_conduction' ? 'Supports & loads' : 'Boundary conditions', { st: stepSt('setup'), id: 'bcs', go: 'setup', kids: bcKids }));
  if (p.study === 'external_aero') n.push(treeNode(1, '✣', 'Rotors', { sub: `${p.rotors.length}`, id: 'rot', go: 'setup|[data-rotor="0"]', kids: p.rotors.map((r, i) => treeNode(2, '◎', `Rotor ${i + 1}`, { sub: r.thrust_N != null ? `${fmt(r.thrust_N)} N` : 'auto thrust', id: 'rot' + i, go: `setup|[data-rotor="${i}"]` })) }));
  if ((p.moving_zones || []).length || p.study === 'external_aero' || p.study === 'cht_enclosure')
    n.push(treeNode(1, '⟳', 'Moving zones', { sub: `${(p.moving_zones || []).length}`, id: 'mz', go: 'setup|[data-mzone]', kids: (p.moving_zones || []).map((z, i) => treeNode(2, '⟳', z.name || `Zone ${i + 1}`, { sub: `${fmt(z.rpm)} rpm · ${z.method === 'sliding' ? 'sliding mesh' : 'MRF'}`, id: 'mz' + i, go: `setup|[data-mzone="${i}"]` })) }));
  const ms = p.mesh.stats;
  n.push(treeNode(1, '▦', 'Mesh', { sub: ms && p.mesh.status !== 'none' ? `${ms.cells.toLocaleString()} cells${p.mesh.status === 'stale' ? ' (out of date)' : ''}` : 'none', st: stepSt('mesh'), id: 'mesh', go: 'mesh' }));
  const solKids = [treeNode(2, '⚙', 'Solver settings', { sub: p.settings.analysis || '', id: 'set', go: 'setup|[data-setting]' })];
  if (spec.solver !== 'builtin_fem' && !S.simple) solKids.push(treeNode(2, '⌘', 'Advanced (OpenFOAM)', { sub: Object.keys(p.foam_overrides || {}).length ? 'edited' : '', id: 'adv', go: 'solve|[data-advanced]' }));
  n.push(treeNode(1, '▶', 'Solution', { sub: { done: 'solved', running: 'running', failed: 'failed', stale: 'out of date', interrupted: 'interrupted' }[p.solve.status] || 'not run',
    st: p.solve.status === 'failed' ? 'err' : stepSt('solve'), id: 'sol', go: 'solve', kids: solKids }));
  const fields = S.results?.fields || [];
  n.push(treeNode(1, '◐', 'Results', { sub: hasResults() ? '' : 'none', st: hasResults() ? (p.solve.status === 'stale' ? 'warn' : 'ok') : '', id: 'res', go: 'results',
    kids: hasResults() && fields.length ? fields.map(f => treeNode(2, '◔', f.name, { sub: f.unit, id: 'f' + f.name, go: `results|#fieldSel|field:${f.name}`, sel: S.step === 'results' && S.field === f.name })) : null }));
  el.innerHTML = treeNode(0, '◇', p.name, { sub: '', id: 'root', go: S.step, kids: n });
  el.querySelectorAll('.tn').forEach(t => t.onclick = ev => {
    const id = t.dataset.tid;
    if (ev.target.classList.contains('tw') && t.querySelector('.tw').textContent) { TREE_OPEN[id] = TREE_OPEN[id] === false; renderTree(); return; }
    treeGo(t.dataset.go);
  });
}
function treeGo(go) {
  const [step, sel, extra] = (go || '').split('|');
  if (!step) return;
  if (extra?.startsWith('bc:')) { S.activeBc = extra.slice(3); refreshViewerColors(); }
  if (extra?.startsWith('field:')) { S.field = extra.slice(6); S.range = null; S.autoRange = true; }
  if (S.step !== step) goStep(step); else { renderPanel(); updateSelectHint(); }
  if (extra?.startsWith('field:')) showResults();
  if (sel) setTimeout(() => {
    const t = $('#panel').querySelector(sel);
    if (!t) return;
    const box = t.closest('.item-card, .field, details, .airEdit') || t;
    if (box.tagName === 'DETAILS') box.open = true;
    box.scrollIntoView({ block: 'center', behavior: 'smooth' });
    box.classList.remove('flash'); void box.offsetWidth; box.classList.add('flash');
  }, 60);
}

// ------------------------------------------------------------------ messages / solver log (under the 3D view)
S.dock = { tab: 'msgs', min: false };
let dockPoll = null;
function renderDock() {
  const d = $('#dock');
  d.classList.toggle('hidden', !S.p);
  requestAnimationFrame(() => viewer.setInset(S.p ? d.offsetHeight : 0));
  $('#viewport').classList.toggle('dockOpen', !!S.p && !S.dock.min);
  if (!S.p) return;
  d.classList.toggle('min', S.dock.min);
  $('#dockToggle').textContent = S.dock.min ? '▴' : '▾';
  d.querySelectorAll('[data-dock]').forEach(b => b.classList.toggle('active', b.dataset.dock === S.dock.tab));
  const body = $('#dockBody');
  clearTimeout(dockPoll);
  if (S.dock.min) return;
  if (S.dock.tab === 'msgs') {
    const cs = S.p.checks || [];
    const ic = { error: '✕', warning: '⚠', ok: '✓', info: 'ℹ' };
    body.innerHTML = cs.length ? cs.map(c => `<div class="msg ${c.level}" data-step="${c.step || ''}" data-target="${esc(c.target || '')}" ${c.target ? 'title="Click to go to the input that fixes this"' : ''}><span class="lv">${ic[c.level] || '·'}</span>
      <span class="stp">${esc(c.step || '')}</span><span>${esc(c.message || c.text || '')}</span></div>`).join('') : '<span class="muted">No messages.</span>';
    // a click goes to the step, and to the input that fixes the message when it has one (scrolled to, opened, flashed)
    body.querySelectorAll('.msg').forEach(m => m.onclick = () => m.dataset.step && treeGo(m.dataset.step + (m.dataset.target ? '|' + m.dataset.target : '')));
  } else {
    const j = runningJob() || Object.values(S.jobs).filter(Boolean).sort((a, b) => (b.started || 0) - (a.started || 0))[0];
    if (!j) { body.innerHTML = '<span class="muted">No job has run since the project was opened. Logs of solver runs appear here.</span>'; return; }
    api(`/api/jobs/${j.id}?log_tail=300&max_points=2`).then(r => {
      const atEnd = body.scrollTop + body.clientHeight >= body.scrollHeight - 4;
      body.innerHTML = `<div class="muted">${esc(r.kind)} · ${esc(r.status)} · ${esc(r.message)}</div><pre>${esc(r.log.join('\n'))}</pre>`;
      if (atEnd) body.scrollTop = body.scrollHeight;
      if (r.status === 'running' && S.dock.tab === 'log') dockPoll = setTimeout(renderDock, 2500);
    }).catch(() => {});
  }
}
document.querySelectorAll('[data-dock]').forEach(b => b.onclick = () => { S.dock.tab = b.dataset.dock; S.dock.min = false; renderDock(); });
$('#dockToggle').onclick = () => { S.dock.min = !S.dock.min; renderDock(); };
document.addEventListener('focusout', () => setTimeout(() => { if (panelDirty) renderPanel(); }, 50));

function welcomePanel() {
  return `<h2>Welcome</h2><p class="muted">Pick a study to start.</p>
  <ol class="small"><li>Import STEP</li><li>Mark faces</li><li>Mesh</li><li>Solve</li><li>Results</li></ol>`;
}

// the "which engine for which problem" table of the Multiphysics tab (rows: GET /api/engine_guide)
function guideHtml(rows, esc) {
  const eng = e => `<span class="chip ${!e.offered ? 'bad' : e.installed === false ? 'warn' : e.installed ? 'ok' : ''}" title="${esc(e.reason || '')}">${esc(e.name)}</span>${e.role ? ` <span class="muted small">${esc(e.role)}</span>` : ''}`;
  return `<h3>Which engine for which problem</h3><table class="grid"><thead><tr><th>Problem</th><th>Engine</th><th>Fidelity / cost</th><th>Needs</th><th></th></tr></thead><tbody>
    ${rows.map(r => `<tr><td>${esc(r.problem)}${r.planned ? ' <span class="chip">planned</span>' : ''}</td>
      <td>${r.engines.map(eng).join('<br>')}</td><td>${esc(r.fidelity)}<br><span class="muted small">${esc(r.cost)}</span></td><td>${esc(r.needs)}</td>
      <td>${r.exists ? `<button class="small" data-new="${esc(r.study)}">Create</button>` : ''}</td></tr>`).join('')}</tbody></table>`;
}

function renderWelcome() {
  const st = S.meta.studies;
  const saveDir = localStorage.getItem('simwb.saveDir') || '';
  const icons = { thermo: '🌡️', fan: '🌀', drone: '🚁', wave: '📳', beam: '🏗️', flutter: '✈️' };
  // tabs: the single-physics studies, and one tab "Multiphysics" for the studies that couple several physics
  // (a study with category 'multiphysics')
  const multi = Object.entries(st).filter(([, s]) => s.category === 'multiphysics');
  const tabs = [{ id: 'studies', label: 'Studies' }, ...(multi.length ? [{ id: 'multiphysics', label: 'Multiphysics', multi: true }] : [])];
  if (!tabs.some(t => t.id === S.welcomeTab)) S.welcomeTab = 'studies';
  const card = ([k, s]) => `
      <div class="card"><div class="ico">${icons[s.icon] || '◆'}</div><h3>${esc(s.label)}</h3><p>${esc(s.description)}</p>
      <span class="badge" title="${s.solver === 'builtin_fem' ? 'Solved by the workbench itself: nothing else to install' : 'Flow simulation with OpenFOAM, set up by the workbench on the first run'}">${s.badge ? esc(s.badge) : s.solver === 'builtin_fem' ? 'Built-in solver · seconds to minutes' : 'OpenFOAM flow simulation · minutes to hours'}</span>
      <div class="row"><button data-new="${k}">Use my STEP file</button>
      ${s.examples.map(e => `<button class="ghost" data-new="${k}" data-ex="${esc(e)}">${s.examples.length > 1 ? 'Example: ' + esc(e.replace('.step', '').replace(/_/g, ' ')) : 'Try example'}</button>`).join('')}</div></div>`;
  const tabBody = S.welcomeTab === 'studies'
    ? `<div class="cards">${Object.entries(st).filter(([, s]) => s.category !== 'multiphysics').map(card).join('')}</div>`
    : `<p class="muted small" style="margin:14px 0 0">Several kinds of physics solved together: the result of one changes the other.</p>
      <div class="cards">${multi.map(([k, s]) => card([k, s]).replace('<p>', `<div class="coupled">${(s.physics || []).map(x => `<span class="chip">${esc(x)}</span>`).join('<span class="muted">⇄</span>')}</div><p>`)).join('')}</div>
      ${S.guide ? guideHtml(S.guide, esc) : ''}`;
  if (S.welcomeTab !== 'studies' && !S.guide && !S.guideLoading) {  // (the guide table is loaded once, when the tab is first shown)
    S.guideLoading = true;
    api('/api/engine_guide').then(d => { S.guide = d.rows; S.guideLoading = false; renderWelcome(); }).catch(() => { S.guideLoading = false; });
  }
  $('#welcome').innerHTML = `<div class="welcomeInner">
    <h1>What do you want to simulate?</h1>
    <div class="saveIn"><label class="field" title="Every new study asks where to save it: the folder chooser opens at this folder. The study gets its own sub-folder there, with a CLAUDE.md so that Claude Code started in it knows the study"><span>Studies are saved where you choose (the chooser opens at)</span>
      <span style="display:flex;gap:6px"><input type="text" id="saveDir" value="${esc(saveDir)}" placeholder="${esc(S.meta.workspace || '')}" spellcheck="false">
      <button class="ghost small" id="pickDir">Browse…</button><button class="ghost small" id="openDir" title="Add a study you saved earlier (a folder with a project.json) to the list">Open a saved study…</button></span></label></div>
    <div class="wtabs" role="tablist">${tabs.map(t => `<button role="tab" class="${t.id === S.welcomeTab ? 'active' : ''}" data-wtab="${esc(t.id)}" title="${t.multi ? 'Multiphysics: several kinds of physics solved together' : 'Studies of one kind of physics'}">${t.multi ? '⚛ ' : ''}${esc(t.label)}</button>`).join('')}</div>
    ${tabBody}
    ${S.projects.length ? `<h3 class="muted">Recent projects</h3><div class="recent">${S.projects.map(p => `
      <div class="item" data-open="${esc(p.id)}"><span><b>${esc(p.name)}</b> <span class="muted small">${esc(st[p.study]?.label || p.study)}</span><br><span class="muted small">${esc(p.path || '')}</span></span>
      <span class="row"><span class="muted small">${esc(p.updated || '')}</span>
      <button class="ghost small" data-del="${esc(p.id)}" title="Delete this study and all its files">🗑</button></span></div>`).join('')}</div>` : ''}
  </div>`;
  $('#welcome').querySelectorAll('[data-wtab]').forEach(b => b.onclick = () => { S.welcomeTab = b.dataset.wtab; renderWelcome(); });
  $('#welcome').querySelectorAll('[data-new]').forEach(b => b.onclick = async () => {
    const study = b.dataset.new, ex = b.dataset.ex;
    const name = ex ? ex.replace('.step', '').replace(/_/g, ' ') : st[study].label.split(' ')[0] + ' study';
    // ask before anything is created: the study is saved in the folder the user chooses (the assistant and Claude Code
    // start in it)
    const d = await act(() => api('/api/pick_folder', { json: { start: $('#saveDir').value.trim() } }));
    const location = d?.path || '';
    if (!location) { toast('Nothing created: choose the folder to save the study in', true); return; }
    localStorage.setItem('simwb.saveDir', location); $('#saveDir').value = location;
    const p = await act(() => api('/api/projects', { json: { name, study, example: ex, location } }));
    await loadProjects(); await openProject(p.id);
  });
  $('#pickDir').onclick = async () => {
    const d = await act(() => api('/api/pick_folder', { json: { start: $('#saveDir').value.trim() } }));
    if (d?.path) { $('#saveDir').value = d.path; localStorage.setItem('simwb.saveDir', d.path); }
  };
  $('#openDir').onclick = async () => {
    const d = await act(() => api('/api/pick_folder', { json: { start: $('#saveDir').value.trim() } }));
    if (!d?.path) return;
    const p = await act(() => api('/api/projects/open_folder', { json: { path: d.path } }));
    await loadProjects(); await openProject(p.id);
  };
  $('#welcome').querySelectorAll('[data-open]').forEach(d => d.onclick = () => openProject(d.dataset.open));
  $('#welcome').querySelectorAll('[data-del]').forEach(b => b.onclick = async ev => {
    ev.stopPropagation();  // (not: open it)
    deleteProject(b.dataset.del);
  });
}

// ---- air volume (automatic air around the parts): shape, size, drag handles in the view
const AIR_SHAPES = { box: 'Box', c_domain: 'C-shape (curved inlet)', cylinder: 'Cylinder along the flow' };
const AIR_SIZES = {
  box: [['min.0', 'x from'], ['max.0', 'x to'], ['min.1', 'y from'], ['max.1', 'y to'], ['min.2', 'z from'], ['max.2', 'z to']],
  c_domain: [['radius', 'Radius (inlet curve)'], ['downstream', 'Downstream'], ['span_min', 'Span from'], ['span_max', 'Span to']],
  cylinder: [['radius', 'Radius'], ['upstream', 'Upstream'], ['downstream', 'Downstream']],
};
function airSpec() {  // the air volume as built (+ unapplied edits), or null when there is none to edit
  const p = S.p, d = p?.geometry?.info?.domain;
  if (!d?.spec || p.geometry.status !== 'ready') return null;
  if (p.study === 'cht_enclosure' && (p.domain.enclosure_mode || 'body') !== 'auto_box') return null;
  const key = JSON.stringify(d.spec);
  if (!S.air || S.air.key !== key || S.air.pid !== p.id) S.air = { key, pid: p.id, spec: JSON.parse(key), dirty: false };
  return S.air.spec;
}
const airGet = (sp, k) => k.includes('.') ? sp[k.split('.')[0]][+k.split('.')[1]] : sp[k];
function airSet(sp, k, v) { if (k.includes('.')) sp[k.split('.')[0]][+k.split('.')[1]] = v; else sp[k] = v; }
function airVolumeHtml() {
  const sp = airSpec();
  if (!sp) return '';
  const stl = (S.p.geometry.info.source_format || 'step') !== 'step';
  const ax = ['+x', '-x', '+y', '-y', '+z', '-z'];
  let h = `<div class="grid2"><label class="field"><span>Shape</span><select data-air="shape" ${stl ? 'disabled title="A curved air volume needs a STEP file"' : ''}>${Object.entries(AIR_SHAPES).map(([k, l]) =>
    `<option value="${k}" ${k === sp.shape ? 'selected' : ''}>${l}</option>`).join('')}</select></label>`;
  if (S.p.study === 'cht_enclosure') {
    const cur = (sp.flow_sign > 0 ? '+' : '-') + 'xyz'[sp.flow_axis];
    h += `<label class="field"><span>Flow direction</span><select data-air="flow_axis">${ax.map(a => `<option ${a === cur ? 'selected' : ''}>${a}</option>`).join('')}</select></label>`;
  }
  if (sp.shape === 'c_domain')
    h += `<label class="field"><span>Span axis (along the curve)</span><select data-air="span_axis">${['x', 'y', 'z'].filter(a => a !== 'xyz'[sp.flow_axis]).map(a =>
      `<option ${a === 'xyz'[sp.span_axis] ? 'selected' : ''}>${a}</option>`).join('')}</select></label>`;
  h += AIR_SIZES[sp.shape].map(([k, l]) => `<label class="field"><span>${l} <span class="unit">[mm]</span></span>
    <input type="number" step="any" data-airsize="${k}" value="${fmt(airGet(sp, k) * 1000, 5)}"></label>`).join('');
  h += `</div><div class="row" style="margin-top:6px"><button class="small" data-action="airApply" ${S.air.dirty ? '' : 'disabled'}>Apply</button>
    <button class="ghost small" data-action="airFit" title="Size the air volume automatically around the parts">Fit to parts</button>
    <button class="ghost small" data-action="airView" title="Zoom out to the whole air volume">Show all</button></div>
    <p class="small muted">${sp.fitted ? 'Fitted to the parts. ' : ''}Drag the orange handles in the view or type sizes${sp.shape === 'box' ? '' : ' (from the centre of the parts)'}, then Apply.
    ${sp.shape === 'c_domain' ? 'The curved face is the inlet' + (S.p.study === 'cht_enclosure' ? ' (Auto inlet/outlet in Setup assigns it, with uniform flow along the flow direction)' : '') + '; the flat end downstream is the outlet.' : ''}</p>`;
  return h;
}
function refreshAirEditor() {
  const sp = S.step === 'geometry' ? airSpec() : null;
  viewer.setAirEditor(sp, (k, v, final) => {
    if (!S.air) return;
    airSet(S.air.spec, k, v); S.air.dirty = true;
    const inp = document.querySelector(`[data-airsize="${k}"]`); if (inp) inp.value = fmt(v * 1000, 5);
    const ap = document.querySelector('[data-action="airApply"]'); if (ap) ap.disabled = false;
    if (final) refreshAirEditor();
  }, S.air?.dirty);
}

// ---- geometry
function panelGeometry() {
  const p = S.p, g = p.geometry, spec = studySpec();
  let h = `<h2>${esc(spec.label)}</h2><h3>STEP file</h3>`;
  if (g.error) h += `<div class="note ${g.status === 'ready' ? '' : 'warn'}" style="margin-bottom:8px"><b>${/^CAD edit/.test(g.error) ? 'The last CAD edit failed:' : 'The last import failed:'}</b> ${esc(g.error)}
</div>`;
  if (g.status === 'ready') {
    h += `<div class="item-card"><div class="head"><span class="name">📄 ${esc(g.original_name || 'source.step')}</span>
      <button class="ghost small" data-action="replace">Replace…</button></div>
      <div class="small" style="margin-top:6px"><b>Orientation</b> <span class="muted">turn the parts about their middle to line them up
        with the axes (e.g. nose along +x, top along +z)${g.rotation ? ' · rotated' : ''}</span></div>
      <p class="small" style="margin:4px 0"><b>Drag a ring</b> around the model in the 3D view to turn it
        (<span style="color:#e23b3b">x</span> · <span style="color:#22c55e">y</span> · <span style="color:#3b82f6">z</span>; hold Shift for 5° steps), then press Apply.
        <button class="ghost small" data-action="rotRings">${S.rotGizmo === false ? 'Show rings' : 'Hide rings'}</button></p>
      <p class="small" style="margin:4px 0"><button class="ghost small" data-action="alignPick" title="Click a flat face of the model, then choose the coordinate plane to lay it on">⬓ Align a face to a plane…</button></p>
      ${alignHtml()}
      <div class="row" style="margin-top:4px;flex-wrap:wrap;gap:4px">${['x', 'y', 'z'].map(a =>
        `<button class="ghost small" data-action="rotate" data-axis="${a}" data-deg="-90" title="Turn -90° about ${a}">${a.toUpperCase()} −90°</button>`
        + `<button class="ghost small" data-action="rotate" data-axis="${a}" data-deg="90" title="Turn +90° about ${a} (right-handed)">${a.toUpperCase()} +90°</button>`).join('')}
        <select id="rotAxis" class="small"><option>x</option><option>y</option><option selected>z</option></select>
        <input id="rotDeg" type="number" step="any" value="45" style="width:64px" title="Angle in degrees">
        <button class="ghost small" data-action="rotate" data-custom="1">Turn</button>
        ${g.rotation && !S.rotPending ? '<button class="ghost small" data-action="rotate" data-reset="1">Reset</button>' : ''}</div>
      ${S.rotPending ? `<div class="note" style="margin-top:6px">Preview: the parts are turned in the view only (the air box and flow arrows stay). Press Apply, or just go on: the turn is applied when you leave this step.
        <div class="row" style="margin-top:4px;gap:6px"><button class="small" data-action="rotApply">Apply orientation</button>
        <button class="ghost small" data-action="rotCancel">Cancel</button></div></div>` : ''}</div>`;
  }
  h += `<div class="drop ${g.status === 'ready' ? 'hidden' : ''}" id="drop">Drop a <b>STEP</b>, <b>STL</b> or <b>OBJ</b> file here or <u>click to browse</u>
    <input type="file" id="fileIn" accept=".step,.stp,.stl,.obj" hidden><div class="small" style="margin-top:6px">Scale <select id="scaleSel" onclick="event.stopPropagation()"><option value="1">×1 (STL/OBJ: mm)</option><option value="0.001">×0.001</option>
    <option value="25.4">inch→mm ×25.4</option><option value="1000">×1000</option></select></div></div>`;
  if (g.status !== 'ready') {
    h += `<div class="row" style="margin-top:8px"><span class="muted small">Example:</span>${(spec.examples || S.meta.examples).map(e =>
      `<button class="ghost small" data-action="example" data-name="${esc(e)}">${esc(e)}</button>`).join('')}</div>`;
  }
  if (g.status === 'ready') {
    h += `<h3>Bodies</h3><div class="list">`;
    p.body_list.forEach((b, i) => {
      const bb = b.bbox, size = [0, 1, 2].map(k => fmt1((bb[k + 3] - bb[k]) * 1000)).join(' × ');
      const col = b.role === 'fluid' ? '#9ebcf2' : BODY_COLORS[i % BODY_COLORS.length];
      h += `<div class="item-card" data-body="${b.id}"><div class="head"><span class="swatch" style="background:${col}"></span>
        <span class="name">${esc(b.name)}</span><span class="muted small">${b.role === 'fluid' ? 'air' : 'solid'}</span>
        ${b.role === 'solid' ? `<button class="ghost small" data-action="toggleBody" title="Hide/show this part">${S.hiddenBodies.has(b.id) ? '🚫' : '👁'}</button>` : ''}</div>
        <div class="muted small">${size} mm · ${fmt(b.volume_cm3)} cm³</div></div>`;
    });
    h += `</div>`;
    h += cadEditsHtml();
    if (p.study === 'cht_enclosure') {
      const src = g.info.source_bodies || [];
      const mode = p.domain.enclosure_mode || 'body';
      h += `<h3>Air volume</h3>
        <div class="field full"><select data-domain="mode">
          <option value="body" ${mode === 'body' ? 'selected' : ''}>A body in the STEP is the air (duct / enclosure interior)</option>
          <option value="auto_box" ${mode === 'auto_box' ? 'selected' : ''}>Create an air box around the parts</option></select></div>`;
      if (mode === 'body') {
        const cur = p.domain.enclosure_body ?? g.info.domain?.enclosure_body;
        h += `<div class="field full" style="margin-top:6px">Which body is the air?<select data-domain="body">${src.map((b, i) =>
          `<option value="${i}" ${i === cur ? 'selected' : ''}>${esc(b.name)} (${fmt(b.volume * 1e6)} cm³)</option>`).join('')}</select></div>`;
      } else {
        h += airVolumeHtml();
      }
    }
    if (p.study === 'external_aero') {
      const parts = [...new Set(S.faces.map(f => f.part).filter(Boolean))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
      if (parts.length) {
        h += `<h3>Parts</h3><div class="list">${parts.map(n => `<div class="item-card" data-part="${esc(n)}"><div class="head">
          <span class="name">${esc(n)}</span><button class="ghost small" data-action="togglePart" title="Hide/show">${S.hiddenParts.has(n) ? '🚫' : '👁'}</button></div></div>`).join('')}</div>`;
      }
      const ff = p.domain.farfield || { upstream: 8, downstream: 16, side: 8 };
      h += `<h3>Air volume</h3>${airVolumeHtml()}<details open><summary>Automatic size (vehicle spans)</summary>
        <p class="small muted" style="margin:4px 0">Distance from the parts to the air-box faces, in spans of the parts (${fmt((S.p.geometry?.info?.ref_length || 0) * 1000)} mm).
        Compact boxes mesh and solve faster; the far-field faces then sit closer and can bias drag by a few per cent.</p>
        <div class="row" style="gap:4px;margin-bottom:6px">${Object.entries(FF_PRESETS).map(([n, v]) => `<button class="ghost small" data-action="ffPreset" data-ffp="${n}" title="${v.join(' / ')} spans upstream / downstream / side">${n}</button>`).join('')}</div>
        <div class="grid2">
        ${['upstream', 'downstream', 'side'].map(k => `<label class="field"><span>${k}</span><input type="number" step="0.5" min="0.5" data-ff="${k}" value="${ff[k]}"></label>`).join('')}
        <button class="ghost small" data-action="applyFF">Fit with these</button></div></details>`;
    }
    h += `<p style="margin-top:16px"><button class="big" data-goto="setup">Next: setup →</button></p>`;
  }
  return h;
}

// far-field distances (spans of the parts) upstream / downstream / side
const FF_PRESETS = { 'Minimal (test run)': [1, 2, 1], Compact: [3, 6, 3], Standard: [5, 10, 5], Large: [8, 16, 8] };

// ---- setup
// small "?" next to a setting: hover (or focus) shows what it is and when to adjust it
const tip = (text) => text ? `<span class="tip" tabindex="0" data-tip="${esc(text)}">?</span>` : '';
(() => {
  const box = document.createElement('div');
  box.id = 'tipBox';
  document.body.appendChild(box);
  const show = (el) => {
    box.textContent = el.dataset.tip;
    box.style.display = 'block';
    const r = el.getBoundingClientRect(), w = box.offsetWidth, h = box.offsetHeight;
    box.style.left = Math.max(6, Math.min(r.left - 8, window.innerWidth - w - 6)) + 'px';
    box.style.top = (r.bottom + 6 + h > window.innerHeight ? Math.max(6, r.top - h - 6) : r.bottom + 6) + 'px';
  };
  const hide = () => { box.style.display = 'none'; };
  document.addEventListener('mouseover', e => { const t = e.target.closest?.('.tip'); if (t) show(t); else hide(); });
  document.addEventListener('focusin', e => { const t = e.target.closest?.('.tip'); if (t) show(t); });
  document.addEventListener('focusout', hide);
  document.addEventListener('scroll', hide, true);
})();

function paramInputs(spec, values, attr) {
  return spec.filter(pp => !(S.simple && pp.adv)).filter(pp => !pp.show_if || Object.entries(pp.show_if).every(([k, v]) => Array.isArray(v) ? v.map(String).includes(String(values[k])) : String(values[k]) === String(v))).map(pp => {
    if (pp.options) {
      const why = pp.option_help?.[values[pp.key]];
      const all = pp.help || (pp.option_help ? Object.entries(pp.option_help).map(([k, v]) => `${k}: ${v}`).join('  ') : '');
      return `<label class="field"><span>${esc(pp.label)}${tip(all)}</span><select ${attr}="${pp.key}">${pp.options.map(o =>
        `<option ${String(values[pp.key]) === String(o) ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`
        + (why ? `<p class="explain">${esc(why)}</p>` : '');
    }
    return `<label class="field"><span>${esc(pp.label)}${pp.unit ? ` <span class="unit">[${esc(pp.unit)}]</span>` : ''}${tip(pp.help)}</span>
      <input type="number" step="any" ${attr}="${pp.key}" value="${values[pp.key] ?? ''}"></label>`;
  }).join('');
}

// A suggested end time for a transient run with sliding-mesh zones: long enough for the wake to settle, not longer.
// The longest of: 5 revolutions of the fastest zone (the start-up of the rotation), 2 revolutions of the slowest zone
// (when the zones differ), and 3 flow-through times of the model (its length along the flow / the speed; none when the
// air is still). One time step is 0.5 degree of the fastest zone, so the cost is 720 steps per revolution of it.
function endTimeAdvice(p) {
  if (p.study !== 'external_aero' || p.settings.analysis !== 'transient') return null;
  const sl = (p.moving_zones || []).filter(z => z.method === 'sliding' && z.rpm);
  if (!sl.length) return null;
  const fast = sl.reduce((a, z) => Math.abs(z.rpm) > Math.abs(a.rpm) ? z : a), slow = sl.reduce((a, z) => Math.abs(z.rpm) < Math.abs(a.rpm) ? z : a);
  const rev = z => 60 / Math.abs(z.rpm);
  const bb = p.geometry?.info?.parts_bbox, ax = 'xyz'.indexOf(String(p.settings.forward_axis || '+x')[1]);
  const L = bb && ax >= 0 ? bb[ax + 3] - bb[ax] : 0, U = +p.settings.speed || 0;
  const parts = [[5 * rev(fast), `5 revolutions of ${fast.name} (${fmt(Math.abs(fast.rpm), 4)} rpm, ${fmt(rev(fast) * 1000, 3)} ms each)`]];
  if (sl.length > 1) parts.push([2 * rev(slow), `2 revolutions of ${slow.name} (${fmt(Math.abs(slow.rpm), 4)} rpm)`]);
  if (L > 0 && U > 0.5) parts.push([3 * L / U, `3 flow-through times of the model (${fmt(L * 1000, 4)} mm at ${fmt(U, 3)} m/s)`]);
  const best = parts.reduce((a, b) => b[0] > a[0] ? b : a);
  const t = +best[0].toPrecision(2) >= best[0] ? +best[0].toPrecision(2) : +(best[0] * 1.05).toPrecision(2);  // (two figures, rounded up)
  const steps = t * Math.abs(fast.rpm) * Math.PI / 30 / (0.5 * Math.PI / 180);
  return { t, why: best[1], steps, revs: t / rev(fast), cur: +p.settings.end_time || 0, curRevs: (+p.settings.end_time || 0) / rev(fast), fast };
}
function endTimeAdviceHtml(p) {
  const a = endTimeAdvice(p);
  if (!a) return '';
  const same = Math.abs(a.cur - a.t) <= 0.05 * a.t, long = a.cur > 3 * a.t;
  return `<div class="item-card" style="margin-top:6px"><div class="small"><b>Suggested end time: ${fmtTime(a.t)}</b>
    <span class="muted">(${fmt(a.revs, 3)} revolutions of ${esc(a.fast.name)}, about ${Math.round(a.steps).toLocaleString()} time steps)</span>
    ${same ? '<span class="muted"> · this is your end time</span>' : `<button class="ghost small" data-endtime="${a.t}" style="margin-left:6px" title="Set the end time to ${fmtTime(a.t)}">Use it</button>`}</div>
    <div class="small muted" style="margin-top:3px">Set by ${esc(a.why)}: the longest of 5 revolutions of the fastest zone, 2 of the slowest and 3 flow-through times of the model. The first revolutions are the start-up of the rotation and of the wake: average the forces over the last revolution only. Raise it for steadier averages (the cost grows in proportion).</div>
    ${long ? `<div class="small" style="margin-top:3px;color:#e9a23b">Your end time of ${fmtTime(a.cur)} is ${fmt(a.curRevs, 3)} revolutions of ${esc(a.fast.name)}: about ${Math.round(a.cur / a.t * a.steps).toLocaleString()} time steps.</div>` : ''}</div>`;
}

function panelSetup() {
  const p = S.p, spec = studySpec();
  if (p.geometry.status !== 'ready') return `<p class="muted">Import geometry first.</p>`;
  let h = `<h2>Setup</h2><details class="about" ${S.aboutOpen?.[p.study] === false ? '' : 'open'} data-about><summary>About this simulation: <b>${esc(spec.label)}</b></summary>
    <p class="explain">${esc(spec.explain || spec.description)}</p><p class="small muted" style="margin:3px 0">Solver: ${esc(spec.solver_label)}</p></details>`;
  if (Object.values(S.jobs).some(j => j?.status === 'running' && j.project === p.id))
    h += `<div class="note warn small">A job is running: changes apply to the next run.</div>`;
  const solids = p.body_list.filter(b => b.role === 'solid');
  if (solids.length) {
    h += `<h3>Parts & materials</h3><div class="list">`;
    const mats = S.meta.materials.solids;
    solids.forEach(b => {
      const i = p.body_list.indexOf(b);
      h += `<div class="item-card" data-body="${b.id}"><div class="head"><span class="swatch" style="background:${BODY_COLORS[i % BODY_COLORS.length]}"></span>
        <input type="text" class="name" data-bodyname value="${esc(b.name)}" style="border:0;padding:0;font-weight:600;background:transparent">
        <button class="ghost small" data-action="flashBody" title="Flash in 3D">✦</button>
        <button class="ghost small" data-action="toggleBody" title="Hide/show this part">${S.hiddenBodies.has(b.id) ? '🚫' : '👁'}</button></div>
        <div class="grid2"><label class="field"><span>Material</span><select data-bodymat>${Object.entries(mats).map(([k, m]) =>
          `<option value="${k}" ${k === b.material ? 'selected' : ''}>${esc(m.label)}</option>`).join('')}</select></label>
        ${spec.body_params.length ? `<label class="field"><span>Heat generated <span class="unit">[W]</span></span><input type="number" step="any" min="0" data-bodyheat value="${b.heat_W || 0}"></label>` : ''}</div>
        ${b.material_is_default ? `<div class="row small" style="margin-top:4px"><span class="muted">Default material –</span><button class="ghost small" data-action="confirmMat">✓ Use ${esc(b.material_label)}</button></div>` : ''}</div>`;
    });
    h += `</div>`;
  }
  h += `<h3>Boundary conditions</h3>`;
  const nat = p.study === 'cht_enclosure' && p.settings.air_flow === 'natural';
  h += `<p class="small muted">Other faces: ${nat ? (p.domain.enclosure_mode === 'auto_box' ? 'Adiabatic wall (automatic air box sides: open to the room)' : 'Adiabatic wall') : esc(spec.default_face_bc.label)}</p>`;
  if (p.study === 'cht_enclosure' && !nat) h += `<div class="row" style="margin-bottom:8px"><button class="ghost small" data-action="autoIO">✨ Auto-detect inlet & outlet</button></div>`;
  h += `<div class="list">`;
  for (const bc of p.bcs) {
    const t = spec.face_bc_types[bc.type];
    const active = bc.id === S.activeBc;
    if (bc.plane_id) {  // the condition of a symmetry plane: set with the plane (below), shown here greyed out and linked to it
      const pl = (p.symmetry || []).find(q => q.id === bc.plane_id);
      h += `<div class="item-card" data-bc="${bc.id}" data-bclinked="${esc(bc.plane_id)}" style="opacity:.55" title="Set by the symmetry plane below: move, flip or remove the plane there">
        <div class="head"><span class="swatch" style="background:${t?.color}"></span><span class="name" style="font-weight:600">${esc(bc.name)}</span>
          <span class="muted small">🔗 plane · ${bc.faces.length} face${bc.faces.length === 1 ? '' : 's'}</span></div>
        <div class="muted small">${esc(t?.label)} · connected to <b>${esc(pl ? pl.name : 'its plane')}</b> in Symmetry planes below (change it there)</div></div>`;
      continue;
    }
    h += `<div class="item-card ${active ? 'active' : ''}" data-bc="${bc.id}">
      <div class="head"><span class="swatch" style="background:${t?.color}"></span>
        <input type="text" class="name" data-bcname value="${esc(bc.name)}" style="border:0;padding:0;font-weight:600;background:transparent">
        ${bc.plane_id ? `<span class="muted small" title="The faces follow the plane: move or flip the plane below">plane · ${bc.faces.length} face${bc.faces.length === 1 ? '' : 's'}</span>`
          : `<button class="${active ? '' : 'ghost'} small" data-action="pickBc">${active ? 'Picking…' : `Faces (${bc.faces.length})`}</button>`}
        <button class="danger small" data-action="delBc" title="Delete">✕</button></div>
      <div class="muted small" title="${esc(t?.help || '')}">${esc(t?.label)} ⓘ</div>
      ${t?.params.length ? `<div class="grid2">${paramInputs(t.params, bc.params, 'data-bcparam')}</div>` : ''}</div>`;
  }
  h += `</div><div class="row" style="margin-top:8px"><select id="newBcType"><option value="">＋ Add boundary condition…</option>
    ${Object.entries(spec.face_bc_types).map(([k, t]) => `<option value="${k}">${esc(t.label)}</option>`).join('')}</select></div>`;

  h += symmetryHtml();
  if (p.study === 'external_aero') {
    h += `<h3>Rotors</h3><p class="small muted">Blank thrust = carries the weight.</p><div class="list">`;
    p.rotors.forEach((r, i) => {
      h += `<div class="item-card" data-rotor="${i}"><div class="head"><span class="swatch" style="background:#ff8a00"></span>
        <span class="name">Rotor ${i + 1}</span><span class="muted small">(${r.center.map(c => fmt1(c * 1000, 0)).join(', ')}) mm</span>
        <button class="danger small" data-action="delRotor">✕</button></div>
        <div class="grid2"><label class="field"><span>Radius <span class="unit">[mm]</span></span><input type="number" step="any" data-rotor-radius value="${fmt1(r.radius * 1000)}"></label>
        <label class="field"><span>Thrust <span class="unit">[N]</span></span><input type="number" step="any" data-rotor-thrust placeholder="auto" value="${r.thrust_N ?? ''}"></label></div>
        <p class="small" style="margin:4px 0"><button class="${S.axisPick?.kind === 'rotor' && S.axisPick.index === i ? '' : 'ghost'} small" data-action="pickAxis" data-kind="rotor"
          title="Click a cylindrical face (a motor shaft or can) or near a circular edge: the disc's centre and axis are put on it">⌖ Centre on a cylinder or circular edge…</button></p></div>`;
    });
    h += `</div><div class="row" style="margin-top:8px"><button class="ghost small" data-action="detectRotors">✨ Detect on motor tops</button>
      ${S.p.rotors.some(r => r.thrust_N != null) ? `<button class="ghost small" data-action="autoThrust" title="Thrusts carry the weight at the set pitch">Reset thrusts to automatic</button>` : ''}</div>`;
  }
  if (p.study === 'external_aero' || p.study === 'cht_enclosure') h += movingZonesHtml();
  if (spec.settings.length) {
    h += `<h3>${p.study === 'external_aero' ? 'Flow condition' : p.study === 'vibration_modal' ? 'Modes and vibration source' : (p.study === 'aeroelastic_flutter' || p.study === 'aeroelastic_flutter_cfd') ? 'Air flow and modes' : p.study === 'static_structural' ? 'Loads' : 'Flow & solver settings'}</h3>
      <div class="grid2">${paramInputs(spec.settings.filter(q => q.key !== 'n_procs'), p.settings, 'data-setting')}</div>`;  // (cores: Solve step)
    if (p.study === 'external_aero') h += endTimeAdviceHtml(p) + aeroInfo();
  }
  h += checklist();
  h += `<p style="margin-top:14px"><button class="big" data-goto="mesh">Next: mesh →</button></p>`;
  return h;
}

// Apply the previewed orientation (the geometry is rebuilt); `then`: the step to go to once it is requested
async function applyOrientation(then) {
  const M = S.rotPending; S.rotPending = null; viewer.setPreviewRotation(null); alignReset();
  try {
    const r = await api(P('/rotate'), { json: { matrix: M } });
    S.jobs.prepare = r.job; toast('Orientation applied: rebuilding the geometry…');
  } catch (e) { toast(e.message, true, 9000); }
  if (then) { S.step = then; if (then !== 'geometry') S.activeBc = null; }
  renderAll();
}
// ---- centre of rotation from a round feature (a cylindrical face or a circular edge)
// circular edges are drawn and pickable while a centre of rotation is being picked
async function refreshEdgePick() {
  if (!S.axisPick || S.step !== 'setup') { viewer.setEdgePick(null); return; }
  try {
    const edges = (await api(P('/circular_edges'))).edges;
    if (!S.axisPick) return;
    viewer.setEdgePick(edges, e => axisPicked({ kind: 'edge', center: e.center, axis: e.axis, radius: e.radius, length: null, nearest: e.nearest, angle_deg: e.angle_deg, face: e.faces[0] }));
    if (!edges.length) toast('No circular edges on this model: click a cylindrical face instead');
  } catch (e) { toast(e.message, true); }
}
function axisObliqueHtml() {
  const o = S.axisOblique;
  if (!o) return '';
  return `<div class="note warn small" style="margin:4px 0">The ${o.kind === 'cylinder' ? 'cylinder' : 'edge'} you picked has its axis ${fmt(o.angle_deg, 3)}° away from the ${o.nearest.slice(1)} axis, and a
    rotating zone has to lie along x, y or z. Turn the whole model so that this axis lies along ${o.nearest.replace('-', '−')}?
    <div class="row" style="margin-top:4px;gap:6px"><button class="small" data-action="axisTurn">Turn the model</button><button class="ghost small" data-action="axisKeep">Leave it</button></div></div>`;
}
async function axisPicked(info) {
  const ap = S.axisPick;
  if (!ap) return;
  const c = info.center, mm = v => v.map(x => fmt1(x * 1000)).join(', ');
  viewer.setAxisMarker({ center: c, axis: info.axis, radius: info.radius, length: info.length });
  setTimeout(() => { if (!S.axisOblique) viewer.setAxisMarker(null); }, 9000);
  S.axisPick = null; viewer.setEdgePick(null); updateSelectHint(); refreshViewerColors();
  if (ap.kind === 'zone') {
    if (info.angle_deg > 0.5) { S.axisOblique = info; renderPanel(); return; }
    const zs = (S.p.moving_zones || []).map(z => ({ ...z, center: [...z.center] })), z = zs[ap.index];
    if (!z) return;
    const letter = info.nearest[1];
    z.axis = z.axis[1] === letter ? z.axis : '+' + letter;  // (keeps the sense of rotation when the axis is the same line)
    z.center = c.slice();
    await saveZones(zs);
    toast(`${z.name}: centre (${mm(c)}) mm, axis ${z.axis} - from the ${info.kind === 'cylinder' ? 'cylinder' : 'circular edge'} (radius ${fmt1(info.radius * 1000)} mm)`);
  } else {
    const rot = S.p.rotors.map(r => ({ ...r })), r = rot[ap.index];
    if (!r) return;
    const cur = r.axis || [0, 0, 1], flip = cur[0] * info.axis[0] + cur[1] * info.axis[1] + cur[2] * info.axis[2] < 0;
    r.center = c.slice(); r.axis = flip ? info.axis.map(x => -x) : info.axis;
    S.p = await api(P('/rotors'), { json: { rotors: rot } }); renderAll(); viewer.setRotors(S.p.rotors, true);
    toast(`Rotor ${ap.index + 1}: centre (${mm(c)}) mm, axis ${r.axis.map(x => fmt1(x, 3)).join(', ')}`);
  }
}

// ---- align a face: click a flat face, choose the plane to lay it on (a preview turn, then Apply)
function alignRotation(n, t) {  // the shortest turn taking the unit vector n onto t (mirrors simwb/orient.py)
  const dot = n[0] * t[0] + n[1] * t[1] + n[2] * t[2];
  if (dot > 1 - 1e-12) return [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  const I = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  if (dot < -1 + 1e-12) {  // opposite: a half turn about an axis normal to n
    const k = n.map(Math.abs).indexOf(Math.min(...n.map(Math.abs))), e = [0, 0, 0]; e[k] = 1;
    let v = [n[1] * e[2] - n[2] * e[1], n[2] * e[0] - n[0] * e[2], n[0] * e[1] - n[1] * e[0]];
    const l = Math.hypot(...v); v = v.map(x => x / l);
    return I.map((r, i) => r.map((_, j) => 2 * v[i] * v[j] - I[i][j]));
  }
  const v = [n[1] * t[2] - n[2] * t[1], n[2] * t[0] - n[0] * t[2], n[0] * t[1] - n[1] * t[0]], sn = Math.hypot(...v);
  const K = [[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]].map(r => r.map(x => x / sn));
  const K2 = K.map((r, i) => r.map((_, j) => K[i][0] * K[0][j] + K[i][1] * K[1][j] + K[i][2] * K[2][j]));
  return I.map((r, i) => r.map((x, j) => x + sn * K[i][j] + (1 - dot) * K2[i][j]));
}
const matVec = (R, v) => R.map(r => r[0] * v[0] + r[1] * v[1] + r[2] * v[2]);
const matMul = (A, B) => A.map(r => [0, 1, 2].map(k => r[0] * B[0][k] + r[1] * B[1][k] + r[2] * B[2][k]));
function updateAlignMarker() {
  const a = S.align, bb = partsBox();
  if (!a || !bb || S.step !== 'geometry') { viewer.setAlignMarker(null); return; }
  const R = S.rotPending || a.base, c = partsCenter();
  const rel = matVec(R, [a.point[0] - c[0], a.point[1] - c[1], a.point[2] - c[2]]);
  viewer.setAlignMarker({ point: [rel[0] + c[0], rel[1] + c[1], rel[2] + c[2]], dir: matVec(R, a.normal),
    length: 0.35 * Math.max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]) });
}
function alignTo(to) {
  const a = S.align, v = { '+x': [1, 0, 0], '-x': [-1, 0, 0], '+y': [0, 1, 0], '-y': [0, -1, 0], '+z': [0, 0, 1], '-z': [0, 0, -1] }[to];
  if (!a || !v) return;
  a.target = to;
  S.rotPending = matMul(alignRotation(matVec(a.base, a.normal), v), a.base);
  viewer.setPreviewRotation(S.rotPending, partsCenter());
  updateAlignMarker(); renderPanel();
}
function alignReset() {  // leave the alignment: forget the faces, put the air-box display back as it was
  S.align = null; S.alignPick = false; S.alignWallPick = false;
  if (S.showAirSaved != null) { S.showAir = S.showAirSaved; S.showAirSaved = null; $('#optAir').checked = S.showAir; viewer.setShowAir(S.showAir); }
  updateAlignMarker(); refreshViewerColors(); updateSelectHint();
}
function startRefPick() {  // the next click picks the plane: the tunnel walls are shown (flow and cooling studies)
  S.alignWallPick = true;
  if (S.faces.some(f => ['farfield', 'enclosure'].includes(f.kind))) {
    if (S.showAirSaved == null) S.showAirSaved = !!S.showAir;
    S.showAir = true; $('#optAir').checked = true; viewer.setShowAir(true);
  }
  updateSelectHint();
}
function alignToWall(away) {  // the face lies against the tunnel wall: its outward normal along the wall's (or against it)
  const a = S.align, w = a?.wall;
  if (!w) return;
  a.target = null; a.away = !!away;
  const t = away ? w.normal.map(x => -x) : w.normal;
  S.rotPending = matMul(alignRotation(matVec(a.base, a.normal), t), a.base);
  viewer.setPreviewRotation(S.rotPending, partsCenter());
  updateAlignMarker(); renderPanel();
}
function wallName(w) {
  if (w.kind === 'face') return `face ${w.face}`;
  const d = w.nearest, base = { '-z': 'floor', '+z': 'ceiling' }[d] || `wall facing ${d.replace('-', '−')}`;
  return w.role === 'inlet' || w.role === 'outlet' ? `${w.role} (${base})` : base;
}
function alignHtml() {
  const a = S.align;
  if (!a) return S.alignPick ? '<p class="small muted">Click a flat face in the 3D view…</p>' : '';
  const nm = t => t.replace('-', '−');
  const row = (plane, opts) => `<div class="row small" style="gap:4px;margin:2px 0"><span style="width:62px">${plane}</span>${opts.map(([t, lbl]) =>
    `<button class="${a.target === t ? '' : 'ghost'} small" data-action="alignTo" data-to="${t}" title="Lay the face on the ${plane}, facing ${nm(t)}">${lbl}${a.nearest === t ? ' ★' : ''}</button>`).join('')}</div>`;
  return `<div class="note" style="margin:4px 0"><b>Face ${a.face}</b> (${fmt1(a.area_m2 * 1e6, 0)} mm²) points ${nm(a.nearest)} now (★ = nearest).
    Choose the plane to lay it on:
    ${row('XY plane', [['+z', 'facing up +z'], ['-z', 'facing down −z']])}${row('YZ plane', [['+x', 'facing +x'], ['-x', 'facing −x']])}${row('XZ plane', [['+y', 'facing +y'], ['-y', 'facing −y']])}
    <div class="small" style="margin-top:6px">… or align it to a plane in the 3D view (a tunnel wall or another flat face):
      <button class="${a.wall ? '' : 'ghost'} small" data-action="alignWall">${S.alignWallPick ? 'Click the plane in the 3D view…' : a.wall ? 'Pick another plane' : '🖱 Pick the plane'}</button></div>
    ${a.wall ? `<div class="row small" style="gap:4px;margin:3px 0"><span>Plane: <b>${esc(wallName(a.wall))}</b></span>
        <button class="${a.away ? 'ghost' : ''} small" data-action="alignToWall" data-away="0" title="${a.wall.kind === 'face' ? 'The face points the same way as the plane' : 'The face lies against the wall'}">${a.wall.kind === 'face' ? 'same way' : 'facing the wall'}</button>
        <button class="${a.away ? '' : 'ghost'} small" data-action="alignToWall" data-away="1" title="${a.wall.kind === 'face' ? 'The face points the opposite way: face to face' : 'The face looks away from the wall'}">${a.wall.kind === 'face' ? 'face to face' : 'facing away'}</button></div>` : ''}
    <div class="row" style="margin-top:4px"><button class="ghost small" data-action="alignDone">Done</button></div></div>`;
}

// ---- orientation gizmo: rings around the model that turn it (the preview turns about the middle of the parts)
function partsCenter() {  // the point a turn pivots on (the centre of mass of the parts, as the server uses)
  const pv = S.p?.geometry?.pivot;
  if (pv) return pv;
  const bb = partsBox();
  return bb ? [0, 1, 2].map(i => (bb[i] + bb[i + 3]) / 2) : [0, 0, 0];
}
function rotAbout(a, t) {  // right-handed turn by t about x (0), y (1) or z (2)
  const c = Math.cos(t), s = Math.sin(t), [i, j] = [[1, 2], [2, 0], [0, 1]][a];
  const m = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  m[i][i] = c; m[i][j] = -s; m[j][i] = s; m[j][j] = c;
  return m;
}
function refreshRotateGizmo() {
  const bb = partsBox(), on = S.p && S.step === 'geometry' && S.p.geometry.status === 'ready' && S.rotGizmo !== false && bb
    && !Object.values(S.jobs).some(j => j?.status === 'running');
  if (!on) { viewer.setRotateGizmo(null); return; }
  const base = { R: null };
  viewer.setRotateGizmo({
    center: partsCenter(), size: Math.max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]),
    onRotate: (axis, ang, final) => {
      if (ang === 0 && !final) base.R = S.rotPending || [[1, 0, 0], [0, 1, 0], [0, 0, 1]];   // a drag starts
      const st = rotAbout(axis, ang), R0 = base.R || [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
      S.rotPending = st.map(r => [0, 1, 2].map(k => r[0] * R0[0][k] + r[1] * R0[1][k] + r[2] * R0[2][k]));
      viewer.setPreviewRotation(S.rotPending, partsCenter()); updateAlignMarker();
      const info = $('#rotInfo');
      info.textContent = `Turning about ${'xyz'[axis]}: ${(ang * 180 / Math.PI).toFixed(1)}°`;
      info.classList.remove('hidden');
      if (final) { info.classList.add('hidden'); renderPanel(); }
    },
  });
}

// ---- symmetry planes: cut the model at a mirror plane and solve one side (the results show the mirror image)
function planeAxis(pl) {  // the axis a plane is normal to (-1: oblique)
  const a = pl.normal.map(Math.abs).indexOf(Math.max(...pl.normal.map(Math.abs)));
  return Math.abs(pl.normal[a]) > 0.9999 ? a : -1;
}
function partsBox() {
  const info = S.p?.geometry?.info || {};
  return info.parts_bbox || info.bbox || null;
}
// CAD edits made through the assistant (add / cut shapes, holes, fillets ...): kept as a list that is replayed on the imported
// file, so each one can be removed again
function cadEditsHtml() {
  const edits = S.p.edits || [], busy = Object.values(S.jobs).some(j => j?.status === 'running');
  let h = `<h3>CAD edits${tip('Changes to the model made by the assistant (ask it in the chat: "drill a 6 mm hole through the plate", "round the edges", "mirror the wing"). They are replayed on the imported file every time the geometry is built, so any of them can be removed again; the file itself is never changed. Faces get new numbers after an edit: check boundary conditions that sit on edited faces.')}</h3>`;
  if (!edits.length) return h + `<p class="small muted" style="margin:2px 0 6px">None. Ask the assistant to edit the model (holes, fillets, cuts, mirrored or patterned copies, new shapes).</p>`;
  h += `<div class="list">`;
  for (const e of edits) {
    h += `<div class="item-card"><div class="head"><span class="muted small">${e.index}</span>
      <span class="name small" style="font-family:monospace;font-weight:500;white-space:normal;word-break:break-word">${esc(e.text)}</span>
      <button class="danger small" data-cadedit="remove" data-n="${e.index}" ${busy ? 'disabled' : ''} title="Remove this edit (the ones after it are replayed without it)">✕</button></div></div>`;
  }
  h += `</div><div class="row" style="margin-top:6px"><button class="ghost small" data-cadedit="remove" data-n="${edits.length}" ${busy ? 'disabled' : ''}>↶ Undo last</button>
    <button class="ghost small" data-cadedit="clear" ${busy ? 'disabled' : ''} title="Remove every edit: back to the file as imported">Remove all</button></div>`;
  return h;
}
async function cadEdit(body) {
  try {
    const r = await api(P('/geometry/edits'), { json: body });
    if (r.job) { S.jobs.prepare = r.job; toast('Rebuilding the geometry…'); }
    S.p = await api(P(''));
    renderAll();
  } catch (e) { toast(e.message, true, 9000); }
}
function bindCadEdits(el) {
  el.querySelectorAll('[data-cadedit]').forEach(b => b.onclick = () => {
    const k = b.dataset.cadedit;
    if (k === 'clear') cadEdit({ action: 'clear' });
    else cadEdit({ action: 'remove', number: +b.dataset.n });
  });
}
function symmetryHtml() {
  const p = S.p, busy = Object.values(S.jobs).some(j => j?.status === 'running');
  const planes = p.symmetry || [], d = S.symDraft;
  const fx = v => String(+(v * 1000).toFixed(3));
  let h = `<h3>Symmetry planes${tip('A mirror plane cuts the air volume: only the side you keep is meshed and solved (half the cells for one plane), and the results can show the mirror image again. Use it when the shape AND the loads or the flow are mirror-symmetric. Add a plane, drag it with the round handle in the 3D view (or type its position), flip it to keep the other side, then press Set: everything on the other side is removed.')}</h3>`;
  h += `<p class="small muted" style="margin:2px 0 6px">Cut a symmetric model in half (or a quarter): fewer cells, the same answer.</p><div class="list">`;
  for (const pl of planes) {
    const a = planeAxis(pl), side = a >= 0 ? `${pl.normal[a] > 0 ? '+' : '−'}${'xyz'[a]}` : 'oblique';
    const linked = (p.bcs || []).find(b => b.plane_id === pl.id);
    h += `<div class="item-card" data-symplane="${esc(pl.id)}"><div class="head"><span class="swatch" style="background:#4cc9f0"></span>
      <span class="name">${esc(pl.name)}</span><span class="muted small">keeps ${side}</span>
      <button class="ghost small" data-sym="flip" data-id="${esc(pl.id)}" ${busy ? 'disabled' : ''} title="Keep the other side of the plane">⇄ Flip</button>
      <button class="danger small" data-sym="remove" data-id="${esc(pl.id)}" ${busy ? 'disabled' : ''} title="Remove the plane: the removed side comes back">✕</button></div>
      ${linked ? `<div class="muted small" style="margin:2px 0 4px">🔗 Its boundary condition (symmetry, ${linked.faces.length} face${linked.faces.length === 1 ? '' : 's'}) is listed under Boundary conditions, greyed out: it follows this plane.</div>` : ''}
      ${a >= 0 ? `<div class="grid2"><label class="field"><span>Position ${'xyz'[a]} <span class="unit">[mm]</span></span><input type="number" step="any" data-sympos="${esc(pl.id)}" value="${fx(pl.point[a])}" ${busy ? 'disabled' : ''}></label></div>` : ''}
      ${p.study === 'external_aero' ? `<label class="small" style="display:block;margin-top:4px" title="Off: the plane only ends the air volume, and the model stays whole (a part that crosses the plane is only meshed on the kept side anyway). On: the part of the model beyond the plane is deleted from the geometry too."><input type="checkbox" data-symcut="${esc(pl.id)}" ${pl.cut_parts ? 'checked' : ''} ${busy ? 'disabled' : ''}> Also delete the model beyond the plane</label>` : ''}</div>`;
  }
  if (d) {
    h += `<div class="item-card active" data-symdraft><div class="head"><span class="swatch" style="background:#ffb703"></span><span class="name">New plane</span>
      <span class="muted small">keeps ${d.keep > 0 ? '+' : '−'}${d.axis}</span>
      <button class="ghost small" data-sym="flipdraft" title="Keep the other side">⇄ Flip</button><button class="danger small" data-sym="cancel" title="Discard">✕</button></div>
      <div class="grid2"><label class="field"><span>Normal to</span><select data-symdraftaxis>${['x', 'y', 'z'].map(x => `<option ${x === d.axis ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
      <label class="field"><span>Position <span class="unit">[mm]</span></span><input type="number" step="any" data-symdraftpos value="${fx(d.pos)}"></label></div>
      ${p.study === 'external_aero'
        ? `<p class="small" style="margin:4px 0;color:#e63946">The air volume ends at the plane (the red side is left out of it). The model itself stays whole unless you tick the box. Drag the round handle in the 3D view, or type the position.</p>
      <label class="small" style="display:block;margin:2px 0 6px"><input type="checkbox" data-symdraftcut ${d.cutParts ? 'checked' : ''}> Also delete the model beyond the plane</label>`
        : `<p class="small" style="margin:4px 0;color:#e63946">The red side will be removed from the model. Drag the round handle in the 3D view, or type the position.</p>`}
      <button class="big small" data-sym="set" ${busy ? 'disabled' : ''}>${p.study === 'external_aero' ? 'Set plane: trim the air volume' : 'Set plane: cut the model'}</button></div>`;
  }
  h += `</div><div class="row" style="margin-top:6px;flex-wrap:wrap">${['x', 'y', 'z'].map(a => `<button class="ghost small" data-sym="add" data-axis="${a}" ${busy || planes.length >= 3 ? 'disabled' : ''}>＋ Plane ⟂ ${a}</button>`).join('')}
    <button class="ghost small" data-sym="face" ${busy || planes.length >= 3 ? 'disabled' : ''} title="Click a flat face of the model: the plane goes through it">＋ From a face</button></div>`;
  if (planes.length) h += `<p class="small muted" style="margin-top:6px">${planes.length === 1 ? 'Half' : planes.length === 2 ? 'A quarter' : 'An eighth'} of the model is solved. ${p.study === 'external_aero' ? 'Forces and moments in the results are those of the whole body. The oncoming flow must run along the plane (no sideslip).' : 'Loads and heat sources you enter act on the kept part only.'} The Results step can show the mirrored half.</p>`;
  return h;
}
function symItems() {
  const p = S.p, bb = partsBox();
  if (!p || !bb || p.geometry.status !== 'ready' || !['geometry', 'setup'].includes(S.step)) return [];
  const c = [0, 1, 2].map(i => (bb[i] + bb[i + 3]) / 2), size = 1.5 * Math.max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]);
  const onPlane = (pt, n) => { const dd = c.reduce((a, v, i) => a + (v - pt[i]) * n[i], 0); return c.map((v, i) => v - dd * n[i]); };  // the centre of the parts, moved onto the plane
  const items = (p.symmetry || []).map(pl => ({ id: pl.id, normal: pl.normal, point: onPlane(pl.point, pl.normal), size }));
  if (S.symDraft) {
    const d = S.symDraft, n = [0, 0, 0], pt = c.slice(), a = 'xyz'.indexOf(d.axis);
    n[a] = d.keep; pt[a] = d.pos;
    items.push({ id: 'draft', normal: n, point: pt, size, draft: true });
  }
  return items;
}
function refreshSymmetryEditor() { viewer.setSymmetryEditor(symItems(), symDragged); }
function symDragged(id, pt, final) {
  if (id === 'draft') {
    const a = 'xyz'.indexOf(S.symDraft.axis); S.symDraft.pos = pt[a];
    const inp = $('[data-symdraftpos]'); if (inp) inp.value = String(+(pt[a] * 1000).toFixed(3));
    if (final) refreshSymmetryEditor();
    return;
  }
  if (!final) return;
  const pl = (S.p.symmetry || []).find(q => q.id === id);
  if (!pl) return;
  const a = planeAxis(pl);
  symApply(a >= 0 ? { action: 'update', id, pos_mm: pt[a] * 1000 } : { action: 'update', id, point: pt });
}
async function symApply(body) {
  try {
    const r = await api(P('/symmetry'), { json: body });
    if (r.job) { S.jobs.prepare = r.job; toast('Rebuilding the geometry with the plane…'); }
    else S.p = await api(P(''));
    renderAll();
  } catch (e) { toast(e.message, true, 9000); refreshSymmetryEditor(); renderPanel(); }
}
function bindSymmetry(el) {
  const draftAt = axis => {
    const bb = partsBox();
    return { axis, pos: bb ? (bb['xyz'.indexOf(axis)] + bb['xyz'.indexOf(axis) + 3]) / 2 : 0, keep: 1 };
  };
  el.querySelectorAll('[data-sym]').forEach(b => b.onclick = async () => {
    const k = b.dataset.sym;
    if (k === 'add') { S.symDraft = draftAt(b.dataset.axis); renderPanel(); refreshSymmetryEditor(); viewer.fit?.(); }
    else if (k === 'face') { S.symPick = true; S.activeBc = null; updateSelectHint(); refreshViewerColors(); toast('Click a flat face of the model'); }
    else if (k === 'cancel') { S.symDraft = null; renderPanel(); refreshSymmetryEditor(); }
    else if (k === 'flipdraft') { S.symDraft.keep *= -1; renderPanel(); refreshSymmetryEditor(); }
    else if (k === 'set') {
      const d = S.symDraft; S.symDraft = null;
      await symApply({ action: 'add', axis: d.axis, pos_mm: d.pos * 1000, keep: d.keep > 0 ? '+' : '-', cut_parts: !!d.cutParts });
    } else if (k === 'flip') await symApply({ action: 'update', id: b.dataset.id, flip: true });
    else if (k === 'remove') await symApply({ action: 'remove', id: b.dataset.id });
  });
  const ax = $('[data-symdraftaxis]', el);
  if (ax) ax.onchange = () => { const old = S.symDraft; S.symDraft = { ...draftAt(ax.value), keep: old.keep, cutParts: old.cutParts }; renderPanel(); refreshSymmetryEditor(); };
  const dc = $('[data-symdraftcut]', el);
  if (dc) dc.onchange = () => { S.symDraft.cutParts = dc.checked; };
  el.querySelectorAll('[data-symcut]').forEach(c => c.onchange = () => symApply({ action: 'update', id: c.dataset.symcut, cut_parts: c.checked }));
  const dp = $('[data-symdraftpos]', el);
  if (dp) dp.onchange = () => { const v = parseFloat(dp.value); if (Number.isFinite(v)) { S.symDraft.pos = v / 1000; refreshSymmetryEditor(); } };
  el.querySelectorAll('[data-sympos]').forEach(i => i.onchange = () => {
    const v = parseFloat(i.value);
    if (Number.isFinite(v)) symApply({ action: 'update', id: i.dataset.sympos, pos_mm: v });
  });
}

// ---- moving zones (rotating cylinders of air: propellers, fans, wheels)
const MZ_HELP = {
  mrf: 'MRF (moving reference frame): use with Analysis = steady. The mesh does NOT rotate: the air inside the zone is '
    + 'solved in a frame turning with the part, which gives its torque and the flow it drives for one blade position. Fast - '
    + 'the usual choice for fans and propellers. To see the part rotate, use Sliding mesh.',
  sliding: 'Sliding mesh: transient. The cells inside the zone really rotate; the zone boundary slides along the outer '
    + 'mesh (two AMI patches). Shows blade passing and unsteady wakes; needs Analysis = transient and much more time.',
};
const ZONE_CELLS = { quick: 36, coarse: 48, medium: 64, fine: 96, very_fine: 128 };  // = gmsh_worker.ZONE_CELLS
function zonePartFaces(part) {
  return S.faces.filter(f => (f.part === part || (!f.part && part === '(body)')) && f.centroid && f.area > 0);
}
function zonePartBox(part) {
  const v = S.faces.filter(f => f.part === part || (!f.part && part === '(body)')).flatMap(f => Array.from(f.mesh?.vertices || []));
  const pts = []; for (let i = 0; i < v.length; i += 3) pts.push([v[i], v[i + 1], v[i + 2]]);
  return pts;
}
function movingZonesHtml() {
  const p = S.p, zs = p.moving_zones || [];
  const parts = [...new Set(S.faces.filter(f => !['farfield', 'enclosure'].includes(f.kind)).map(f => f.part || '(body)'))]
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const num = (i, k, v, unit, label, step = 'any') => `<label class="field"><span>${label}${unit ? ` <span class="unit">[${unit}]</span>` : ''}</span>
    <input type="number" step="${step}" data-mz="${k}" value="${v}"></label>`;
  let h = `<h3>Moving zones</h3><p class="small muted" style="margin:4px 0">A cylinder of air that turns with a propeller, fan, rotor or wheel inside
    it. Put the whole rotating part inside the zone, and keep the zone clear of the parts that do not rotate. The zone must turn about the real
    axis of the part: use <b>Centre on a cylinder or circular edge</b> to put its centre and axis on a shaft, a hub bore or a round edge.</p>
    ${axisObliqueHtml()}<div class="list">`;
  zs.forEach((z, i) => {
    h += `<div class="item-card" data-mzone="${i}"><div class="head"><span class="swatch" style="background:#9b6cff"></span>
      <input type="text" class="name" data-mz="name" value="${esc(z.name)}" style="border:0;padding:0;font-weight:600;background:transparent">
      <span class="muted small">${fmt(z.rpm)} rpm</span><button class="danger small" data-action="delZone" title="Delete">✕</button></div>
      <div class="grid2"><label class="field"><span>Method</span><select data-mz="method"><option value="mrf" ${z.method === 'mrf' ? 'selected' : ''}>MRF (steady)</option>
        ${p.study === 'external_aero' ? `<option value="sliding" ${z.method === 'sliding' ? 'selected' : ''}>Sliding mesh (transient)</option>` : ''}</select></label>
        <p class="explain">${esc(MZ_HELP[z.method])}</p>
        ${num(i, 'rpm', z.rpm, 'rpm', 'Speed (+ = right-hand about the axis)')}
        <label class="field"><span>Axis</span><select data-mz="axis">${['+x', '-x', '+y', '-y', '+z', '-z'].map(a => `<option ${a === z.axis ? 'selected' : ''}>${a}</option>`).join('')}</select></label>
        ${num(i, 'cx', fmt1(z.center[0] * 1000), 'mm', 'Centre x')}${num(i, 'cy', fmt1(z.center[1] * 1000), 'mm', 'Centre y')}${num(i, 'cz', fmt1(z.center[2] * 1000), 'mm', 'Centre z')}
        ${num(i, 'radius', fmt1(z.radius * 1000), 'mm', 'Radius')}${num(i, 'length', fmt1(z.length * 1000), 'mm', 'Length along the axis')}
        <label class="field"><span>Cell size inside <span class="unit">[mm]</span></span><input type="number" step="any" data-mz="cell_size"
          placeholder="auto" value="${z.cell_size ? fmt(z.cell_size * 1000) : ''}" title="Finer cells round the rotating part (blades, tip vortices). Empty = automatic"></label>
        <label class="field"><span>Fit around part</span><select data-mzfit><option value="">choose…</option>${parts.map(n => `<option>${esc(n)}</option>`).join('')}</select></label></div>
      <p class="small" style="margin:4px 0"><button class="${S.axisPick?.kind === 'zone' && S.axisPick.index === i ? '' : 'ghost'} small" data-action="pickAxis" data-kind="zone"
        title="Click a cylindrical face (a shaft, a hub bore) or near a circular edge on the model: the zone's centre and axis are put exactly on it">⌖ Centre on a cylinder or circular edge…</button></p></div>`;
  });
  h += `</div><p class="row" style="margin-top:6px"><button class="ghost small" data-action="addZone">＋ Add rotating zone</button></p>`;
  if (zs.length) h += `<p class="small muted">Changing a zone needs a new mesh (its boundary is meshed exactly).</p>`;
  return h;
}
function zonesForViewer() {
  return (S.p?.moving_zones || []).map(z => ({ ...z, axisVec: AXIS_VEC[z.axis] }));
}
async function saveZones(zs) {
  S.p = await api(P('/moving_zones'), { json: { zones: zs } });
  renderAll(); viewer.setMovingZones(zonesForViewer(), true);
}

function aeroInfo() {
  const s = S.p.settings, n = S.p.rotors.length;
  if (!n) return '';
  const W = s.mass_kg * 9.81, cth = Math.max(Math.cos(s.pitch_deg * Math.PI / 180), 0.2);
  const tot = S.p.rotors.reduce((a, r) => a + (r.thrust_N ?? W / cth / n), 0);
  return `<div class="note small" style="margin-top:8px">Weight ${fmt(W)} N · rotor thrust ${fmt(tot)} N</div>`;
}

function checklist() {
  const c = S.p.checks || [];
  return `<h3>Readiness</h3><div class="checks">${c.map(x => `<div class="check ${x.level}" ${x.target ? `data-go="${esc(`${x.step}|${x.target}`)}" style="cursor:pointer" title="Click to go to the input that fixes this"` : ''}><span class="dot"></span><span>${esc(x.message)}</span></div>`).join('')}</div>`;
}

// ---- mesh
// part sizing (as Ansys face / body sizing): a cell size on all the faces of a part, growing smoothly into the air
function partFaces(part) {
  return S.faces.filter(f => !['farfield', 'enclosure'].includes(f.kind) && (f.part || '(body)') === part).map(f => f.id);
}
function meshPartSizingHtml() {
  const rs = S.p.mesh.refinements || [];
  const parts = [...new Set(S.faces.filter(f => !['farfield', 'enclosure'].includes(f.kind)).map(f => f.part || '(body)'))];
  return `<details class="expert" ${rs.length ? 'open' : ''}><summary>Part sizing (${rs.length})</summary>
    <p class="small muted" style="margin:4px 0">A cell size on the surface of a part (e.g. the propeller blades), growing to the
      surrounding size within about ten cells. Cheaper than a box round the part: only the cells near its surface are small.</p>
    ${rs.map((r, i) => `<div class="item-card"><div class="head"><select data-psz="${i}" data-pk="part" style="flex:1">
        ${parts.map(n => `<option ${n === r.part ? 'selected' : ''}>${esc(n)}</option>`).join('')}${r.part ? '' : '<option selected>(selected faces)</option>'}</select>
        <button class="danger small" data-action="delPartSize" data-i="${i}" title="Delete">✕</button></div>
      <label class="field"><span>Surface cell size <span class="unit">[mm]</span></span><input type="number" step="any" min="0" data-psz="${i}" data-pk="size" value="${fmt(r.size * 1000)}"></label></div>`).join('')}
    <button class="ghost small" data-action="addPartSize" ${parts.length ? '' : 'disabled'}>+ Part sizing</button></details>`;
}
async function savePartSizing(rs) {
  S.p = await api(P('/mesh/settings'), { json: { refinements: rs.map(r => ({ faces: r.faces, size_mm: r.size * 1000, part: r.part || null })) } });
  renderPanel();
}

function meshRegionsHtml() {
  const rs = S.p.mesh.regions || [];
  const parts = [...new Set(S.faces.filter(f => !['farfield', 'enclosure'].includes(f.kind)).map(f => f.part || '(body)'))];
  const n = (i, k, j, v) => `<input type="number" step="any" data-region="${i}" data-rk="${k}" data-rj="${j}" value="${fmt1(v * 1000)}" style="width:100%">`;
  const cyl = r => r.shape === 'cylinder';
  return `<details class="expert" ${rs.length ? 'open' : ''}><summary>Refinement regions (${rs.length})</summary>
    <p class="small muted" style="margin:4px 0">Boxes or cylinders of finer cells (bodies of influence), e.g. round a propeller, in a wake or
      a gap; the cells grow smoothly to the surrounding size outside them. Moving zones have their own "cell size inside" in Setup.</p>
    ${rs.map((r, i) => `<div class="item-card"><div class="head"><input type="text" class="name" data-region="${i}" data-rk="name" value="${esc(r.name)}"
        style="border:0;padding:0;font-weight:600;background:transparent"><button class="danger small" data-action="delRegion" data-i="${i}" title="Delete">✕</button></div>
      ${cyl(r) ? `<table class="summary small"><tr><td></td><td>x [mm]</td><td>y [mm]</td><td>z [mm]</td></tr>
        <tr><td>start</td>${[0, 1, 2].map(j => `<td>${n(i, 'start', j, r.start[j])}</td>`).join('')}</tr>
        <tr><td>end</td>${[0, 1, 2].map(j => `<td>${n(i, 'end', j, r.end[j])}</td>`).join('')}</tr></table>`
      : `<table class="summary small"><tr><td></td><td>x [mm]</td><td>y [mm]</td><td>z [mm]</td></tr>
        <tr><td>min</td>${[0, 1, 2].map(j => `<td>${n(i, 'min', j, r.min[j])}</td>`).join('')}</tr>
        <tr><td>max</td>${[0, 1, 2].map(j => `<td>${n(i, 'max', j, r.max[j])}</td>`).join('')}</tr></table>`}
      ${(() => { const vol = cyl(r) ? Math.PI * (r.radius ** 2 - (r.inner_radius || 0) ** 2) * Math.hypot(...[0, 1, 2].map(k => r.end[k] - r.start[k]))
          : [0, 1, 2].reduce((v, k) => v * (r.max[k] - r.min[k]), 1);
        const nc = vol / (0.12 * r.size ** 3);  // tets of edge h: ~0.12 h^3
        return `<p class="small ${nc > 3e6 ? '' : 'muted'}" style="margin:2px 0;${nc > 3e6 ? 'color:#ffb347' : ''}">≈ ${nc >= 1e6 ? fmt(nc / 1e6, 2) + ' million' : Math.round(nc / 1e3) + ' thousand'} cells in this ${cyl(r) ? (r.inner_radius ? 'ring' : 'cylinder') : 'box'}${nc > 3e6 ? ' - very slow to mesh and solve on a PC: use a larger cell size or a smaller region' : ''}</p>`; })()}
      <div class="grid2"><label class="field"><span>Cell size <span class="unit">[mm]</span></span>${n(i, 'size', 0, r.size)}</label>
        ${cyl(r) ? `<label class="field"><span>Radius <span class="unit">[mm]</span></span>${n(i, 'radius', 0, r.radius)}</label>
          <label class="field" title="0 = solid cylinder; larger = a ring (tube) of fine cells, e.g. along the path of propeller tip vortices"><span>Inner radius <span class="unit">[mm]</span></span>${n(i, 'inner_radius', 0, r.inner_radius || 0)}</label>`
        : `<label class="field"><span>Fit around part</span><select data-regionfit="${i}"><option value="">choose…</option>${parts.map(x => `<option>${esc(x)}</option>`).join('')}</select></label>`}</div></div>`).join('')}
    <div class="row"><button class="ghost small" data-action="addRegion">+ Box</button>
      <button class="ghost small" data-action="addRegionCyl">+ Cylinder</button>
      ${(S.p.moving_zones || []).length && S.p.flow_dir ? `<button class="ghost small" data-action="addWake" title="A cylinder of zone-size cells downstream of each rotating zone (two diameters long), to carry the wake and its tip vortices">+ Wake behind rotating zones</button>
        <button class="ghost small" data-action="addTipRing" title="A ring of fine cells along the path of the blade-tip vortices (0.65-1.0 zone radius, from the zone into the wake, one radius long): resolves the tip vortices without refining the whole wake. Use with the DES flow model.">+ Tip-vortex rings</button>` : ''}</div></details>`;
}
function meshLayersHtml() {
  const L = S.p.mesh.layers || { n: 0, growth: 1.2, first: null };
  return `<details class="expert" ${L.n ? 'open' : ''}><summary>Wall layers (prism / inflation)${L.n ? ` · ${L.n}` : ''}</summary>
    <p class="small muted" style="margin:4px 0">Thin cells stacked on the walls to resolve the boundary layer (friction, separation, y+).
      ${S.p.mesh.method === 'hex' ? 'They are made together with the hex-dominant mesh (snappyHexMesh); the Mesh statistics report how much of the wall got them.'
        : 'OpenFOAM adds them before the run (snappyHexMesh, layer-only); where the space is too tight it leaves them out, and the run log reports the coverage.'}</p>
    <div class="grid2"><label class="field"><span>Number of layers</span><input type="number" min="0" max="20" step="1" data-layers="n" value="${L.n}" title="0 = off"></label>
      <label class="field"><span>Growth ratio</span><input type="number" min="1" max="2" step="0.05" data-layers="growth" value="${L.growth}"></label>
      <label class="field"><span>First layer <span class="unit">[mm]</span></span><input type="number" step="any" data-layers="first_mm" placeholder="auto"
        value="${L.first ? fmt(L.first * 1000) : ''}" title="Empty = automatic: sized for y+ ≈ 30 (wall functions) from the flight speed and the tip speed of rotating zones"></label></div></details>`;
}
function panelMesh() {
  const p = S.p, m = p.mesh;
  if (p.geometry.status !== 'ready') return `<p class="muted">Import geometry first.</p>`;
  const presets = [['quick', 'Quick look'], ['coarse', 'Coarse'], ['medium', 'Medium'], ['fine', 'Fine'], ['very_fine', 'Very fine']];
  const running = S.jobs.mesh?.status === 'running';
  const hexOn = m.method === 'hex' && p.study === 'external_aero';
  let h = `<h2>Mesh</h2>
    ${p.study === 'external_aero' ? `<h3>Mesh type</h3><div class="seg">
      <button data-meshmethod="tet" class="${hexOn ? '' : 'on'}" title="Tetrahedral cells made by Gmsh. Works for everything: rotors, moving zones, symmetry planes, any air volume.">Tetrahedral</button>
      <button data-meshmethod="hex" class="${hexOn ? 'on' : ''}" ${p.hex_unsupported ? 'disabled' : ''}
        title="${esc(p.hex_unsupported || 'Mostly hexahedral cells made by OpenFOAM snappyHexMesh: usually fewer, more orthogonal cells and a finer wake. Body in the automatic box of air.')}">Hex-dominant</button></div>
      ${p.hex_unsupported ? `<p class="small muted" style="margin:4px 0">Hex-dominant: ${esc(p.hex_unsupported)}</p>`
        : hexOn ? `<p class="small muted" style="margin:4px 0">snappyHexMesh (OpenFOAM): background box, surface refinement by the preset, a wake region and wall layers. About a minute per 100,000 cells.</p>` : ''}` : ''}
    <h3>Resolution</h3><div class="seg">${presets.map(([k, l]) => `<button data-preset="${k}" class="${m.preset === k ? 'on' : ''}">${l}</button>`).join('')}</div>
    <details class="expert" ${m.size_max || m.size_min || m.defeature ? 'open' : ''}><summary>Advanced sizes and defeaturing</summary><div class="grid2">
      <label class="field"><span>Max cell size <span class="unit">[mm]</span></span><input type="number" step="any" data-mesh="size_max_mm" placeholder="auto" value="${m.size_max ? fmt(m.size_max * 1000) : ''}"></label>
      <label class="field"><span>Near-wall size <span class="unit">[mm]</span></span><input type="number" step="any" data-mesh="size_min_mm" placeholder="auto" value="${m.size_min ? fmt(m.size_min * 1000) : ''}"></label>
      <label class="field" title="Defeaturing (virtual topology): groups of faces smaller than this across - embossed text, logos - are meshed together with the face they sit on, so the mesh smooths over them instead of failing with 'surfaces intersect'. Empty = off. Try about ${fmt(2e-3 * (p.geometry.info?.ref_length || 0) * 1000, 2)} mm; with fine cells on those faces it can make surface meshing very slow - removing the text in CAD is the sure way."><span>Defeature size <span class="unit">[mm]</span></span><input type="number" step="any" min="0" data-defeature placeholder="off" value="${m.defeature ? fmt(m.defeature * 1000) : ''}"></label>
    </div></details>
    ${meshPartSizingHtml()}${meshRegionsHtml()}${p.study === 'external_aero' ? meshLayersHtml() : ''}
    <p><button class="big" data-action="genMesh" ${running ? 'disabled' : ''}>${running ? 'Meshing…' : m.status === 'ready' ? 'Regenerate mesh' : 'Generate mesh'}</button></p>`;
  if (m.status === 'stale') h += `<div class="note warn">The setup changed since this mesh was made — regenerate it.</div>`;
  if (m.stats && (m.status === 'ready' || m.status === 'stale')) {
    const s = m.stats, q = s.quality;
    h += `<h3>Statistics</h3><div class="stats">
      <span>Cells</span><b>${s.cells.toLocaleString()}</b><span>Nodes</span><b>${s.nodes.toLocaleString()}</b>
      <span>Near-wall size</span><b>${fmt(s.sizes.near_wall * 1000)} mm</b><span>Bulk size</span><b>${fmt(s.sizes.body * 1000)} mm</b>
      <span>Min / mean quality</span><b>${fmt(q.min, 2)} / ${fmt(q.mean, 2)}</b><span>Poor cells (&lt;0.1)</span><b>${q.n_poor}</b>
      ${s.foam_quality ? `<span>Max non-orthogonality</span><b>${fmt1(s.foam_quality.non_orthogonality_max)}°</b>
        <span>Max skewness</span><b>${fmt(s.foam_quality.skewness_max, 2)}</b>` : ''}
      ${s.defeatured_faces ? `<span>Defeatured faces</span><b>${s.defeatured_faces}</b>` : ''}
      ${s.method === 'hex' && s.cell_types ? `<span>Hexahedra</span><b>${(100 * s.cell_types.hexahedra / Math.max(s.cells, 1)).toFixed(0)} %</b>
        <span>Prisms / polyhedra</span><b>${s.cell_types.prisms.toLocaleString()} / ${s.cell_types.polyhedra.toLocaleString()}</b>
        <span>Max aspect ratio</span><b>${fmt1(s.hex.aspect_ratio_max)}</b>
        ${s.hex.layers_extruded_percent != null ? `<span>Wall faces with layers</span><b>${fmt1(s.hex.layers_extruded_percent)} %</b>` : ''}` : ''}
      <span>Time</span><b>${s.seconds} s</b></div>
      <h3>Cell quality</h3><canvas class="chart" id="histo" style="height:110px"></canvas>
      <div class="row" style="gap:6px;align-items:center;margin:4px 0" title="Show only the cells below this quality, on the CAD model, to see where the mesher struggles (thin gaps, small faces, sharp corners)">
        <label class="row" style="gap:4px"><input type="checkbox" id="poorOn" ${S.meshPoor.on ? 'checked' : ''} style="width:auto"> Show cells below</label>
        <input type="number" id="poorThr" min="0.01" max="0.9" step="0.05" value="${S.meshPoor.thr}" style="width:70px">
        <button class="ghost small" data-action="poorZoom" ${S.meshPoor.on ? '' : 'disabled'}>Zoom to worst</button>
        <span class="small muted" id="poorInfo"></span></div>
      ${meshVerdict(s)}
      <p><button class="big" data-goto="solve">Next: solve →</button></p>`;
  }
  return h;
}

function hexVerdict(s) {
  // the hex-dominant (snappyHexMesh) mesh: judged by checkMesh's numbers, the layers and the cell limit
  const f = s.foam_quality, x = s.hex, warn = [], info = [];
  info.push(`Surface cells ${fmt(x.surface_cell_mm)} mm; background ${fmt(x.base_mm)} mm (${x.levels} refinement levels).`);
  if (f.non_orthogonality_max > 70) warn.push(`Max non-orthogonality ${fmt1(f.non_orthogonality_max)}°: above 70° OpenFOAM may be unstable.`);
  if (f.skewness_max > 4) warn.push(`Max skewness ${fmt(f.skewness_max, 2)} is above 4.`);
  if ((x.problems || []).length) warn.push(...x.problems.map(t => `checkMesh: ${t}`));
  if (x.layers_extruded_percent != null && S.p.mesh.layers?.n && x.layers_extruded_percent < 50)
    warn.push(`Only ${fmt1(x.layers_extruded_percent)} % of the wall faces got layers: the parts are thin for these cells. Use a finer preset or refine the thin faces.`);
  else if (x.layers_extruded_percent != null && S.p.mesh.layers?.n) info.push(`${fmt1(x.layers_extruded_percent)} % of the wall faces got layers.`);
  warn.push(...(x.warnings || []));
  const infoHtml = `<div class="small muted" style="margin-top:6px">${info.map(esc).join('<br>')}</div>`;
  const real = warn.filter(w => !/concave/i.test(w));
  const cls = real.length ? 'note warn' : 'note';
  return `<div class="${cls}">${real.length ? '' : '✓ Mesh looks good.'}${warn.map(esc).join('<br>')}${infoHtml}</div>`;
}

function meshVerdict(s) {
  if (s.method === 'hex' && s.hex) return hexVerdict(s);
  const q = s.quality, f = s.foam_quality, st = S.p.study;
  const warn = [], info = [];
  // 1. cell shape
  if (q.n_poor > 0) warn.push(`${q.n_poor} poorly shaped cells – try a finer preset.`);
  if (f && f.non_orthogonality_max > 75) warn.push('High non-orthogonality (>75°): OpenFOAM may be unstable.');
  // 2. resolution
  const across = s.sizes.feature / s.sizes.near_wall;
  info.push(`Thinnest solid feature ${fmt(s.sizes.feature * 1000)} mm ≈ ${fmt(across, 2)} cells across.`);
  if (s.sizes.min_gap) info.push(`Narrowest air gap ${fmt(s.sizes.min_gap * 1000)} mm ≈ ${fmt(s.sizes.cells_across_gap, 2)} cells across.`);
  if (st === 'cht_enclosure' && s.sizes.cells_across_gap && s.sizes.cells_across_gap < 6)
    warn.push(`~${fmt(s.sizes.cells_across_gap, 2)} cells across the air gaps: use at least 6 for final numbers.`);
  if (st === 'external_aero' && across < 2) warn.push('Thin parts have < 2 cells across: use a finer preset.');
  if (st === 'heat_conduction' && across < 1.5) warn.push('Thin parts are ~1 cell thick: Medium or Fine is more accurate.');
  if ((st === 'vibration_modal' || st === 'static_structural' || st === 'aeroelastic_flutter' || st === 'aeroelastic_flutter_cfd' || st === 'thermal_stress') && across < 1.5) warn.push('Thin walls are ~1 element thick: check with Fine.');
  if (st === 'external_aero') {
    // flat-plate estimate of y+ of the first cell centre (tets: ~0.2 h from the wall)
    const set = S.p.settings, V = Math.max(set.speed, 1), L = S.p.geometry.info.ref_length;
    const nu = set.fluid === 'water' ? 1.0e-6 : 1.516e-5;
    const Re = V * L / nu, cf = 0.0576 * Re ** -0.2, ut = V * Math.sqrt(cf / 2);
    const yp = 0.2 * s.sizes.near_wall * ut / nu;
    info.push(`Estimated wall y+ ≈ ${fmt(yp, 2)} (30–300 ideal).`);
    if (yp > 500) warn.push('y+ is high: use a finer preset.');
  }
  if (!SOLID_STUDIES.includes(st) && s.cells < 60000) warn.push('Coarse for CFD: a first look only.');
  const infoHtml = `<div class="small muted" style="margin-top:6px">${info.map(esc).join('<br>')}</div>`;
  if (!warn.length) return `<div class="note">✓ Mesh looks good.${infoHtml}</div>`;
  return `<div class="note warn">${warn.map(esc).join('<br>')}${infoHtml}</div>`;
}

function drawHistogram() {
  const c = $('#histo'); if (!c) return;
  const q = S.p.mesh.stats.quality;
  const W = c.width = c.clientWidth * devicePixelRatio, H = c.height = c.clientHeight * devicePixelRatio;
  const g = c.getContext('2d');
  const mx = Math.max(...q.histogram, 1);
  const n = q.histogram.length, bw = W / n;
  q.histogram.forEach((v, i) => {
    const hgt = v > 0 ? Math.max(2, (Math.log10(v + 1) / Math.log10(mx + 1)) * (H - 18 * devicePixelRatio)) : 0;
    const col = turbo(1 - i / (n - 1));
    g.fillStyle = `rgb(${col.map(x => x * 255 | 0).join(',')})`;
    g.fillRect(i * bw + 1, H - 14 * devicePixelRatio - hgt, bw - 2, hgt);
  });
  g.fillStyle = getComputedStyle(document.body).color; g.font = `${10 * devicePixelRatio}px sans-serif`;
  g.fillText('0 (bad)', 2, H - 2); const t = '1 (ideal)'; g.fillText(t, W - g.measureText(t).width - 2, H - 2);
}

// ---- solve
function panelSolve() {
  const p = S.p, spec = studySpec();
  let h = `<h2>Solve</h2>`;
  if (p.mesh.status !== 'ready') h += `<div class="note warn">${p.mesh.status === 'stale' ? 'The mesh is out of date.' : 'Generate a mesh first.'}</div>`;
  const errs = (p.checks || []).filter(c => c.level === 'error');
  if (errs.length) h += `<div class="note warn" style="margin-top:6px">Fix before solving:<br>${errs.map(e => '• ' + esc(e.message)).join('<br>')}</div>`;
  const of = spec.solver.startsWith('openfoam');
  const ccx = ['vibration_modal', 'static_structural', 'thermal_stress'].includes(p.study) && String(p.settings.structural_solver || '').startsWith('calculix');
  h += `<h3>Solver</h3><div class="item-card"><b>${esc(ccx ? 'CalculiX (separate open-source program), quadratic tetrahedra' : spec.solver_label)}</b><div id="foamStatus" class="small muted">${of ? 'Checking for OpenFOAM…' : ''}</div><div id="ccxStatus" class="small muted"></div></div>
    <details id="softwareBox" class="expert" style="margin-top:6px"><summary class="small">Software used for each step</summary><div id="softwareTable" class="small muted">Loading…</div></details>`;
  setTimeout(() => { loadSoftware(); if (ccx) checkCcx(); }, 0);
  if (of) {
    const s = p.settings;
    const serial = S.foam && S.foam.backend === 'windows' && !S.foam.parallel;
    const nMax = Math.max(S.foam?.cores || 8, s.n_procs || 0);
    const coreOpts = [0, ...Array.from({ length: nMax }, (_, i) => i + 1)]
      .map(n => `<option value="${n}" ${n === (s.n_procs || 0) ? 'selected' : ''}>${n ? n : `Auto (${coresFor(p, 0)})`}</option>`).join('');
    h += `<div class="grid2">${s.analysis === 'transient' ? '' : `<label class="field"><span>Max iterations${tip("Upper limit of solver iterations for a steady run (ignored for transient runs). The run stops earlier once the key results stop changing. Raise it, or use Continue, when a run ends not converged; lower it for a quick preview.")}</span><input type="number" data-setting="iterations" value="${s.iterations}"></label>`}
      ${serial ? `<div class="field"><span>CPU cores${tip("Parallel runs are still being set up, so this run uses one core.")}</span><span class="small">1</span></div>`
        : `<label class="field"><span>CPU cores${tip("How many processor cores the solver uses. Auto = about 20,000 cells per core, at most the physical cores. Leave it on Auto; set fewer to keep the computer responsive for other work. More than the physical core count (hyper-threads) was measured to give no speedup: the solver is limited by memory bandwidth.")}</span><select data-setting="n_procs">${coreOpts}</select></label>`}</div>`;
  }
  const running = S.jobs.solve?.status === 'running';
  const ready = p.mesh.status === 'ready' && !errs.length;
  const eta = of && p.mesh.status === 'ready' ? runTimeEstimate(p) : null;
  if (eta && !running) h += `<p class="small muted">Expected run time ≈ <b>${eta}</b></p>`;
  const noFoam = of && S.foam && !S.foam.available && S.foamJob?.status !== 'running';  // (running: the solve waits for it)
  // start from another study's solution (same geometry): a transient propeller run from a converged steady one
  const srcs = p.study === 'external_aero' ? (S.projects || []).filter(q => q.id !== p.id && q.study === p.study) : [];
  if (srcs.length && !running) h += `<label class="field"><span>Start from${tip("Initial flow: uniform, or the latest solution of another study of the same geometry. Choose a converged steady (MRF) study when you run a transient sliding-mesh propeller: it then needs one or two revolutions instead of several. Leave it on Uniform flow for a first run.")}</span><select id="initFrom"><option value="">Uniform flow</option>${srcs.map(q => `<option value="${esc(q.id)}" ${q.id === S.initFrom ? 'selected' : ''}>${esc(q.name)}</option>`).join('')}</select></label>`;
  h += `<p class="row" style="margin-top:12px"><button class="big" data-action="${noFoam ? 'export' : 'solve'}" ${running || !ready ? 'disabled' : ''}>${running ? 'Running…' : noFoam ? 'Write OpenFOAM case (OpenFOAM not installed)' : '▶ Run simulation'}</button></p>`;
  const tr = p.settings.analysis === 'transient';
  if (of && tr && p.solve.case_dir && !running && ['failed', 'interrupted'].includes(p.solve.status) && !p.solve.setup_changed && S.foam?.available)
    h += `<p class="row small" title="Carries on from the last time the solver saved (the steps after it are run again) to the end time">Resume the stopped run from its last saved time
      <button class="ghost small" data-action="continueRun">Resume</button></p>`;
  else if (of && tr && p.solve.case_dir && !running && p.solve.status === 'done' && !p.solve.setup_changed && S.foam?.available) {
    const end = +p.settings.end_time || 0;
    // a sliding-mesh run: one more turn of the fastest zone, saved every 'Saved frame every' degrees (a smooth video)
    const rpmMax = Math.max(0, ...(p.moving_zones || []).filter(z => z.method === 'sliding' && z.rpm).map(z => Math.abs(z.rpm)));
    const degF = +p.settings.frame_deg || 0, last = S.results?.times?.at(-1) || end;
    const contEnd = rpmMax ? last + 60 / rpmMax : end * 2;
    const contWi = rpmMax && degF > 0 ? degF / (6 * rpmMax) : end / Math.max((p.settings.n_frames || 21) - 1, 1);
    h += `<p class="row small">Continue the run to <input type="number" id="contEnd" value="${+contEnd.toPrecision(6)}" step="any" style="width:90px"> s, saving every
      <input type="number" id="contWi" value="${+contWi.toPrecision(4)}" step="any" style="width:90px"> s${tip("Carries the finished run on from its last saved time; the times saved so far are kept. The suggestion is one more turn of the fastest rotating zone, saved every 'Saved frame every' degrees (a smooth video; the interval is rounded to whole time steps). A longer save interval keeps the disk use down (each saved time of a large mesh takes a few hundred MB).")}
      <button class="ghost small" data-action="extendRun">Continue</button></p>`;
  }
  else if (of && !tr && p.solve.case_dir && p.solve.status !== 'running' && !running && ['done', 'failed', 'interrupted'].includes(p.solve.status) && !p.solve.setup_changed && S.foam?.available)
    h += `<p class="row small">Continue the last run for <input type="number" id="contIters" value="1000" style="width:80px"> more iterations
      <button class="ghost small" data-action="continueRun">Continue</button></p>`;
  if (of) h += `<p class="row expert"><button class="ghost small" data-action="export" ${!ready || running ? 'disabled' : ''}>Write OpenFOAM case only</button>
    ${p.solve.case_dir ? `<a class="small" href="${P('/case.zip')}">Download case (.zip)</a>` : ''}</p>`;
  if (running) h += `<p class="row">${of && S.jobs.solve?.kind === 'solve' ? `<button class="small" data-action="stopSolve" title="Write the current solution and show it; the run can be continued later">Stop and keep results</button>` : ''}
    <button class="danger small" data-action="cancelSolve" title="Kill the solver now; continue later from the last saved iteration">Cancel</button></p>`;
  if (p.solve.status === 'interrupted' && !running) h += `<div class="note warn">The last run was stopped before it finished${p.settings.analysis === 'transient' ? ' (cancelled, or the workbench or computer stopped)' : ''}. Continue it below, or run again.</div>`;
  if (of) h += advancedHtml();
  h += `<div id="solveLive"></div>`;
  if (p.solve.status === 'done') h += `<p><button class="big" data-goto="results">View results →</button></p>`;
  if (p.solve.status === 'failed') h += `<div class="note warn">The last run failed.<pre class="log">${esc(p.solve.error_tail || '')}</pre></div>`;
  if (of) setTimeout(() => S.foam ? checkFoamShow() : checkFoam(), 0);
  if (of && S.caseFiles?.pid !== p.id) {  // (case files written earlier for this project)
    S.caseFiles = { pid: p.id };
    api(P('/case_files')).then(r => { S.caseFiles = { ...r, pid: p.id }; if (r.generated && S.step === 'solve') renderPanel(); }).catch(() => {});
  }
  return h;
}

// CPU cores a CFD run will use (mirrors runner.auto_procs: ~20k cells per core, at most the physical cores)
// ---- advanced OpenFOAM: numerics + any case file
function advancedHtml() {
  const p = S.p, adv = p.foam_advanced || {}, ov = Object.keys(p.foam_overrides || {});
  const vals = Object.fromEntries(S.meta.foam_advanced.map(q => [q.key, adv[q.key] ?? q.default]));
  const n = Object.keys(adv).length;
  const files = S.caseFiles?.pid === p.id ? S.caseFiles : null;
  const grp = {};
  for (const f of files?.files || []) (grp[f.split('/')[0]] ||= []).push(f);
  return `<details class="adv expert" data-advanced ${S.advOpen ? 'open' : ''}><summary><b>Advanced (OpenFOAM)</b>
      <span class="muted small">${n ? `${n} setting${n > 1 ? 's' : ''} changed` : 'defaults'}${ov.length ? ` · ${ov.length} edited file${ov.length > 1 ? 's' : ''}` : ''}</span></summary>
    <p class="small muted" style="margin:4px 0">For experienced users. The defaults are chosen for each study; change these only when a
      run will not converge or you need a specific method. 0 / default = keep the study's value.</p>
    <div class="grid2">${paramInputs(S.meta.foam_advanced, vals, 'data-adv')}</div>
    ${n ? `<p class="row small"><button class="ghost small" data-action="advReset">Reset all to defaults</button></p>` : ''}
    <h3>Case files</h3>
    <p class="small muted" style="margin:4px 0">Every OpenFOAM dictionary of this case. Open one to read or edit it: your version replaces the
      generated file on every run (until you revert it), so any OpenFOAM feature can be used.</p>
    <p class="row"><button class="ghost small" data-action="caseGen" ${p.mesh.status === 'none' ? 'disabled title="Generate the mesh first"' : ''}>${files?.generated ? '↻ Refresh case files' : 'Show case files'}</button></p>
    ${files?.generated ? `<div class="caseFiles">${Object.entries(grp).map(([g, fs]) => `<div class="cfGroup"><div class="muted small">${esc(g)}/</div>
      ${fs.map(f => `<a href="#" class="cf ${ov.includes(f) ? 'edited' : ''}" data-casefile="${esc(f)}">${esc(f.slice(g.length + 1))}${ov.includes(f) ? ' ✎' : ''}</a>`).join('')}</div>`).join('')}</div>` : ''}
    ${ov.filter(f => !files?.files?.includes(f)).map(f => `<div class="small">✎ ${esc(f)} <a href="#" data-casefile="${esc(f)}">open</a></div>`).join('')}
  </details>`;
}
async function openCaseFile(path) {
  let f;
  try { f = await api(P(`/case_file?path=${encodeURIComponent(path)}`)); } catch (e) { toast(e.message, true); return; }
  const d = document.createElement('div');
  d.className = 'modalBack';
  d.innerHTML = `<div class="modal editor"><h3>${esc(f.path)} ${f.overridden ? '<span class="small" style="color:var(--warn)">(edited)</span>' : ''}</h3>
    <p class="small muted">OpenFOAM dictionary. Saved edits replace the generated file before every run; settings in the panels that write
      this file then no longer reach it until you revert.</p>
    <textarea spellcheck="false">${esc(f.text)}</textarea>
    <div class="row" style="justify-content:flex-end;margin-top:8px">
      ${f.overridden ? '<button class="ghost" data-r="revert">Revert to generated</button>' : ''}
      <button class="ghost" data-r="cancel">Cancel</button><button data-r="save">Save</button></div></div>`;
  const close = () => d.remove();
  d.querySelector('[data-r="cancel"]').onclick = close;
  d.querySelector('[data-r="save"]').onclick = () => act(async () => {
    await api(P('/case_file'), { json: { path: f.path, text: d.querySelector('textarea').value } });
    close(); S.p = await api(P('')); renderAll();
  }, 'Saved: used on the next run');
  const rv = d.querySelector('[data-r="revert"]');
  if (rv) rv.onclick = () => act(async () => {
    await api(P('/case_file'), { json: { path: f.path, text: null } });
    close(); S.caseFiles = { ...(await api(P('/case_files/generate'), { method: 'POST' })), pid: S.pid }; S.p = await api(P('')); renderAll();
  }, 'Reverted to the generated file');
  document.body.appendChild(d);
  d.querySelector('textarea').focus();
}

function coresFor(p, n = p.settings.n_procs) {
  if (S.foam && !(S.foam.parallel || S.foam.backend !== 'windows')) return 1;
  const cores = S.foam?.cores || 1;
  return n || Math.max(1, Math.min(cores, Math.floor((p.mesh.stats?.cells || 0) / 20000)));
}

const SOLID_STUDIES = ['heat_conduction', 'vibration_modal', 'static_structural', 'aeroelastic_flutter', 'aeroelastic_flutter_cfd', 'thermal_stress'];
// serial run time from the reference runs: seconds per cell per iteration and typical iterations to auto-stop
const RUN_COST = { cht_enclosure: [5.1e-6, 1700], external_aero: [6.2e-6, 500] };
function runTimeEstimate(p) {
  const c = RUN_COST[p.study], cells = p.mesh.stats?.cells;
  if (!c || !cells) return null;
  let n = Math.min(c[1], p.settings.iterations || c[1]);
  const np = coresFor(p);
  if (p.settings.analysis === 'transient') {
    // time steps: fixed, or Courant ~1 on the near-wall cells at the flow speed (a rough guess: the run adapts it)
    const inl = p.bcs.find(b => b.type === 'velocity_inlet')?.params?.velocity;
    const U = Math.max(p.study === 'external_aero' ? p.settings.speed || 5 : inl || 1, 0.1);
    const h = p.mesh.stats?.sizes?.near_wall || 1e-3;
    // sliding mesh: the time step follows the rotation (0.5 degree per step); each step runs every PIMPLE outer
    // corrector: measured ~2.2e-5 s x cells x outer correctors / cores per step (2.74 M cells, 8 cores: 37.7 s with
    // 5 outer + 2 non-orthogonal correctors, 22.4 s with 3 + 1)
    const sl = (p.moving_zones || []).filter(z => z.method === 'sliding' && z.rpm);
    const w = Math.max(0, ...sl.map(z => Math.abs(z.rpm) * Math.PI / 30));
    const steps = p.settings.time_step > 0 ? p.settings.end_time / p.settings.time_step
      : w > 0 ? p.settings.end_time * w / (0.5 * Math.PI / 180) : p.settings.end_time / (h / U);
    const outer = +(p.foam_advanced?.n_outer || (w > 0 ? 5 : 1));
    const min = w > 0 ? steps * 2.2e-5 * cells * outer / Math.max(np, 1) / 60
      : cells * steps * 2 * c[0] / 60 / (np > 1 ? np * 0.6 : 1);  // a time step ~ 2 steady iterations
    if (w > 0) {
      const txt = m => m < 90 ? `${Math.max(1, Math.round(m))} min` : `${fmt(m / 60, 2)} h`;
      return `${txt(min)} (${Math.round(steps).toLocaleString()} time steps of 0.5° rotation, ${outer} outer correctors each; plus splitting and joining)`;
    }
    const txt = m => m < 10 ? `${Math.max(1, Math.round(m))} min` : m < 90 ? `${Math.round(m / 5) * 5} min` : `${fmt(m / 60, 2)} h`;
    return `${txt(min)} (about ${Math.round(steps).toLocaleString()} time steps - a rough guess)`;
  }
  const min = cells * n * c[0] / 60 / (np > 1 ? np * 0.6 : 1);  // ~60 % parallel efficiency (memory-bound)
  const txt = m => m < 10 ? `${Math.max(1, Math.round(m))} min` : m < 90 ? `${Math.round(m / 5) * 5} min` : `${fmt(m / 60, 2)} h`;
  // auto trim may solve a second time at the trimmed attitude
  return p.study === 'external_aero' && (p.settings.auto_trim ?? 'on') === 'on' ? `${txt(min)} (up to ${txt(2 * min)} if auto trim re-runs)` : txt(min);
}

async function checkFoam(refresh = false) {
  const el = $('#foamStatus'); if (!el) return;
  const before = S.foam ? `${S.foam.available}/${S.foam.backend}/${S.foam.parallel}` : null;
  S.foam = await api('/api/openfoam' + (refresh ? '?refresh=1' : ''));
  if (S.foam.install_job) S.foamJob = S.foam.install_job;  // re-attach after a page reload
  const after = `${S.foam.available}/${S.foam.backend}/${S.foam.parallel}`;
  if (before !== after && S.step === 'solve') { renderPanel(); return; }  // run button / cores field depend on it
  if (S.foam.available) {
    el.innerHTML = `✓ OpenFOAM found via <b>${esc(S.foam.backend)}</b> <span class="muted">${esc(S.foam.detail)}</span>`;
  } else if (S.foamJob?.status === 'running') {
    renderFoamInstall();
  } else {
    const failed = S.foamJob?.status === 'failed' ? `<div class="note warn">Installation failed: ${esc(S.foamJob.error || '')}</div>` : '';
    el.innerHTML = `<div class="note warn" style="margin-top:6px"><b>OpenFOAM not found.</b>${failed}
      ${S.foam.installable ? `<p class="row"><button class="small" id="foamInstall">Install OpenFOAM</button>
        <span class="small muted">~200 MB download, no admin rights</span></p>` : ''}
      <details${S.foam.installable ? '' : ' open'}><summary class="small">Other ways to install</summary><pre class="log" style="background:none;color:inherit;padding:0">${esc(S.foam.help || '')}</pre></details>
      <button class="ghost small" id="foamRecheck">Re-check</button></div>`;
    const rc = $('#foamRecheck'); if (rc) rc.onclick = () => checkFoam(true);
    const b = $('#foamInstall');
    if (b) b.onclick = () => act(async () => {
      b.disabled = true;
      S.foamJob = (await api('/api/openfoam/install', { method: 'POST' })).job;
      renderFoamInstall();
    }).catch(() => { b.disabled = false; });
  }
}

// ---- which software does each step of the workflow (GET /api/projects/{id}/software)
const ELEMENT_NAMES = { geometry: 'Geometry import', meshing: 'Meshing', mesh_layers: 'Wall layers', setup: 'Setup (faces, loads, materials)',
  solver: 'Solver', post: 'Results and viewer', video: 'Video export', assistant: 'Claude assistant' };
async function loadSoftware() {
  const el = $('#softwareTable'); if (!el || !S.p) return;
  try {
    const d = await api(P('/software'));
    el.innerHTML = `<table class="grid"><tr><th>Step</th><th>Software</th><th>Licence</th><th></th></tr>${d.elements.map(r =>
      `<tr><td>${esc(ELEMENT_NAMES[r.element] || r.element)}</td><td>${esc(r.software)}</td><td>${esc(r.licence)}</td>
        <td>${r.kind === 'program' ? (r.installed ? `✓ ${esc(r.version || 'installed')}` : '<span class="warn">not installed</span>') : ''}</td></tr>`).join('')}</table>`;
  } catch { el.textContent = ''; }
}

// ---- CalculiX: found or set up on request (a separate GPL program, downloaded by the user)
let ccxPoll = null;
async function checkCcx(refresh = false) {
  const el = $('#ccxStatus'); if (!el) return;
  clearTimeout(ccxPoll);
  const c = await api('/api/calculix' + (refresh ? '?refresh=1' : ''));
  const j = c.install_job;
  if (j && j.status === 'running') {
    el.innerHTML = `<div class="note"><b>Installing CalculiX…</b> ${esc(j.message)}<div class="bar" style="margin:6px 0"><div class="fill" style="width:${(j.progress * 100).toFixed(1)}%"></div></div></div>`;
    ccxPoll = setTimeout(() => checkCcx(), 1500);
    return;
  }
  if (c.available) { el.innerHTML = `✓ CalculiX ${esc(c.version || '')} found <span class="muted">(${esc(c.source || '')})</span>`; return; }
  el.innerHTML = `<div class="note warn" style="margin-top:6px"><b>CalculiX is not installed.</b>
    ${c.installable ? `<p class="row"><button class="small" id="ccxInstall">Install CalculiX</button><span class="small muted">~150 MB download from conda-forge, no admin rights</span></p>` : ''}
    <div class="small">${esc(c.help || '')}</div> <button class="ghost small" id="ccxRecheck">Re-check</button></div>`;
  const b = $('#ccxInstall');
  if (b) b.onclick = () => act(async () => { b.disabled = true; await api('/api/calculix/install', { method: 'POST' }); checkCcx(); });
  const rc = $('#ccxRecheck'); if (rc) rc.onclick = () => checkCcx(true);
}

let foamPoll = null;
async function renderFoamInstall() {
  // progress of the built-in OpenFOAM installation (job of the pseudo-project "_openfoam")
  clearTimeout(foamPoll);
  try { S.foamJob = await api(`/api/jobs/${S.foamJob.id}?log_tail=0`); } catch { return; }
  const j = S.foamJob, el = $('#foamStatus');
  if (j.status === 'running') {
    if (el) el.innerHTML = `<div class="note" style="margin-top:6px"><b>Installing OpenFOAM…</b> ${esc(j.message)}
      <div class="bar" style="margin:6px 0"><div class="fill" style="width:${(j.progress * 100).toFixed(1)}%"></div></div>
      <button class="ghost small" id="foamCancel">Cancel</button></div>`;
    const c = $('#foamCancel');
    if (c) c.onclick = () => { c.disabled = true; c.textContent = 'Cancelling…'; api(`/api/jobs/${j.id}/cancel`, { method: 'POST' }); };
    foamPoll = setTimeout(renderFoamInstall, 1500);
    return;
  }
  if (j.status === 'done') toast('OpenFOAM installed');
  else if (j.status === 'failed') toast(`OpenFOAM installation failed: ${j.error}`, true, 8000);
  S.foam = null;
  if (S.step === 'solve') renderPanel();  // run button switches from "Write case" to "Run simulation"
}

function checkFoamShow() {
  // status already known: fill the box without another request
  const el = $('#foamStatus'); if (!el) return;
  if (S.foam.available) el.innerHTML = `✓ OpenFOAM found via <b>${esc(S.foam.backend)}</b> <span class="muted">${esc(S.foam.detail)}</span>`;
  else checkFoam(true);
}

let solvePoll = null;
async function renderSolveLive() {
  const el = $('#solveLive'); if (!el) return;
  const j = S.jobs.solve;
  if (!j) { el.innerHTML = ''; return; }
  clearTimeout(solvePoll);
  let d;
  try { d = await api(`/api/jobs/${j.id}?log_tail=60`); } catch { return; }
  const res = d.extra?.residuals;
  el.innerHTML = `<h3>Progress</h3><div class="small">${esc(d.message)} · ${d.elapsed}s</div>
    ${d.extra?.max_T_C != null ? `<div class="small">Current max solid temperature: <b>${fmt1(d.extra.max_T_C)} °C</b></div>` : ''}
    ${res && res.iters.length ? `<div class="small" id="residNow">${latestResiduals(res)}</div><canvas class="chart" id="resid"></canvas>` : ''}
    ${d.extra?.solver_tail?.length ? `<details ${d.status === 'running' ? 'open' : ''}><summary>Solver output (latest lines)</summary><pre class="log" id="solverTail">${esc(d.extra.solver_tail.join('\n'))}</pre></details>` : ''}
    <details ${d.status === 'failed' ? 'open' : ''}><summary>Log</summary><pre class="log">${esc(d.log.join('\n'))}</pre></details>`;
  if (res && res.iters.length) drawResiduals($('#resid'), res);
  el.querySelectorAll('pre').forEach(pre => { pre.scrollTop = pre.scrollHeight; });
  if (d.status === 'running') solvePoll = setTimeout(renderSolveLive, 2500);
}

// the newest initial residual of every solved field as text: "step / iteration 213 · Ux 1.2e-4 · p 3.1e-2 ..."
function latestResiduals(res) {
  const n = res.iters.length;
  const parts = Object.entries(res.residuals).map(([k, v]) => {
    const x = [...v].reverse().find(q => q != null && q > 0);
    return x == null ? null : `${esc(k)} <b>${x.toExponential(1)}</b>`;
  }).filter(Boolean);
  return `Latest residuals (step ${n}, time/iteration ${esc(String(res.iters[n - 1]))}): ${parts.join(' · ')}`;
}

function drawResiduals(c, res) {
  if (!c) return;
  const W = c.width = c.clientWidth * devicePixelRatio, H = c.height = c.clientHeight * devicePixelRatio;
  const g = c.getContext('2d');
  const series = Object.entries(res.residuals).filter(([k, v]) => v.some(x => x != null && x > 0));
  let lo = 1e-8, hi = 1;
  const pad = 28 * devicePixelRatio;
  const n = res.iters.length;
  const X = i => pad + (W - pad - 6) * i / Math.max(n - 1, 1);
  const Y = v => 6 + (H - pad) * (Math.log10(hi) - Math.log10(Math.max(v, lo))) / (Math.log10(hi) - Math.log10(lo));
  g.strokeStyle = 'rgba(127,127,127,.25)'; g.fillStyle = 'rgba(127,127,127,.9)'; g.font = `${9 * devicePixelRatio}px sans-serif`;
  for (let e = 0; e >= -8; e -= 2) { const y = Y(10 ** e); g.beginPath(); g.moveTo(pad, y); g.lineTo(W, y); g.stroke(); g.fillText(`1e${e}`, 2, y + 3); }
  series.forEach(([k, v], si) => {
    const col = turbo((si + 0.5) / series.length);
    g.strokeStyle = `rgb(${col.map(x => x * 255 | 0).join(',')})`; g.lineWidth = 1.2 * devicePixelRatio;
    g.beginPath(); let started = false;
    v.forEach((x, i) => { if (x == null || x <= 0) return; const px = X(i), py = Y(x); started ? g.lineTo(px, py) : g.moveTo(px, py); started = true; });
    g.stroke();
  });
  // legend in a box (top right)
  const lh = 11 * devicePixelRatio, bw = 110 * devicePixelRatio, bx = W - bw - 4;
  g.fillStyle = 'rgba(255,255,255,.85)'; g.fillRect(bx, 4, bw, lh * series.length + 6);
  series.forEach(([k], si) => {
    const col = turbo((si + 0.5) / series.length);
    g.fillStyle = `rgb(${col.map(x => x * 255 | 0).join(',')})`;
    g.fillText(k, bx + 4, 4 + lh * (si + 1));
  });
}

// ---- results
function hasResults() { return ['done', 'stale'].includes(S.p?.solve.status) && S.p.solve.summary; }
function panelResults() {
  const p = S.p;
  if (!hasResults()) return `<h2>Results</h2><p class="muted">${S.jobs.solve?.status === 'running' ? 'Running…' : 'No results yet.'}</p>`;
  const stale = p.solve.status === 'stale'
    ? `<div class="note warn" style="margin-bottom:8px">Out of date: setup changed. <a href="#" data-goto="solve">Re-run</a></div>` : '';
  return `<h2>Results</h2>${stale}<div id="resSummary"></div>
    <h3>Display</h3><div class="grid2">
      <label class="field"><span>Field</span><select id="fieldSel"></select></label>
      <div id="timeBar" class="field full"></div>
      <label class="field"><span>Min</span><input type="number" step="any" id="rngMin"></label>
      <label class="field"><span>Max</span><input type="number" step="any" id="rngMax"></label>
      <label class="field"><span>Automatic range</span><input type="checkbox" id="autoRange" ${S.autoRange ? 'checked' : ''} style="width:auto"></label>
      ${(p.symmetry || []).length ? `<label class="field full" title="The solved part was cut at ${p.symmetry.length === 1 ? 'a symmetry plane' : 'symmetry planes'}: show its mirror image${p.symmetry.length === 1 ? '' : 's'} (the model and every result drawn) to see the whole body"><span>Show mirrored half</span><input type="checkbox" id="mirrorOn" ${S.mirror !== false ? 'checked' : ''} style="width:auto"></label>` : ''}
      <label class="field" title="See-through surfaces and sections, to look at the vortices and contour planes behind them"><span>Opacity</span><input type="range" id="surfOp" min="0.1" max="1" step="0.05" value="${S.surfOpacity ?? 1}"></label></div>
    <div id="isoBox"></div><div id="contourBox"></div><div id="visBox"></div>
    <h3>Export</h3><p class="row"><button class="ghost small" data-action="exportReport" title="The study as one page: setup, mesh, key results, warnings and the picture of the 3D view as it is now. Opens in any browser; print it to PDF from there.">Report</button>
      <button class="ghost small" data-action="exportCsv" title="A table with one row per mesh node: position and every result (opens in Excel)">CSV table</button>
      <button class="ghost small expert" data-action="exportVtu" title="The mesh with every result field as a VTK file, for ParaView, PyVista or VisIt">VTK (ParaView)</button></p>`;
}

async function loadResultsPanel() {
  if (!hasResults()) return;
  if (!S.results) {
    try { S.results = await api(P('/results/fields')); S.timeIdx = null; renderTree(); } catch (e) { toast(e.message, true); return; }
  }
  const r = S.results;
  if (!S.field || !r.fields.find(f => f.name === S.field)) S.field = r.fields.find(f => f.name === 'Flutter mode' || f.name === 'Divergence shape')?.name || r.fields[0]?.name;
  const ib = $('#isoBox'); if (ib) { ib.innerHTML = isoHtml(); bindIso(); }
  const cb = $('#contourBox'); if (cb) { cb.innerHTML = contourHtml(); bindContours(); }
  // (volume cloud, arrows and particles are views of a flow: not offered for studies of solid parts)
  const vb = $('#visBox'); if (vb && !SOLID_STUDIES.includes(S.p.study)) { vb.innerHTML = visHtml(); bindVis(); }
  const mo = $('#mirrorOn');
  if (mo) mo.onchange = () => { S.mirror = mo.checked; viewer.setMirrors(S.mirror ? (S.p.symmetry || []) : [], true); };
  const sel = $('#fieldSel');
  if (!sel) return;
  sel.innerHTML = r.fields.map(f => `<option ${f.name === S.field ? 'selected' : ''}>${esc(f.name)}</option>`).join('');
  sel.onchange = () => {
    S.field = sel.value; S.range = null; S.autoRange = true;
    // air-only fields: hiding the air would leave nothing to see -> show a horizontal section instead
    if (S.p.study === 'external_aero' && S.field === 'Velocity' && !S.clip.axis) {
      // velocity on no-slip walls is meaningless: show the symmetry-plane section instead
      const side = ['x', 'y', 'z'].find(a => !S.p.settings.forward_axis.includes(a) && !S.p.settings.up_axis.includes(a));
      S.clip = { axis: side, pos: 500 };
      $('#clipAxis').value = side; $('#clipPos').value = 500; clipLabel(); viewer.setView('+' + side);
    }
    if (S.p.study === 'cht_enclosure' && S.field !== 'Temperature' && !S.clip.axis) {
      S.hideAir = false; $('#hideAir').checked = false;
      const b = S.p.geometry.info.parts_bbox, g = S.p.geometry.info.bbox;
      S.clip = { axis: 'z', pos: Math.round(1000 * ((b[2] + b[5]) / 2 - g[2]) / (g[5] - g[2])) };
      $('#clipAxis').value = 'z'; $('#clipPos').value = S.clip.pos; clipLabel();
    }
    timeNote();
    showResults();
  };
  timeBar();
  $('#surfOp').oninput = e => { S.surfOpacity = +e.target.value; clearTimeout(S.surfOpT); S.surfOpT = setTimeout(showResults, 120); };
  $('#autoRange').onchange = e => { S.autoRange = e.target.checked; if (S.autoRange) S.range = null; showResults(); };
  for (const id of ['rngMin', 'rngMax']) $('#' + id).onchange = () => {
    S.autoRange = false; $('#autoRange').checked = false;
    S.range = [parseFloat($('#rngMin').value), parseFloat($('#rngMax').value)]; showResults();
  };
  const sm = r.summary || S.p.solve.summary;
  $('#resSummary').innerHTML = summaryHtml(sm) + extrasHtml(sm);
  $('#resSummary').querySelectorAll('[data-action]').forEach(b => b.onclick = ev => action(b.dataset.action, b, ev));
  $('#resSummary').querySelectorAll('[data-mode]').forEach(tr => tr.onclick = () => {
    S.field = 'Mode ' + tr.dataset.mode; S.range = null; S.autoRange = true; sel.value = S.field;
    $('#resSummary').querySelectorAll('[data-mode]').forEach(x => x.classList.toggle('sel', x === tr));
    showResults();
  });
  const anim = $('#animModes');
  if (anim) anim.onchange = () => { S.animate = anim.checked; showResults(); };
  if (sm?.history) drawHistory($('#histChart'), sm.history);
  if (sm?.sine) drawSweep($('#sineChart'), sm.sine.frequency_Hz, sm.sine.max_stress_MPa, 'MPa');
  if (sm?.kind === 'flutter_cfd') {
    drawDampingVsSpeed($('#cfdDamp'), sm.damping_curve, sm.strip?.sweep, sm.flutter);
    const sp = (sm.speeds || []).filter(x => x.series?.t?.length).slice(-1)[0];
    if (sp) drawModalSeries($('#cfdSeries'), sp.series, { tRelease: sp.t_release });
  }
  if (sm?.sweep) { drawFlutter($('#flutterF'), sm.sweep, 'f', sm.critical?.speed_m_s); drawFlutter($('#flutterD'), sm.sweep, 'd', sm.critical?.speed_m_s); }
  $('#resSummary').querySelectorAll('[data-field]').forEach(tr => tr.onclick = () => {
    S.field = tr.dataset.field; S.range = null; S.autoRange = true; sel.value = S.field;
    $('#resSummary').querySelectorAll('[data-mode],[data-field]').forEach(x => x.classList.toggle('sel', x === tr));
    showResults();
  });
  showResults();
}

// response curve of a sweep on logarithmic axes (peaks span decades)
function drawSweep(c, f, y, unit) {
  if (!c || !f?.length) return;
  const dp = devicePixelRatio, W = c.width = c.clientWidth * dp, H = c.height = c.clientHeight * dp, g = c.getContext('2d');
  const pos = y.filter(v => v > 0);
  if (!pos.length) return;
  const lx = Math.log10, x0 = lx(f[0]), x1 = lx(f[f.length - 1]);
  let y0 = Math.floor(lx(Math.min(...pos))), y1 = Math.ceil(lx(Math.max(...pos)));
  if (y1 - y0 < 1) y0 -= 1;
  const padL = 46 * dp, padB = 14 * dp;
  const X = v => padL + (W - padL - 6) * (lx(v) - x0) / Math.max(x1 - x0, 1e-9), Y = v => 6 + (H - padB - 6) * (y1 - lx(Math.max(v, 10 ** y0))) / (y1 - y0);
  g.strokeStyle = 'rgba(127,127,127,.25)'; g.fillStyle = 'rgba(127,127,127,.9)'; g.font = `${9 * dp}px sans-serif`;
  for (let e = y0; e <= y1; e++) { const yy = Y(10 ** e); g.beginPath(); g.moveTo(padL, yy); g.lineTo(W, yy); g.stroke(); g.fillText(`${fmtS(10 ** e)} ${e === y1 ? unit : ''}`, 2, yy + 3 + (e === y1 ? 6 * dp : 0)); }
  for (let e = Math.ceil(x0); e <= Math.floor(x1); e++) { const xx = X(10 ** e); g.beginPath(); g.moveTo(xx, 0); g.lineTo(xx, H - padB); g.stroke(); if (xx > padL + 50 * dp && xx < W - 100 * dp) g.fillText(`${fmtS(10 ** e)} Hz`, xx + 2, H - 2); }
  g.fillText(`${fmtS(f[0])} Hz`, padL, H - 2);
  const end = `${fmtS(f[f.length - 1])} Hz`; g.fillText(end, W - g.measureText(end).width - 2, H - 2);
  g.strokeStyle = '#ff9f1c'; g.lineWidth = 1.5 * dp; g.beginPath();
  f.forEach((v, i) => i ? g.lineTo(X(v), Y(y[i])) : g.moveTo(X(v), Y(y[i]))); g.stroke();
}

// flutter diagrams: one line per mode against the air speed (frequency in Hz, damping ratio in %); a dashed line
// marks the critical speed and the damping chart has its zero line
function drawFlutter(c, sw, what, vf) {
  if (!c || !sw?.speed_m_s?.length) return;
  const dp = devicePixelRatio, W = c.width = c.clientWidth * dp, H = c.height = c.clientHeight * dp, g = c.getContext('2d');
  const V = sw.speed_m_s, key = what === 'f' ? 'frequency_Hz' : 'damping_percent';
  const vals = sw.roots.flatMap(r => r[key]);
  let lo = what === 'f' ? 0 : Math.max(-30, Math.min(...vals, -1)), hi = what === 'f' ? Math.max(...vals) * 1.05 : Math.min(30, Math.max(...vals, 1));
  if (!(hi > lo)) hi = lo + 1;
  const padL = 40 * dp, padB = 14 * dp, x0 = V[0], x1 = V[V.length - 1];
  const X = v => padL + (W - padL - 6) * (v - x0) / Math.max(x1 - x0, 1e-9), Y = v => 6 + (H - padB - 6) * (hi - v) / (hi - lo);
  g.strokeStyle = 'rgba(127,127,127,.25)'; g.fillStyle = 'rgba(127,127,127,.9)'; g.font = `${9 * dp}px sans-serif`; g.lineWidth = dp;
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) { const v = lo + (hi - lo) * i / ticks, yy = Y(v); g.beginPath(); g.moveTo(padL, yy); g.lineTo(W, yy); g.stroke(); g.fillText(fmtS(v), 2, yy + 3); }
  g.fillText(`${fmtS(x0)}`, padL, H - 2); const end = `${fmtS(x1)} m/s`; g.fillText(end, W - g.measureText(end).width - 2, H - 2);
  if (what === 'd') { g.strokeStyle = 'rgba(127,127,127,.9)'; g.beginPath(); g.moveTo(padL, Y(0)); g.lineTo(W, Y(0)); g.stroke(); }
  if (vf) { g.strokeStyle = '#e4572e'; g.setLineDash([4 * dp, 3 * dp]); g.beginPath(); g.moveTo(X(vf), 0); g.lineTo(X(vf), H - padB); g.stroke(); g.setLineDash([]); g.fillStyle = '#e4572e'; g.fillText(`${fmtS(vf)} m/s`, Math.min(X(vf) + 3, W - 60 * dp), 12 * dp); }
  const colors = ['#2563eb', '#e4572e', '#2a9d8f', '#8338ec', '#ff9f1c', '#06aed5', '#d62828', '#6a994e'];
  g.lineWidth = 1.6 * dp;
  sw.roots.forEach((r, i) => {
    g.strokeStyle = colors[i % colors.length]; g.beginPath();
    r[key].forEach((v, k) => { const yy = Y(Math.min(Math.max(v, lo), hi)); k ? g.lineTo(X(V[k]), yy) : g.moveTo(X(V[k]), yy); });
    g.stroke();
  });
}


// transient results: time slider + play
function fmtTime(t) { return t >= 3600 ? `${fmt(t / 3600, 3)} h` : t >= 120 ? `${fmt(t / 60, 3)} min` : `${fmt(t, 3)} s`; }
function timeBar() {
  const el = $('#timeBar'), ts = S.results?.times || [];
  if (!el) return;
  if (ts.length < 2) {   // a steady result: no time slider, but the showcase look and a turntable video
    if (!S.results?.fields?.length) { el.innerHTML = ''; el.style.display = 'none'; return; }
    el.style.display = '';
    el.innerHTML = `<div class="row"><span class="small muted" style="flex:1">Showcase video</span>
      <select id="timeFps" class="small" title="Frames per second of the video">${[12, 24, 30].map(f => `<option value="${f}" ${f === (S.videoFps || 24) ? 'selected' : ''}>${f} fps</option>`).join('')}</select>
      <select id="timeSweep" class="small" title="What moves in the video: the camera turns once around the model, or the section plane sweeps through the model along an axis (the field shown on the plane, seen straight on)">${[['', 'turntable'], ['y', 'section along y'], ['x', 'section along x'], ['z', 'section along z']].map(([v, l]) => `<option value="${v}" ${v === (S.videoSweep || '') ? 'selected' : ''}>${l}</option>`).join('')}</select>
      <button class="ghost small" id="timePromo" title="The look for a showcase video: velocity on the model, no section, a smoke volume, particles, the whole model in 3D">🎬 Promo look</button>
      <button class="ghost small" id="timeRec" title="Save a turntable MP4: the camera turns once around the model, the result drawn as shown now, with the colour scale">● Turntable MP4</button></div>`;
    $('#timeFps').onchange = e => { S.videoFps = +e.target.value; };
    $('#timeSweep').onchange = e => { S.videoSweep = e.target.value; };
    $('#timePromo').onclick = () => promoLook(() => showResults());
    $('#timeRec').onclick = () => recordVideo(() => showResults());
    return;
  }
  if (S.timeIdx == null || S.timeIdx >= ts.length) S.timeIdx = ts.length - 1;
  el.style.display = '';
  el.innerHTML = `<span>Time <b id="timeLbl">${fmtTime(ts[S.timeIdx])}</b></span><div class="row">
    <button class="ghost small" id="timePlay" title="Play">${S.playing ? '❚❚' : '▶'}</button>
    <input type="range" id="timeIdx" min="0" max="${ts.length - 1}" value="${S.timeIdx}" style="flex:1">
    <select id="timeFps" class="small" title="Frames per second of the video">${[4, 8, 12, 24, 30].map(f => `<option value="${f}" ${f === (S.videoFps || 12) ? 'selected' : ''}>${f} fps</option>`).join('')}</select>
    ${videoCanTurn() ? `<select id="timeSub" class="small" title="Pictures per saved time in the video: the rotating parts turn smoothly in between (the flow changes at the saved times). Not offered with vortex surfaces or the volume: they cannot turn with the blades, so the blades would turn through a frozen flow that jumps at every saved time. Every saved time is one picture then.">${[1, 2, 4, 8].map(k => `<option value="${k}" ${k === (S.videoSub || 4) ? 'selected' : ''}>${k}×</option>`).join('')}</select>` : ''}
    <select id="timeOrbit" class="small" title="Camera orbit: the camera turns this many degrees around the model while the video runs (0 = fixed view)">${[0, 30, 60, 90, 180, 360].map(d => `<option value="${d}" ${d === (S.videoOrbit || 0) ? 'selected' : ''}>${d ? `orbit ${d}°` : 'no orbit'}</option>`).join('')}</select>
    <button class="ghost small" id="timePromo" title="The look for a showcase video: velocity on the model, vortex surfaces, a pale smoke slipstream that stays behind the model, the whole model in 3D (no section), a camera orbit of 90°, 8 frames per second">🎬 Promo look</button>
    <button class="ghost small" id="timeRec" title="Save the animation as an MP4 video: every saved time is drawn as it is shown now (field, section, vortex surfaces, view), with the colour scale and the time">● MP4</button></div>
    <div id="timeNote" class="small muted"></div>`;
  timeNote();
  const go = i => { S.timeIdx = i; $('#timeIdx').value = i; $('#timeLbl').textContent = fmtTime(ts[i]); return showResults(); };
  $('#timeIdx').oninput = e => go(+e.target.value);
  $('#timeFps').onchange = e => { S.videoFps = +e.target.value; };
  if ($('#timeSub')) $('#timeSub').onchange = e => { S.videoSub = +e.target.value; };
  $('#timeOrbit').onchange = e => { S.videoOrbit = +e.target.value; };
  $('#timePromo').onclick = () => promoLook(go);
  $('#timeRec').onclick = () => recordVideo(go);
  $('#timePlay').onclick = async () => {
    S.playing = !S.playing; $('#timePlay').textContent = S.playing ? '❚❚' : '▶';
    if (S.playing && S.timeIdx >= ts.length - 1) await go(0);
    while (S.playing && $('#timeIdx') && S.timeIdx < ts.length - 1) {
      const t0 = performance.now();
      await go(S.timeIdx + 1);
      await new Promise(r => setTimeout(r, Math.max(0, 250 - (performance.now() - t0))));
    }
    S.playing = false; if ($('#timePlay')) $('#timePlay').textContent = '▶';
  };
}

// MP4 of the transient result: each saved time is shown, drawn into a picture with the colour scale and the time,
// and the pictures are encoded on the server (even frame rate however long a time takes to load)
// for scripts that drive the page (storyboards, screenshots): the state, the 3D view and the drawing functions
window.simwb = { S, viewer, showResults, videoOverlay: (...a) => videoOverlay(...a) };
async function recordVideo(go) {
  const ts = S.results?.times || [], turn = ts.length < 2;   // (a steady result: a turntable, no time)
  if (!S.results || S.recording) return;
  S.playing = false; S.recording = true;
  const btn = $('#timeRec'), frames = [];
  const orbit = (turn ? (S.videoOrbit || 360) : (S.videoOrbit || 0)) * Math.PI / 180, base = viewer.cameraState();  // (camera orbit over the video)
  let nPic = 0;
  try {
    if (turn && S.videoSweep) {   // the section plane moves through the model along an axis, seen straight on
      const sw = S.videoSweep, ax = 'xyz'.indexOf(sw), n = Math.round(5 * (S.videoFps || 24));
      const b = resultsBounds();
      const w = b[ax + 3] - b[ax], pad = clipPad(b[ax], b[ax + 3]), saved = { ...S.clip };
      let oldRange = null;
      try {
        S.clip = { axis: sw, pos: S.clip.pos }; if ($('#clipAxis')) $('#clipAxis').value = sw;
        viewer.setView('+' + sw);
        // zoomed to the model as seen on the plane (the plane covers the whole air box): a box without depth along the axis
        const mid = (b[ax] + b[ax + 3]) / 2, pad8 = i => 0.06 * (b[i + 3] - b[i]);
        viewer.fitTo([0, 1, 2].map(i => i === ax ? mid : b[i] - pad8(i)), [0, 1, 2].map(i => i === ax ? mid : b[i + 3] + pad8(i)));
        // one colour scale for the whole sweep; speed: from rest to 1.35 x the free stream (the extremes at the walls
        // would wash the rest of the picture out)
        const fld = S.results.fields.find(f => f.name === S.field), U = S.results.summary?.speed_m_s;
        if (fld) {
          oldRange = [S.autoRange, S.range]; S.autoRange = false;
          S.range = S.field === 'Velocity' && U > 0 ? [0, Math.min(fld.max, 1.35 * U)] : [fld.min, fld.max];
        }
        for (let i = 0; i < n; i++) {
          if (btn) btn.textContent = `${i + 1}/${n}`;
          const x = b[ax] + w * i / (n - 1);   // from the first face of the model to the last
          S.clip.pos = 1000 * (x - (b[ax] - pad)) / (w + 2 * pad);
          if ($('#clipPos')) $('#clipPos').value = S.clip.pos; clipLabel();
          await showResults();
          await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
          const c = viewer.snapshot(); videoOverlay(c, null, `${sw} = ${fmt1(x * 1000)} mm`);
          frames.push(await new Promise(r => c.toBlob(r, 'image/jpeg', 0.92)));
        }
      } finally { if (oldRange) { S.autoRange = oldRange[0]; S.range = oldRange[1]; } S.clip = saved; if ($('#clipAxis')) $('#clipAxis').value = saved.axis || ''; if ($('#clipPos')) $('#clipPos').value = saved.pos; clipLabel(); await showResults(); }
    } else if (turn) {  // one picture per step of the turn: 4 s at the chosen frame rate
      const n = Math.round(4 * (S.videoFps || 24));
      for (let i = 0; i < n; i++) {
        if (btn) btn.textContent = `${i + 1}/${n}`;
        viewer.setCameraState(base); viewer.orbitBy(orbit * i / n);   // (a full turn: the last picture is not the first again)
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
        const c = viewer.snapshot(); videoOverlay(c, null);
        frames.push(await new Promise(r => c.toBlob(r, 'image/jpeg', 0.92)));
      }
    }
    // rotating parts: k pictures per saved time, the parts turned on in between (a propeller moves 30 degrees or
    // more between saved times - and a multiple of the blade spacing looks like a still propeller)
    // (with vortex surfaces or the volume shown: one picture per saved time. They are computed at the saved times and
    // cannot turn with the blades: in-between pictures showed the blades turning through a frozen flow that jumped
    // at every saved time - choppy, and the model out of step with the results)
    const k = videoCanTurn() ? (S.videoSub || 4) : 1;
    for (let i = 0; i < (turn ? 0 : ts.length); i++) {
      if (btn) btn.textContent = `${i + 1}/${ts.length}`;
      await go(i);
      for (let j = 0; j < (i < ts.length - 1 ? k : 1); j++) {
        const t = ts[i] + (j / k) * ((ts[i + 1] ?? ts[i]) - ts[i]);
        if (j) { viewer.spin(t); if (!S.clip.axis) viewer.spinOverlay(t - ts[i]); }
        if (orbit) { viewer.setCameraState(base); viewer.orbitBy(orbit * nPic / Math.max((ts.length - 1) * k, 1)); }
        nPic++;
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));  // drawn
        const c = viewer.snapshot();
        videoOverlay(c, t);
        frames.push(await new Promise(r => c.toBlob(r, 'image/jpeg', 0.92)));
      }
    }
    if (btn) btn.textContent = 'encoding…';
    const fd = new FormData();
    frames.forEach((b, i) => fd.append('frames', b, `f${String(i).padStart(5, '0')}.jpg`));
    fd.append('fps', String(S.videoFps || 12));
    // a copy stays in the study's videos folder (the browser's download can fail or be renamed): name = field, kind, time
    fd.append('save_as', `${(S.field || 'result').replace(/[^\w-]+/g, '_')}-${turn ? (S.videoSweep ? 'section-' + S.videoSweep : 'turntable') : 'time'}-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')}`);
    const r = await fetch(P('/results/video'), { method: 'POST', body: fd, headers: { 'X-SimWB': '1' } });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.error || e.detail || r.statusText); }
    const url = URL.createObjectURL(await r.blob()), a = document.createElement('a');
    a.href = url; a.download = `${S.pid}-${(S.field || 'result').replace(/[^\w-]+/g, '_')}.mp4`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    toast(`Video saved: ${frames.length} frames at ${S.videoFps || 12} fps` + (r.headers.get('X-Saved-To') ? ` (a copy in ${r.headers.get('X-Saved-To')})` : ''), false, 9000);
  } catch (e) { toast(e.message, true); } finally {
    if (orbit) viewer.setCameraState(base);
    S.recording = false;
    if ($('#timeRec')) $('#timeRec').textContent = turn ? '● Turntable MP4' : '● MP4';
  }
}
// the look for a showcase (promo) video in one click: velocity on the model, vortex surfaces for the wake, a pale smoke
// slipstream that stays behind the model (the cloud stops at what is in front of it), the whole model in 3D (no
// section, the mirrored half shown), no edge lines, 8 frames per second and a 90 degree camera orbit
async function promoLook(go) {
  if (S.recording || !S.results) return;
  const has = n => S.results.fields.some(f => f.name === n);
  if (has('Velocity')) { S.field = 'Velocity'; S.range = null; S.autoRange = true; if ($('#fieldSel')) $('#fieldSel').value = 'Velocity'; }
  S.clip = { axis: '', pos: 500 }; $('#clipAxis').value = ''; $('#clipPos').value = 500; clipLabel();
  if (S.p.symmetry?.length) { S.mirror = true; if ($('#mirrorOn')) $('#mirrorOn').checked = true; viewer.setMirrors(S.p.symmetry, true); }
  if (has('Q-criterion')) { S.iso.on = true; S.iso.color = has('Velocity') ? 'Velocity' : S.iso.color; S.iso.k = 48; }
  // the smoke is the vortex strength (Q, log scale): it has the structure of the flow (filaments, swirls) where the speed
  // is one smooth bubble of accelerated air; it glows around the vortex surfaces instead of fogging the model
  const vf = has('Q-criterion') ? 'Q-criterion' : has('Velocity') ? 'Velocity' : null;
  Object.assign(S.vol, { on: !!vf, field: vf, style: 'smoke', auto: true, density: vf === 'Q-criterion' ? 3 : 5, key: '' });
  S.vis.region = 'wake'; S.vec.on = false; S.par.on = false; for (const st of [S.vec, S.par]) st.key = '';
  const steady = (S.results.times || []).length < 2;
  if (steady) {   // a steady result: the field on a section plane that sweeps through the model (no vortex surfaces, no cloud)
    S.iso.on = false; S.vol.on = false;
    const span = ['x', 'y', 'z'].find(a => !(S.p.settings.forward_axis || '+x').includes(a) && !(S.p.settings.up_axis || '+z').includes(a)) || 'y';
    S.videoSweep = span; if ($('#timeSweep')) $('#timeSweep').value = span;
  }
  S.videoFps = steady ? 24 : 8; S.videoOrbit = steady ? 360 : 0;  // (transient: one picture per saved time, 8 fps keeps 40 times about 5 s long, fixed isometric view; steady: a turn around the model)
  viewer.setShowEdges(false); if ($('#optEdges')) $('#optEdges').checked = false;
  viewer.setView('iso');
  if ($('#visBox')) { $('#visBox').innerHTML = visHtml(); bindVis(); }
  if ($('#isoOn')) { $('#isoOn').checked = true; if ($('#isoK')) $('#isoK').value = S.iso.k; if ($('#isoColor')) $('#isoColor').value = S.iso.color; }
  if ($('#timeFps')) $('#timeFps').value = String(S.videoFps);
  if ($('#timeOrbit')) $('#timeOrbit').value = String(S.videoOrbit);
  if (S.vol.on) { const sel = $('#volField'); if (sel) sel.value = S.vol.field; }
  await showResults();
  toast('Promo look set: press ● MP4 to record it');
}
// in-between video pictures (blades turned on, flow left at the saved time) only make sense when what is shown
// follows the blades: not with vortex surfaces or the volume
function videoCanTurn() { return !!spinZones()?.length && !isoOn() && !S.vol.on; }
function videoOverlay(c, t, extra) {
  const g = c.getContext('2d'), k = c.width / Math.max(1, viewer.canvas.clientWidth);  // (device pixels)
  const L = S.legendInfo, pad = 14 * k;
  g.save(); g.font = `${13 * k}px system-ui, sans-serif`; g.textBaseline = 'top';
  if (L) {  // colour scale, as in the view
    const w = 200 * k, h = 12 * k, x = pad, y = pad + 20 * k;
    g.fillStyle = 'rgba(20,24,30,0.75)'; g.fillRect(x - 8 * k, pad - 6 * k, w + 16 * k, 62 * k);
    g.fillStyle = '#fff'; g.fillText(`${L.name} [${L.unit}]`, x, pad);
    const gr = g.createLinearGradient(x, 0, x + w, 0);
    for (let i = 0; i <= 10; i++) gr.addColorStop(i / 10, `rgb(${turbo(L.reverse ? 1 - i / 10 : i / 10).map(v => v * 255 | 0).join(',')})`);
    g.fillStyle = gr; g.fillRect(x, y, w, h);
    g.fillStyle = '#fff';
    const ticks = [L.range[0], (L.range[0] + L.range[1]) / 2, L.range[1]];
    ticks.forEach((v, i) => { const s = fmt(v, 4), tw = g.measureText(s).width; g.fillText(s, x + (w * i) / 2 - tw * (i / 2), y + h + 4 * k); });
  }
  const lines = [...(t != null ? [`t = ${fmtTime(t)}`] : []), ...(extra ? [extra] : []), S.p?.name || ''];
  const qf = isoOn() && S.results?.fields.find(f => f.name === 'Q-criterion');
  if (qf) lines.push(`Vortex surfaces: Q = ${fmt(isoLevel(qf), 3)} 1/s², coloured by ${S.iso.color}`);
  g.font = `${15 * k}px system-ui, sans-serif`; g.textBaseline = 'bottom'; g.fillStyle = '#fff';
  g.shadowColor = 'rgba(0,0,0,0.6)'; g.shadowBlur = 4 * k;
  lines.forEach((s, i) => { const tw = g.measureText(s).width; g.fillText(s, c.width - pad - tw, c.height - pad - (lines.length - 1 - i) * 20 * k); });
  g.restore();
}

// a field that hardly changes over the saved times plays as a still picture: say so
// degrees the fastest sliding zone turns between two saved times (null: nothing slides or fewer than two times)
function frameStepDeg() {
  const rpm = Math.max(0, ...(S.p?.moving_zones || []).filter(z => z.method === 'sliding' && z.rpm).map(z => Math.abs(z.rpm)));
  const ts = S.results?.times || [];
  return rpm && ts.length > 1 ? { deg: 6 * rpm * (ts[1] - ts[0]), rpm } : null;
}
function timeNote() {
  const el = $('#timeNote'), f = S.results?.fields.find(x => x.name === S.field);
  if (!el) return;
  const c = f?.change_over_time;
  const st = frameStepDeg();
  if (st && st.deg > 12) {  // the video can only be as smooth as the saved times are close
    el.textContent = `The saved times are ${fmt(st.deg, 3)}° of blade turn apart: a video steps by that much per picture. For a smooth video continue the run (Solve step) with a save interval of about ${fmt(8 / (6 * st.rpm), 3)} s (8° of turn): the blades, vortices and field then stay in step.`;
    return;
  }
  el.textContent = c != null && c < 0.03 ? `99 % of the points change by less than ${fmt(c * 100, 2)} % of the ${S.field} colour range between the saved times (${fmtTime(S.results.times[0])} to ${fmtTime(S.results.times.at(-1))}), so the animation looks still: this solution had already settled by the first saved time. To see the start-up, run a shorter end time (more saved times early on).` : '';
}

// history plot (linear axes): {t:[], series:{name:[]}}
function drawHistory(c, h) {
  if (!c) return;
  const W = c.width = c.clientWidth * devicePixelRatio, H = c.height = c.clientHeight * devicePixelRatio;
  const g = c.getContext('2d'), dp = devicePixelRatio;
  const series = Object.entries(h.series);
  // y range: skip the start-up transient when asked (e.g. the force spike of a flow started impulsively)
  const all = series.flatMap(([, v]) => v.filter((x, i) => !(h.t[i] < (h.y_from_t || 0)))).filter(Number.isFinite);
  if (!all.length) return;
  let lo = Math.min(...all), hi = Math.max(...all);
  if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
  const t1 = h.t[h.t.length - 1] || 1, padL = 46 * dp, padB = 14 * dp;
  const X = t => padL + (W - padL - 6) * t / t1, Y = v => 6 + (H - padB - 6) * (hi - v) / (hi - lo);
  g.strokeStyle = 'rgba(127,127,127,.25)'; g.fillStyle = 'rgba(127,127,127,.9)'; g.font = `${9 * dp}px sans-serif`;
  const dec = Math.min(6, Math.max(0, Math.ceil(-Math.log10((hi - lo) / 4))));  // enough decimals to tell ticks apart
  for (let i = 0; i <= 4; i++) {
    const v = lo + (hi - lo) * i / 4, y = Y(v);
    g.beginPath(); g.moveTo(padL, y); g.lineTo(W, y); g.stroke(); g.fillText(v.toFixed(dec), 2, y + 3);
  }
  g.fillText(fmtTime(0), padL, H - 2); g.fillText(fmtTime(t1), W - 50 * dp, H - 2);
  g.save(); g.beginPath(); g.rect(padL, 0, W - padL, H - padB); g.clip();
  series.forEach(([k, v], si) => {
    const col = turbo((si + 0.5) / series.length);
    g.strokeStyle = `rgb(${col.map(x => x * 255 | 0).join(',')})`; g.lineWidth = 1.4 * dp;
    g.setLineDash(k.endsWith(' mean') ? [4 * dp, 3 * dp] : []);
    g.beginPath(); v.forEach((x, i) => i ? g.lineTo(X(h.t[i]), Y(x)) : g.moveTo(X(h.t[i]), Y(x))); g.stroke();
  });
  g.restore(); g.setLineDash([]);
  const lh = 11 * dp, bw = 130 * dp, bx = padL + 4;
  g.fillStyle = 'rgba(20,24,32,.85)'; g.fillRect(bx, 4, bw, lh * series.length + 6);
  series.forEach(([k], si) => {
    const col = turbo((si + 0.5) / series.length);
    g.fillStyle = `rgb(${col.map(x => x * 255 | 0).join(',')})`; g.fillText(k, bx + 4, 4 + lh * (si + 1));
  });
}

// a vector for display: components that are round-off against the largest one (or against ref) are shown as 0
function fmtVec(v, ref = v) {
  const big = Math.max(...ref.map(Math.abs), ...v.map(Math.abs));
  return v.map(x => Math.abs(x) < 1e-9 * big ? '0' : fmt(x)).join(' / ');
}
function kpi(lbl, val, unit = '') { return `<div class="kpi"><div class="lbl">${esc(lbl)}</div><div class="val">${val}<span class="small muted"> ${esc(unit)}</span></div></div>`; }

function convergenceHtml(c) {
  if (!c) return '';
  if (c.transient) return `<div class="note small ${c.completed ? '' : 'warn'}" style="margin-bottom:8px">${esc(c.note)}${c.time_step_s ? ` Time step ${fmtTime(c.time_step_s.min)}–${fmtTime(c.time_step_s.max)}${c.max_courant != null ? `, max Courant number ${fmt(c.max_courant, 2)}` : ''}.` : ''}</div>`;
  const canContinue = S.p.solve.status === 'done' && !S.p.solve.setup_changed;
  if (c.converged) return `<div class="note small" style="margin-bottom:8px">✓ Converged after ${c.iterations} iterations. ${esc(c.note || '')}</div>`;
  return `<div class="note warn" style="margin-bottom:8px"><b>Not converged</b> after ${c.iterations} iterations: preliminary numbers.
    ${canContinue ? `<div class="row" style="margin-top:6px">Continue for <input type="number" id="contIters" value="1000" style="width:80px"> iterations
    <button class="small" data-action="continueRun">Continue run</button></div>` : ''}</div>`;
}

function extrasHtml(s) {
  let h = '';
  const mc = S.p.mesh_check;
  const running = S.jobs.check?.status === 'running';
  h += `<h3>Mesh sensitivity</h3>`;
  if (mc) {
    h += `<table class="summary"><tr><td><b>Output</b></td><td><b>${esc(mc.from)} → ${esc(mc.to)}${mc.to2 ? ' → ' + esc(mc.to2) : ''}</b></td></tr>
      ${mc.rows.map(r => `<tr><td>${esc(r.name)}</td><td>${fmt(r.coarse)} → ${fmt(r.fine)} ${esc(r.unit)} (${fmt(r.change_percent, 2)}%${r.raw_change_percent != null && Math.abs(r.raw_change_percent - r.change_percent) > 0.5 ? `; ${fmt(r.raw_change_percent, 1)}% of its own value` : ''}${r.resolved === false ? '; within the iteration error' : ''})${r.fine2 != null ? ` → ${fmt(r.fine2)} ${esc(r.unit)}${r.convergence_type === 'oscillatory' && r.gci_percent != null ? ` · oscillating: ±${fmt(r.gci_percent, 2)} % (half range)` : r.gci_percent != null ? ` · GCI ${fmt(r.gci_percent, 2)} %, order ${fmt(r.order, 2)}${r.order_limited_to ? ` (above the formal ${r.order_limited_to}: GCI uses ${r.order_limited_to})` : ''}` : ` · ${esc(r.convergence_type || '')}`}` : ''}</td></tr>`).join('')}</table>
      <div class="note ${mc.max_change_percent > 5 || mc.stale ? 'warn' : ''} small" style="margin-top:6px">${mc.stale
        ? '<b>Outdated:</b> the setup or results changed after this check – run it again. ' : ''}${esc(mc.verdict)}
      (${mc.cells_from.toLocaleString()} → ${mc.cells_to.toLocaleString()}${mc.cells_to2 ? ' → ' + mc.cells_to2.toLocaleString() : ''} cells)${mc.note_recomputed ? ' ' + esc(mc.note_recomputed) : ''}</div>`;
  } else {
    h += `<p class="small muted">Re-run one preset finer and compare.</p>`;
  }
  const settledRun = (!s.convergence || s.convergence.converged) && S.p.solve.status !== 'stale';
  const cost = RUN_COST[S.p.study], cells = S.p.mesh.stats?.cells;
  // the next preset has roughly 2-3x the cells; the mapped start saves part of the iterations
  const est = cost && cells ? [cells * 2 * cost[1] * 0.6 * cost[0] / 60, cells * 3 * cost[1] * cost[0] / 60] : null;
  const fmtMin = m => m < 90 ? `${Math.max(1, Math.round(m))} min` : `${fmt(m / 60, 2)} h`;
  if (S.p.mesh.preset !== 'very_fine')
    h += `<p><button class="ghost small" data-action="meshCheck" ${running || !settledRun ? 'disabled' : ''}>${running && S.jobs.check?.extra?.check_kind !== 'flow-model comparison' ? 'Checking…' : mc ? 'Check again (one level finer)' : 'Run mesh-sensitivity check'}</button>
      ${['quick', 'coarse', 'medium'].includes(S.p.mesh.preset) ? `<button class="ghost small expert" data-action="meshCheck3" ${running || !settledRun ? 'disabled' : ''} title="Two finer meshes: grid-convergence index (about 3-4x the time of one check)">Three-level check (GCI)</button>` : ''}
      ${!settledRun ? `<span class="small muted"> ${S.p.solve.status === 'stale' ? 'after re-running' : 'once converged'}</span>` : est ? `<span class="small muted"> ≈ ${fmtMin(est[0])}–${fmtMin(est[1])}</span>` : ''}</p>`;
  if (S.p.study === 'cht_enclosure') {
    const fc = S.p.model_check;
    const used = s.flow_model || (s.flow_regime?.suggested === 'kOmegaSST' ? 'kOmegaSST' : 'laminar');
    const other = used === 'kOmegaSST' ? 'laminar' : 'k-ω SST';
    h += `<h3>Flow model</h3>`;
    if (fc) h += `<table class="summary"><tr><td><b>Output</b></td><td><b>${esc(fc.from)} → ${esc(fc.to)}</b></td></tr>
      ${fc.rows.map(r => `<tr><td>${esc(r.name)}</td><td>${fmt(r.a)} → ${fmt(r.b)} ${esc(r.unit)} (${fmt(r.change_percent, 2)}%${r.resolved === false ? ', within the iteration error' : ''}) · design value ${fmt(r.conservative)}${r.combined_uncertainty_percent != null ? ` ±${fmt(r.combined_uncertainty_percent, 2)} % (mesh, iterations; the higher value covers the model spread)` : ''}</td></tr>`).join('')}</table>
      <div class="note ${fc.stale ? 'warn' : ''} small" style="margin-top:6px">${fc.stale ? '<b>Outdated:</b> the setup or results changed after this comparison. ' : ''}${esc(fc.note)}</div>`;
    else h += `<p class="small muted">Solve again with the other flow model${s.flow_regime?.transitional ? ' (transitional flow)' : ''}.</p>`;
    const fcRunning = running && S.jobs.check?.extra?.check_kind === 'flow-model comparison';
    h += `<p><button class="ghost small" data-action="modelCheck" ${running || !settledRun ? 'disabled' : ''}>${fcRunning ? 'Comparing…' : `Compare with ${other} (same mesh)`}</button>
      ${!settledRun ? `<span class="small muted"> ${S.p.solve.status === 'stale' ? 'after re-running' : 'once converged'}</span>`
        : est ? `<span class="small muted"> ≈ ${fmtMin(est[0] / 2.5)}–${fmtMin(est[1] / 3)}</span>` : ''}</p>`;
  }
  const prev = S.p.previous_result;
  if (prev && S.p.solve.status === 'done') {
    const rows = Object.entries(prev.values || {}).filter(([, v]) => v != null);
    const cur = { 'Max temperature': s.T_max, 'Hottest part temperature': s.hottest_part?.T_max,
      'Thermal resistance': s.thermal_resistance_K_per_W, 'Pressure drop': s.pressure_drop_Pa, 'Drag': s.drag_N,
      'Vertical aero force': s.vertical_aero_force_N, 'Suggested pitch': s.suggested_pitch_deg };
    for (const m of (s.modes || []).filter(m => !m.rigid_body)) cur[`Mode ${m.elastic_index} frequency`] = m.frequency_Hz;
    if (rows.length) h += `<details><summary>Compared with the previous run (${esc(prev.finished)})</summary><table class="summary">
      ${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${fmt(v)} → ${fmt(cur[k])}</td></tr>`).join('')}</table></details>`;
  }
  const notes = [...(s.warnings || [])];
  if (s.warning_stale) notes.push(s.warning_stale);
  if (notes.length) h += `<div class="note warn small" style="margin-top:8px">${notes.map(esc).join('<br>')}</div>`;
  if (s.assumptions?.length) h += `<details><summary>Model assumptions</summary><ul class="small">${s.assumptions.map(a => `<li>${esc(a)}</li>`).join('')}</ul></details>`;
  return h;
}

function summaryHtml(s) {
  if (!s) return '';
  const st = S.p.study;
  let h = (S.p.solve.note ? `<div class="note warn" style="margin-bottom:8px">${esc(S.p.solve.note)}</div>` : '') + convergenceHtml(s.convergence);
  const settled = !s.convergence || s.convergence.converged;
  if (s.history) h += `<h3>${st === 'external_aero' ? 'Forces over time' : 'Temperature over time'}</h3><canvas class="chart" id="histChart" style="height:150px"></canvas>
    <p class="small muted">${st === 'heat_conduction' ? 'Solid: max, dashed: mean per part.' : esc(s.history_note || '')}${s.drag_fluctuation_percent != null ? ` Drag fluctuates by ±${fmt(s.drag_fluctuation_percent, 2)} % (standard deviation) over that time.` : ''}</p>`;
  if (!SOLID_STUDIES.includes(st) && ['quick', 'coarse'].includes(S.p.mesh.preset)) {
    // only quote numbers that were measured on this project (mesh-sensitivity check), never generic tolerances
    const mc = S.p.mesh_check;
    const measured = mc && !mc.stale && mc.from === S.p.mesh.preset
      ? `Measured on this project (${esc(mc.from)} → ${esc(mc.to)}): ${mc.rows.map(r => `${esc(r.name)} ${fmt(r.raw_change_percent ?? r.change_percent, 1)} %`).join(', ')}.`
      : 'Run the mesh-sensitivity check below.';
    const gapNote = '';
    h += `<div class="note warn small" style="margin-bottom:8px"><b>${S.p.mesh.preset === 'quick' ? 'Quick-look' : 'Coarse'} mesh – indicative only.</b> ${measured}${gapNote}</div>`;
  }
  if (st === 'heat_conduction') {
    const tr = s.transient;
    if (tr) h += `<p class="small muted">Transient: ${fmtTime(tr.end_time_s)} in ${tr.steps} steps of ${fmtTime(tr.time_step_s)}. Numbers below are at the end time${tr.T_max_any_time > s.T_max + 0.05 ? `; the highest temperature at any time was ${fmt1(tr.T_max_any_time)} °C` : ''}.</p>`;
    h += `<div class="kpis">${kpi('Max temperature', fmt1(s.T_max), '°C')}${kpi('Min temperature', fmt1(s.T_min), '°C')}</div>
      <h3>Parts</h3><table class="summary">${Object.values(s.bodies).map(b => `<tr><td>${esc(b.name)}</td><td>max ${fmt1(b.T_max)} °C · mean ${fmt1(b.T_mean)} °C</td></tr>`).join('')}</table>
      <h3>Heat flow by boundary</h3><table class="summary">${Object.entries(s.boundaries).map(([k, b]) =>
        `<tr><td>${esc(k)}</td><td>${b.heat_out_W != null ? fmt(b.heat_out_W) + ' W out' : b.heat_in_W != null ? fmt(b.heat_in_W) + ' W in' : ''}${b.heat_out_radiation_W != null ? ` (${fmt(b.heat_out_convection_W)} convection + ${fmt(b.heat_out_radiation_W)} radiation)` : ''} · ${fmt1(b.T_mean)} °C avg${b.h_mean_W_m2K != null ? ` · h ${fmt(b.h_mean_W_m2K, 2)} W/m²K mean (${fmt(b.h_range_W_m2K[0], 2)}–${fmt(b.h_range_W_m2K[1], 2)}, computed)` : ''}</td></tr>`).join('')}</table>
      <p class="small muted">Heat in ${fmt(s.energy_balance.heat_in_W)} W = out ${fmt(s.energy_balance.heat_out_W)} W${s.energy_balance.heat_stored_W != null ? ` + stored ${fmt(s.energy_balance.heat_stored_W)} W (at the end time)` : ''}</p>`;
  } else if (st === 'vibration_modal') {
    const el = s.modes.filter(m => !m.rigid_body), band = s.excitation_band_Hz;
    const pct = x => x < 5e-4 ? '0' : fmt(100 * x, 2);  // (below 0.05 %: no participation worth quoting)
    h += `<div class="kpis">${kpi('First natural frequency', fmt(s.first_frequency_Hz), 'Hz')}${kpi('Mass', fmt(s.mass_kg * 1000), 'g')}
      ${band ? kpi('Modes in excitation range', String((s.modes_in_band || []).length), `of ${el.length}`) : ''}</div>
      ${s.band_note ? `<div class="note small ${s.band_caution ? 'warn' : ''}">${s.band_caution ? '△' : '✓'} ${esc(s.band_note)}</div>` : ''}
      <h3>Natural frequencies</h3><p class="small muted">Click a mode to show it.</p>
      <table class="summary modes"><tr><td><b>Mode</b></td><td><b>Frequency</b></td><td><b>Effective mass x / y / z</b></td></tr>
      ${el.map(m => `<tr data-mode="${m.elastic_index}" class="${S.field === 'Mode ' + m.elastic_index ? 'sel' : ''}" style="cursor:pointer"><td>${m.elastic_index}</td>
        <td>${m.in_excitation_band ? '⚠ ' : m.near_excitation_band ? '<span title="Within 1.4x of the excitation range: below the recommended margin" style="color:#d08700">△</span> ' : ''}<b>${fmt(m.frequency_Hz)}</b> Hz</td>
        <td>${m.effective_mass_fraction ? `${pct(m.effective_mass_fraction.x)} / ${pct(m.effective_mass_fraction.y)} / ${pct(m.effective_mass_fraction.z)} %` : '<span class="muted">n/a (free part)</span>'}</td></tr>`).join('')}
      ${s.cumulative_mass_fraction ? `<tr><td colspan="2" class="muted">Sum of these modes</td><td class="muted">${pct(s.cumulative_mass_fraction.x)} / ${pct(s.cumulative_mass_fraction.y)} / ${pct(s.cumulative_mass_fraction.z)} %</td></tr>` : ''}</table>
      <label class="row small" style="margin-top:6px"><input type="checkbox" id="animModes" ${S.animate !== false ? 'checked' : ''}> Animate</label>
      <p class="small muted">${s.unknowns?.toLocaleString()} unknowns · ${fmt(s.seconds)} s${s.added_mass_kg ? ` · added mass ${fmt(s.added_mass_kg * 1000)} g` : ''}${s.n_rigid_body_modes ? ` · ${s.n_rigid_body_modes} rigid-body modes hidden` : ''} · colours = relative motion</p>`;
    if (s.sine) {
      const q = s.sine, w = q.worst;
      h += `<h3>Shake test: ${fmt(q.accel_g)} g sine along ${esc(q.axis)}, ${fmt(q.damping_percent)} % damping</h3>
        <div class="kpis">${kpi('Highest stress', fmt(w.max_stress_MPa), 'MPa')}${kpi('at', fmt(w.frequency_Hz), 'Hz')}
          ${kpi('Largest movement', fmt(w.max_displacement_mm), 'mm')}${w.safety_factor_yield != null ? kpi('Safety factor (yield)', fmt(w.safety_factor_yield, 2)) : ''}</div>
        <canvas class="chart" id="sineChart" style="height:150px"></canvas>
        <p class="small muted">Highest stress anywhere in the part against the shaking frequency (both axes logarithmic), ${fmtS(q.range_Hz[0])}–${fmtS(q.range_Hz[1])} Hz. Each peak is a resonance; the fields "Shake test: …" show the part at the worst one.</p>
        ${q.peaks.length ? `<table class="summary"><tr><td><b>Mode</b></td><td><b>Frequency</b></td><td><b>Stress</b></td><td><b>Movement</b></td><td><b>Acceleration</b></td></tr>
        ${q.peaks.map(k => `<tr style="white-space:nowrap"><td>${k.mode}</td><td>${fmt(k.frequency_Hz)} Hz</td><td><b>${fmt(k.max_stress_MPa)}</b> MPa</td><td>${fmtS(k.max_displacement_mm)} mm</td><td>${fmt(k.max_acceleration_g)} g</td></tr>`).join('')}</table>` : ''}`;
    }
  } else if (st === 'aeroelastic_flutter_cfd') {
    h += flutterCfdSummaryHtml(s, esc, fmt)
      + `<h3>Damping against air speed</h3><canvas class="chart" id="cfdDamp" style="height:150px"></canvas>
         <h3>Modal motion after the release</h3><canvas class="chart" id="cfdSeries" style="height:130px"></canvas>`;
  } else if (st === 'aeroelastic_flutter') {
    const fl = s.flutter, dv = s.divergence, cr = s.critical, mg = s.margin;
    const el = (s.modes || []).filter(m => !m.rigid_body);
    h += cr
      ? `<div class="kpis">${kpi(cr.type === 'flutter' ? 'Flutter speed' : 'Divergence speed', fmt(cr.speed_m_s), 'm/s')}
          ${fl && cr.type === 'flutter' ? kpi('Flutter frequency', fmt(fl.frequency_Hz), 'Hz') : ''}
          ${kpi('Mach', fmt(cr.mach, 3))}${mg ? kpi('Margin on design speed', fmt(mg.ratio, 3), `(needs ≥ ${fmt(mg.required, 3)})`) : ''}</div>
        ${mg && mg.ratio < mg.required ? `<div class="note warn small">△ Flutter margin ${fmt(mg.ratio, 3)} is below the ${fmt(mg.required, 3)} usually required.</div>` : mg ? `<div class="note small">✓ Margin ${fmt(mg.ratio, 3)} on the design speed ${fmt(mg.design_speed_m_s)} m/s.</div>` : ''}`
      : `<div class="kpis">${kpi('First natural frequency', fmt(s.first_frequency_Hz), 'Hz')}${kpi('Highest speed examined', fmt(s.speed_range_m_s[1]), 'm/s')}</div>
        <div class="note small">✓ No flutter or divergence up to ${fmt(s.speed_range_m_s[1])} m/s.</div>`;
    if (fl) h += `<p class="small">Flutter motion: ${fl.mode_content.map(c => `mode ${c.mode} (${fmt(c.share_percent, 3)} %)`).join(' with ')} - the modes merge at ${fmt(fl.frequency_Hz)} Hz. Equivalent airspeed ${fmt(fl.equivalent_airspeed_m_s)} m/s.</p>`;
    if (dv) h += `<p class="small">Divergence (static instability): ${fmt(dv.speed_m_s)} m/s (Mach ${fmt(dv.mach, 3)}).</p>`;
    else if (s.divergence_speed_any_m_s) h += `<p class="small muted">Divergence would occur at ${fmt(s.divergence_speed_any_m_s)} m/s, above the speeds examined.</p>`;
    h += `<h3>Frequency against air speed</h3><canvas class="chart" id="flutterF" style="height:140px"></canvas>
      <h3>Damping against air speed</h3><canvas class="chart" id="flutterD" style="height:120px"></canvas>
      <p class="small muted">Each line follows one mode as the air speed rises. The motion grows where a damping line crosses zero (flutter); the modes approach each other in frequency just before.</p>
      <h3>Shapes</h3><p class="small muted">Click a row to show it${fl ? ' (the flutter motion is shown at the phase of its largest movement)' : ''}.</p>
      <table class="summary modes"><tr><td><b>Shape</b></td><td><b>Frequency</b></td></tr>
      ${fl ? `<tr data-field="Flutter mode" class="${S.field === 'Flutter mode' ? 'sel' : ''}" style="cursor:pointer"><td><b>Flutter mode</b></td><td>${fmt(fl.frequency_Hz)} Hz at ${fmt(fl.speed_m_s)} m/s</td></tr>` : ''}
      ${dv ? `<tr data-field="Divergence shape" class="${S.field === 'Divergence shape' ? 'sel' : ''}" style="cursor:pointer"><td><b>Divergence shape</b></td><td>at ${fmt(dv.speed_m_s)} m/s</td></tr>` : ''}
      ${el.map(m => `<tr data-mode="${m.elastic_index}" class="${S.field === 'Mode ' + m.elastic_index ? 'sel' : ''}" style="cursor:pointer"><td>Mode ${m.elastic_index}</td><td>${fmt(m.frequency_Hz)} Hz</td></tr>`).join('')}</table>
      <label class="row small" style="margin-top:6px"><input type="checkbox" id="animModes" ${S.deform.mode === 'animate' ? 'checked' : ''}> Animate</label>
      <p class="small muted">Air ${fmt(s.air.density_kg_m3, 3)} kg/m³ at ${fmt(s.air.altitude_m)} m · ${s.strips.count} strips · span ${fmt(s.strips.span_m * 1000)} mm · aspect ratio ${fmt(s.strips.aspect_ratio, 3)} · structure ${fmt(s.structure_mass_kg * 1000)} g · ${fmt(s.seconds)} s</p>`;
  } else if (st === 'static_structural' || st === 'thermal_stress') {
    const sf = s.min_safety_factor;
    h += `<div class="kpis">${s.thermal ? kpi('Temperature range', `${fmt1(s.thermal.T_min)} to ${fmt1(s.thermal.T_max)}`, '°C') + kpi('Stress-free at', fmt1(s.thermal.T_ref), '°C') : ''}${kpi('Max von Mises stress', fmt(s.max_von_mises_MPa), 'MPa')}${kpi('Max deformation', fmt(s.max_displacement_mm), 'mm')}
      ${sf != null ? kpi('Safety factor (yield)', fmt(sf, 2), '') : ''}</div>
      <h3>Parts</h3><table class="summary">${s.bodies.map(b => `<tr><td>${esc(b.name)}</td><td>max ${fmt(b.max_von_mises_MPa)} MPa${b.yield_MPa ? ` · yield ${fmt(b.yield_MPa)} MPa · <b>SF ${fmt(b.safety_factor, 2)}</b>` : ' · no yield strength (brittle/composite)'} · ${fmt(b.max_displacement_mm)} mm</td></tr>`).join('')}</table>
      <p class="small">Load ${fmtVec(s.applied_force_N)} N · reaction ${fmtVec(s.reaction_force_N, s.applied_force_N)} N (x / y / z)</p>
      ${Object.entries(s.boundary_conditions || {}).some(([, b]) => b.reaction_N) ? `<details><summary class="small">Force at each support</summary><table class="summary">${Object.entries(s.boundary_conditions).filter(([, b]) => b.reaction_N).map(([k, b]) => `<tr><td>${esc(k)}</td><td>${fmtVec(b.reaction_N, s.applied_force_N)} N</td></tr>`).join('')}</table></details>` : ''}
      <label class="row small" style="margin-top:6px"><input type="checkbox" id="animModes" ${S.animate !== false ? 'checked' : ''}> Animate deformation</label>
      <p class="small muted">${s.n_dof?.toLocaleString()} unknowns · ${fmt(s.solve_time_s)} s · unaveraged peak ${fmt(s.peak_von_mises_unaveraged_MPa)} MPa${s.stress_error_percent != null ? ` · <span title="How far the stress of the single elements is from the smoothed stress, in the energy norm. Below about 10 %: the mesh resolves the stress field well. The field 'Stress error estimate' shows where it is largest.">estimated stress error ${fmt(s.stress_error_percent, 2)} %</span>` : ''}</p>`;
  } else if (st === 'cht_enclosure' && s.natural) {
    const eb = s.energy_imbalance_percent;
    if (eb > 5) h += `<div class="note warn" style="margin-bottom:8px">Energy balance: ${fmt(s.heat_out_W)} W leave of ${fmt(s.heat_input_W)} W (${fmt(eb, 2)}% off). Continue the run or refine the mesh before trusting the temperatures.</div>`;
    h += `<div class="kpis">${kpi('Hottest: ' + (s.hottest_part?.label || ''), fmt1(s.hottest_part?.T_max), '°C')}${kpi('Thermal resistance (to the room)', fmt(s.thermal_resistance_K_per_W), 'K/W')}
      ${kpi('Max air speed', fmt(s.velocity_max, 2), 'm/s')}${kpi('Room', fmt1(s.room_T), '°C')}</div>
      <h3>Parts</h3><table class="summary">${Object.entries(s.solids).map(([k, b]) => `<tr><td>${esc(b.label || k)}${b.heat_W ? ` (${fmt(b.heat_W)} W)` : ''}</td><td>max ${fmt1(b.T_max)} °C</td></tr>`).join('')}</table>
      <h3>Where the heat goes (no fan)</h3><table class="summary">
        <tr><td>Warm air rising out of the openings</td><td>${fmt(s.heat_carried_by_air_W)} W (${fmt(s.flow_m3_per_h, 2)} m³/h${s.heat_conducted_at_openings_W ? `; ${fmt(s.heat_conducted_at_openings_W, 2)} W of it conducted where room air comes in next to warm air - a larger air box makes this smaller` : ''})</td></tr>
        ${s.radiation ? `<tr><td>Radiation out to the room</td><td>${fmt(s.heat_radiated_out_W)} W</td></tr>` : ''}
        ${s.heat_through_walls_W ? `<tr><td>Through cooled walls</td><td>${fmt(s.heat_through_walls_W)} W</td></tr>` : ''}
        <tr><td><b>Total</b></td><td>${fmt(s.heat_out_W)} W of ${fmt(s.heat_input_W)} W in${eb != null ? ` (${fmt(eb, 2)} % off)` : ''}</td></tr></table>
      <p class="small muted">${esc(s.flow_regime?.note || '')}.${s.radiation ? '' : ' Radiation is off: with no fan it often carries 30-50 % of the heat, so these temperatures are on the high side.'}</p>`;
  } else if (st === 'cht_enclosure') {
    const eb = s.transient ? 0 : s.energy_imbalance_percent ?? (s.heat_input_W ? Math.abs(s.heat_carried_by_air_W - s.heat_input_W) / s.heat_input_W * 100 : 0);  // (transient: heat is still being stored)
    if (eb > 5) h += `<div class="note warn" style="margin-bottom:8px">Energy balance: the air carries away ${fmt(s.heat_carried_by_air_W)} W of ${fmt(s.heat_input_W)} W (${fmt(eb, 2)}% off). Continue the run or refine the mesh before trusting the temperatures.</div>`;
    h += `<div class="kpis">${kpi('Hottest: ' + (s.hottest_part?.label || ''), fmt1(s.hottest_part?.T_max), '°C')}${kpi('Thermal resistance', fmt(s.thermal_resistance_K_per_W), 'K/W')}
      ${kpi('Air temperature rise', fmt1((s.outlet_T_mixed ?? s.outlet_T_area_avg) - s.inlet_T), 'K')}${kpi('Pressure drop (total)', fmt(s.pressure_drop_Pa), 'Pa')}</div>
      <h3>Parts</h3><table class="summary">${Object.entries(s.solids).map(([k, b]) => `<tr><td>${esc(b.label || k)}${b.heat_W ? ` (${fmt(b.heat_W)} W)` : ''}</td><td>max ${fmt1(b.T_max)} °C</td></tr>`).join('')}</table>
      <p class="small muted">Air flow ${fmt(s.flow_m3_per_h)} m³/h · outlet ${fmt1(s.outlet_T_mixed ?? s.outlet_T_area_avg)} °C (mixed mean)${s.static_pressure_drop_Pa != null ? ` · static pressure drop ${fmt(s.static_pressure_drop_Pa)} Pa` : ''}.
      ${s.transient ? `At the end time the air carries away ${fmt(s.heat_carried_by_air_W)} W of ${fmt(s.heat_input_W)} W; the rest is still heating the parts.` : `Energy balance: the air carries away ${fmt(s.heat_carried_by_air_W)} W of ${fmt(s.heat_input_W)} W (${fmt(eb, 2)}% off).`}
      ${s.flow_regime?.note && !s.flow_regime.transitional ? '<br>' + esc(s.flow_regime.note) + '.' : ''}</p>
      ${s.flow_regime?.transitional ? `<div class="note warn small">${S.p.model_check && !S.p.model_check.stale
        ? esc(s.flow_regime.note.split(';')[0]) + `; transitional range - measured model spread on this mesh: ${S.p.model_check.rows.map(r => `${esc(r.name)} ${fmt(r.change_percent, 2)} %`).join(', ')} (see Flow model below).`
        : esc(s.flow_regime.note) + '. Use "Compare with the other flow model" below to bracket the answer.'}</div>` : ''}
      ${s.convergence?.converged && s.convergence?.monitor?.dp_band_percent != null ? `<p class="small muted">Pressure drop wanders by up to ±${fmt(s.convergence.monitor.dp_band_percent, 2)} % between 150-iteration averages over the settled part of the run (steady solver, slightly unsteady flow behind the fins).</p>` : ''}
      ${s.correlation_check && (!settled || s.correlation_check.skipped) ? `<p class="small muted">Hand-calculation cross-check skipped: the run has not converged.</p>` : ''}
      ${s.correlation_check && settled && !s.correlation_check.skipped ? `<div class="note small ${Math.abs(s.correlation_check.difference_percent) > (s.correlation_check.expected_band_percent || 25) ? 'warn' : ''}"><b>Hand-calculation cross-check.</b> ${esc(s.correlation_check.note)}</div>` : ''}`;
  } else {
    const rotDrag = s.rotor_drag_N != null && s.rotor_drag_coeff_s_per_m;
    h += `<div class="kpis">${s.drag_N != null ? kpi(rotDrag ? 'Frame drag' : 'Drag', fmt(s.drag_N), 'N') : ''}${rotDrag ? kpi('Rotor drag (model)', fmt(s.rotor_drag_N), 'N') : ''}${s.CdA_m2 != null ? kpi('Frame CdA', fmt(s.CdA_m2 * 1e4), 'cm²') : ''}
      ${S.p.rotors?.length ? kpi('Aero vertical force', fmt(s.vertical_aero_force_N), 'N') + kpi('Rotor thrust', fmt(s.rotor_thrust_total_N), 'N')
        : (s.coefficients ? kpi('Drag coefficient', fmt(s.coefficients.CD, 3), '') + kpi('Lift', fmt(s.coefficients.lift_N), 'N') : '')}</div>
      ${s.coefficients ? `<p class="small muted">Coefficients (reference area ${fmt(s.coefficients.A_ref_m2 * 1e4)} cm², ${esc(s.coefficients.A_ref_source)}; reference length ${fmt(s.coefficients.L_ref_m * 1e3)} mm): C<sub>D</sub> ${fmt(s.coefficients.CD, 3)}, C<sub>L</sub> ${fmt(s.coefficients.CL, 3)}, C<sub>S</sub> ${fmt(s.coefficients.CS, 3)}${s.coefficients.Cm != null ? `, C<sub>m</sub> ${fmt(s.coefficients.Cm, 3)} (pitch, nose-up +), C<sub>l</sub> ${fmt(s.coefficients.Cl, 3)} (roll), C<sub>n</sub> ${fmt(s.coefficients.Cn, 3)} (yaw)` : ''}.${s.aero_moment_Nm && !S.p.rotors?.length ? ` Moments about the moment point: pitch ${fmt(s.aero_moment_Nm.pitch)}, roll ${fmt(s.aero_moment_Nm.roll)}, yaw ${fmt(s.aero_moment_Nm.yaw)} N·m.` : ''}</p>` : ''}
      ${rotDrag ? `<p class="small muted">Rotor drag model k = ${fmt(s.rotor_drag_coeff_s_per_m, 3)} s/m</p>` : ''}
      ${s.trim_history_note ? `<div class="note" style="margin-top:8px">${esc(s.trim_history_note)} First run: pitch ${fmt1(s.trim_history[0].pitch_deg)}°, frame drag ${fmt(s.trim_history[0].drag_N)} N.</div>` : ''}
      ${s.trim_note ? `<div class="note" style="margin-top:8px">${esc(s.trim_note)}${s.suggested_pitch_deg != null ? `<br>Estimated trim for level flight: pitch <b>${fmt1(s.suggested_pitch_deg)}°</b>${s.trim_sensitivity ? ` (${fmt1(s.trim_sensitivity[0].pitch_deg)}–${fmt1(s.trim_sensitivity[2].pitch_deg)}° for rotor-drag k ±40 %)` : ''}, total thrust <b>${fmt(s.trim_thrust_total_N)} N</b> (now ${fmt1(S.p.settings.pitch_deg)}°). ${esc(s.trim_note_detail || '')}
        <div class="row" style="margin-top:6px"><button class="small" data-action="applyTrim" data-pitch="${s.suggested_pitch_deg}">Use ${fmt1(s.suggested_pitch_deg)}° and re-run${rotDrag ? '' : ' (lower bound: rotor drag not included)'}</button></div>` : ''}</div>` : ''}
      ${s.pitch_moment_Nm != null ? `<p class="small muted">Net moments about the CG (aerodynamics + rotor thrusts): pitch ${fmt(s.pitch_moment_Nm)} N·m (nose-up +), roll ${fmt(s.roll_moment_Nm)} N·m, yaw ${fmt(s.yaw_moment_Nm)} N·m${s.aero_pitch_moment_Nm != null ? `; aerodynamic part alone: pitch ${fmt(s.aero_pitch_moment_Nm)} N·m` : ''}.
        ${s.pitch_moment_parts_Nm ? `Aerodynamic pitch moment = frame ${fmt(s.pitch_moment_parts_Nm.frame)} + rotor drag at the hubs ${fmt(s.pitch_moment_parts_Nm.rotor_drag)} N·m.` : ''}
        ${s.rotor_thrust_trim_N ? `Rotor thrusts that balance pitch/roll: ${s.rotor_thrust_trim_N.map(x => fmt(x)).join(' / ')} N${s.cg_set === false ? ' (indicative: the CG height is not set, and the rotor-drag moment depends on it)' : ''}.` : ''}
        ${s.cg_sensitivity ? `<br>With the CG ${s.cg_sensitivity.map(c => `${fmt(c.cg_up_mm)} mm up: ${c.rotor_thrust_trim_N.map(x => fmt(x)).join('/')} N`).join(' · ')}.` : ''}</p>` : ''}
      ${s.trim_sensitivity ? `<p class="small muted">Sensitivity to the rotor-drag coefficient k (frame forces from this run): ${s.trim_sensitivity.map(t => `k = ${fmt(t.k_s_per_m, 2)} s/m → pitch ${fmt1(t.pitch_deg)}°, thrust ${fmt(t.thrust_N)} N, total drag ${fmt(t.total_drag_N)} N`).join(' · ')}.</p>` : ''}
      ${s.moving_zones?.length ? `<h3>Rotating zones</h3><table class="summary">${s.moving_zones.map(z => `<tr><td>${esc(z.name)}<br><span class="muted small">${fmt(z.rpm)} rpm · ${z.method === 'sliding' ? 'sliding mesh' : 'MRF'}</span></td>
        <td>${z.propeller ? propHtml(z.propeller) : `torque <b>${fmt(z.torque_Nm)} N·m</b> about its axis${z.torque_Nm * z.rpm < 0 ? ' (against the rotation)' : ' (driving the rotation)'}<br>power ${fmt(Math.abs(z.power_W))} W ${z.power_W >= 0 ? 'to turn it' : 'given by the flow'}`}</td></tr>`).join('')}</table>
        <p class="small muted">Torque of the air on all walls about each zone's axis${s.transient ? ' (averaged over the second half of the run for the forces; moment at the end time)' : ''}.
        ${s.moving_zones.some(z => z.method === 'mrf') ? 'MRF: the mesh does not turn - the air in the zone is solved in a frame rotating with the part, so the results show one blade position. ' : ''}${s.moving_zones.some(z => z.method === 'sliding') ? 'Sliding mesh: the zone cells turn with the part; the time slider shows them rotating.' : ''}</p>` : ''}
      ${s.yPlus ? `<p class="small muted">Wall y+: mean ${fmt(s.yPlus.mean)}, max ${fmt(s.yPlus.max)}</p>` : ''}`;
  }
  return h;
}

async function showResults() {
  if (S.step !== 'results' || !S.results || !S.field) return;
  const hide = S.hideAir ? (S.results.summary?.default_hidden_regions || []).join(',') : '';
  const ti = S.results.times?.length > 1 ? `&time=${S.timeIdx ?? -1}` : '';
  const url = S.clip.axis ? P(`/results/clip?field=${encodeURIComponent(S.field)}&axis=${S.clip.axis}&pos=${clipValue()}&hide=${hide}${ti}`)
    : P(`/results/surface?field=${encodeURIComponent(S.field)}&hide=${hide}&groups=${encodeURIComponent((S.results.summary?.default_groups || []).join(','))}${ti}`);
  let d;
  try { d = await api(url); } catch (e) { toast(e.message, true); return; }
  // with a section, show the cut CAD geometry behind the plane for context
  viewer.showGeometry(!!S.clip.axis);
  viewer.setGeometryClip(S.clip.axis ? S.clip.axis : null, S.clip.axis ? clipValue() : 0);
  spinTo(ti ? S.results.times[S.timeIdx ?? S.results.times.length - 1] : 0);
  if (S.clip.axis) refreshViewerColors();
  const fi = ti && S.results.fields.find(f => f.name === S.field);  // over time: one colour scale for all times
  const fld = !fi && S.results.fields.find(f => f.name === S.field);
  const gf = S.clip.axis && fld;   // a section: the range of the whole field
  const U = S.results.summary?.speed_m_s;
  // (pressure: the extremes sit on the wall at the leading edge and the suction peak - a scale from them leaves the field
  // around the body in one colour; the range that holds nearly all the air shows it, the peaks run off the scale)
  const robustP = fld && S.field === 'Pressure' && S.p.study === 'external_aero' && fld.p01 != null ? [fld.p01, fld.p99] : null;
  const gr = gf && (S.field === 'Velocity' && U > 0 && S.p.study === 'external_aero' ? [0, Math.min(gf.max, 1.35 * U)]
    : robustP || [gf.min, gf.max]);
  const range = S.autoRange || !S.range ? (fi ? [fi.min, fi.max] : gr || robustP || [d.min, d.max]) : S.range;
  if (S.autoRange) { const a = $('#rngMin'), b = $('#rngMax'); if (a) { a.value = fmt(range[0], 4); b.value = fmt(range[1], 4); } }
  // probing reads the picture as drawn: no swinging shape while it is on; labels belong to one field / time / cut
  viewer.setOverlay(d, { range, opacity: S.surfOpacity ?? 1, animate: S.animate !== false && !S.recording && !S.probeOn });
  const pk = [S.field, S.timeIdx ?? -1, S.clip.axis, S.clip.axis ? S.clip.pos : 0].join('|');
  if (pk !== S.probeKey) { S.probeKey = pk; S.probes = []; }
  S.probeUnit = d.unit; viewer.setProbes(S.probes || []);
  await showIso(ti);
  S.shownRange = range;   // (the contour planes of this field use it too)
  await showContours(ti);
  await showVis(ti);
  const mode = S.p.study === 'vibration_modal' && (S.results.summary || S.p.solve.summary)?.modes?.find(m => 'Mode ' + m.elastic_index === S.field);
  const fs = S.p.study === 'aeroelastic_flutter' ? (S.results.summary || S.p.solve.summary) : null;
  const fmode = fs?.modes?.find(m => 'Mode ' + m.elastic_index === S.field);
  const flabel = fs && (S.field === 'Flutter mode' && fs.flutter ? `Flutter mode – ${fmt(fs.flutter.frequency_Hz)} Hz at ${fmt(fs.flutter.speed_m_s)} m/s`
    : S.field === 'Divergence shape' && fs.divergence ? `Divergence shape – at ${fmt(fs.divergence.speed_m_s)} m/s` : fmode ? `${S.field} – ${fmt(fmode.frequency_Hz)} Hz` : null);
  legend(flabel || (mode ? `${S.field} – ${fmt(mode.frequency_Hz)} Hz` : S.field), d.unit, range);
}

// the parts inside a rotating zone (the propeller), from their own force monitor: thrust, torque, coefficients
function propHtml(q) {
  const g = q.thrust_N / 9.81 * 1000;
  return `thrust <b>${fmt(q.thrust_N)} N</b> (${fmt(g, 3)} gf)<br>torque <b>${fmt(q.torque_Nm)} N·m</b> · power <b>${fmt(q.power_W)} W</b>`
    + (q.CT != null ? `<br><span class="small">C<sub>T</sub> ${fmt(q.CT, 3)} · C<sub>P</sub> ${fmt(q.CP, 3)} · J ${fmt(q.J, 3)}${q.efficiency ? ` · η ${fmt(100 * q.efficiency, 3)} %` : ''} · D ${fmt1(q.diameter_m * 1000)} mm · tip Mach ${fmt(q.tip_mach, 2)}</span>` : '')
    + `<br><span class="muted small">averaged over the ${esc(q.average_over)}${q.thrust_fluctuation_percent != null ? `; thrust varies ±${fmt(q.thrust_fluctuation_percent, 2)} %` : ''}</span>`;
}
// sliding zones of a transient result: the CAD parts inside them (drawn behind a section) turn with the mesh, so
// the propeller is where the flow saw it at that time - in the view, when playing and in the video
function spinZones() {
  if (!(S.results?.times?.length > 1)) return null;
  return (S.p?.moving_zones || []).filter(z => z.method === 'sliding' && z.rpm).map(z =>
    ({ center: z.center, axis: AXIS_VEC[z.axis], radius: z.radius, length: z.length, omega: z.rpm * Math.PI / 30 }));
}
function spinTo(t) {
  const zs = spinZones(), key = JSON.stringify(zs);
  if (key !== S.spinKey) { viewer.setSpinZones(zs); S.spinKey = key; }
  viewer.spin(t);
}

// vortex surfaces: isosurface of the Q-criterion (Q > 0: rotation beats strain - vortex cores), coloured by a field;
// the level is a fraction of the largest Q over the run (log slider), so tip vortices and wake can be picked out
S.iso = { on: null, k: null, color: 'Velocity', opacity: 1, gap: null, smooth: 1 };
async function showIso(ti) {
  const qf = S.results?.fields.find(f => f.name === 'Q-criterion');
  if (!isoOn() || !qf || S.step !== 'results') { viewer.setIso(null); return; }
  const level = isoLevel(qf);
  const cf = S.results.fields.find(f => f.name === S.iso.color) || qf;
  const hide = (S.results.summary?.default_hidden_regions || []).join(',');
  try {
    const d = await api(P(`/results/iso?field=Q-criterion&level=${level}&color=${encodeURIComponent(cf.name)}&hide=${hide}&wall_gap=${isoGap() / 1000}&smooth=${S.iso.smooth}${ti || ''}`));
    viewer.setIso(d, { range: [cf.p01 ?? cf.min, cf.p99 ?? cf.max], opacity: S.iso.opacity });  // (the range that holds the flow: the extremes sit on the blades)
    const n = $('#isoInfo'); if (n) n.textContent = `Q = ${fmt(level, 3)} 1/s² · ${d.n_tris.toLocaleString()} triangles`;
  } catch (e) { toast(e.message, true); }
}
// the shear next to the walls gives a large Q too: leave out a thin layer there (default 2 % of the reference
// length, or of the smallest rotating zone's radius: 2 % of a whole drone frame hid the tip vortices where they
// leave the blades)
// Q is largest on the walls (boundary-layer shear, orders of magnitude above the vortices in the flow), so the
// starting level is physical: a vortex that turns at the rotation rate of the propeller (or, with nothing rotating,
// at the flow speed over the reference length) has Q of about half the rate squared
function isoRef() {
  const w = Math.max(0, ...(S.p?.moving_zones || []).map(z => Math.abs(z.rpm || 0) * Math.PI / 30));
  const L = S.results?.summary?.coefficients?.L_ref_m, U = S.p?.settings?.speed;
  const rate = w > 0 ? w : (L && U ? U / L : 0);
  return rate > 0 ? 0.5 * rate * rate : null;
}
function isoK(qf) {  // slider position: decades below the largest Q
  if (S.iso.k != null) return S.iso.k;
  const ref = isoRef();
  return ref ? Math.min(60, Math.max(2, Math.round(10 * Math.log10(qf.max / ref)))) : 40;
}
const isoLevel = qf => qf.max * 10 ** (-isoK(qf) / 10);
const isoOn = () => S.iso.on ?? !!S.p?.moving_zones?.length;  // on by default where there is a propeller to look at
// contour planes: cuts through the flow coloured by any field (velocity, pressure, ...), each with its own opacity;
// they stay next to the surfaces, so a velocity plane can sit behind the vortex tubes
S.contours = [];
const contourPos = c => {  // position of the plane in the model: slider 0-1000 over the limits of the solved model
  const b = resultsBounds(), ax = 'xyz'.indexOf(c.axis);
  return b[ax] + (b[ax + 3] - b[ax]) * c.pos / 1000;
};
async function showContours(ti) {
  const shownRange = S.shownRange;
  if (S.step !== 'results' || !S.results) { viewer.setContours(null); return; }
  const list = [];
  for (const c of S.contours) {
    const f = S.results.fields.find(f => f.name === c.field); if (!f || f.name === 'y+') continue;
    try {
      const payload = await api(P(`/results/plane?field=${encodeURIComponent(c.field)}&axis=${c.axis}&pos=${contourPos(c)}&box=${visBox().join(',')}&n=${Math.max(S.vis.res, 96)}${ti || ''}`));
      // automatic scale: the colours of the picture for the field shown on the model (one legend for all), else the
      // range that holds the flow
      c.shown = shownRange && c.field === S.field ? shownRange : [f.p01 ?? f.min, f.p99 ?? f.max];
      list.push({ payload, range: c.auto === false ? [c.lo, c.hi] : c.shown, opacity: c.opacity });
    } catch (e) { toast(e.message, true); }
  }
  viewer.setContours(list);
}
function contourHtml() {
  const fields = S.results.fields.filter(f => f.name !== 'y+');
  return `<h3>Contour planes</h3><p class="small muted" style="margin:2px 0 6px">Cuts through the flow coloured by a field. Add several, move them with the slider and make them see-through to look at the vortices behind.</p>
    ${S.contours.map((c, i) => `<div class="card" style="padding:6px 8px;margin-bottom:6px"><div class="grid2">
      <label class="field"><span>Field</span><select data-c="${i}" data-k="field">${fields.map(f => `<option ${f.name === c.field ? 'selected' : ''}>${esc(f.name)}</option>`).join('')}</select></label>
      <label class="field"><span>Plane</span><select data-c="${i}" data-k="axis">${['x', 'y', 'z'].map(a => `<option ${a === c.axis ? 'selected' : ''}>${a}</option>`).join('')}</select></label>
      <label class="field full"><span>Position <span class="unit">${c.axis} = ${fmt1(contourPos(c) * 1000)} mm</span></span><input type="range" min="0" max="1000" value="${c.pos}" data-c="${i}" data-k="pos"></label>
      <label class="field"><span>Opacity</span><input type="range" min="0.1" max="1" step="0.05" value="${c.opacity}" data-c="${i}" data-k="opacity"></label>
      <label class="field" title="Automatic: the range that holds nearly all of the flow. Untick it to type your own range"><span>Scale</span><span style="display:flex;gap:4px;align-items:center"><input type="checkbox" data-c="${i}" data-k="auto" ${c.auto === false ? '' : 'checked'} style="width:auto"> auto</span></label>
      <label class="field"><span>From – to</span><span style="display:flex;gap:4px"><input type="number" step="any" data-c="${i}" data-k="lo" value="${+(c.auto === false ? c.lo : c.shown?.[0] ?? 0).toPrecision(4)}" ${c.auto === false ? '' : 'disabled'}><input type="number" step="any" data-c="${i}" data-k="hi" value="${+(c.auto === false ? c.hi : c.shown?.[1] ?? 1).toPrecision(4)}" ${c.auto === false ? '' : 'disabled'}></span></label>
      <button class="danger small" data-c="${i}" data-k="del" title="Remove this contour plane">✕ Remove</button></div></div>`).join('')}
    <button class="ghost small" id="addContour">+ Add contour plane</button>`;
}
function bindContours() {
  const re = () => showContours(S.results.times?.length > 1 ? `&time=${S.timeIdx ?? -1}` : '');
  let t; const later = () => { clearTimeout(t); t = setTimeout(re, 120); };
  const redraw = () => { $('#contourBox').innerHTML = contourHtml(); bindContours(); };
  $('#addContour')?.addEventListener('click', () => {
    const fd = S.p.flow_dir || [1, 0, 0], ax = 'xyz'[fd.map(Math.abs).indexOf(Math.max(...fd.map(Math.abs)))];
    const side = 'xyz'.replace(ax, '')[1];  // a plane along the flow (not across it): the wake is in view
    S.contours.push({ field: S.results.fields.find(f => f.name === 'Velocity') ? 'Velocity' : S.field, axis: side, pos: 500, opacity: 0.6 });
    redraw(); re();
  });
  $('#contourBox')?.querySelectorAll('[data-c]').forEach(el => {
    const c = S.contours[+el.dataset.c], k = el.dataset.k;
    if (k === 'del') el.onclick = () => { S.contours.splice(+el.dataset.c, 1); redraw(); re(); };
    else el.addEventListener(el.type === 'range' ? 'input' : 'change', () => {
      if (k === 'auto') { c.auto = el.checked; if (!c.auto) { c.lo = c.shown?.[0] ?? 0; c.hi = c.shown?.[1] ?? 1; } redraw(); re(); return; }
      c[k] = ['pos', 'opacity', 'lo', 'hi'].includes(k) ? +el.value : el.value;
      if (k === 'axis' || k === 'field') redraw();
      else if (k === 'pos') el.closest('label').querySelector('.unit').textContent = `${c.axis} = ${fmt1(contourPos(c) * 1000)} mm`;
      later();
    });
  });
}
// ---- volume rendering, vector arrows, animated particles. Each has a scale (the range its colours and, for the
// volume, its opacity run over) that is automatic (the range of the data) or a custom value; arrows also have a length
// scale and particles a speed.
const visState = () => ({
  vis: { region: null, res: 96 },
  vol: { on: false, field: null, auto: true, lo: 0, hi: 1, density: 4, style: 'smoke', data: null, key: '' },
  vec: { on: false, axis: null, pos: 500, n: 24, auto: true, lo: 0, hi: 1, lenAuto: true, len: 1, data: null, key: '' },
  par: { on: false, seed: null, n: 1500, auto: true, lo: 0, hi: 1, speedAuto: true, speed: 1, size: 3, trail: 16, data: null, key: '' },
});
function resetVis() { Object.assign(S, visState()); viewer?.setVolume(null); viewer?.setArrows(null); viewer?.setParticles(null); }
Object.assign(S, visState());
// the region the three views look at (resampled on a grid): around the propeller / with the wake / the whole body
function visBox() {
  const fd = (S.p.flow_dir || [1, 0, 0]).map(Number), ax = fd.map(Math.abs).indexOf(Math.max(...fd.map(Math.abs)));
  let lo, hi, down, up, side;
  const mode = S.vis.region || (S.p.moving_zones?.length ? 'rotor' : 'wake');
  if (mode === 'rotor' && S.p.moving_zones?.length) {
    const z = S.p.moving_zones[0], R = z.radius;
    lo = z.center.map(c => c - 1.3 * R); hi = z.center.map(c => c + 1.3 * R); up = 0.7 * R; down = 3 * R;
  } else {
    const b = S.p.geometry.info.parts_bbox, e = Math.max(b[3] - b[0], b[4] - b[1], b[5] - b[2]);
    lo = b.slice(0, 3).map(v => v - 0.5 * e); hi = b.slice(3).map(v => v + 0.5 * e);
    up = 0; down = mode === 'body' ? 0 : 1.5 * e;
    if (mode === 'all') { const g = S.p.geometry.info.parts_bbox, pad = 1.5 * e; lo = g.slice(0, 3).map(v => v - pad); hi = g.slice(3).map(v => v + pad); up = down = 0; }
  }
  if (fd[ax] > 0) hi[ax] += down; else lo[ax] -= down;
  if (fd[ax] > 0) lo[ax] -= up; else hi[ax] += up;
  return lo.concat(hi);
}
const visFields = () => S.results.fields.filter(f => f.name !== 'y+').concat(visVectorField() ? [{ name: 'Vorticity', unit: '1/s' }] : []);
const visVectorField = () => S.results.fields.find(f => f.name === 'Velocity');
const fmtS = v => (Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-2 && v !== 0)) ? Number(v).toExponential(2) : fmt(v, 4);
async function visFetch(st, url, key) {
  if (st.key !== key) { st.data = await api(url); st.key = key; }
  return st.data;
}
async function showVis(ti) {
  const box = visBox().join(','), t = ti || '', res = S.vis.res, fd = (S.p.flow_dir || [1, 0, 0]).join(',');
  const info = (id, txt) => { const n = $(id); if (n) n.textContent = txt; };
  try {
    if (S.vol.on) {
      const f = S.vol.field || (visFields().find(f => f.name === 'Velocity') || visFields()[0]).name;
      info('#volInfo', 'Resampling…');
      const d = await visFetch(S.vol, P(`/results/volume?field=${encodeURIComponent(f)}&box=${box}&n=${res}${t}`), `${f}|${box}|${res}|${t}`);
      if (S.vol.applied !== d) { viewer.setVolume(d, {}); S.vol.applied = d; } applyVolume(); info('#volInfo', `${f}: ${fmtS(d.lo)} – ${fmtS(d.hi)} ${d.unit}${d.log ? ' (log scale)' : ''} · ${d.dims.join('×')} grid`);
    } else { viewer.setVolume(null); S.vol.applied = null; }
    if (S.vec.on && visVectorField()) {
      const ax = S.vec.axis || visPlaneAxis(), c = { axis: ax, pos: S.vec.pos };
      const d = await visFetch(S.vec, P(`/results/vectors?axis=${ax}&pos=${contourPos(c)}&box=${box}&n=${S.vec.n}${t}`), `${ax}|${S.vec.pos}|${box}|${S.vec.n}|${t}`);
      applyArrows(); info('#vecInfo', `${d.n} arrows · speed ${fmtS(d.min)} – ${fmtS(d.max)} ${d.unit}`);
    } else { viewer.setArrows(null); }
    if (S.par.on && visVectorField()) {
      info('#parInfo', 'Tracing…');
      const seed = S.par.seed || (S.p.moving_zones?.length ? 'zone' : 'upstream'), sbox = seed === 'zone' ? zoneSeedBox() : '';
      const d = await visFetch(S.par, P(`/results/particles?box=${box}&n=${S.par.n}&seed=${seed === 'zone' ? 'box' : seed}${sbox ? '&sbox=' + sbox : ''}&flow=${fd}&grid=${Math.min(S.vis.res, 120)}${t}`), `${seed}|${S.par.n}|${box}|${t}`);
      applyParticles(); info('#parInfo', `${d.n} particles · speed ${fmtS(d.min)} – ${fmtS(d.max)} ${d.unit} (paths in the flow at this time)`);
    } else { viewer.setParticles(null); S.par.applied = null; }
  } catch (e) { toast(e.message, true); }
}
// a disk of seeds just in front of the first rotating zone, a little wider than the propeller: the particles that
// pass through the propeller are the ones that show the slipstream and the tip vortices
function zoneSeedBox() {
  const z = S.p.moving_zones[0], R = z.radius, fd = (S.p.flow_dir || [1, 0, 0]).map(Number), ax = fd.map(Math.abs).indexOf(Math.max(...fd.map(Math.abs)));
  const lo = z.center.map(c => c - 1.1 * R), hi = z.center.map(c => c + 1.1 * R), off = (fd[ax] > 0 ? -1 : 1) * 0.9 * R;
  lo[ax] = z.center[ax] + off - 0.05 * R; hi[ax] = z.center[ax] + off + 0.05 * R;
  return lo.concat(hi).join(',');
}
function visPlaneAxis() {  // a plane along the flow, not across it
  const fd = (S.p.flow_dir || [1, 0, 0]).map(Math.abs), ax = 'xyz'[fd.indexOf(Math.max(...fd))];
  return 'xyz'.replace(ax, '')[1];
}
// custom or automatic scale: the numbers the data came with, or what the user typed
function visRange(st, d, def) { return st.auto ? [d[def[0]], d[def[1]]] : [st.lo, st.hi]; }
function applyVolume() {
  const d = S.vol.data; if (!d) return;
  let lo = d.lo, hi = d.hi;
  const U = S.p?.settings?.speed;
  if (d.log) lo = Math.max(d.lo, d.hi * 10 ** -(S.vol.field === 'Q-criterion' ? 3 : 1.3));  // automatic: the strongest decades
  else if (S.vol.field === 'Velocity' && U > 0) lo = Math.min(Math.max(d.lo, 1.15 * U), d.hi * 0.9);  // faster than the free stream: the slipstream
  const [a, b] = S.vol.auto ? [lo, hi] : [S.vol.lo, S.vol.hi];
  const nm = v => d.log ? Math.log10(Math.max(v, 1e-30) / d.lo) / Math.log10(d.hi / d.lo) : (v - d.lo) / ((d.hi - d.lo) || 1);
  viewer.setVolumeWindow({ lo: Math.min(1, Math.max(0, nm(a))), hi: Math.min(1, Math.max(0, nm(b))), density: S.vol.density, steps: 200, style: S.vol.style });
  const A = $('#volLo'), B = $('#volHi'); if (S.vol.auto && A) { A.value = +a.toPrecision(4); B.value = +b.toPrecision(4); }
}
function applyArrows() {
  const d = S.vec.data; if (!d) return;
  const range = S.vec.auto ? [d.min, d.max] : [S.vec.lo, S.vec.hi];
  const lenScale = S.vec.lenAuto ? 1.2 * d.h / (d.p95 || d.max || 1) : S.vec.len / 1000;
  viewer.setArrows(d, { range, lenScale });
  const A = $('#vecLo'), B = $('#vecHi'); if (S.vec.auto && A) { A.value = +range[0].toPrecision(4); B.value = +range[1].toPrecision(4); }
  const L = $('#vecLen'); if (S.vec.lenAuto && L) L.value = +(lenScale * 1000).toPrecision(3);
}
function applyParticles() {
  const d = S.par.data; if (!d) return;
  const range = S.par.auto ? [d.min, d.max] : [S.par.lo, S.par.hi];
  const rate = 25 * (S.par.speedAuto ? 1 : S.par.speed);
  if (S.par.applied === d && S.par.appliedTrail === S.par.trail) viewer.setParticleStyle({ range, rate, size: S.par.size });
  else { viewer.setParticles(d, { range, rate, size: S.par.size, trail: S.par.trail }); S.par.applied = d; S.par.appliedTrail = S.par.trail; }
  const A = $('#parLo'), B = $('#parHi'); if (S.par.auto && A) { A.value = +range[0].toPrecision(4); B.value = +range[1].toPrecision(4); }
  const V = $('#parSpeed'); if (S.par.speedAuto && V) V.value = 1;
}
function scaleRow(id, st, unit) {
  return `<label class="field" title="Automatic: the range of the data. Untick it to type your own range"><span>Scale <span class="unit">${unit}</span></span>
      <span style="display:flex;gap:4px;align-items:center"><input type="checkbox" id="${id}Auto" ${st.auto ? 'checked' : ''} style="width:auto"> auto</span></label>
    <label class="field"><span>From – to</span><span style="display:flex;gap:4px"><input type="number" step="any" id="${id}Lo" value="${st.lo}" ${st.auto ? 'disabled' : ''}><input type="number" step="any" id="${id}Hi" value="${st.hi}" ${st.auto ? 'disabled' : ''}></span></label>`;
}
function visHtml() {
  const fo = visFields().map(f => `<option ${f.name === (S.vol.field || 'Velocity') ? 'selected' : ''}>${esc(f.name)}</option>`).join('');
  const hasVec = !!visVectorField(), mode = S.vis.region || (S.p.moving_zones?.length ? 'rotor' : 'wake');
  const unitOf = n => S.results.fields.find(f => f.name === n)?.unit || '';
  return `<h3>Volume rendering</h3><p class="small muted" style="margin:2px 0 6px">The whole flow as a see-through cloud: colour and opacity follow the value, so wakes and slipstreams show up in 3D. The region and grid are shared by the three views below.</p>
    <div class="grid2">
      <label class="field" title="The box the three views look at"><span>Region</span><select id="visRegion">${[['rotor', 'Around the propeller'], ['wake', 'Body and wake'], ['body', 'Body only'], ['all', 'Whole air box']].map(([v, l]) => `<option value="${v}" ${v === mode ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="field" title="Grid points along the longest side of the region: more = finer, slower"><span>Grid</span><select id="visRes">${[48, 64, 96, 128, 160].map(v => `<option ${v === S.vis.res ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
      <label class="field"><span>Show</span><input type="checkbox" id="volOn" ${S.vol.on ? 'checked' : ''} style="width:auto"></label>
      <label class="field"><span>Field</span><select id="volField">${fo}</select></label>
      ${scaleRow('vol', S.vol, esc(unitOf(S.vol.field || 'Velocity')))}
      <label class="field full"><span>Density</span><input type="range" id="volDens" min="0.5" max="30" step="0.5" value="${S.vol.density}"></label>
      <label class="field" title="Smoke: a pale cloud that thickens where the value is high, like smoke in the flow. Colour map: the colours of the scale (a see-through version of the surface colours). The cloud stops at the model in front of it either way"><span>Look</span><select id="volStyle">${[['smoke', 'Smoke'], ['map', 'Colour map']].map(([v, l]) => `<option value="${v}" ${v === S.vol.style ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <span class="small muted full" id="volInfo"></span></div>
    <h3>Vector arrows</h3><p class="small muted" style="margin:2px 0 6px">Arrows of the flow on a plane, pointing the way the air moves, coloured by speed.</p>
    ${hasVec ? `<div class="grid2">
      <label class="field"><span>Show</span><input type="checkbox" id="vecOn" ${S.vec.on ? 'checked' : ''} style="width:auto"></label>
      <label class="field"><span>Plane</span><select id="vecAxis">${['x', 'y', 'z'].map(a => `<option ${a === (S.vec.axis || visPlaneAxis()) ? 'selected' : ''}>${a}</option>`).join('')}</select></label>
      <label class="field full"><span>Position <span class="unit" id="vecPosLbl"></span></span><input type="range" id="vecPos" min="0" max="1000" value="${S.vec.pos}"></label>
      <label class="field" title="Arrows across the region"><span>Density</span><input type="number" id="vecN" min="4" max="80" step="1" value="${S.vec.n}"></label>
      <label class="field" title="Length of an arrow per unit of speed. Automatic: the typical arrow is about one spacing long"><span>Length <span class="unit">mm per m/s</span></span>
        <span style="display:flex;gap:4px;align-items:center"><input type="checkbox" id="vecLenAuto" ${S.vec.lenAuto ? 'checked' : ''} style="width:auto"> auto <input type="number" step="any" id="vecLen" value="${S.vec.len}" ${S.vec.lenAuto ? 'disabled' : ''}></span></label>
      ${scaleRow('vec', S.vec, 'm/s')}
      <span class="small muted full" id="vecInfo"></span></div>` : '<p class="small muted">No velocity field in these results.</p>'}
    <h3>Animated particles</h3><p class="small muted" style="margin:2px 0 6px">Dots carried along the flow, with a short tail. The paths follow the flow at the time shown (move the time slider to see it change).</p>
    ${hasVec ? `<div class="grid2">
      <label class="field"><span>Show</span><input type="checkbox" id="parOn" ${S.par.on ? 'checked' : ''} style="width:auto"></label>
      <label class="field" title="Where the particles start: a sheet upstream (like smoke released into the stream) or everywhere in the region"><span>Start</span><select id="parSeed">${[...(S.p.moving_zones?.length ? [['zone', 'In front of the propeller']] : []), ['upstream', 'Upstream sheet'], ['everywhere', 'Everywhere']].map(([v, l]) => `<option value="${v}" ${v === (S.par.seed || (S.p.moving_zones?.length ? 'zone' : 'upstream')) ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
      <label class="field" title="Length of the fading tail behind each dot (path steps)"><span>Tail</span><input type="number" id="parTrail" min="2" max="60" step="1" value="${S.par.trail}"></label>
      <label class="field"><span>Particles</span><input type="number" id="parN" min="50" max="6000" step="50" value="${S.par.n}"></label>
      <label class="field"><span>Dot size <span class="unit">px</span></span><input type="number" id="parSize" min="1" max="12" step="1" value="${S.par.size}"></label>
      <label class="field" title="Playback speed. Automatic: a particle moves at about 25 path steps per second; type a multiplier to slow it down or speed it up"><span>Speed <span class="unit">× playback</span></span>
        <span style="display:flex;gap:4px;align-items:center"><input type="checkbox" id="parSpeedAuto" ${S.par.speedAuto ? 'checked' : ''} style="width:auto"> auto <input type="number" step="any" min="0.05" id="parSpeed" value="${S.par.speed}" ${S.par.speedAuto ? 'disabled' : ''}></span></label>
      ${scaleRow('par', S.par, 'm/s')}
      <span class="small muted full" id="parInfo"></span></div>` : '<p class="small muted">No velocity field in these results.</p>'}`;
}
function bindVis() {
  const ti = () => S.results.times?.length > 1 ? `&time=${S.timeIdx ?? -1}` : '';
  const go = () => showVis(ti());
  const redraw = () => { $('#visBox').innerHTML = visHtml(); bindVis(); };
  let timer; const later = fn => { clearTimeout(timer); timer = setTimeout(fn, 150); };
  const on = (id, ev, fn) => $(id)?.addEventListener(ev, fn);
  const num = (id, st, key, after) => on(id, 'input', e => { const v = parseFloat(e.target.value); if (Number.isFinite(v)) { st[key] = v; later(after); } });
  const scale = (id, st, after) => {
    on(`#${id}Auto`, 'change', e => { st.auto = e.target.checked; if (!st.auto) { st.lo = +$(`#${id}Lo`).value; st.hi = +$(`#${id}Hi`).value; } $(`#${id}Lo`).disabled = $(`#${id}Hi`).disabled = st.auto; after(); });
    num(`#${id}Lo`, st, 'lo', after); num(`#${id}Hi`, st, 'hi', after);
  };
  on('#visRegion', 'change', e => { S.vis.region = e.target.value; for (const st of [S.vol, S.vec, S.par]) st.key = ''; go(); });
  on('#visRes', 'change', e => { S.vis.res = +e.target.value; S.vol.key = ''; go(); });
  // volume
  on('#volOn', 'change', e => { S.vol.on = e.target.checked; go(); });
  on('#volField', 'change', e => { S.vol.field = e.target.value; S.vol.density = { Vorticity: 3, 'Q-criterion': 5 }[S.vol.field] || 6; go().then(redraw); });
  scale('vol', S.vol, applyVolume);
  on('#volDens', 'input', e => { S.vol.density = +e.target.value; applyVolume(); });
  on('#volStyle', 'change', e => { S.vol.style = e.target.value; applyVolume(); });
  // arrows
  on('#vecOn', 'change', e => { S.vec.on = e.target.checked; go(); });
  on('#vecAxis', 'change', e => { S.vec.axis = e.target.value; go(); lbl(); });
  const lbl = () => { const n = $('#vecPosLbl'); if (n) { const c = { axis: S.vec.axis || visPlaneAxis(), pos: S.vec.pos }; n.textContent = `${c.axis} = ${fmt1(contourPos(c) * 1000)} mm`; } };
  on('#vecPos', 'input', e => { S.vec.pos = +e.target.value; lbl(); later(go); });
  lbl();
  num('#vecN', S.vec, 'n', go);
  on('#vecLenAuto', 'change', e => { S.vec.lenAuto = e.target.checked; if (!S.vec.lenAuto) S.vec.len = +$('#vecLen').value || 1; $('#vecLen').disabled = S.vec.lenAuto; applyArrows(); });
  num('#vecLen', S.vec, 'len', applyArrows);
  scale('vec', S.vec, applyArrows);
  // particles
  on('#parOn', 'change', e => { S.par.on = e.target.checked; go(); });
  on('#parSeed', 'change', e => { S.par.seed = e.target.value; go(); });
  num('#parN', S.par, 'n', go);
  on('#parTrail', 'input', e => { S.par.trail = Math.min(60, Math.max(2, Math.round(+e.target.value || 16))); later(applyParticles); });
  on('#parSize', 'input', e => { S.par.size = +e.target.value || 3; applyParticles(); });
  on('#parSpeedAuto', 'change', e => { S.par.speedAuto = e.target.checked; if (!S.par.speedAuto) S.par.speed = +$('#parSpeed').value || 1; $('#parSpeed').disabled = S.par.speedAuto; applyParticles(); });
  num('#parSpeed', S.par, 'speed', applyParticles);
  scale('par', S.par, applyParticles);
  if (S.vol.on || S.vec.on || S.par.on) { applyVolume(); applyArrows(); }
}
function isoGap() {
  if (S.iso.gap != null) return S.iso.gap;
  const L = Math.min(S.results?.summary?.coefficients?.L_ref_m || Infinity, ...(S.p?.moving_zones || []).map(z => z.radius));
  return Number.isFinite(L) ? +(L * 20).toPrecision(2) : 0;
}
function isoHtml() {
  if (!S.results?.fields.find(f => f.name === 'Q-criterion')) return '';
  const opts = S.results.fields.filter(f => f.name !== 'y+').map(f => `<option ${f.name === S.iso.color ? 'selected' : ''}>${esc(f.name)}</option>`).join('');
  return `<h3>Vortex surfaces</h3><p class="small muted" style="margin:2px 0 6px">Surfaces of equal Q-criterion: they wrap the vortex cores - tip vortices
      spiralling off the blades, the wake breaking up. Move the slider right to show weaker vortices too (more surfaces), left for only the strongest.</p>
    <div class="grid2"><label class="field"><span>Show</span><input type="checkbox" id="isoOn" ${isoOn() ? 'checked' : ''} style="width:auto"></label>
      <label class="field"><span>Colour by</span><select id="isoColor">${opts}</select></label>
      <label class="field full"><span>Weaker vortices shown</span><input type="range" id="isoK" min="2" max="60" step="1" value="${isoK(S.results.fields.find(f => f.name === 'Q-criterion'))}"></label>
      <label class="field" title="Leave out the surfaces this close to the walls: the shear in the boundary layer gives a large Q there too and would hide the vortices"><span>Keep clear of walls [mm]</span><input type="number" id="isoGap" min="0" step="0.5" value="${isoGap()}"></label>
      <label class="field" title="Average Q over the neighbouring cells before drawing (0-3 passes): rounds off the lumps of single cells"><span>Smoothing</span><input type="number" id="isoSm" min="0" max="3" step="1" value="${S.iso.smooth}"></label>
      <label class="field"><span>Opacity</span><input type="range" id="isoOp" min="0.2" max="1" step="0.05" value="${S.iso.opacity}"></label>
      <span class="small muted full" id="isoInfo"></span></div>`;
}
function bindIso() {
  const re = () => showIso(S.results.times?.length > 1 ? `&time=${S.timeIdx ?? -1}` : '');
  $('#isoOn')?.addEventListener('change', e => { S.iso.on = e.target.checked; re(); });
  $('#isoColor')?.addEventListener('change', e => { S.iso.color = e.target.value; re(); });
  $('#isoK')?.addEventListener('change', e => { S.iso.k = +e.target.value; re(); });
  $('#isoSm')?.addEventListener('change', e => { S.iso.smooth = Math.min(3, Math.max(0, Math.round(+e.target.value || 0))); re(); });
  $('#isoGap')?.addEventListener('change', e => { S.iso.gap = Math.max(0, +e.target.value || 0); re(); });
  $('#isoOp')?.addEventListener('change', e => { S.iso.opacity = +e.target.value; re(); });
}

function legend(name, unit, [lo, hi], reverse = false) {
  S.legendInfo = { name, unit, range: [lo, hi], reverse };
  const el = $('#legend');
  const stops = Array.from({ length: 11 }, (_, i) => `rgb(${turbo(reverse ? 1 - i / 10 : i / 10).map(x => x * 255 | 0).join(',')}) ${i * 10}%`).join(',');
  el.innerHTML = `<b>${esc(name)}</b> <span class="muted">[${esc(unit)}]</span><div class="bar" style="background:linear-gradient(90deg,${stops})"></div>
    <div class="ticks"><span>${fmt(lo, 4)}</span><span>${fmt((lo + hi) / 2, 4)}</span><span>${fmt(hi, 4)}</span></div>`;
  el.classList.remove('hidden');
}

// ------------------------------------------------------------------ viewport mode per step
// the section slider covers the limits of the model along the axis, no more (it covered 1.5 x the size on each side)
function clipPad(lo, hi) { return 0; }
// the limits of the model that has results: its box, cut at the symmetry planes. A half model (the other half is
// its mirror image) has no results beyond the plane, but its box is that of the whole body - the first half of the
// slider showed an empty plane. The air box is cut at the planes, so it gives the limit.
function resultsBounds() {
  const info = S.p.geometry.info, ext = S.p.study === 'external_aero';
  const b = (ext ? info.parts_bbox : info.bbox).slice(), d = info.domain;
  if (ext && d?.box_min && d?.box_max && S.p.symmetry?.length) {
    for (let k = 0; k < 3; k++) { b[k] = Math.max(b[k], d.box_min[k]); b[k + 3] = Math.min(b[k + 3], d.box_max[k]); }
  }
  return b;
}
function clipValue() {
  const ax = 'xyz'.indexOf(S.clip.axis);
  const b = resultsBounds();
  const lo = b[ax], hi = b[ax + 3];
  const pad = clipPad(lo, hi);
  return (lo - pad) + (hi - lo + 2 * pad) * S.clip.pos / 1000;
}

S.meshPoor = { on: false, thr: 0.1, worst: null };
async function showMesh() {
  if (S.p.mesh.status !== 'ready' && S.p.mesh.status !== 'stale') { viewer.clearOverlay(); viewer.showGeometry(true); return; }
  if (S.meshPoor.on) {  // mesh metrics: only the poor cells, on the CAD model
    try {
      const d = await api(P(`/mesh/view?poor=${S.meshPoor.thr}`));
      viewer.setOverlay(d, { wire: true, range: [0, S.meshPoor.thr], reverse: true });
      viewer.setZoneWire(null);
      viewer.showGeometry(true);
      S.meshPoor.worst = d.worst || null;
      const n = $('#poorInfo');
      if (n) n.textContent = d.n_poor ? `${d.n_poor.toLocaleString()} cells${d.n_poor > 20000 ? ' (worst 20,000 shown)' : ''}, worst ${fmt(d.worst.quality, 2)}` : 'none';
      legend('Cell quality', '-', [0, S.meshPoor.thr], true);
    } catch (e) { toast(e.message, true); }
    return;
  }
  const hideFluid = S.p.study === 'cht_enclosure' && S.hideAir;
  const q = S.clip.axis ? `?clip_axis=${S.clip.axis}&clip_pos=${clipValue()}&hide_fluid=${hideFluid}` : `?hide_fluid=${hideFluid && S.p.study === 'cht_enclosure'}`;
  try {
    const d = await api(P('/mesh/view' + q));
    viewer.setOverlay(d, { wire: true, range: [0, 1], reverse: true });
    viewer.setZoneWire(d.zone_lines);
    viewer.showGeometry(false);
    legend('Cell quality', '-', [0, 1], true);
  } catch (e) { toast(e.message, true); }
}

function renderViewportMode() {
  const step = S.step;
  const canProbe = !!S.p && step === 'results' && hasResults();
  $('#probeBtn').classList.toggle('hidden', !canProbe);
  if (!canProbe) { setProbe(false); viewer.setProbes([]); }
  // section: on the CAD faces (to reach faces inside) or on the mesh / results
  const clipOn = S.p && S.p.geometry.status === 'ready' && (step !== 'mesh' || S.p.mesh.status === 'ready' || S.p.mesh.status === 'stale');
  $('#clipTools').classList.toggle('hidden', !clipOn);
  $('#hideAirLbl').classList.toggle('hidden', !S.p || S.p.study !== 'cht_enclosure' || (step !== 'mesh' && step !== 'results'));
  $('#optAir').parentElement.classList.toggle('hidden', !S.p || !['external_aero', 'cht_enclosure'].includes(S.p.study));
  clipLabel();
  $('#legend').classList.add('hidden');
  viewer.setRotors(S.p?.rotors || [], S.p?.study === 'external_aero' && (step === 'setup' || step === 'geometry' || step === 'mesh'));
  viewer.setMovingZones(zonesForViewer(), !!S.p && (step === 'setup' || step === 'geometry' || step === 'mesh'));
  viewer.setRegions(S.p?.mesh?.regions, !!S.p && step === 'mesh');
  viewer.setBcGlyphs(bcGlyphs());
  refreshAirEditor();
  refreshSymmetryEditor();
  refreshRotateGizmo();
  updateAlignMarker();
  if (step !== 'setup') { viewer.setAxisMarker(null); viewer.setEdgePick(null); }
  viewer.setMirrors(step === 'results' && S.p && hasResults() && S.mirror !== false ? (S.p.symmetry || []) : [], true);
  if (!S.p) { viewer.clearOverlay(); return; }
  if (step !== 'mesh') viewer.setZoneWire(null);
  if (step !== 'results') { viewer.setSpinZones(null); S.spinKey = null; viewer.setIso(null); viewer.setContours(null); viewer.setVolume(null); viewer.setArrows(null); viewer.setParticles(null); }
  if (step === 'mesh') { viewer.setGeometryClip(null); showMesh(); return; }
  if (step === 'results' && hasResults()) { viewer.showGeometry(false); return; }
  viewer.clearOverlay(); viewer.showGeometry(true); refreshOverlay(); refreshViewerColors();
}

function setProbe(on) {
  if (!!S.probeOn === on) return;
  S.probeOn = on; viewer.probeMode = on;
  $('#probeBtn').classList.toggle('on', on);
  $('#three').style.cursor = on ? 'crosshair' : 'default';
}
viewer.onProbe = hit => {
  if (!hit) { toast('Click on the coloured result (the surface or the section).'); return; }
  (S.probes ||= []).push({ point: hit.point, text: `${fmt(hit.value, 4)} ${S.probeUnit || ''}`.trim() });
  viewer.setProbes(S.probes);
};
$('#probeBtn').onclick = () => { setProbe(!S.probeOn); if (S.step === 'results') showResults(); };
$('#shotBtn').onclick = () => {
  const c = viewer.snapshot();
  const times = S.results?.times || [];
  if (S.step === 'results' && S.legendInfo) videoOverlay(c, times.length > 1 ? times[S.timeIdx ?? times.length - 1] : null);
  c.toBlob(b => saveBlob(b, `${S.pid || 'view'}-${S.step}.png`), 'image/png');
};

$('#clipAxis').onchange = e => {
  S.clip.axis = e.target.value; clipLabel(); refreshOverlay();
  if (S.clip.axis) viewer.setView('+' + S.clip.axis);  // look straight at the cut
};
let clipT;
let clipPlaying = false;
$('#clipPlay').onclick = async () => {   // sweep the section along its axis, from one side of the model to the other
  if (clipPlaying) { clipPlaying = false; return; }
  if (!S.clip.axis) return;
  clipPlaying = true; $('#clipPlay').innerHTML = '&#10074;&#10074;';
  const step = 12;
  if (S.clip.pos >= 1000 - step) S.clip.pos = 0;
  while (clipPlaying && S.clip.axis && S.clip.pos < 1000) {
    S.clip.pos = Math.min(1000, S.clip.pos + step); $('#clipPos').value = S.clip.pos; clipLabel();
    const t0 = performance.now(); await showResults();
    await new Promise(r => setTimeout(r, Math.max(0, 150 - (performance.now() - t0))));
  }
  clipPlaying = false; if ($('#clipPlay')) $('#clipPlay').innerHTML = '&#9654;';
};
$('#clipPos').oninput = e => { S.clip.pos = +e.target.value; clipLabel(); clearTimeout(clipT); clipT = setTimeout(refreshOverlay, 120); };
function clipLabel() {
  const el = $('#clipVal');
  if (!el) return;
  $('#clipPlay')?.classList.toggle('hidden', !S.clip.axis);
  el.textContent = S.clip.axis && S.p ? `${S.clip.axis} = ${fmt1(clipValue() * 1000)} mm` : '';
}
$('#hideAir').checked = S.hideAir;
$('#hideAir').onchange = e => { S.hideAir = e.target.checked; refreshOverlay(); };
function refreshOverlay() {
  if (S.step === 'mesh') showMesh();
  else if (S.step === 'results' && hasResults()) showResults();
  else if (S.p?.geometry.status === 'ready') viewer.setGeometryClip(S.clip.axis || null, S.clip.axis ? clipValue() : 0);
}
document.querySelectorAll('#viewTools [data-view]').forEach(b => b.onclick = () => {
  const v = viewer.setView(b.dataset.view);
  document.querySelectorAll('#viewTools [data-view]').forEach(x => x.textContent = x.dataset.label || x.textContent);
  if (v && v.startsWith('-')) b.textContent = '−' + b.dataset.view.slice(1).toUpperCase();
});
document.querySelectorAll('#viewTools [data-view]').forEach(b => b.dataset.label = b.textContent);
$('#optEdges').onchange = e => viewer.setShowEdges(e.target.checked);
$('#optAir').onchange = e => { S.showAir = e.target.checked; viewer.setShowAir(S.showAir); };

// ------------------------------------------------------------------ panel event binding
function bindPanel(el) {
  el.querySelectorAll('[data-goto]').forEach(b => b.onclick = () => goStep(b.dataset.goto));
  el.querySelectorAll('.check[data-go]').forEach(c => c.onclick = () => treeGo(c.dataset.go));
  const drop = $('#drop', el);
  if (drop) {
    const inp = $('#fileIn', el);
    drop.onclick = () => inp.click();
    inp.onchange = () => inp.files[0] && upload(inp.files[0]);
    drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
    drop.ondragleave = () => drop.classList.remove('over');
    drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); e.dataTransfer.files[0] && upload(e.dataTransfer.files[0]); };
  }
  el.querySelectorAll('[data-action]').forEach(b => b.onclick = ev => action(b.dataset.action, b, ev));
  el.querySelectorAll('[data-meshmethod]').forEach(b => b.onclick = () => act(async () => {
    S.p = await api(P('/mesh/settings'), { json: { method: b.dataset.meshmethod } }); renderPanel();
  }));
  el.querySelectorAll('[data-preset]').forEach(b => b.onclick = () => act(async () => {
    S.p = await api(P('/mesh/settings'), { json: { preset: b.dataset.preset } }); renderPanel();
  }));
  const saveRegions = async rs => { S.p = await api(P('/mesh/settings'), { json: { regions: rs } }); renderPanel(); viewer.setRegions(S.p.mesh.regions, true); };
  const regionsCopy = () => (S.p.mesh.regions || []).map(r => ({ ...r, min: [...r.min], max: [...r.max], ...(r.start ? { start: [...r.start], end: [...r.end] } : {}) }));
  el.querySelectorAll('[data-region]').forEach(inp => inp.onchange = () => act(async () => {
    const rs = regionsCopy(), r = rs[+inp.dataset.region], k = inp.dataset.rk;
    if (k === 'name') r.name = inp.value;
    else if (k === 'size' || k === 'radius' || k === 'inner_radius') r[k] = parseFloat(inp.value) / 1000;
    else r[k][+inp.dataset.rj] = parseFloat(inp.value) / 1000;
    await saveRegions(rs);
  }));
  el.querySelectorAll('[data-regionfit]').forEach(sel => sel.onchange = () => sel.value && act(async () => {
    const pts = zonePartBox(sel.value); if (!pts.length) return;
    const rs = regionsCopy(), r = rs[+sel.dataset.regionfit];
    const lo = [0, 1, 2].map(k => Math.min(...pts.map(q => q[k]))), hi = [0, 1, 2].map(k => Math.max(...pts.map(q => q[k])));
    const pad = 0.15 * Math.max(...[0, 1, 2].map(k => hi[k] - lo[k]));
    r.min = lo.map(x => x - pad); r.max = hi.map(x => x + pad);
    await saveRegions(rs);
  }));
  el.querySelectorAll('[data-layers]').forEach(inp => inp.onchange = () => act(async () => {
    const L = S.p.mesh.layers || { n: 0, growth: 1.2, first: null };
    const body = { n: L.n, growth: L.growth, first_mm: L.first ? L.first * 1000 : null };
    body[inp.dataset.layers] = inp.value === '' ? null : parseFloat(inp.value);
    S.p = await api(P('/mesh/settings'), { json: { layers: body } }); renderPanel();
  }));
  el.querySelectorAll('[data-psz]').forEach(inp => inp.onchange = () => act(async () => {
    const rs = (S.p.mesh.refinements || []).map(r => ({ ...r })), r = rs[+inp.dataset.psz];
    if (inp.dataset.pk === 'part') { r.part = inp.value; r.faces = partFaces(inp.value); }
    else r.size = Math.max(1e-6, parseFloat(inp.value) / 1000 || r.size);
    await savePartSizing(rs);
  }));
  $('#poorOn', el)?.addEventListener('change', e => { S.meshPoor.on = e.target.checked; renderPanel(); showMesh(); });
  $('#poorThr', el)?.addEventListener('change', e => { S.meshPoor.thr = Math.min(0.9, Math.max(0.01, +e.target.value || 0.1)); if (S.meshPoor.on) showMesh(); });
  el.querySelectorAll('[data-defeature]').forEach(i => i.onchange = () => act(async () => {
    S.p = await api(P('/mesh/settings'), { json: { defeature_mm: i.value === '' ? 0 : parseFloat(i.value) } }); renderPanel();
  }));
  el.querySelectorAll('[data-mesh]').forEach(i => i.onchange = () => act(async () => {
    S.p = await api(P('/mesh/settings'), { json: { [i.dataset.mesh]: i.value === '' ? 0 : parseFloat(i.value) } }); renderPanel();
  }));
  el.querySelectorAll('[data-setting]').forEach(i => i.onchange = () => act(async () => {
    const v = i.tagName === 'SELECT' ? i.value : parseFloat(i.value);
    S.p = await api(P('/settings'), { json: { [i.dataset.setting]: v } });
    renderPanel(); renderSteps(); viewer.setBcGlyphs(bcGlyphs());  // (flow direction, gravity, ...)
    if (['forward_axis', 'up_axis'].includes(i.dataset.setting)) viewer.setRotors(S.p.rotors, true);
  }));
  el.querySelectorAll('[data-endtime]').forEach(b => b.onclick = () => act(async () => {
    S.p = await api(P('/settings'), { json: { end_time: parseFloat(b.dataset.endtime) } });
    renderPanel(); renderSteps();
  }));
  el.querySelectorAll('[data-mzone]').forEach(card => {
    const i = +card.dataset.mzone;
    const upd = fn => act(async () => { const zs = S.p.moving_zones.map(z => ({ ...z, center: [...z.center] })); fn(zs[i]); await saveZones(zs); });
    card.querySelectorAll('[data-mz]').forEach(inp => inp.onchange = () => upd(z => {
      const k = inp.dataset.mz, v = inp.value;
      if (k === 'name' || k === 'axis' || k === 'method') z[k] = v;
      else if (k === 'rpm') z.rpm = parseFloat(v);
      else if (k === 'radius' || k === 'length') z[k] = parseFloat(v) / 1000;
      else if (k === 'cell_size') z.cell_size = v === '' || !(parseFloat(v) > 0) ? null : parseFloat(v) / 1000;
      else z.center['xyz'.indexOf(k[1])] = parseFloat(v) / 1000;
    }));
    const fit = card.querySelector('[data-mzfit]');
    fit.onchange = () => fit.value && upd(z => {
      const pts = zonePartBox(fit.value);
      if (!pts.length) return;
      // the rotation axis passes through the part's area-weighted centroid (the bounding-box centre of a
      // three-blade propeller is off the hub); along the axis the zone is centred on the part's extent
      const fs = zonePartFaces(fit.value), A = fs.reduce((s, f) => s + f.area, 0);
      const c0 = A > 0 ? [0, 1, 2].map(k => fs.reduce((s, f) => s + f.centroid[k] * f.area, 0) / A)
        : [0, 1, 2].map(k => pts.reduce((s, q) => s + q[k], 0) / pts.length);
      const a = AXIS_VEC[z.axis].map(Math.abs);
      let rmax = 0, amin = 1e30, amax = -1e30;
      for (const q of pts) {
        const d = q.map((x, k) => x - c0[k]), ax = d[0] * a[0] + d[1] * a[1] + d[2] * a[2];
        rmax = Math.max(rmax, Math.hypot(d[0] - ax * a[0], d[1] - ax * a[1], d[2] - ax * a[2]));
        amin = Math.min(amin, ax); amax = Math.max(amax, ax);
      }
      z.center = c0.map((x, k) => x + a[k] * (amin + amax) / 2);
      // gap to the part = 0.6 zone-boundary cells (the check asks for >= 0.5): R = rmax + 0.6 * 2 pi R / N
      const N = ZONE_CELLS[S.p.mesh?.preset] || 40;
      z.radius = rmax / (1 - 1.2 * Math.PI / N);
      z.length = (amax - amin) + 2 * 0.6 * 2 * Math.PI * z.radius / N;
    });
  });
  el.querySelectorAll('[data-adv]').forEach(i => i.onchange = () => act(async () => {
    const v = i.tagName === 'SELECT' ? i.value : parseFloat(i.value);
    S.p = await api(P('/foam_advanced'), { json: { [i.dataset.adv]: v } }); renderPanel(); renderTree();
  }));
  el.querySelectorAll('[data-casefile]').forEach(a => a.onclick = ev => { ev.preventDefault(); openCaseFile(a.dataset.casefile); });
  const ad = el.querySelector('details[data-advanced]');
  if (ad) ad.ontoggle = () => { S.advOpen = ad.open; };
  el.querySelectorAll('[data-body]').forEach(card => {
    if (!$('[data-bodymat]', card)) return;  // geometry-step cards only have the hide toggle
    const id = card.dataset.body;
    const send = body => act(async () => { S.p = await api(P(`/bodies/${id}`), { json: body }); renderPanel(); renderSteps(); });
    $('[data-bodymat]', card).onchange = e => send({ material: e.target.value });
    const heat = $('[data-bodyheat]', card);
    if (heat) heat.onchange = e => send({ heat_W: parseFloat(e.target.value || 0) });
    $('[data-bodyname]', card).onchange = e => send({ name: e.target.value });
  });
  el.querySelectorAll('[data-bc]').forEach(card => {
    const id = card.dataset.bc;
    const patch = body => act(async () => { S.p = await api(P(`/bcs/${id}`), { method: 'PATCH', json: body }); renderPanel(); refreshViewerColors(); });
    card.querySelectorAll('[data-bcparam]').forEach(i => i.onchange = () => patch({ params: { [i.dataset.bcparam]: parseFloat(i.value) } }));
    const nm = $('[data-bcname]', card);  // (the condition of a symmetry plane has no editable name)
    if (nm) nm.onchange = e => patch({ name: e.target.value });
  });
  el.querySelectorAll('[data-rotor]').forEach(card => {
    const i = +card.dataset.rotor;
    const upd = (k, v) => act(async () => {
      const rot = S.p.rotors.map(r => ({ ...r })); rot[i][k] = v;
      S.p = await api(P('/rotors'), { json: { rotors: rot } }); renderPanel(); viewer.setRotors(S.p.rotors, true);
    });
    $('[data-rotor-radius]', card).onchange = e => upd('radius', parseFloat(e.target.value) / 1000);
    $('[data-rotor-thrust]', card).onchange = e => upd('thrust_N', e.target.value === '' ? null : parseFloat(e.target.value));
  });
  const nb = $('#newBcType', el);
  if (nb) nb.onchange = () => act(async () => {
    if (!nb.value) return;
    const t = nb.value;
    if (t === 'symmetry') { nb.value = ''; S.symPick = true; S.activeBc = null; updateSelectHint(); refreshViewerColors(); return; }
    S.p = await api(P('/bcs'), { json: { type: t, faces: [], name: studySpec().face_bc_types[t].label } });
    S.activeBc = S.p.bcs[S.p.bcs.length - 1].id;
    renderPanel(); refreshViewerColors(); updateSelectHint();
  });
  el.querySelectorAll('[data-air]').forEach(s => s.onchange = () => act(async () => {
    const k = s.dataset.air;
    await api(P('/domain'), { json: k === 'shape' ? { shape: s.value } : { [k]: s.value, fit: true } });
  }, 'Rebuilding air volume…'));
  el.querySelectorAll('[data-airsize]').forEach(i => i.oninput = () => {
    const v = parseFloat(i.value);
    if (!Number.isFinite(v) || !S.air) return;
    airSet(S.air.spec, i.dataset.airsize, v / 1000); S.air.dirty = true;
    const ap = el.querySelector('[data-action="airApply"]'); if (ap) ap.disabled = false;
    refreshAirEditor();
  });
  if (S.step === 'geometry') refreshAirEditor();
  el.querySelectorAll('[data-domain]').forEach(s => s.onchange = () => act(async () => {
    const body = s.dataset.domain === 'mode' ? { enclosure_mode: s.value } : { enclosure_body: parseInt(s.value) };
    await api(P('/domain'), { json: body });
  }));
  bindSymmetry(el);
  bindCadEdits(el);
}

async function upload(file) {
  const fd = new FormData(); fd.append('file', file);
  const scale = $('#scaleSel')?.value || '1';
  await act(() => api(P(`/import?scale=${scale}`), { method: 'POST', body: fd }), `Importing ${file.name}…`);
}

async function action(name, btn) {
  const card = btn.closest('[data-bc],[data-body],[data-rotor],[data-mzone]');
  switch (name) {
    case 'caseGen': return act(async () => {
      btn.disabled = true; btn.textContent = 'Writing case files…';
      S.caseFiles = { ...(await api(P('/case_files/generate'), { method: 'POST' })), pid: S.pid }; S.advOpen = true; renderPanel();
    });
    case 'advReset': return act(async () => {
      S.p = await api(P('/foam_advanced'), { json: Object.fromEntries(S.meta.foam_advanced.map(q => [q.key, q.default])) }); renderPanel(); renderTree();
    });
    case 'addZone': return act(async () => {
      const pb = S.p.geometry.info.parts_bbox, up = S.p.settings.up_axis || '+z';
      const c = [0, 1, 2].map(k => (pb[k] + pb[k + 3]) / 2), half = Math.max(...[0, 1, 2].map(k => (pb[k + 3] - pb[k]) / 2));
      const old = S.p.moving_zones || [], radius = half * 0.6;
      let name = `zone${old.length + 1}`; for (let i = old.length + 1; old.some(z => z.name === name); i++) name = `zone${i + 1}`;
      // a further zone (a second propeller) must not overlap the others: next to the last one, across the part,
      // along the longest direction at right angles to the zone axis
      const last = old[old.length - 1];
      if (last) {
        const ax = 'xyz'.indexOf(up[1]), side = [0, 1, 2].filter(k => k !== ax).sort((p, q) => (pb[q + 3] - pb[q]) - (pb[p + 3] - pb[p]))[0];
        for (let k = 0; k < 3; k++) c[k] = last.center[k];
        c[side] = last.center[side] + 2.2 * Math.max(radius, last.radius);
      }
      const zs = [...old, { name, center: c, axis: up, radius, length: half * 0.3, rpm: 3000,
        method: S.p.study === 'external_aero' && S.p.settings.analysis === 'transient' ? 'sliding' : 'mrf' }];
      await saveZones(zs);
    }, 'Zone added: set its size and speed, or fit it around the rotating part');
    case 'pickAxis': {
      const owner = btn.closest('[data-mzone],[data-rotor]');
      const kind = btn.dataset.kind, index = +(kind === 'zone' ? owner.dataset.mzone : owner.dataset.rotor);
      S.axisOblique = null; viewer.setAxisMarker(null);
      S.axisPick = S.axisPick?.kind === kind && S.axisPick.index === index ? null : { kind, index };
      updateSelectHint(); renderPanel(); refreshViewerColors(); refreshEdgePick(); return;
    }
    case 'axisTurn': return act(async () => {  // turn the model so the picked axis lies along the nearest coordinate axis
      const o = S.axisOblique; if (!o) return;
      const D = alignRotation(o.axis, AXIS_VEC[o.nearest]);
      S.axisOblique = null; viewer.setAxisMarker(null);
      const r = await api(P('/rotate'), { json: { matrix: D, snap_zones: true } });
      S.jobs.prepare = r.job; renderAll();
      toast('Turning the model… then pick the cylinder or edge again: the zone will follow it');
    });
    case 'axisKeep': S.axisOblique = null; viewer.setAxisMarker(null); renderPanel(); return;
    case 'delZone': return act(async () => {
      if (!await confirmBox('Delete moving zone', `Delete ${esc(S.p.moving_zones[+card.dataset.mzone].name)}?`, 'Delete')) return;
      await saveZones(S.p.moving_zones.filter((_, i) => i !== +card.dataset.mzone));
    });
    case 'example': return act(() => api(P('/import_example'), { json: { name: btn.dataset.name } }), 'Importing example…');
    case 'replace': $('#drop').classList.toggle('hidden'); return;
    case 'addRegion': return act(async () => {
      const pb = S.p.geometry.info.parts_bbox, c = [0, 1, 2].map(k => (pb[k] + pb[k + 3]) / 2);
      const h = [0, 1, 2].map(k => (pb[k + 3] - pb[k]) / 4), sz = (S.p.mesh.stats?.sizes?.near_wall || Math.max(...h) / 10);
      const rs = [...(S.p.mesh.regions || []), { name: `region${(S.p.mesh.regions || []).length + 1}`,
        min: c.map((x, k) => x - h[k]), max: c.map((x, k) => x + h[k]), size: sz }];
      S.p = await api(P('/mesh/settings'), { json: { regions: rs } }); renderPanel(); viewer.setRegions(S.p.mesh.regions, true);
    }, 'Region added: set its box and cell size, or fit it around a part');
    case 'addRegionCyl': return act(async () => {
      const pb = S.p.geometry.info.parts_bbox, c = [0, 1, 2].map(k => (pb[k] + pb[k + 3]) / 2);
      const h = [0, 1, 2].map(k => (pb[k + 3] - pb[k]) / 4), up = AXIS_VEC[S.p.settings.up_axis || '+z'].map(Math.abs);
      const half = Math.max(...h), sz = (S.p.mesh.stats?.sizes?.near_wall || half / 10);
      const rs = [...(S.p.mesh.regions || []), { name: `region${(S.p.mesh.regions || []).length + 1}`, shape: 'cylinder',
        start: c.map((x, k) => x - up[k] * half), end: c.map((x, k) => x + up[k] * half), radius: half / 2, size: sz }];
      S.p = await api(P('/mesh/settings'), { json: { regions: rs } }); renderPanel(); viewer.setRegions(S.p.mesh.regions, true);
    }, 'Cylinder added: set its end points, radius and cell size');
    case 'addWake': return act(async () => {
      const fd = S.p.flow_dir, N = ZONE_CELLS[S.p.mesh?.preset] || 40;
      const rs = (S.p.mesh.regions || []).filter(r => !(S.p.moving_zones || []).some(z => r.name === `${z.name} wake`));
      for (const z of S.p.moving_zones || []) {
        const size = z.cell_size || 2 * Math.PI * z.radius / N;
        rs.push({ name: `${z.name} wake`, shape: 'cylinder', start: [...z.center],
          end: z.center.map((x, k) => x + fd[k] * 4 * z.radius), radius: z.radius, size });
      }
      S.p = await api(P('/mesh/settings'), { json: { regions: rs } }); renderPanel(); viewer.setRegions(S.p.mesh.regions, true);
    }, 'Wake regions added downstream of the rotating zones (zone cell size, two diameters long)');
    case 'addTipRing': return act(async () => {
      // the tip vortices leave the blade tips (just inside the zone radius) and the slipstream contracts behind
      // the propeller: a ring 0.65-1.0 R from the zone's upstream face to one radius downstream, cells of R / 50
      const fd = S.p.flow_dir;
      const rs = (S.p.mesh.regions || []).filter(r => !(S.p.moving_zones || []).some(z => r.name === `${z.name} tip vortices`));
      for (const z of S.p.moving_zones || []) {
        const a = z.center.map((x, k) => x - fd[k] * (z.length || 0) / 2);
        rs.push({ name: `${z.name} tip vortices`, shape: 'cylinder', start: a, end: a.map((x, k) => x + fd[k] * z.radius),
          radius: z.radius, inner_radius: 0.65 * z.radius, size: z.radius / 50 });
      }
      S.p = await api(P('/mesh/settings'), { json: { regions: rs } }); renderPanel(); viewer.setRegions(S.p.mesh.regions, true);
    }, 'Tip-vortex rings added behind the rotating zones (cells of 1/50 of the zone radius)');
    case 'delRegion': return act(async () => {
      S.p = await api(P('/mesh/settings'), { json: { regions: (S.p.mesh.regions || []).filter((_, i) => i !== +btn.dataset.i) } }); renderPanel();
      viewer.setRegions(S.p.mesh.regions, true);
    });
    case 'rotate': {
      if (btn.dataset.reset) return act(() => api(P('/rotate'), { json: { reset: true } }), 'Restoring the original orientation…');
      // live: turn the view now, rebuild the geometry once on Apply
      const ax = btn.dataset.custom ? $('#rotAxis').value : btn.dataset.axis;
      const t = (btn.dataset.custom ? parseFloat($('#rotDeg').value) : parseFloat(btn.dataset.deg)) * Math.PI / 180;
      if (!Number.isFinite(t)) return;
      const c = Math.round(Math.cos(t) * 1e12) / 1e12, sn = Math.round(Math.sin(t) * 1e12) / 1e12;
      const [i, j] = { x: [1, 2], y: [2, 0], z: [0, 1] }[ax];
      const st = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]; st[i][i] = c; st[i][j] = -sn; st[j][i] = sn; st[j][j] = c;
      const R0 = S.rotPending || [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
      S.rotPending = st.map(r => [0, 1, 2].map(k => r[0] * R0[0][k] + r[1] * R0[1][k] + r[2] * R0[2][k]));
      viewer.setPreviewRotation(S.rotPending, partsCenter()); renderPanel(); return;
    }
    case 'alignPick': alignReset(); S.alignPick = true; updateSelectHint(); renderPanel(); return;
    case 'alignTo': alignTo(btn.dataset.to); return;
    case 'alignWall': startRefPick(); updateSelectHint(); renderPanel(); return;
    case 'alignToWall': alignToWall(btn.dataset.away === '1'); return;
    case 'alignDone': alignReset(); renderPanel(); return;
    case 'rotRings': S.rotGizmo = S.rotGizmo === false; renderPanel(); refreshRotateGizmo(); return;
    case 'rotCancel': S.rotPending = null; viewer.setPreviewRotation(null); alignReset(); renderPanel(); return;
    case 'rotApply': {
      return applyOrientation(null);
    }
    case 'pickBc': {
      const id = card.dataset.bc;
      S.activeBc = S.activeBc === id ? null : id;
      renderPanel(); refreshViewerColors(); updateSelectHint();
      if (S.activeBc) viewer.flash(S.p.bcs.find(b => b.id === id).faces);
      return;
    }
    case 'delBc': if (!await confirmBox('Delete boundary condition', `Delete “${esc(S.p.bcs.find(b => b.id === card.dataset.bc)?.name)}”?`, 'Delete')) return;
      return act(async () => {
      S.p = await api(P(`/bcs/${card.dataset.bc}`), { method: 'DELETE' });
      if (S.activeBc === card.dataset.bc) S.activeBc = null;
      renderPanel(); refreshViewerColors(); updateSelectHint();
    });
    case 'flashBody': {
      const id = +card.dataset.body;
      viewer.flash(S.faces.filter(f => f.volumes.includes(id)).map(f => f.id)); return;
    }
    case 'togglePart': {
      const n = btn.closest('[data-part]').dataset.part;
      S.hiddenParts.has(n) ? S.hiddenParts.delete(n) : S.hiddenParts.add(n);
      applyGeometry(); renderPanel(); return;
    }
    case 'toggleBody': {
      const id = +card.dataset.body;
      S.hiddenBodies.has(id) ? S.hiddenBodies.delete(id) : S.hiddenBodies.add(id);
      applyGeometry(); renderPanel(); return;
    }
    case 'confirmMat': {
      const b = S.p.body_list.find(x => x.id === +card.dataset.body);
      return act(async () => { S.p = await api(P(`/bodies/${b.id}`), { json: { material: b.material } }); renderAll(); });
    }
    case 'modelCheck': return act(async () => { const r = await api(P('/model_check'), { method: 'POST' }); S.jobs.check = r.job; renderPanel(); }, 'Flow-model comparison started (same mesh, other model)');
    case 'meshCheck3': return act(async () => { const r = await api(P('/mesh_check'), { json: { levels: 2 } }); S.jobs.check = r.job; renderPanel(); }, 'Three-level mesh check started');
    case 'meshCheck': return act(async () => { const r = await api(P('/mesh_check'), { method: 'POST' }); S.jobs.check = r.job; renderPanel(); }, 'Mesh check started (re-meshing one level finer and re-solving)');
    case 'applyTrim': return act(async () => {
      S.p = await api(P('/apply_trim'), { method: 'POST' });
      const r = await api(P('/solve'), { json: {} }); S.jobs.solve = r.job; goStep('solve');
    }, 'Re-running at the trim pitch');
    case 'extendRun': {
      const end = parseFloat($('#contEnd')?.value), wi = parseFloat($('#contWi')?.value);
      return act(async () => { const r = await api(P('/solve'), { json: { end_time: end, write_interval: wi } }); S.jobs.solve = r.job; goStep('solve'); });
    }
    case 'exportVtu': case 'exportCsv': {
      const f = name === 'exportVtu' ? 'vtu' : 'csv', nt = S.results?.times?.length || 0;
      return act(() => download(P(`/results/export?format=${f}${nt > 1 ? `&time=${S.timeIdx ?? -1}` : ''}`), `${S.pid}-results.${f}`), 'Results saved to your Downloads folder');
    }
    case 'exportReport': {
      const c = viewer.snapshot();
      if (S.legendInfo) videoOverlay(c, null);
      return act(() => download(P('/report'), `${S.pid}-report.html`, { json: { image: c.toDataURL('image/jpeg', 0.9) } }), 'Report saved to your Downloads folder: open it in a browser (print to PDF from there)');
    }
    case 'continueRun': {
      const n = parseInt($('#contIters')?.value || '1000');
      return act(async () => { const r = await api(P('/solve'), { json: { continue_iterations: n } }); S.jobs.solve = r.job; goStep('solve'); });
    }
    case 'autoIO': return act(async () => { S.p = await api(P('/auto_inlet_outlet'), { method: 'POST' }); renderAll(); }, 'Inlet and outlet assigned');
    case 'detectRotors': return act(async () => { S.p = await api(P('/rotors/detect'), { json: {} }); renderAll(); }, 'Rotors placed on motor tops');
    case 'autoThrust': return act(async () => { S.p = await api(P('/rotors/auto_thrust'), { method: 'POST' }); renderAll(); }, 'Rotor thrusts are automatic again');
    case 'delRotor': return act(async () => {
      const rot = S.p.rotors.filter((_, i) => i !== +card.dataset.rotor);
      S.p = await api(P('/rotors'), { json: { rotors: rot } }); renderAll();
    });
    case 'genMesh': return act(() => api(P('/mesh'), { method: 'POST' }));
    case 'solve': return act(async () => { S.initFrom = $('#initFrom')?.value || ''; const r = await api(P('/solve'), { json: S.initFrom ? { init_from: S.initFrom } : {} }); S.jobs.solve = r.job; renderPanel(); });
    case 'export': return act(async () => { const r = await api(P('/solve'), { json: { export_only: true } }); S.jobs.solve = r.job; }, 'Writing OpenFOAM case…');
    case 'cancelSolve': return S.jobs.solve && api(`/api/jobs/${S.jobs.solve.id}/cancel`, { method: 'POST' });
    case 'stopSolve': return act(() => api(P('/solve/stop'), { method: 'POST' }), 'Stopping: writing the current solution…').catch(() => {});
    case 'airApply': {
      const sp = S.air.spec, size = {};
      for (const [k] of AIR_SIZES[sp.shape]) {
        const [a, i] = k.split('.');
        if (i != null) (size[a] = size[a] || [...sp[a]])[+i] = sp[a][+i]; else size[k] = sp[k];
      }
      return act(() => api(P('/domain'), { json: { size } }), 'Rebuilding air volume…');
    }
    case 'airFit': return act(() => api(P('/domain'), { json: { fit: true } }), 'Fitting the air volume to the parts…');
    case 'airView': { const sp = airSpec(); if (sp) viewer.fitTo(sp.bbox_min, sp.bbox_max); return; }
    case 'addPartSize': {
      const parts = [...new Set(S.faces.filter(f => !['farfield', 'enclosure'].includes(f.kind)).map(f => f.part || '(body)'))];
      const part = parts[0], st = S.p.mesh.stats?.sizes;
      const size = (st?.near_wall || (S.p.geometry.info?.ref_length || 0.1) / 100) / 2;
      return act(() => savePartSizing([...(S.p.mesh.refinements || []), { part, faces: partFaces(part), size }]));
    }
    case 'delPartSize': {
      const rs = [...(S.p.mesh.refinements || [])]; rs.splice(+btn.dataset.i, 1);
      return act(() => savePartSizing(rs));
    }
    case 'poorZoom': {
      const w = S.meshPoor.worst; if (!w) return;
      const r = w.size * 6;
      viewer.fitTo(w.center.map(c => c - r), w.center.map(c => c + r)); return;
    }
    case 'ffPreset': {
      const v = FF_PRESETS[btn.dataset.ffp];
      return act(() => api(P('/domain'), { json: { farfield: { upstream: v[0], downstream: v[1], side: v[2] }, fit: true } }), 'Rebuilding air volume…');
    }
    case 'applyFF': {
      const ff = {}; document.querySelectorAll('[data-ff]').forEach(i => ff[i.dataset.ff] = parseFloat(i.value));
      return act(() => api(P('/domain'), { json: { farfield: ff, fit: true } }), 'Rebuilding air volume…');
    }
  }
}

// ------------------------------------------------------------------ chat
const chat = { log: {}, busy: {} };
function chatMsgs() {
  if (!chat.log[S.pid]) {
    try { chat.log[S.pid] = JSON.parse(localStorage.getItem('simwb.chat.' + S.pid) || '[]'); } catch { chat.log[S.pid] = []; }
    for (const m of chat.log[S.pid]) if (m.role === 'term') m.done = true;  // (a command from an earlier page is over)
  }
  return chat.log[S.pid];
}
function chatSave(pid) {
  try { localStorage.setItem('simwb.chat.' + pid, JSON.stringify((chat.log[pid] || []).filter(m => m.role !== 'status').slice(-120))); } catch { }
}
function mdTable(block) {
  const rows = block.trim().split('\n').filter(l => !/^\|?\s*:?-{2,}/.test(l.replace(/\|/g, '').trim() ? l : '---'));
  const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  const [head, ...body] = rows.filter(l => !/^\s*\|?[\s:|-]+\|?\s*$/.test(l));
  return `<table class="summary"><tr>${cells(head).map(c => `<td><b>${c}</b></td>`).join('')}</tr>${body.map(r => `<tr>${cells(r).map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</table>`;
}
function md(s) {
  let h = esc(s);
  h = h.replace(/((?:^\|.*\|\s*$\n?){2,})/gm, m => mdTable(m) + '\n\n');
  h = h.replace(/```([\s\S]*?)```/g, (_, c) => `<pre class="log">${c}</pre>`);
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  h = h.replace(/^### (.*)$/gm, '<b>$1</b>').replace(/^## (.*)$/gm, '<b>$1</b>');
  h = h.replace(/(^|\n)((?:[-*] .*(?:\n|$))+)/g, (_, pre, block) => pre + '<ul>' + block.trim().split('\n').map(l => `<li>${l.replace(/^[-*] /, '')}</li>`).join('') + '</ul>');
  return h.split(/\n{2,}/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
}
function chatRender() {
  const el = $('#chatLog');
  if (!S.pid) { el.innerHTML = `<div class="muted small">Open or create a project to chat with Claude about it.</div>`; return; }
  const msgs = chatMsgs();
  el.innerHTML = msgs.length ? msgs.map(m => {
    if (m.role === 'tool') return `<div class="tool ${m.ok === false ? 'bad' : m.ok ? 'ok' : ''}"><span class="ic">${m.ok === false ? '!' : m.ok ? '✓' : '…'}</span>${esc(m.label)}</div>`;
    if (m.role === 'user') return `<div class="msg user">${esc(m.text)}</div>`;
    if (m.role === 'term') return `<div class="msg term"><div class="cmd">› ${esc(m.cmd)}</div>${m.lines.map(l => `<div${l.err ? ' class="err"' : ''}>${esc(l.t).replace(/(https?:\/\/[^\s<]+)/g, (u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${u.length > 48 ? 'open this link' : u}</a>`)}</div>`).join('')}${m.done ? '' : '<div class="typing">running…</div>'}</div>`;
    if (m.role === 'error') return `<div class="msg error">${esc(m.text)}</div>`;
    if (m.role === 'status') return `<div class="typing">${esc(m.text)}</div>`;
    return `<div class="msg assistant">${md(m.text)}</div>`;
  }).join('') : `<div class="muted small">Ask Claude to set up, mesh, run or explain this project.</div>`;
  if (chat.busy[S.pid]) el.innerHTML += `<div class="typing">Claude is working…</div>`;
  el.scrollTop = el.scrollHeight;
  $('#chatStop').classList.toggle('hidden', !chat.busy[S.pid] && !term.busy);
  $('#chatSend').disabled = !!chat.busy[S.pid];
}
const TOOL_LABELS = {
  get_project_state: 'Reading project', find_faces: 'Finding faces', highlight_faces: 'Highlighting faces',
  set_body: 'Setting part properties', add_boundary_condition: 'Adding boundary condition', update_boundary_condition: 'Updating boundary condition',
  delete_boundary_condition: 'Deleting boundary condition', set_settings: 'Changing settings', generate_mesh: 'Generating mesh',
  run_simulation: 'Running simulation', get_results: 'Reading results', check_setup: 'Checking setup', list_options: 'Looking up options',
  auto_inlet_outlet: 'Detecting inlet/outlet', detect_rotors: 'Placing rotors', set_rotors: 'Setting rotors', set_mesh_settings: 'Mesh settings',
  get_run_status: 'Checking run status', set_domain: 'Rebuilding domain', openfoam_status: 'Checking OpenFOAM',
  probe_results: 'Probing results', wait_for_run: 'Waiting for the run', cancel_job: 'Cancelling job', rename_project: 'Renaming project',
};
function onChatEvent(m) {
  const msgs = (chat.log[m.project] ||= []);
  const last = msgs[msgs.length - 1];
  if (last?.role === 'status') msgs.pop();
  if (m.type === 'chat_text') msgs.push({ role: 'assistant', text: m.text });
  else if (m.type === 'chat_tool') msgs.push({ role: 'tool', name: m.name, label: TOOL_LABELS[m.name] || m.name });
  else if (m.type === 'chat_tool_result') {
    const t = [...msgs].reverse().find(x => x.role === 'tool' && x.name === m.name && x.ok === undefined);
    if (t) { t.ok = m.ok; if (!m.ok) t.label += ' – ' + m.preview.replace('ERROR: ', ''); }
  } else if (m.type === 'chat_status') msgs.push({ role: 'status', text: m.text });
  else if (m.type === 'chat_error') { msgs.push({ role: 'error', text: m.text }); chat.busy[m.project] = false; claudeStatus(); }
  else if (m.type === 'chat_done') chat.busy[m.project] = false;
  chatSave(m.project);
  if (m.project === S.pid) chatRender();
}
function sendChat(text) {
  text = text.trim();
  if (text && S.pid && term.busy) { termRun(text); return; }        // input of the running command
  if (text.startsWith('/')) { const l = termLine(text); if (l) termRun(l); return; }
  if (!text || !S.pid || chat.busy[S.pid]) return;
  chatMsgs().push({ role: 'user', text });
  chatSave(S.pid);
  chat.busy[S.pid] = true;
  const ctx = `[GUI context: user is on the "${S.step}" step${S.activeBc ? `, editing boundary condition ${S.activeBc}` : ''}] `;
  const history = chatMsgs().filter(m => m.role === 'user' || m.role === 'assistant').slice(-21, -1)
    .map(m => ({ role: m.role, text: m.text.slice(0, 2000) }));
  ws.send(JSON.stringify({ type: 'chat', project: S.pid, text: ctx + text, history }));
  chatRender();
}
function chatSuggestions() {
  const el = $('#chatSuggest');
  if (!S.p) { el.innerHTML = ''; return; }
  const st = S.p.study, step = S.step;
  const sug = {
    heat_conduction: ['Help me set up heat sources and cooling for this part', 'Which faces lose the most heat?', 'Explain the results'],
    cht_enclosure: ['Help me set up the air flow and heat loads for this part', 'Is laminar or turbulent flow right here?', 'How can I lower the chip temperature?'],
    external_aero: ['Help me set up the flow for this body', 'What are the drag and lift coefficients?', 'Is the mesh fine enough?'],
    aeroelastic_flutter: ['Which faces are the aerodynamic surface of this wing?', 'Is the flutter margin enough?', 'How can I raise the flutter speed?'],
    vibration_modal: ['Which faces should be fixed supports for this part?', 'Will it resonate with a motor at 6000-24000 rpm?', 'How can I raise the first frequency?'],
    aeroelastic_flutter_cfd: ['Is the root face the right one?', 'Which air speeds should I run?', 'Is the damping estimate reliable?'],
    thermal_stress: ['Which faces should be supports so the part can expand without locking?', 'Where is the thermal stress highest?', 'What stress-free temperature should I use?'],
    static_structural: ['Help me set up the supports and loads for this part', 'Is the safety factor acceptable?', 'Where is the stress highest and how can I lower it?'],
  }[st] || [];
  if (step === 'results') sug.unshift('Summarise these results for me');
  el.innerHTML = sug.slice(0, 3).map(s => `<button>${esc(s)}</button>`).join('');
  el.querySelectorAll('button').forEach(b => b.onclick = () => sendChat(b.textContent));
}
$('#chatForm').onsubmit = e => { e.preventDefault(); const t = $('#chatInput').value; $('#chatInput').value = ''; sendChat(t); };
$('#chatInput').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chatForm').requestSubmit(); } };
$('#chatStop').onclick = () => { if (term.busy) api(P('/terminal/stop'), { method: 'POST' }).catch(e => termAdd(e.message, true)); else ws.send(JSON.stringify({ type: 'chat_stop', project: S.pid })); };
$('#chatCode').onclick = async () => {
  if (!S.pid) return;
  const r = await act(() => api(P('/open_claude'), { json: {} }));
  if (r?.message) toast(r.message);
};
$('#chatNew').onclick = () => { chat.log[S.pid] = []; chatSave(S.pid); ws.send(JSON.stringify({ type: 'chat_reset', project: S.pid })); chatRender(); };
$('#brandHome').onclick = () => { openProject(null); renderProjectBox(); };
$('#quitBtn').onclick = async () => {
  if (!await confirmBox('Close the workbench', 'This stops anything still running (meshing, solver runs in every project; a stopped run keeps no results). Projects are saved.', 'Close workbench')) return;
  try { await api('/api/shutdown', { method: 'POST' }); } catch (e) { toast(e.message, true); return; }
  S.closed = true;
  document.body.innerHTML = `<div style="display:flex;height:100vh;align-items:center;justify-content:center;flex-direction:column;gap:8px;color:#ccd">
    <h2>The workbench is closed</h2><p class="muted">All its processes have been stopped. You can close this tab; start it again with run_workbench.bat.</p></div>`;
};
$('#chatToggle').onclick = () => { $('#layout').classList.toggle('nochat'); localStorage.setItem('simwb.chat', $('#layout').classList.contains('nochat') ? '0' : '1'); };
if (localStorage.getItem('simwb.chat') === '0') $('#layout').classList.add('nochat');
async function claudeStatus() {
  const el = $('#claudeAuth'), btn = $('#chatLogin');
  try {
    const s = await api('/api/claude/status');
    if (s.loggedIn) {
      el.textContent = s.authMethod === 'api_key' ? 'Using ANTHROPIC_API_KEY' : `Logged in${s.email ? ' as ' + s.email : ''}${s.subscriptionType ? ' (' + s.subscriptionType + ')' : ''}`;
      btn.classList.add('hidden'); $('#chatLogout').classList.toggle('hidden', s.authMethod === 'api_key');
    } else {
      el.textContent = 'Not logged in to Claude'; btn.classList.remove('hidden'); btn.textContent = 'Log in'; $('#chatLogout').classList.add('hidden');
    }
  } catch { el.textContent = 'Claude status unknown'; }
}
// the Claude command line inside the chat: a line starting with / runs a claude command in the folder the study is
// saved in (/login, /logout, /status, /ask question ...); anything else goes to the assistant. Output is shown in the
// conversation. While a command waits for input (a login code), the next line goes to it.
const term = { busy: false };
const TERM_HELP = 'Commands (they run the claude command line in the study folder):\n/login   log in to Claude\n/logout  log out\n/status  who is logged in\n/ask question   one plain question to Claude (no study tools, no shell)\n/version, /help\nAnything without a / goes to the assistant, which can set up and run your study.';
function termMsg(cmd) { const m = { role: 'term', cmd, lines: [], done: false }; chatMsgs().push(m); return m; }
function termLast() { const l = chatMsgs(); for (let i = l.length - 1; i >= 0; i--) if (l[i].role === 'term') return l[i]; return null; }
async function termRun(line) {
  if (!S.pid) return;
  if (term.busy) {   // input of the running command
    try { await api(P('/terminal'), { json: { line } }); } catch (e) { termAdd(e.message, true); }
    return;
  }
  const m = termMsg(line); chatSave(S.pid); chatRender();
  try { await api(P('/terminal'), { json: { line } }); term.busy = true; chatRender(); }
  catch (e) { m.lines.push({ t: e.message, err: true }); m.done = true; chatRender(); }
}
function termAdd(text, err) { const m = termLast(); if (m) { m.lines.push({ t: text, err: !!err }); chatRender(); } }
function onTermEvent(m) {
  if (m.type === 'term_out') { const t = termLast(); if (t) { t.lines.push({ t: m.text }); if (t.lines.length > 400) t.lines.shift(); chatRender(); } }
  else if (m.type === 'term_done') {
    term.busy = false; const t = termLast();
    if (t) { t.lines.push({ t: `[exit ${m.code}]`, err: !!m.code }); t.done = true; chatSave(S.pid); }
    chatRender();
    if (m.auth) { ws.send(JSON.stringify({ type: 'chat_reset', project: S.pid })); claudeStatus(); }  // the assistant reconnects with the new login
  }
}
function termLine(text) {  // "/cmd args" -> the line for the claude command line, or null (handled here)
  const [cmd, ...rest] = text.slice(1).trim().split(/\s+/), arg = rest.join(' ');
  if (cmd === 'help' || cmd === '?') { const m = termMsg('/help'); m.lines.push({ t: TERM_HELP }); m.done = true; chatSave(S.pid); chatRender(); return null; }
  if (cmd === 'ask') return arg ? `-p "${arg.replace(/"/g, "'")}"` : null;
  return ['login', 'logout', 'status', 'version'].includes(cmd) ? (cmd === 'version' ? '--version' : cmd) : (termAdd2(text), null);
}
function termAdd2(text) { const m = termMsg(text); m.lines.push({ t: 'Unknown command. Type /help', err: true }); m.done = true; chatRender(); }
$('#chatLogin').onclick = () => termRun('login');
$('#chatLogout').onclick = () => termRun('logout');

// ------------------------------------------------------------------ boot
(async function boot() {
  await loadMeta();
  await loadProjects();
  connectWs();
  claudeStatus();
  await openProject(S.pid && S.projects.find(p => p.id === S.pid) ? S.pid : null);
  // built-in OpenFOAM being set up in the background: show its progress in the status bar
  api('/api/openfoam').then(f => { S.foam = f; if (f.install_job) { S.foamJob = f.install_job; renderJobBar(); } }).catch(() => {});
})();
