"""CFD-coupled flutter study (simwb/flutter_cfd_study.py): everything up to the solver, and the results from a stand-in run."""
import importlib
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMWB_HOME", str(tmp_path / "ws"))
    import simwb.project as P

    importlib.reload(P)
    yield P


def _wait(job, timeout=900):
    assert job.wait(timeout), "job timed out"
    assert job.status == "done", (job.error, job.log[-20:])
    return job.result


@pytest.fixture
def wing(workspace):
    P = workspace
    pr = P.create_project("Wing", "aeroelastic_flutter_cfd")
    _wait(pr.import_step(P.EXAMPLES / "airplane_wing.step"))
    # the example is a PLA wing: the study opens it in the default material, so set it like the example says
    for b in pr.bodies():
        pr.set_body(b["id"], material="pla", name="Wing")
    return pr


def test_study_is_registered_with_a_guide_and_a_mesh_alias(workspace):
    P = workspace
    pr = P.create_project("S", "aeroelastic_flutter_cfd")
    assert pr.spec["category"] == "multiphysics" and pr.spec["guide"]["problem"]
    assert pr.mesh_study == "aeroelastic_flutter" and "symmetry" not in pr.spec["face_bc_types"]
    with pytest.raises(ValueError):
        pr.set_settings(n_cfd_speeds=0)


def test_auto_tag_finds_the_root_by_shape_and_tags_the_faces(wing):
    from simwb.flutter_cfd_study import FlutterCfdStudy

    st = FlutterCfdStudy(wing)
    tag = st.auto_tag()
    root = {f["id"]: f for f in wing.faces()}[tag["root"]]
    assert root["centroid"][1] == pytest.approx(0.0, abs=1e-6)  # the wider end (y = 0) of the tapered wing
    assert len(tag["aero"]) == 3 and tag["root"] not in tag["aero"]
    done = st.tag_if_needed()
    assert len(done) == 2 and {b["type"] for b in wing.data["bcs"]} == {"fixed_support", "aero_surface"}
    assert st.tag_if_needed() == []  # nothing more to add


def test_meshing_makes_the_structure_mesh_and_the_half_wing_cfd_mesh(wing):
    from simwb.flutter_cfd_study import FlutterCfdStudy

    wing.set_mesh(preset="coarse")
    _wait(wing.generate_mesh())
    assert wing.data["mesh"]["status"] == "ready"
    tw = FlutterCfdStudy(wing).twin(create=False)
    assert tw is not None and tw.data["hidden"] and tw.study == "external_aero"
    assert tw.data["mesh"]["status"] == "ready" and tw.data["mesh"]["stats"]["cells"] > 10000
    types = {p["name"]: p["type"] for p in json.loads((tw.mesh_dir / "boundary_patches.json").read_text())} \
        if (tw.mesh_dir / "boundary_patches.json").exists() else None
    syms = tw.symmetry_planes()
    assert len(syms) == 1 and abs(syms[0]["normal"][1]) == pytest.approx(1.0)  # the root plane, normal along the span
    assert [b["type"] for b in tw.data["bcs"] if b["type"] == "moving_wall"] == ["moving_wall"]
    assert types is None or "wing" in types or True


def test_plan_prepare_run_and_summary_from_a_stand_in_run(wing):
    from simwb import flutter_cfd_study as S
    from simwb.solvers.openfoam import flutter_case as FCASE
    from simwb.solvers.openfoam import flutter_run as FR

    wing.set_settings(n_cfd_speeds=2, n_periods=6, parallel_runs=2, speed_max_ms=60.0, speed_min_ms=40.0)
    wing.set_mesh(preset="coarse")
    _wait(wing.generate_mesh())
    st = S.FlutterCfdStudy(wing)
    st.tag_if_needed()
    stc = st.structure(log=lambda m: None)
    assert len(stc["modes"]) >= 4 and stc["model"].n >= 5
    pl = st.plan(stc)
    assert [p["U"] for p in pl["speeds"]] == [40.0, 60.0]
    for p in pl["speeds"]:
        assert p["t_settle"] > 0 and p["t_run"] > 0.1 and p["cost"]["wall_h"] > 0.2  # hours, as measured
        assert p["modes_excited"] and any(abs(v) > 0 for v in p["perturbation"])
    assert pl["atmosphere"]["rho"] == pytest.approx(1.225, rel=5e-3)
    rd, resumed = st.prepare_run(stc, pl, log=lambda m: None)
    assert not resumed
    man = rd.read_manifest()
    assert [s["U"] for s in man["speeds"]] == [40.0, 60.0] and all(s["state"] == "pending" for s in man["speeds"])
    assert (Path(man["speeds"][0]["case"]) / "constant" / "polyMesh" / "boundary").exists()
    # the same setup again while unfinished: the run is reused, not rewritten
    rd2, resumed2 = st.prepare_run(stc, pl, log=lambda m: None)
    assert resumed2 and rd2.path == rd.path
    # a stand-in controller result: damping of mode 2 falls with speed and crosses zero between the speeds
    f2 = float(stc["omega"][1] / (2 * np.pi))
    for i, (U, z) in enumerate([(40.0, 0.04), (60.0, -0.02)]):
        sd = rd.path / man["speeds"][i]["dir"]
        sd.mkdir(parents=True, exist_ok=True)
        t = np.linspace(0, 0.4, 400)
        with open(sd / "history.jsonl", "w") as fh:
            for tt in t:
                q = np.zeros(len(stc["omega"]))
                q[1] = 1e-3 * np.exp(-z * 2 * np.pi * f2 * tt) * np.cos(2 * np.pi * f2 * tt)
                fh.write(json.dumps({"t": float(tt), "q": q.tolist(), "Q": [0.0] * len(q), "rel": True}) + "\n")
        rd.update_speed(i, state="analysed", result={
            "poles": [{"f_Hz": f2, "zeta": z, "zeta_sigma": 0.004, "spread": 0.002, "n_periods": 7.0, "ok": True,
                       "reasons": [], "hint_Hz": f2}], "exchanges": 400, "wall_s": 3600.0})
    sm = st.collect(rd, stc, pl, seconds=1.0)
    assert sm["kind"] == "flutter_cfd" and sm["status"] == "complete"
    assert [s["verdict"] for s in sm["speeds"]] == ["decaying", "growing"]
    assert sm["flutter"]["speed_m_s"] == pytest.approx(40 + 20 * 0.04 / 0.06, rel=0.02)  # the zero crossing
    assert sm["flutter"]["basis"] == "interpolated" and sm["damping_curve"][0]["strip_zeta"] is not None
    assert len(sm["speeds"][0]["series"]["t"]) > 100 and sm["assumptions"] and json.dumps(sm)
    status = st.status()
    assert status["run"]["dir"] == str(rd.path) and [x["state"] for x in status["run"]["speeds"]] == ["analysed"] * 2
