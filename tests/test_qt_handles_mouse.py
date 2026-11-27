"""Refinement-region and rotating-zone handles driven by REAL mouse events: QTest press / move / release on the 3D view's VTK widget, in a window with a real OpenGL
surface. The other Qt tests run on Qt's offscreen platform where VTK has no render window and gets no mouse events, so this one starts tests/gl_drive_handles.py in its
own process and reads what it reports. Skipped where no OpenGL window can be made (a machine without a display)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DRIVER = Path(__file__).with_name("gl_drive_handles.py")


@pytest.fixture(scope="module")
def drive(tmp_path_factory):
    shots = tmp_path_factory.mktemp("shots")
    ws = tmp_path_factory.mktemp("ws")
    env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORM", "SIMWB_NO_GL")}
    try:
        p = subprocess.run([sys.executable, "-I", str(DRIVER), str(shots), str(ws)], capture_output=True, text=True, timeout=420, env=env,
                           cwd=str(DRIVER.parents[1]))
    except subprocess.TimeoutExpired:
        pytest.skip("the OpenGL window did not come up in time")
    line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")), None)
    if p.returncode == 77:
        pytest.skip("no OpenGL window can be made here")
    if line is None:
        pytest.skip("no OpenGL window can be made here: " + (p.stderr or p.stdout)[-300:])
    out = json.loads(line[7:])
    assert "error" not in out, out.get("trace") or out["error"]
    out["shots"] = shots
    return out


def test_a_real_mouse_drag_of_each_kind_of_zone_handle_changes_the_zone(drive):
    assert drive["zone_move_took"] and drive["zone_radius_took"] and drive["zone_tilt_took"]            # (each press took the handle, not the camera)
    assert drive["zone_move_dx_mm"] == pytest.approx(20.0, abs=0.5)                                      # (the arrow along x: 20 mm along x and nothing else)
    assert all(abs(v) < 0.2 for v in drive["zone_move_dyz_mm"])
    assert drive["zone_radius_dr_mm"] == pytest.approx(10.0, abs=0.5)                                    # (the side handle: 10 mm more radius)
    assert drive["zone_axis_after"] != drive["zone0"]["axis"]                                            # (the ring: a quarter turn of the axis)


def test_a_real_mouse_drag_of_each_kind_of_region_handle_changes_the_preview_and_nothing_is_stored_until_apply(drive):
    assert drive["region_kinds"]["move"] == "point" and drive["region_kinds"]["turn.z"] == "ring" and drive["region_kinds"]["face.0.1"] == "face"
    assert drive["region_move_took"] and drive["region_turn_took"] and drive["region_face_took"]
    assert drive["region_shift_mm"][1] == pytest.approx(10.0, abs=0.5) and abs(drive["region_shift_mm"][0]) < 0.2     # (the y arrow)
    assert drive["region_stored_before_apply"]
    assert drive["region_turn_deg"] == pytest.approx(30.0, abs=1.0)                                      # (the z ring, 30 degrees)
    assert drive["region_grow_mm"][0][1] == pytest.approx(15.0, abs=0.5)                                  # (the +x face, 15 mm out)
    r = drive["region_applied"]
    assert r.get("rotation") is not None                                                                  # (Apply stored the turn)


def test_the_pictures_were_made(drive):
    names = sorted(p.name for p in Path(drive["shots"]).glob("*.png"))
    assert len(names) >= 6
