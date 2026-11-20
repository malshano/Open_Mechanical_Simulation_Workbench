"""Smoke test of simwb/web/flutter_cfd.js through node (skipped when node is not installed): the pure functions run on
a made-up summary with a stub canvas that records the calls."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "simwb" / "web" / "flutter_cfd.js"
FIXTURE = Path(__file__).parent / "data" / "flutter_cfd_summary.json"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

HARNESS = r"""
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const m = await import(pathToFileURL(process.argv[2]).href);
const summary = JSON.parse(readFileSync(process.argv[3], 'utf8'));
const calls = [];
const ctx = new Proxy({}, { get: (t, k) => k === 'measureText' ? (s => ({ width: 5 * String(s).length }))
  : (...a) => { calls.push(k); }, set: (t, k, v) => true });
const canvas = (w = 400, h = 160) => ({ clientWidth: w, clientHeight: h, width: 0, height: 0, getContext: () => ctx });
const out = {};
const mk = () => { calls.length = 0; };
mk(); m.drawModalSeries(canvas(), summary.speeds[0].series, { dpr: 1, tRelease: 0.2, envelope: { a0: 0.002, rate: -6 } });
out.series = { calls: calls.length, stroke: calls.filter(c => c === 'stroke').length };
mk(); m.drawModalSeries(canvas(), { t: [], q: [], labels: [] }, { dpr: 1 });
out.empty = calls.includes('fillText');
mk(); m.drawDampingVsSpeed(canvas(), summary.damping_curve, summary.strip.sweep, summary.flutter, { dpr: 2 });
out.damping = { calls: calls.length, arcs: calls.filter(c => c === 'arc').length, fill: calls.filter(c => c === 'fill').length, strokeArc: calls.filter(c => c === 'arc').length };
mk(); m.drawDampingVsSpeed(canvas(), [], null, null, {});
out.dampingEmpty = calls.includes('fillText');
mk(); m.drawDampingVsSpeed(null, summary.damping_curve, null, null, {});
out.nullCanvas = calls.length;
const noStrip = summary.damping_curve.map(({ strip_zeta, ...r }) => r);
out.pick = m.pickStripCurve(summary.strip.sweep, noStrip);
out.pickGiven = m.pickStripCurve(summary.strip.sweep, summary.damping_curve);
out.summary = m.flutterCfdSummaryHtml(summary);
out.partial = m.flutterCfdSummaryHtml({ ...summary, status: 'partial', flutter: null });
out.minimal = m.flutterCfdSummaryHtml({});
out.progress = m.flutterCfdProgressHtml({ state: 'running', total_eta_s: 7300, cost_estimate_h: 4.5, speeds: [
  { U: 30, state: 'done', t: 1.5, t_end: 1.5, exchanges: 750, elapsed_s: 5400 },
  { U: 50, state: 'running', t: 0.6, t_end: 1.5, exchanges: 300, eta_s: 3000 },
  { U: 70, state: 'queued' }] });
out.progressEmpty = m.flutterCfdProgressHtml({});
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    d = tmp_path_factory.mktemp("jsharness")
    h = d / "harness.mjs"
    h.write_text(HARNESS, encoding="utf8")
    r = subprocess.run([NODE, str(h), str(MODULE), str(FIXTURE)], capture_output=True, text=True, encoding="utf8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_charts_draw_without_error(result):
    assert result["series"]["stroke"] >= 5  # grid, zero line, marker, two modes, envelope
    assert result["empty"] and result["dampingEmpty"]  # a message instead of a chart
    assert result["damping"]["arcs"] == 3  # one marker per speed
    assert result["nullCanvas"] == 0


def test_strip_curve_follows_the_nearest_frequency(result):
    # CFD f at 30 m/s is 13.5 Hz -> mode 2 (13.5 Hz) is followed, damping 0.5 % -> 0.005
    pick = {p["U"]: p["zeta"] for p in result["pick"]}
    assert pick[30] == pytest.approx(0.005)
    assert len(result["pick"]) == 7
    # strip_zeta of the points wins when given
    assert [p["zeta"] for p in result["pickGiven"]] == [0.005, 0.001, -0.022]


def test_summary_html(result):
    h = result["summary"]
    for s in ("Flutter speed (CFD)", "48.5 ± 4", "decaying", "neutral", "unreliable", "-6.7 %", "interpolated",
              "complete", "Assumptions", "Linear structure", "Mode 2"):
        assert s in h, s
    assert "&lt;b&gt;" in h and "unreliable <b>" not in h  # warning text is escaped
    assert "partial" in result["partial"] and "No flutter speed found" in result["partial"]
    assert isinstance(result["minimal"], str)


def test_progress_html(result):
    h = result["progress"]
    assert "running" in h and "waiting" in h and "done" in h
    assert "width:40.0%" in h and "about 2.03 h left" in h
    assert "Waiting for the first air speed" in result["progressEmpty"] or "Study" in result["progressEmpty"]
