"""The Setup step of the Qt window has everything the web version's step has: the solver, symmetry planes, rotors, moving zones, the
flow-condition heading, the suggested end time, auto inlet & outlet and the marks on the model for every kind of condition."""
import pytest
from qt_support import ctl, from_thread  # noqa: F401


def wait(c, t=300):
    assert from_thread(c.app, lambda: c.wait_idle(t), t + 20)["idle"]
    c._settle()


def ids(c, prefix="setup."):
    return {w["id"] for w in c.widgets(prefix)["widgets"]}


@pytest.fixture
def bracket(ctl):
    ctl.do("study.example", {"study": "static_structural"})
    wait(ctl)
    ctl.do("step.go", {"step": "setup"})
    return ctl


@pytest.fixture
def aero(ctl, box_step):
    ctl.do("study.new", {"name": "Flow", "study": "external_aero"})
    ctl.do("geometry.import", {"path": str(box_step)})
    wait(ctl)
    ctl.do("step.go", {"step": "setup"})
    return ctl


# ------------------------------------------------------------------------------------------------------------ order and the solver
def test_solver_line_heading_and_expert_settings_open(bracket):
    c = bracket
    assert c.widget_do("setup.about.solver")["value"].startswith("Solver: Built-in")
    assert c.widget_do("setup.setting.structural_solver")["visible"] is True          # (the Advanced block is open in Expert mode)
    c.widget_do("setup.setting.structural_solver", "set", "calculix")
    assert c.engine.state()["settings"]["structural_solver"] == "calculix"
    assert "CalculiX" in c.widget_do("setup.about.solver")["value"]
    from PySide6.QtWidgets import QLabel
    labels = [la.text() for la in c.win.panels["setup"].findChildren(QLabel)]
    assert "LOADS" in labels and "SYMMETRY PLANES" in labels and "Other faces: Free" in labels


# ------------------------------------------------------------------------------------------------------------ symmetry planes
def test_a_symmetry_plane_is_added_flipped_and_removed(bracket):
    c = bracket
    assert {"setup.sym.add_x", "setup.sym.add_y", "setup.sym.add_z", "setup.sym.from_face"} <= ids(c)
    c.widget_do("setup.sym.add_y", "click")
    assert c.win.panels["setup"].sym.draft["axis"] == "y" and "setup.sym.draft.set" in ids(c)
    assert any("New plane" in la.GetInput() for la in c.win.view.label_actors)          # the draft is drawn on the model
    c.widget_do("setup.sym.draft.flip", "click")
    assert c.widget_do("setup.sym.draft.keeps")["value"].endswith("−y")
    c.widget_do("setup.sym.draft.cancel", "click")
    assert c.win.panels["setup"].sym.draft is None
    vol = c.engine.project().bodies()[0]["volume"]
    r = c.do("setup.symmetry", {"action": "add", "axis": "y"})
    assert "Rebuilding" in r["result"]["status"]
    wait(c)
    st = c.engine.state()
    assert len(st["symmetry"]) == 1 and c.engine.project().bodies()[0]["volume"] < 0.75 * vol
    assert any(b.get("plane_id") for b in st["bcs"]) and "setup.bc.0.linked" in ids(c)    # its condition, greyed and linked
    assert "setup.bc.0.delete" not in ids(c)
    n0 = st["symmetry"][0]["normal"][1]
    c.widget_do("setup.sym.0.flip", "click")
    wait(c)
    assert c.engine.state()["symmetry"][0]["normal"][1] == pytest.approx(-n0)
    c.widget_do("setup.sym.0.remove", "click")
    wait(c)
    assert not c.engine.state()["symmetry"] and c.engine.project().bodies()[0]["volume"] == pytest.approx(vol, rel=1e-6)


def test_a_plane_through_a_picked_face_and_a_curved_face_is_refused(bracket):
    c = bracket
    faces = c.engine.project().faces()
    cyl = next(f["id"] for f in faces if f["surface_type"] == "Cylinder")
    c.widget_do("setup.sym.from_face", "set", True)
    assert c.win.panels["setup"].sym.picking
    c.do("view.pick_face", {"id": cyl})
    assert "not flat" in c.widget_do("setup.status")["value"] and not c.engine.state().get("symmetry")
    c.widget_do("setup.bc.add", "set", "Symmetry plane (mirror)")                                     # (no face selected: the next click places it)
    assert c.win.panels["setup"].sym.picking


# ------------------------------------------------------------------------------------------------------------ rotors and zones
def test_rotors_are_added_edited_reset_and_deleted(aero):
    c = aero
    assert {"setup.rotor.detect", "setup.rotor.add", "setup.rotor.auto_thrust", "setup.zone.add"} <= ids(c)
    assert c.widget_do("setup.rotor.auto_thrust")["enabled"] is False                    # (nothing to reset: it says why)
    c.widget_do("setup.rotor.add", "click")
    assert len(c.engine.state()["rotors"]) == 1
    c.widget_do("setup.rotor.0.radius", "set", "30")
    c.widget_do("setup.rotor.0.thrust_N", "set", "4")
    r = c.engine.state()["rotors"][0]
    assert r["radius"] == pytest.approx(0.03) and r["thrust_N"] == 4
    assert any(la.GetInput().startswith("Rotor 1") for la in c.win.view.label_actors)
    assert "Weight" in c.widget_do("setup.rotor.weight")["value"]
    c.widget_do("setup.rotor.auto_thrust", "click")
    assert c.engine.state()["rotors"][0]["thrust_N"] is None
    c.do("setup.rotors", {"action": "set", "index": 0, "thrust_N": 2})
    assert c.engine.state()["rotors"][0]["thrust_N"] == 2
    c.widget_do("setup.rotor.0.delete", "click")
    assert c.engine.state()["rotors"] == []
    c.widget_do("setup.rotor.detect", "click")                                          # (a box has no motors: said, nothing changes)
    assert c.engine.state()["rotors"] == []


def test_moving_zones_are_added_set_fitted_centred_and_deleted(aero, monkeypatch):
    c = aero
    c.widget_do("setup.zone.add", "click")
    z = c.engine.state()["moving_zones"][0]
    assert z["name"] == "zone1" and z["method"] == "mrf"
    c.widget_do("setup.zone.0.rpm", "set", "5000")
    c.widget_do("setup.zone.0.method", "set", "Sliding mesh (transient)")
    c.widget_do("setup.zone.0.axis", "set", "+x")
    z = c.engine.state()["moving_zones"][0]
    assert z["rpm"] == 5000 and z["method"] == "sliding" and z["axis"] == "+x"
    part = c.widget_do("setup.zone.0.fit")["value"]["items"][1]
    c.widget_do("setup.zone.0.fit", "set", part)
    z = c.engine.state()["moving_zones"][0]
    assert z["center"][0] == pytest.approx(0.05, abs=1e-3) and z["radius"] > 0.01    # round the 100 x 20 x 20 mm box, along x
    # the suggested end time of a transient sliding-mesh run, and Use it
    c.do("setup.setting", {"key": "analysis", "value": "transient"})
    c._settle()
    assert "Suggested end time" in c.widget_do("setup.endtime.text")["value"]
    c.do("setup.end_time_use")
    assert c.engine.state()["settings"]["end_time"] == pytest.approx(c.engine.project().end_time_advice()["t"])
    # a centre picked on a cylinder: an axis along x is taken, a tilted one offers to turn the model
    import simwb.project as P
    info = {"face": 1, "kind": "cylinder", "center": [0.01, 0.02, 0.03], "axis": [0, 1, 0], "radius": 0.004, "length": None,
            "nearest": "+y", "angle_deg": 0.0}
    monkeypatch.setattr(P.Project, "axis_info", lambda self, f, pt=None: dict(info))
    c.do("setup.pick_axis", {"kind": "zone", "index": 0, "face": 1})
    z = c.engine.state()["moving_zones"][0]
    assert z["center"] == pytest.approx([0.01, 0.02, 0.03]) and z["axis"] == "+y"
    info.update(axis=[0, 0.2, 0.98], nearest="+z", angle_deg=11.5)
    r = c.do("setup.pick_axis", {"kind": "zone", "index": 0, "face": 1})
    assert r["result"]["oblique"] and "setup.zone.turn" in ids(c)
    c.widget_do("setup.zone.keep", "click")
    assert "setup.zone.turn" not in ids(c)
    c.widget_do("setup.zone.0.delete", "click")
    assert c.engine.state()["moving_zones"] == []


def test_external_flow_heading_and_marks(aero):
    c = aero
    from PySide6.QtWidgets import QLabel
    labels = [la.text() for la in c.win.panels["setup"].findChildren(QLabel)]
    assert "FLOW CONDITION" in labels and "MOVING ZONES" in labels and "ROTORS" in labels
    assert c.widget_do("setup.setting.turbulence")["visible"] is True                      # the flow model shows in Expert mode
    texts = [la.GetInput() for la in c.win.view.label_actors]
    assert any(t.startswith("Air ") for t in texts) and len(c.win.view.arrow_actors) >= 1     # the oncoming air


def test_every_condition_type_gets_a_label_and_the_right_arrows():
    from simwb import studies
    from simwb.qt import bcs
    spec = studies.STUDIES["cht_enclosure"]
    faces = [{"id": i, "normal": [0, 0, 1], "centroid": [0, 0, 0], "kind": "enclosure"} for i in range(1, 6)]
    st = {"study": "cht_enclosure", "study_spec": spec, "settings": {"air_flow": "fan"}, "geometry": {"info": {"bbox": [0, 0, 0, 1, 1, 1]}},
          "bcs": [{"id": "a", "name": "Inlet", "type": "velocity_inlet", "faces": [1], "params": {"velocity": 2, "T": 25, "direction": "normal"}},
                  {"id": "b", "name": "Outlet", "type": "pressure_outlet", "faces": [2], "params": {}},
                  {"id": "c", "name": "Wall", "type": "wall_convection", "faces": [3], "params": {"h": 5, "T_inf": 20}}],
          "body_list": [{"name": "Chip", "heat_W": 3, "centroid": [0.5, 0.5, 0.5]}], "domain": {"enclosure_mode": "auto_box"}}
    tints, labels, arrows = bcs.marks(st, faces, lambda ids: [0.5, 0.5, 0.5], 1.0)
    texts = [la["text"] for la in labels]
    assert tints == {1: spec["face_bc_types"]["velocity_inlet"]["color"], 2: spec["face_bc_types"]["pressure_outlet"]["color"],
                     3: spec["face_bc_types"]["wall_convection"]["color"]}
    assert "Inlet: 2 m/s, 25 °C" in texts
    assert any(t.startswith("Outlet") for t in texts) and any(t.startswith("Wall: 5") for t in texts) and "Chip: 3 W" in texts
    dirs = [a["dir"] for a in arrows]
    assert [0, 0, -1] in dirs and [0, 0, 1] in dirs                                          # in at the inlet, out at the outlet and wall
    assert any("Open sides" in t for t in texts)


def test_auto_inlet_and_outlet_of_a_cooling_study(ctl):
    ctl.do("study.example", {"study": "cht_enclosure"})
    wait(ctl)
    ctl.do("step.go", {"step": "setup"})
    r = ctl.do("setup.auto_inlet_outlet")
    names = r["result"]["bcs"]
    assert "Inlet" in names and "Outlet" in names and "assigned" in r["result"]["status"]


def test_the_setup_panel_does_not_need_more_width_than_a_dock_gives(ctl):
    """A one-line button or a row of several buttons in a rotor, zone or symmetry card used to make the whole panel 890 px wide, so
    the text of every block was cut off at the dock's edge. Rows now wrap and long titles shrink."""
    from simwb.qt.panels.common import FlowLayout
    ctl.do("study.example", {"study": "external_aero"})
    assert from_thread(ctl.app, lambda: ctl.wait_idle(300), 320)["idle"]
    ctl.do("step.go", {"step": "setup"})
    ctl._settle()
    p = ctl.win.panels["setup"]
    assert p.minimumSizeHint().width() <= 520, p.minimumSizeHint().width()          # (it was 893 with the same fonts)
    row = ctl._index()["setup.rotor.detect"].parentWidget()
    assert isinstance(row.layout(), FlowLayout)                                      # the rotor buttons wrap to a second line
