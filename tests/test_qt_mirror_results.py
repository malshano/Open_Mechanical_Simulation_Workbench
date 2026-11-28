"""#63: the Results step shows the whole model made of the solved half and its mirror image, on a synthetic half-model flow result
(tests/flow_support.py with symmetry=True: the half y >= 0 of the air box, the block and the propeller block, the plane y = 0). No solver runs."""
import numpy as np
import pytest
from flow_support import make_flow_study
from qt_support import ctl  # noqa: F401


def ov(ctl):
    return ctl.state()["view"]["overlay"]


def ypos(ctl):
    return ctl.win.view._ov["pos"][:, 1]


def switch(ctl, on):
    ctl.widget_do("results.mirror", "set", on)


def half_study(ctl, **kw):
    pr, rs = make_flow_study(ctl, symmetry=True, **kw)
    ctl.do("results.tab", {"name": "display"})
    return pr, rs


def object_triangles(ctl, mirror):
    ctl.engine.mirror_view = mirror
    return ctl.engine.flow_object("Velocity", -1)["n_tris"]


# ---------------------------------------------------------------- the switch
def test_the_switch_is_in_the_display_tab_and_disabled_when_the_study_has_no_symmetry_plane(ctl):
    make_flow_study(ctl)                                                               # (a whole model: nothing to mirror)
    ctl.do("results.tab", {"name": "display"})
    assert ctl.widget_do("results.mirror")["enabled"] is False
    assert "no symmetry plane" in ctl.win.panels["results"].mirror.toolTip()
    assert ctl.state()["shell"]["mirror"]["available"] is False and "mirror" not in ov(ctl)
    with pytest.raises(ValueError, match="no symmetry plane"):
        ctl.do("results.show", {"mirror": False})


def test_the_switch_is_on_by_default_for_a_study_with_a_plane_and_draws_the_solved_half_twice(ctl):
    pr, rs = half_study(ctl)
    w = ctl.widget_do("results.mirror")
    assert w["enabled"] is True and w["value"] == {"checked": True}
    assert "Show the whole model" in ctl.win.panels["results"].mirror.text() and "mirror the symmetry plane" in ctl.win.panels["results"].mirror.text()
    half = object_triangles(ctl, False)
    ctl.engine.mirror_view = True
    ctl.win.panels["results"]._display_changed()
    o = ov(ctl)
    assert o["triangles"] == 2 * half and o["mirror"] == {"solved_triangles": half, "copies": 2}
    y = ypos(ctl)
    assert y.min() == pytest.approx(-y.max()) and y.min() < -0.2                         # (as far on one side as on the other)
    st = ctl.state()
    assert st["shell"]["mirror"] == {"available": True, "on": True, "planes": ["Symmetry plane 1"], "copies": 2}
    assert o["mirror_planes"] == ["Symmetry plane 1"]


def test_one_click_back_to_the_solved_half_and_forth(ctl):
    half_study(ctl)
    whole = ov(ctl)["triangles"]
    switch(ctl, False)
    o = ov(ctl)
    assert o["triangles"] == whole // 2 and "mirror" not in o and ypos(ctl).min() >= -1e-9
    assert ctl.state()["shell"]["mirror"]["on"] is False and ctl.state()["shell"]["mirror"]["available"] is True
    assert ctl.win.panels["results"].mirror_note.isVisibleTo(ctl.win.panels["results"]) is False
    switch(ctl, True)
    assert ov(ctl)["triangles"] == whole and ctl.win.panels["results"].mirror_note.isVisibleTo(ctl.win.panels["results"])
    assert ctl.do("results.show", {"mirror": False})["result"]["mirror"]["on"] is False        # (the same switch through the API action)
    assert ov(ctl)["triangles"] == whole // 2


def test_the_copy_is_marked_in_the_legend_the_status_line_and_a_faint_plane_outline(ctl):
    half_study(ctl)
    legend = ctl.win.view.legend
    assert [e["name"] for e in legend.extra] == ["mirrored copy"]
    n = len(ctl.win.view.overlay_actors)
    switch(ctl, False)
    assert legend.extra == [] and len(ctl.win.view.overlay_actors) < n                  # (the plane's outline goes with the copy)
    assert "solved part only" in ctl.win.statusBar().currentMessage()
    switch(ctl, True)
    assert "whole model" in ctl.win.statusBar().currentMessage() and "Symmetry plane 1" in ctl.win.statusBar().currentMessage()
    assert [e["name"] for e in legend.extra] == ["mirrored copy"] and len(ctl.win.view.overlay_actors) == n
    # the outline lies in the plane y = 0 and covers what is drawn
    from vtkmodules.util.numpy_support import vtk_to_numpy
    flat = []
    for a in ctl.win.view.overlay_actors:
        pts = vtk_to_numpy(a.GetMapper().GetInput().GetPoints().GetData()) if a.GetMapper().GetInput().GetNumberOfPoints() == 4 else None
        if pts is not None:
            flat.append(pts)
    assert flat and all(np.allclose(p[:, 1], 0.0, atol=1e-9) for p in flat)
    drawn = ctl.win.view._ov["pos"]
    assert flat[0][:, 0].min() <= drawn[:, 0].min() and flat[0][:, 0].max() >= drawn[:, 0].max()


def test_the_view_and_the_picture_show_what_the_view_shows(ctl, tmp_path):
    half_study(ctl)
    b = ctl.win.view.stats()["bounds"]
    assert b[2] < -0.2 and b[3] > 0.2                                                  # (VTK bounds: x0 x1 y0 y1 z0 z1; the view holds the copy: y runs from one side to the other)
    png = tmp_path / "whole.png"
    ctl.do("view.picture", {"path": str(png)})
    assert png.exists() and png.stat().st_size > 1000
    switch(ctl, False)
    assert ctl.win.view.stats()["bounds"][2] >= -1e-6


def test_the_camera_is_fitted_again_when_the_switch_changes(ctl):
    half_study(ctl)
    cam_whole = ctl.win.view.camera()
    switch(ctl, False)
    cam_half = ctl.win.view.camera()
    assert cam_whole["position"] != cam_half["position"]
    switch(ctl, True)
    assert np.allclose(ctl.win.view.camera()["position"], cam_whole["position"], rtol=1e-6)


# ---------------------------------------------------------------- what is mirrored
def test_the_section_view_of_a_flow_cuts_both_sides(ctl):
    half_study(ctl)
    ctl.do("results.tab", {"name": "flow"})
    ctl.do("results.flow", {"planes": [{"axis": "x", "position": 0.5}], "plane_model": "cut"})
    o = ov(ctl)
    assert o["mirror"]["copies"] == 2 and ypos(ctl).min() < -0.2                         # (the object cut open at the plane, on the solved side and on its copy)
    cut = o["mirror"]["solved_triangles"]
    ctl.do("results.flow", {"plane_model": "ghost"})
    assert ov(ctl)["mirror"]["solved_triangles"] > cut                                   # (whole again: more triangles than the cut one)


def test_the_show_in_the_view_list_works_on_both_sides(ctl):
    half_study(ctl, prop=True)
    both = ov(ctl)["triangles"]
    ctl.do("results.visibility", {"show": {"zone1": False}})
    fewer = ov(ctl)
    assert fewer["triangles"] < both and fewer["mirror"]["copies"] == 2
    assert fewer["triangles"] == 2 * fewer["mirror"]["solved_triangles"]                 # (the propeller left both halves: the rest is still whole)


def test_flow_tools_get_their_mirror_images(ctl):
    half_study(ctl)
    f = ctl.win.panels["results"].flow
    ctl.do("results.tab", {"name": "flow"})
    ctl.do("results.flow", {"planes": [{"axis": "x", "position": 0.5}], "vectors": {"on": True, "axis": "x", "position": 0.5, "n": 10}})
    L = ctl.engine.flow_layers(f.request(), -1)
    pl = L["planes"][0]["payload"]
    assert pl["mirror"]["copies"] == 2 and pl["n_tris"] == 2 * pl["mirror"]["solved_tris"]
    from simwb import mirrorview as mv
    import base64
    pos = np.frombuffer(base64.b64decode(pl["positions"]), np.float32).reshape(-1, 3)
    assert pos[:, 1].min() == pytest.approx(-pos[:, 1].max(), abs=1e-5)                  # (the plane across x is whole: one cut across both halves)
    v = L["vectors"]
    vp = np.frombuffer(base64.b64decode(v["positions"]), np.float32).reshape(-1, 3)
    vv = np.frombuffer(base64.b64decode(v["vectors"]), np.float32).reshape(-1, 3)
    n = v["n"] // 2
    assert np.allclose(vp[n:] * [1, -1, 1], vp[:n]) and np.allclose(vv[n:] * [1, -1, 1], vv[:n])      # the y part of the flow (across the plane) changes sign
    ctl.engine.mirror_view = False
    assert "mirror" not in ctl.engine.flow_layers(f.request(), -1)["planes"][0]["payload"]
    assert mv.mirror_vectors(L["vectors"], []) is L["vectors"]


def test_vortex_surfaces_arrows_particles_and_volume_draw_with_the_copy(ctl):
    half_study(ctl)
    ctl.do("results.tab", {"name": "flow"})
    ctl.do("results.flow", {"iso": {"on": True, "k": 30}})
    o = ov(ctl)
    assert o.get("mirror") and o["mirror"]["copies"] == 2                                # (the vortex surfaces and the object, merged and mirrored)
    assert ypos(ctl).min() < -0.2
    ctl.do("results.flow", {"iso": {"on": False}, "streams": {"on": True, "n": 60, "steps": 40}, "volume": {"on": True, "field": "Velocity"}})
    assert {"volume", "paths"} <= set(ctl.win.view.overlay_info["flow_tools"])
    b = ctl.win.view.stats()["bounds"]
    assert b[2] < -0.2 < 0.2 < b[3]                                                      # (the cloud, the paths and the object reach both sides)


def test_the_zones_outline_once_on_the_plane_and_twice_off_it(ctl):
    pr, rs = half_study(ctl, prop=True)
    ctl.do("results.visibility", {"show": {"Moving zones (outline)": True}})
    rv = ctl.win.view.region_view
    assert len(rv.regions) == 1                                                          # (zone1 is centred on the plane, its axis lies in it)
    z = dict(pr.data["moving_zones"][0], center=[1.5, 0.6, 0.0])
    pr.data["moving_zones"] = [z]
    ctl.win.panels["results"].draw_outlines()
    assert len(rv.regions) == 2 and rv.regions[1]["name"].endswith("(mirrored)")
    assert np.allclose(rv.regions[1]["start"][1], -0.6)
    switch(ctl, False)
    assert len(rv.regions) == 1


def test_the_air_box_is_whole_and_the_geometry_step_gets_its_own_back(ctl):
    pr, rs = half_study(ctl)
    faces = [{"id": 1, "kind": "farfield", "volumes": [0], "mesh": {"vertices": [-1, 0.5, -1, 2, 0.5, -1, 2, 0.5, 1], "triangles": [0, 1, 2]}},
             {"id": 2, "kind": "farfield", "symmetry": 0, "volumes": [0], "mesh": {"vertices": [-1, 0, -1, 2, 0, -1, 2, 0, 1], "triangles": [0, 1, 2]}}]
    pr._faces = faces
    st = dict(ctl.engine.state(), geometry={"status": "ready"})
    v = ctl.win.view
    v.set_geometry([], ("farfield",), False)
    ctl.win._air_for_results(st)
    assert v.air_mirror_key is not None and sorted(p[0] for p in v._air_parts) == [1, 1_000_001]      # (the far face and its image; the cut is inside the whole model)
    switch(ctl, False)
    ctl.win._air_for_results(st)
    assert v.air_mirror_key is None and sorted(p[0] for p in v._air_parts) == [1, 2]                   # (the solved part's own air box again)


# ---------------------------------------------------------------- probes and numbers stay on the solved half
def test_a_probe_on_the_copy_reads_the_solved_value_and_says_so(ctl):
    half_study(ctl)
    ctl.do("results.show", {"field": "Pressure"})
    p = ctl.win.panels["results"]
    view = ctl.win.view
    m = view._ov["mirror"]
    n = m["solved_tris"]
    cell_solved, cell_copy = 0, n                                                       # (the first triangle of the half and of its copy)
    c0 = view._ov["pos"][view._ov["idx"][cell_solved]].mean(0)
    c1 = view._ov["pos"][view._ov["idx"][cell_copy]].mean(0)
    assert c1[1] < 0 <= c0[1]
    assert view.mirror_side(cell_solved) == 0 and view.mirror_side(cell_copy) == 1
    back = view.solved_point(cell_copy, c1)
    assert np.allclose(back, c0, atol=1e-5) and back[1] >= 0                             # (a point on the copy maps to the solved point)
    val0, val1 = view.value_at(cell_solved, c0), view.value_at(cell_copy, c1)
    assert val0 == pytest.approx(val1)
    p.probe_on = True
    view.last_hit = {"kind": "surface", "cell": cell_copy, "point": [float(x) for x in c1]}
    p._point_picked(*c1)
    assert p.pins[-1]["mirrored"] is True and "mirrored side" in p.pin_rows[-1]["label"].text()
    view.last_hit = {"kind": "surface", "cell": cell_solved, "point": [float(x) for x in c0]}
    p._point_picked(*c0)
    assert p.pins[-1]["mirrored"] is False and "mirrored" not in p.pin_rows[-1]["label"].text()
    switch(ctl, False)                                                                   # (the point on the copy has nothing to stand on any more)
    assert len(p.pins) == 1 and not p.pins[0]["mirrored"]


def test_a_line_picked_on_the_copy_is_plotted_on_the_solved_part(ctl):
    half_study(ctl)
    p, view = ctl.win.panels["results"], ctl.win.view
    n = view._ov["mirror"]["solved_tris"]
    c1 = view._ov["pos"][view._ov["idx"][n]].mean(0)
    p.line_pick = True
    p.line_ends = []
    view.last_hit = {"kind": "surface", "cell": n, "point": [float(x) for x in c1]}
    p._point_picked(*c1)
    assert p.line_ends[0][1] >= -1e-9 and "mirrored copy" in ctl.win.statusBar().currentMessage()


def test_a_whole_model_keeps_the_summary_numbers_and_the_note_says_whose_they_are(ctl):
    half_study(ctl)
    note = ctl.win.panels["results"].mirror_note.text()
    assert "Probes and plots read the solved part" in note and "whole body's already" in note


# ---------------------------------------------------------------- a sliding zone turning with the time bar and the video
def test_the_mirror_of_each_frame_is_the_mirror_of_the_solved_surface_at_that_time(ctl):
    half_study(ctl, prop=True, moving=4)
    p = ctl.win.panels["results"]
    assert p.timebar.transient
    seen = []
    for i in range(4):
        p.timebar.set_index(i)
        o = ov(ctl)
        m = ctl.win.view._ov["mirror"]
        n = len(ctl.win.view._ov["pos"]) // 2
        pos = ctl.win.view._ov["pos"]
        assert o["mirror"]["copies"] == 2
        assert np.allclose(pos[n:] * [1, -1, 1], pos[:n], atol=1e-5)                    # the copy is the mirror image at this very time
        seen.append(pos[:n][:, 1].max())
        # the propeller alone: it moves +y by 0.05 i on the solved side, so -y by 0.05 i on the copy (it turns the other way about the mirrored axis)
        ctl.do("results.visibility", {"show": {"Object": False}})
        pp = ctl.win.view._ov["pos"]
        nn = len(pp) // 2
        assert pp[:nn][:, 1].max() == pytest.approx(0.3 + 0.05 * i, abs=0.08) and pp[nn:][:, 1].min() == pytest.approx(-pp[:nn][:, 1].max(), abs=1e-5)
        ctl.do("results.visibility", {"all": True})
    assert seen[3] > seen[0]                                                             # (the zone does move)


def test_the_video_has_every_picture_with_the_copy(ctl, tmp_path):
    half_study(ctl, prop=True, moving=5)
    p = ctl.win.panels["results"]
    spans = []
    p.timebar.timeChanged.connect(lambda i: spans.append((ctl.win.view._ov["mirror"]["copies"], float(ctl.win.view._ov["pos"][:, 1].min()))))
    out = tmp_path / "w.mp4"
    v = ctl.do("results.video", {"path": str(out), "fps": 4})["result"]["video"]
    assert v["frames"] == 5 and out.exists() and len(spans) >= 5
    assert all(c == 2 and y < -0.2 for c, y in spans[-5:])


# ---------------------------------------------------------------- saved with the study?
def test_the_switch_is_a_viewing_choice_not_saved_with_the_study(ctl):
    pr, rs = half_study(ctl)
    switch(ctl, False)
    pr.save()
    assert "mirror" not in str(pr.data.get("settings")) and "mirror_view" not in pr.data
    ctl.do("study.open", {"id": pr.id})
    ctl.do("step.go", {"step": "results"})
    assert ctl.engine.mirror_view is False                                               # (a choice of the session: it stays while the program runs)


# ---------------------------------------------------------------- a structural result (not a flow): sections and the deformation
def solid_study(ctl):
    """A static study with a synthetic result on the half y >= 0 of a block (stress and a deformation in mm), and a symmetry plane at y = 0."""
    import simwb.project as P
    from flow_support import flow_resultset
    from simwb import symmetry as sym
    from simwb.results import ResultSet
    f = flow_resultset(n=5, symmetry=True)
    n = f.nodes
    disp = np.stack([0.02 * n[:, 0], 0.5 * (n[:, 1] + 0.3), 0.01 * n[:, 2]], 1)                     # (mm: moves in +y, towards the plane's far side)
    fields = {"Von Mises stress": ("node", 1e6 * (1 + n[:, 0] ** 2), "Pa"), "Total deformation": ("node", disp, "mm")}
    rs = ResultSet(n, f.tets, f.tet_region, f.regions, fields, f.surf_tris, f.surf_cell, f.surf_group, f.groups)
    pr = P.create_project("Solid half", "static_structural")
    pr.results_dir.mkdir(exist_ok=True)
    rs.save(pr.results_dir / "result.npz")
    pr._results = rs
    pr.data["symmetry"] = [sym.make_plane([0, 0, 0], [0, 1, 0], "Symmetry plane 1")]
    pr.data["solve"].update(status="done", summary={})
    pr.save()
    ctl.do("study.open", {"id": pr.id})
    ctl.do("step.go", {"step": "results"})
    ctl.do("results.tab", {"name": "display"})
    return pr, rs


def test_a_structural_result_is_mirrored_too_with_the_section_and_the_deformation(ctl):
    pr, rs = solid_study(ctl)
    assert ctl.widget_do("results.mirror")["enabled"] is True                            # (the study's own plane: no run recorded one)
    ctl.do("results.show", {"field": "Von Mises stress"})
    full = ov(ctl)
    assert full["mirror"]["copies"] == 2 and full["triangles"] == 2 * full["mirror"]["solved_triangles"]
    ctl.do("results.show", {"section": "x", "section_pos": 0.5})
    cut = ov(ctl)
    assert cut["mirror"]["copies"] == 2 and cut["triangles"] < full["triangles"] and ypos(ctl).min() < -0.5
    ctl.do("results.show", {"section": ""})
    ctl.do("results.show", {"field": "Total deformation", "deform": True, "scale": 1})
    pos = ctl.win.view._ov["pos"]
    n = len(pos) // 2
    base = ctl.win.view._ov["base"]
    assert np.allclose(base[n:] * [1, -1, 1], base[:n], atol=1e-5)                       # (the undeformed copy is the mirror image ...)
    assert np.allclose(pos[n:] * [1, -1, 1], pos[:n], atol=1e-5)                         # ... and so is the deformed one: the y displacement of the copy points the other way
    assert not np.allclose(pos[:n], base[:n])
    switch(ctl, False)
    assert ov(ctl)["triangles"] == full["triangles"] // 2
