"""The CFD-coupled flutter study (`aeroelastic_flutter_cfd`): a flexible wing, blade or fin in a 3D flow, solved by OpenFOAM
coupled to the modal structure, at several air speeds, to find where the damping of a mode turns negative (flutter).

How the pieces fit (see docs/ROADMAP.md and simwb/solvers/openfoam/lumped.py for the measured facts):

* The parent project (this study) holds the STEP file, the structure mesh (solid parts, like the strip-theory study), the
  setup (root = fixed support, aerodynamic surface faces, added masses) and the settings. Its modes (built-in FEM) and the
  strip-theory prediction (`flutter_study`) give the speeds to run, the frequencies to expect and the excitation.
* A hidden CFD twin (`external_aero`, transient, the wing a moving wall) is made from the same STEP: the root face goes onto
  a symmetry plane (a half model), every other wing face is the coupled surface. Both meshes are made by one mesh job.
* A run is a folder with a manifest of speeds (`flutter_run`): a detached controller per run, because it takes hours. It
  survives a restart of the server (the project re-attaches to it), saves a checkpoint per speed and ends a hung exchange.
  Every finished speed is analysed (`flutter_damping`: damping with its uncertainty) and appears in the partial results.
* The summary follows the frozen schema of `flutter_plan.build_summary`.

Assumptions the results carry (also listed in the summary): incompressible flow, so Mach 0.3 is the limit; linear modal
structure; the load of the held wing is the reference (the motion is the perturbation around the loaded equilibrium); the
coupling is explicit (fine for a light fluid like air).
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

from . import flutter_plan as plan
from . import materials, studies

MACH_LIMIT_INCOMPRESSIBLE = 0.3
STEP_DT_AT_40MS = 7e-5  # measured: the CFD time step of the wing case at 40 m/s, maxCo 6 (scales with 1 / U and maxCo)
FINISHED = ("analysed", "failed", "stalled", "cancelled")


def _axis_name(v) -> str:
    v = np.asarray(v, float)
    i = int(np.argmax(np.abs(v)))
    return ("+" if v[i] >= 0 else "-") + "xyz"[i]


class FlutterCfdStudy:
    def __init__(self, pr):
        self.pr = pr

    # ------------------------------------------------------------------------------------------ settings and axes
    @property
    def s(self) -> dict:
        return self.pr.data["settings"]

    def flow_vec(self):
        return np.array(studies.AXES[str(self.s.get("flow_direction") or "+x")], float)

    def span_idx(self) -> int:
        return "xyz".index(str(self.s.get("span_axis") or "y"))

    def up_axis(self) -> str:
        """The lift direction: chord x span."""
        c, sp = self.flow_vec(), np.eye(3)[self.span_idx()]
        return _axis_name(np.cross(c, sp))

    def strip_settings(self) -> dict:
        """The settings of the strip-theory study (modes and the prediction), taken from this study's settings."""
        keys = ("flow_direction", "span_axis", "n_modes", "n_strips", "altitude_m", "damping_percent", "finite_wing",
                "design_speed_ms")
        out = {k: self.s[k] for k in keys if k in self.s}
        out["speed_max_ms"] = 0.0  # always sweep to the strip theory's own limit (Mach 0.6): the CFD speed range is separate
        out["n_speeds"] = 100
        return out

    # ------------------------------------------------------------------------------------------ face tagging
    def auto_tag(self) -> dict:
        """Propose the root and the aerodynamic faces from the shape: the root is the larger of the two flat end faces
        across the span axis (a wing is wider at the root); every other face is aerodynamic. Returns {"root": id|None,
        "aero": [ids], "note": str}."""
        sp = self.span_idx()
        faces = [f for f in self.pr.faces() if f.get("kind") not in ("farfield", "enclosure")]
        ends = [f for f in faces if "Plane" in str(f.get("surface_type")) and abs(f["normal"][sp]) > 0.95]
        if not ends:
            return {"root": None, "aero": [f["id"] for f in faces], "note": "No flat end face across the span axis found."}
        root = max(ends, key=lambda f: f["area"])
        note = ("Root chosen as the larger end face (check it: the face that is clamped)."
                if len(ends) > 1 else "Only one flat end face: taken as the root.")
        return {"root": root["id"], "aero": [f["id"] for f in faces if f["id"] != root["id"]], "note": note}

    def tag_if_needed(self) -> list:
        """Add the root support and the aerodynamic surface when the user has set none; returns what was done."""
        types = {b["type"] for b in self.pr.data["bcs"] if b["faces"]}
        done = []
        if "fixed_support" in types and "aero_surface" in types:
            return done
        tag = self.auto_tag()
        if "fixed_support" not in types:
            if tag["root"] is None:
                raise ValueError("Mark the clamped root face as 'Fixed support' (no flat end face could be found).")
            self.pr.add_bc("Root (automatic)", "fixed_support", [tag["root"]])
            done.append(f"Root face set automatically. {tag['note']}")
        if "aero_surface" not in types:
            aero = [i for i in tag["aero"] if i != tag["root"]]
            self.pr.add_bc("Aerodynamic surface (automatic)", "aero_surface", aero)
            done.append("All faces except the root are the aerodynamic surface.")
        return done

    def _root_face_id(self):
        for b in self.pr.data["bcs"]:
            if b["type"] == "fixed_support" and b["faces"]:
                return b["faces"][0]
        return None

    # ------------------------------------------------------------------------------------------ the CFD twin
    def _twin_id(self):
        return (self.pr.data.get("twins") or {}).get("cfd")

    def twin(self, create=True):
        from . import project as P

        tid = self._twin_id()
        if tid:
            try:
                return P.open_project(tid)
            except Exception:  # noqa: BLE001 - folder gone: make it again
                pass
        if not create:
            return None
        loc = self.pr.dir / "twins"
        loc.mkdir(exist_ok=True)
        tw = P.create_project(f"{self.pr.data['name']} (CFD)", "external_aero", str(loc))
        tw.data["hidden"] = True
        tw.save(quiet=True)
        self.pr.data.setdefault("twins", {})["cfd"] = tw.id
        self.pr.save()
        return tw

    def prepare_twin(self, job=None):
        """Geometry of the CFD twin: the same STEP, the root on a symmetry plane, every other wing face a moving wall."""
        tw = self.twin()
        geo = self.pr.data["geometry"]
        src = self.pr.geo_dir / "source.step"
        sig = [geo.get("scale", 1.0), geo.get("source"), src.stat().st_mtime_ns if src.exists() else 0, self._root_face_id(),
               self.s.get("span_axis")]
        if tw.data.get("twin_sig") == sig and tw.data["geometry"].get("status") == "ready":
            return tw
        root_id = self._root_face_id()
        if root_id is None:
            raise ValueError("Mark the root face (Fixed support) first")
        rf = self.pr.face_map()[root_id]
        j = tw.import_step(src, scale=float(geo.get("scale", 1.0)))
        if not j.wait(1800) or j.status != "done":
            raise RuntimeError(f"The CFD geometry could not be prepared: {j.error}")
        L = float((tw.data["geometry"].get("info") or {}).get("ref_length") or 1.0)
        body = [f for f in tw.faces() if f.get("kind") not in ("farfield", "enclosure")]
        c0 = np.asarray(rf["centroid"], float)
        cand = [f for f in body if "Plane" in str(f.get("surface_type"))]
        match = min(cand, key=lambda f: float(np.linalg.norm(np.asarray(f["centroid"], float) - c0)), default=None)
        if match is None or float(np.linalg.norm(np.asarray(match["centroid"], float) - c0)) > 1e-3 * max(L, 1e-9):
            raise ValueError("The root face of the structure was not found in the CFD geometry (rotated or edited part?)")
        j = tw.add_symmetry(faces=[match["id"]])
        if not j.wait(1800) or j.status != "done":
            raise RuntimeError(f"The symmetry plane at the root could not be made: {j.error}")
        wing = [f["id"] for f in tw.faces() if f.get("kind") not in ("farfield", "enclosure")]  # (the root cap is part of the plane now)
        for b in list(tw.data["bcs"]):
            if b["type"] == "moving_wall":
                tw.delete_bc(b["id"])
        tw.add_bc("Wing", "moving_wall", wing, {"motion": "translate", "axis": "+z", "amplitude": 0.0, "frequency": 10.0})
        tw.data["twin_sig"] = sig
        tw.save(quiet=True)
        return tw

    def mesh_twin(self, job, p0=0.5, p1=1.0):
        tw = self.prepare_twin(job)
        tw.set_mesh(preset=str(self.s.get("cfd_mesh") or "coarse"))
        tw._mesh_work(job, p0, p1)
        return tw

    # ------------------------------------------------------------------------------------------ the structure
    def structure(self, log=print) -> dict:
        """Modes, the strip-theory prediction and the lumped-node model of the wing."""
        from .solvers import flutter_study
        from .solvers.openfoam import flutter_cfd as FC

        pr = self.pr
        m = pr.load_mesh()
        props = pr._structural_props()
        res = flutter_study.solve_study(m, props, pr.data["bcs"], self.strip_settings(), log=log)
        keep = [i for i, mm in enumerate(res["modes"]) if not mm["rigid_body"]]
        modes = [res["modes"][i] for i in keep]
        shapes = np.array([res["shapes"][i] for i in keep])
        omega = 2 * math.pi * np.array([mm["frequency_Hz"] for mm in modes])
        mg = np.array([mm["generalized_mass_kg"] for mm in modes])
        tris, ts = m["tris"].astype(int), m["tri_surf"]
        aero_faces = sorted({int(f) for b in pr.data["bcs"] if b["type"] == "aero_surface" for f in b["faces"]})
        sel = np.isin(ts, aero_faces) if aero_faces else np.ones(len(tris), bool)
        aero_nodes = np.unique(tris[sel])
        zeta = float(self.s.get("damping_percent") or 0.0) / 100.0
        node_model = FC.build_node_model(m["nodes"], aero_nodes, shapes, omega, mg, self.flow_vec(),
                                         np.eye(3)[self.span_idx()], n_nodes=int(self.s.get("n_lumped") or 10), zeta=zeta)
        return {"mesh": m, "report": res["report"], "modes": modes, "shapes": shapes, "model": node_model, "omega": omega,
                "mg": mg, "props": props}

    # ------------------------------------------------------------------------------------------ the plan
    def _strip_roots_at(self, report, U):
        """[(frequency_Hz, zeta, mode_index)] of the strip-theory roots at speed U, lowest damping first."""
        out = []
        sw = (report or {}).get("sweep") or {}
        V = np.array(sw.get("speed_m_s") or [])
        for r in sw.get("roots") or []:
            if not len(V):
                break
            f = float(np.interp(U, V, r["frequency_Hz"]))
            z = float(np.interp(U, V, r["damping_percent"])) / 100.0
            idx = int(str(r.get("label", "Mode 1")).split()[-1])
            out.append((f, z, idx))
        return sorted(out, key=lambda x: x[1])

    def critical_root(self, report):
        """(label index of the flutter-critical root, [modes that take part]) from the strip theory: the root whose damping
        falls most steeply over the last 30 % of the speeds up to flutter (or the last speed): the flutter root heads for
        zero there, while modes that barely couple to the air (damping ~0 at every speed) do not change. The participating
        modes are those of the strip flutter motion."""
        sw = (report or {}).get("sweep") or {}
        V = np.array(sw.get("speed_m_s") or [])
        roots = sw.get("roots") or []
        if not len(V) or not roots:
            return 1, [1, 2]
        v_end = float(((report.get("flutter") or {}).get("speed_m_s")) or V[-1])
        drops = []
        for r in roots:
            z = np.array(r["damping_percent"], float)
            drops.append(float(np.interp(0.7 * v_end, V, z) - np.interp(v_end, V, z)))
        k = int(np.argmax(drops))
        if not (report.get("flutter") or {}).get("speed_m_s"):  # no flutter in the sweep: the lowest-damped of the low modes
            low = [i for i, r in enumerate(roots) if int(str(r.get("label", "Mode 1")).split()[-1]) <= 4] or list(range(len(roots)))
            k = min(low, key=lambda i: float(np.interp(v_end, V, np.array(roots[i]["damping_percent"], float))))
        idx = int(str(roots[k].get("label", "Mode 1")).split()[-1])
        part = [int(c["mode"]) for c in ((report.get("flutter") or {}).get("mode_content") or [])][:2]
        modes = sorted(set(part) | {idx}) if part else sorted({idx, 1 if idx != 1 else 2})
        return idx, modes

    def plan(self, st=None, log=print) -> dict:
        """The speeds to run, the run lengths, the excitation and a cost estimate."""
        from .solvers import flutter as F
        from .solvers.openfoam import flutter_damping as FD

        st = st or self.structure(log)
        rep = st["report"]
        atm = F.isa(float(self.s.get("altitude_m") or 0.0))
        v_cfd_max = MACH_LIMIT_INCOMPRESSIBLE * atm["a"]
        lo, hi = float(self.s.get("speed_min_ms") or 0.0), float(self.s.get("speed_max_ms") or 0.0)
        n = int(self.s.get("n_cfd_speeds") or 3)
        warnings = []
        if hi > 0:
            lo = lo if lo > 0 else 0.5 * hi
            speeds = [float(x) for x in np.linspace(lo, hi, max(n, 1))] if n > 1 else [hi]
        else:
            speeds = plan.plan_speeds(rep, n=n, v_max=v_cfd_max)
        speeds = sorted({round(float(u), 3) for u in speeds if u > 0})
        if not speeds:
            raise ValueError("No air speeds to run: set 'Highest air speed' (the strip theory found no instability to plan around)")
        too_fast = [u for u in speeds if u > v_cfd_max]
        if too_fast:
            warnings.append(f"Speeds {', '.join(f'{u:g}' for u in too_fast)} m/s are above Mach {MACH_LIMIT_INCOMPRESSIBLE} "
                            f"({v_cfd_max:.0f} m/s): the incompressible solver ignores compressibility there.")
        mc = float(rep["strips"]["mean_chord_m"])
        span = float(rep["strips"]["span_m"])
        max_co = float(self.s.get("max_co") or 6.0)
        n_per = float(self.s.get("n_periods") or 8)
        cores = self.cores()
        par = int(self.s.get("parallel_runs") or 1)
        per_speed = []
        crit_idx, crit_modes = self.critical_root(rep)
        for U in speeds:
            roots = self._strip_roots_at(rep, U)
            mine = [x for x in roots if x[2] == crit_idx]
            f_crit = mine[0][0] if mine else float(st["omega"][min(crit_idx, len(st["omega"])) - 1] / (2 * math.pi))
            f_int = [f_crit]  # the run is as long as periods of THIS mode need (the others are the partners of the motion)
            t_settle = max(float(self.s.get("settle_chords") or 20) * mc / U, 2.0 * span / U)
            t_run = FD.required_duration(f_int, n_periods=n_per, t_settle=0.0, f_fast_hz=max(f_int))
            dt = STEP_DT_AT_40MS * (40.0 / U) * (max_co / 6.0)
            modes_excited = [m for m in crit_modes if 1 <= m <= len(st["omega"])] or [1]
            v0 = FD.excitation(st["model"], [i - 1 for i in modes_excited], float(self.s.get("excite_tip_mm") or 1.0) * 1e-3)
            cost = plan.cost_estimate(dt, min(f_int), n_per, par_runs=par, t_settle=t_settle)
            per_speed.append({"U": U, "mach": U / atm["a"], "t_settle": t_settle, "t_run": float(t_run), "dt_estimate": dt,
                              "f_interest_Hz": f_int, "modes_excited": modes_excited, "perturbation": [float(x) for x in v0],
                              "cost": cost})
        total_h = sum(p["cost"]["wall_h"] for p in per_speed)  # (each speed's figure is already its share of the parallel batch)
        return {"speeds": per_speed, "warnings": warnings, "atmosphere": atm, "cores": cores, "parallel_runs": par,
                "estimated_wall_h": total_h, "mean_chord_m": mc, "span_m": span,
                "strip_flutter_speed_m_s": ((rep.get("flutter") or {}).get("speed_m_s"))}

    def cores(self) -> int:
        from .solvers.openfoam import runner

        n = int(self.s.get("n_procs") or 0)
        return n if n > 0 else max(1, int(runner.physical_cores()))

    # ------------------------------------------------------------------------------------------ the run
    def settings_hash(self, st) -> str:
        keep = {k: self.s.get(k) for k in sorted(self.s) if k not in ("time_limit_h", "speed_time_limit_h", "stall_timeout_s",
                                                                      "max_retries")}
        info = (self.pr.data["geometry"].get("info") or {})
        blob = json.dumps({"s": keep, "bcs": [[b["type"], sorted(b["faces"])] for b in self.pr.data["bcs"]],
                           "bodies": self.pr.data["bodies"], "bbox": info.get("parts_bbox"),
                           "cells": (self.pr.data["mesh"].get("stats") or {}).get("cells"),
                           "modes": [round(float(x), 4) for x in st["omega"]]}, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:10]

    def run_dir(self, h: str) -> Path:
        return self.pr.dir / "flutter_cfd" / f"run_{h}"

    def latest_run_dir(self):
        root = self.pr.dir / "flutter_cfd"
        runs = sorted(root.glob("run_*"), key=lambda p: p.stat().st_mtime) if root.exists() else []
        return runs[-1] if runs else None

    def prepare_run(self, st, pl, log=print):
        """Write one OpenFOAM case per speed and the manifest; reuse an unfinished run of the same setup."""
        from .solvers.openfoam import cases, runner
        from .solvers.openfoam import flutter_run as FR

        h = self.settings_hash(st)
        rdir = self.run_dir(h)
        rd = FR.RunDir(rdir)
        m = rd.read_manifest() if rdir.exists() else None
        if m and m.get("settings_hash") == h and any(sp["state"] not in FINISHED for sp in m["speeds"]):
            log("Continuing the unfinished run of this setup")
            return rd, True
        if rdir.exists():  # the same setup, already finished: a new folder for a fresh run
            rdir = rdir.with_name(rdir.name + time.strftime("_%H%M%S"))
        tw = self.twin(create=False)
        if tw is None or tw.data["mesh"].get("status") != "ready":
            raise ValueError("Mesh the study first (the structure and the CFD mesh)")
        atm = pl["atmosphere"]
        cm = tw.load_mesh()
        info = tw.data["geometry"]["info"]
        speeds, cases_map = [], {}
        steps = None
        for p in pl["speeds"]:
            U = p["U"]
            cdir = rdir / f"speed_{U:g}" / "case"
            data = json.loads(json.dumps(tw.data))
            data["settings"].update(speed=U, forward_axis=_axis_name(self.flow_vec()), up_axis=self.up_axis(),
                                    analysis="transient", end_time=p["t_settle"] + 0.004 + p["t_run"],
                                    turbulence=str(self.s.get("turbulence") or "kOmegaSST"), n_procs=1)
            log(f"Writing the OpenFOAM case for {U:g} m/s")
            meta = cases.write_case(cdir, data, cm, tw.faces(), info, tw.bodies())
            steps = steps or [x for x in meta["steps"] if x.get("app") in ("checkMesh", "renumberMesh")]
            cases_map[U] = cdir
            speeds.append({"U": U, "settings": {"t_settle": p["t_settle"], "t_run": p["t_run"], "perturbation": p["perturbation"]}})
        cores = pl["cores"]
        par = max(int(pl["parallel_runs"]), 1)
        settings = {"patch": "wing", "rho": atm["rho"], "n_procs": max(cores // par, 1), "cores": cores, "parallel_runs": par,
                    "t_ref": 0.004, "max_co": float(self.s.get("max_co") or 6.0), "n_outer": int(self.s.get("n_outer") or 2),
                    "stall_timeout_s": float(self.s.get("stall_timeout_s") or 120.0),
                    "max_retries": int(self.s.get("max_retries") or 2), "pre_steps": steps or [],
                    "speed_time_limit_s": float(self.s.get("speed_time_limit_h") or 0) * 3600 or None,
                    "total_time_limit_s": float(self.s.get("time_limit_h") or 0) * 3600 or None}
        rd = FR.RunDir.create(rdir, st["model"], speeds, settings, settings_hash=h, cases=cases_map)
        return rd, False

    # ------------------------------------------------------------------------------------------ results
    def collect(self, rd, st, pl, seconds=None, warnings=None, status=None) -> dict:
        """The summary (frozen schema) of the run so far: every finished speed analysed, the rest listed as pending."""
        from .solvers.openfoam import flutter_case as FCASE

        man = rd.read_manifest() or {"speeds": []}
        atm = pl["atmosphere"]
        by_u = {p["U"]: p for p in pl["speeds"]}
        speeds = []
        for e in man["speeds"]:
            res = e.get("result") or {}
            hist = Path(rd.path) / e.get("dir", f"speed_{e['U']:g}") / "history.jsonl"
            t, q = FCASE.read_series(hist) if hist.exists() else (np.zeros(0), np.zeros((0, 0)))
            labels = [f"Mode {i + 1}" for i in range(q.shape[1])] if len(t) else []
            state = {"analysed": "complete"}.get(e.get("state"), e.get("state"))
            speeds.append({"U": e["U"], "mach": e["U"] / atm["a"], "state": state,
                           "t_release": (by_u.get(e["U"]) or {}).get("t_settle"),
                           "duration_s": float(t[-1] - t[0]) if len(t) > 1 else None,
                           "n_exchanges": res.get("exchanges"), "wall_s": res.get("wall_s"),
                           "poles": res.get("poles") or [], "t": t, "q": q, "labels": labels, "lco": bool(res.get("lco"))})
        modes = [{"index": i + 1, "frequency_Hz": float(f) / (2 * math.pi), "generalized_mass_kg": float(m)}
                 for i, (f, m) in enumerate(zip(st["omega"], st["mg"]))]
        warns = list(pl.get("warnings") or []) + list(warnings or [])
        assumptions = [
            "Incompressible CFD (OpenFOAM pimpleFoam, transient RANS) coupled to the linear modal structure; the air speeds "
            f"are limited to Mach {MACH_LIMIT_INCOMPRESSIBLE} (compressibility, shocks are a later step)",
            "Half model: the root face lies on a symmetry plane (the other half moves like this one)",
            "The load of the held wing is the reference: the motion is the perturbation around the loaded equilibrium",
            "The wing surface moves with a stick model of lumped nodes along the span (translation and rotation of each "
            "node from the modes); the mesh follows by Laplacian smoothing; small motion only",
            "Explicit (staggered) coupling, exchanged every time step: sound for a light fluid like air",
            "Damping is read from the decay of the released motion with its uncertainty: a speed whose estimate is "
            "marked unreliable needs a longer run"]
        return plan.build_summary(st["report"], modes, speeds, dict(self.s), (self.twin(False).data["mesh"].get("stats") or {}).get("cells"),
                                  warns, assumptions, seconds, status=status)

    def status(self) -> dict:
        """What the project can say right now without solving: the run (if any) and its partial results."""
        from .solvers.openfoam import flutter_run as FR

        rdir = self.latest_run_dir()
        if rdir is None:
            return {"run": None}
        rd = FR.RunDir(rdir)
        man = rd.read_manifest() or {}
        return {"run": {"dir": str(rdir), "status": rd.attach(), "speeds": [{"U": s["U"], "state": s["state"]} for s in man.get("speeds", [])],
                        "settings_hash": man.get("settings_hash")}}

    # ------------------------------------------------------------------------------------------ the job
    def solve(self, job, JOBS):
        """The solve job of the project: prepare, start the detached controller, follow it, collect the results."""
        from .solvers.openfoam import flutter_run as FR
        from .solvers.openfoam import runner

        pr = self.pr
        t0 = time.time()
        det = runner.detect()
        if not det.get("available"):
            raise RuntimeError("OpenFOAM is not available (the built-in copy is set up on the first start)")
        log = lambda msg: JOBS.update(job, None, msg, log=msg)  # noqa: E731
        JOBS.update(job, 0.02, "Modes and the strip-theory prediction")
        for note in self.tag_if_needed():
            log(note)
        st = self.structure(log)
        pl = self.plan(st, log)
        for w in pl["warnings"]:
            log("Warning: " + w)
        log(f"Plan: speeds {', '.join(f'{p['U']:g}' for p in pl['speeds'])} m/s; about {pl['estimated_wall_h']:.1f} h of computing "
            f"on {pl['cores']} cores (estimate)")
        JOBS.update(job, 0.05, "Preparing the OpenFOAM cases")
        rd, resumed = self.prepare_run(st, pl, log)
        pr.data["solve"] = {"status": "running", "backend": "openfoam", "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "run_dir": str(rd.path)}
        pr.save()
        att = rd.attach()
        if not att.get("alive"):
            rd.start_detached()
            log("Started the run controller (it keeps running if the workbench is closed)")
        else:
            log("The run controller is already running: following it")
        self._monitor(job, rd, JOBS, st, pl)
        return self._finish(rd, st, pl, time.time() - t0)

    def _monitor(self, job, rd, JOBS, st, pl):
        n = max(len(pl["speeds"]), 1)
        last_state, dead_since, cancelled = None, None, False
        while True:
            time.sleep(3.0)
            s = rd.attach()
            man = rd.read_manifest() or {"speeds": []}
            states = [sp["state"] for sp in man["speeds"]]
            done = sum(1 for x in states if x in FINISHED)
            cur = 0.0
            if s.get("t_end") and s.get("t_sim") is not None:
                cur = min(max(float(s["t_sim"]) / float(s["t_end"]), 0.0), 1.0)
            frac = 0.06 + 0.9 * (done + cur) / n
            msg = (f"Speed {s.get('speed_U')} m/s, {s.get('phase') or ''}: t = {float(s.get('t_sim') or 0):.4g} of "
                   f"{float(s.get('t_end') or 0):.4g} s; {done} of {n} speeds done"
                   + (f"; about {float(s['eta_s']) / 3600:.1f} h left" if s.get("eta_s") else ""))
            JOBS.update(job, min(frac, 0.96), msg)
            job.extra["flutter_cfd"] = {"status": s, "speeds": states}
            if getattr(job, "cancel_requested", False) and not cancelled:
                rd.request_cancel()
                cancelled = True
                JOBS.update(job, None, "Cancelling: the solvers are being stopped", log="Cancel requested")
            if s.get("state") in ("done", "partial", "cancelled", "stalled", "failed") or all(x in FINISHED for x in states):
                return
            if s.get("state") == "running" and not s.get("alive"):  # the controller is gone without finishing
                dead_since = dead_since or time.time()
                if time.time() - dead_since > 90:
                    raise RuntimeError("The run controller stopped unexpectedly (see flutter_cfd/<run>/controller.log). "
                                       "Solve again to continue from the last checkpoint.")
            else:
                dead_since = None

    def _finish(self, rd, st, pl, seconds):
        pr = self.pr
        man = rd.read_manifest() or {"speeds": []}
        states = [sp["state"] for sp in man["speeds"]]
        status = "complete" if states and all(x == "analysed" for x in states) else "partial"
        summary = self.collect(rd, st, pl, seconds, status=status)
        # the 3D view: the modes of the structure (the flutter shape comes with the strip study)
        from .results import ResultSet
        from .project import _owner_of_tris

        m = st["mesh"]
        bodies = pr.bodies()
        reg_index = {b["id"]: i for i, b in enumerate(bodies)}
        tr = np.array([reg_index[int(v)] for v in m["tet_vol"]]) if len(bodies) > 1 else np.zeros(len(m["tets"]), int)
        groups, sgroup = pr._tri_groups(m)
        fields = {f"Mode {i + 1}": ("node", sh.astype(np.float32), "relative") for i, sh in enumerate(st["shapes"])}
        rs = ResultSet(m["nodes"], m["tets"], tr, [pr._body_view(b)["name"] for b in bodies], fields,
                       m["tris"], _owner_of_tris(m), sgroup, groups)
        pr.results_dir.mkdir(exist_ok=True)
        rs.save(pr.results_dir / "result.npz")
        pr._results = rs
        (pr.results_dir / "flutter_cfd.json").write_text(json.dumps(summary))
        ok = status == "complete"
        pr.data["solve"] = {"status": "done" if ok or any(s["state"] == "analysed" for s in summary["speeds"]) else "failed",
                            "backend": "openfoam", "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "summary": summary,
                            "run_dir": str(rd.path)}
        pr._remember_previous()
        pr.save()
        return summary
