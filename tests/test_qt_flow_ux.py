"""Beta 5 user-experience fixes of the Results step, on a synthetic flow result (tests/flow_support.py) - no solver is run."""
from flow_support import make_flow_study
from qt_support import ctl  # noqa: F401


def _ov(ctl):
    return ctl.state()["view"]["overlay"]


def _whole_object_triangles(ctl):
    return ctl.engine.flow_object("Velocity", -1)["n_tris"]


# ---------------------------------------------------------------- #60: a contour plane does not clip the model
def test_adding_a_contour_plane_leaves_the_whole_model_visible(ctl):
    """Fixes #60. The model was cut open at the plane as a side effect of a single contour plane; now it is whole (and see-through, so the
    plane behind its near half still shows). Cutting is the explicit 'section view' choice."""
    make_flow_study(ctl)
    whole = _whole_object_triangles(ctl)
    ctl.do("results.tab", {"name": "flow"})
    ov = _ov(ctl)
    assert ov["flow_tools"] == ["planes"] and ov["planes"][0]["axis"] == "y"          # (the result opens with one plane through the middle)
    assert ov["triangles"] == whole                                                    # the model is not clipped
    assert ov["opacity"] < 1.0                                                         # (see-through by default)
    for axis, pos in (("x", 0.3), ("z", 0.7)):                                         # another contour, on any axis: still whole
        ctl.do("results.flow", {"planes": [{"axis": axis, "position": pos}]})
        assert _ov(ctl)["triangles"] == whole and _ov(ctl)["planes"][0]["axis"] == axis
    ctl.do("results.flow", {"planes": [{"axis": "y"}, {"axis": "x", "position": 0.8}, {"axis": "z"}]})
    assert _ov(ctl)["triangles"] == whole and len(_ov(ctl)["planes"]) == 3


def test_a_solid_model_with_planes_is_whole_and_opaque(ctl):
    make_flow_study(ctl)
    whole = _whole_object_triangles(ctl)
    ctl.do("results.tab", {"name": "flow"})
    ctl.do("results.flow", {"plane_model": "solid"})
    assert _ov(ctl)["triangles"] == whole and _ov(ctl)["opacity"] == 1.0


def test_the_section_view_is_an_explicit_choice(ctl):
    """The cut still exists, but only when asked for in the Flow tab ('Cut open at plane 1'), and it goes away again."""
    make_flow_study(ctl)
    whole = _whole_object_triangles(ctl)
    ctl.do("results.tab", {"name": "flow"})
    ctl.do("results.flow", {"plane_model": "cut"})
    cut = _ov(ctl)["triangles"]
    assert 0 < cut < whole
    ctl.do("results.flow", {"plane_model": "ghost"})
    assert _ov(ctl)["triangles"] == whole


def test_the_model_choice_is_a_labelled_control_that_only_shows_with_a_plane(ctl):
    make_flow_study(ctl)
    ctl.do("results.tab", {"name": "flow"})
    f = ctl.win.panels["results"].flow
    assert f.plane_model_row.isVisibleTo(f)
    assert [f.plane_model.itemText(i) for i in range(3)] == ["Whole model, see-through", "Whole model, solid", "Cut open at plane 1 (section view)"]
    ctl.do("results.flow", {"planes": []})
    assert not f.plane_model_row.isVisibleTo(f)

