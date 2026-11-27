"""Rotors (actuator discs of a drone, external aerodynamics) and moving zones (rotating cylinders of air: propellers, fans, wheels) in the
Setup step, as the web version has them:

* Rotors: one card each (centre, radius, thrust - blank = carries the weight -, delete, centre on a cylinder or circular edge picked in
  the 3D view); Detect on motor tops, Reset thrusts to automatic, and (new) Add rotor.
* Moving zones: one card each (name, method MRF / sliding mesh, speed, axis, centre, radius, length, cell size inside, fit around a part,
  centre on a cylinder or circular edge); Add rotating zone. A picked axis that is not along x, y or z offers to turn the model.

The engine keeps them (simwb.project set_rotors / detect_rotors / reset_thrusts / set_moving_zones / new_moving_zone / zone_around_part /
axis_info / rotate_geometry)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QVBoxLayout, QWidget

from .. import orient, studies
from .. import zones as zone_tools
from . import theme
from .forms import ParamForm, fmt_num
from .ids import wid
from .panels.common import FlowLayout, _h, _muted
from .zonehandles import ZoneHandles

MZ_HELP = {
    "mrf": "MRF (moving reference frame): use with Analysis = steady. The mesh does NOT rotate: the air inside the zone is solved in a frame "
           "turning with the part, which gives its torque and the flow it drives for one blade position. Fast - the usual choice for fans "
           "and propellers. To see the part rotate, use Sliding mesh.",
    "sliding": "Sliding mesh: transient. The cells inside the zone really rotate; the zone boundary slides along the outer mesh (two AMI "
               "patches). Shows blade passing and unsteady wakes; needs Analysis = transient and much more time.",
}
PICK_TIP = ("Then click a cylindrical face (a shaft, a hub bore, a motor can) or near a circular edge in the 3D view: the centre and axis "
            "are put exactly on it")


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("item")
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


def _swatch(colour: str, tip: str) -> QLabel:
    sw = QLabel("")
    sw.setFixedSize(14, 14)
    sw.setStyleSheet(f"background: {colour}; border: 1px solid {theme.tokens()['line_strong']}; border-radius: 3px;")
    sw.setToolTip(tip)
    return sw


def _btn(text, id, tip, kind="ghostBorder") -> QPushButton:
    b = wid(QPushButton(text), id)
    b.setObjectName(kind)
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def _mm(v) -> float:
    return round(float(v) * 1000, 3)


class Rotors:
    """The Rotors and Moving zones blocks of the Setup panel. The panel's `axis_pick` = ('rotor' | 'zone', index) while a centre of
    rotation is being picked; `oblique`: a picked axis that is not along x, y or z (the offer to turn the model)."""

    def __init__(self, panel):
        self.panel = panel
        self.oblique: dict | None = None
        self.turn: dict = {}                       # {zone index: degrees}: how far the preview has turned what is inside the zone
        self.playing: int | None = None            # the zone whose turn preview is running
        self._timer = QTimer(panel)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)
        self._where: dict = {}                     # classification of the parts of each zone, kept while nothing changes
        self.sliders: dict = {}
        self.handle_zone: int | None = 0           # the zone whose handles are drawn in the 3D view (its "Show handles" box is ticked)
        self.forms: dict = {}                      # zone index -> the ParamForm of its card (the numbers follow a handle that is being dragged)
        self.handles = ZoneHandles(self)           # move / resize / turn the axis of a zone by dragging (zonehandles.py)

    @property
    def pr(self):
        return self.panel.engine.project()

    def _do(self, fn, msg=""):
        try:
            out = fn()
        except Exception as e:  # noqa: BLE001
            self.panel._fail(e)
            return None
        if msg:
            self.panel.say(msg)
        self.panel.redo()
        return out

    # ------------------------------------------------------------------ rotors
    def rotors(self) -> list:
        return [dict(r) for r in self.panel.engine.state().get("rotors") or []]

    def set_rotor(self, i: int, **kw):
        rot = self.rotors()
        if not 0 <= i < len(rot):
            raise ValueError(f"No rotor {i + 1} (there are {len(rot)})")
        rot[i].update(kw)
        return self._do(lambda: self.pr.set_rotors(rot))

    def delete_rotor(self, i: int):
        rot = self.rotors()
        rot.pop(i)
        return self._do(lambda: self.pr.set_rotors(rot), f"Rotor {i + 1} deleted")

    def add_rotor(self):
        st = self.panel.engine.state()
        info = st["geometry"].get("info") or {}
        pb = info.get("parts_bbox") or info.get("bbox")
        if not pb:
            return self.panel._fail(ValueError("Import a geometry first"))
        up = studies.AXES[st["settings"].get("up_axis") or "+z"]
        a = [abs(x) for x in up].index(1.0)
        c = [(pb[k] + pb[k + 3]) / 2 for k in range(3)]
        c[a] = pb[a + 3] if up[a] > 0 else pb[a]
        size = max(pb[k + 3] - pb[k] for k in range(3))
        rot = self.rotors() + [{"center": c, "axis": list(up), "radius": 0.15 * size, "thrust_N": None}]
        return self._do(lambda: self.pr.set_rotors(rot), "Rotor added above the parts: centre it on a motor (⌖) and set its radius")

    def detect(self):
        return self._do(lambda: self.pr.detect_rotors(apply=True), "Rotors placed on the motor tops")

    def auto_thrust(self):
        return self._do(lambda: self.pr.reset_thrusts(), "Rotor thrusts are automatic again: together they carry the weight")

    # ------------------------------------------------------------------ zones
    def zones(self) -> list:
        return [dict(z, center=list(z["center"])) for z in self.panel.engine.state().get("moving_zones") or []]

    def _save_zones(self, zs, msg=""):
        return self._do(lambda: self.pr.set_moving_zones(zs), msg)

    def add_zone(self):
        try:
            z = self.pr.new_moving_zone()
        except Exception as e:  # noqa: BLE001
            return self.panel._fail(e)
        self.handle_zone = len(self.zones())                        # (the new zone shows its handles: drag it where it belongs)
        return self._save_zones(self.zones() + [z], "Zone added: drag it into place, set its size and speed, or fit it around the rotating part")

    def set_zone(self, i: int, **kw):
        zs = self.zones()
        if not 0 <= i < len(zs):
            raise ValueError(f"No moving zone {i + 1} (there are {len(zs)})")
        z = zs[i]
        for k, v in kw.items():
            if k in ("cx", "cy", "cz"):
                z["center"]["xyz".index(k[1])] = float(v) / 1000
            elif k in ("radius", "length"):
                z[k] = float(v) / 1000
            elif k == "cell_size":
                z[k] = None if v in (None, "") or not float(v) > 0 else float(v) / 1000
            elif k == "rpm":
                z[k] = float(v)
            else:
                z[k] = v
        return self._save_zones(zs)

    def set_handle_zone(self, i: int, on: bool):
        if on:
            self.handle_zone = i
        elif self.handle_zone == i:
            self.handle_zone = None
        self.panel.redo()

    def fit_zone(self, i: int, part: str):
        zs = self.zones()
        try:
            zs[i] = self.pr.zone_around_part(zs[i], part)
        except Exception as e:  # noqa: BLE001
            return self.panel._fail(e)
        return self._save_zones(zs, f"{zs[i]['name']} fitted round {part}")

    def delete_zone(self, i: int):
        zs = self.zones()
        z = zs.pop(i)
        if self.handle_zone is not None and self.handle_zone >= i:
            self.handle_zone = (self.handle_zone - 1 if self.handle_zone > i else i) if zs else None
            if self.handle_zone is not None and self.handle_zone >= len(zs):
                self.handle_zone = len(zs) - 1
        return self._save_zones(zs, f"Moving zone {z['name']} deleted")

    # ------------------------------------------------------------------ the zones in the 3D view, and the turn preview
    def where(self, i: int, z: dict) -> list:
        """How each part lies to zone i: inside, partly or outside (zones.classify_parts), kept until the zone or the geometry changes."""
        key = (repr({k: z.get(k) for k in ("center", "axis", "radius", "length")}), self.panel.engine.state()["geometry"].get("updated"))
        hit = self._where.get(i)
        if hit is None or hit[0] != key:
            hit = (key, zone_tools.classify_parts(z, self.pr.faces()))
            self._where[i] = hit
        return hit[1]

    def draw_zones(self, active=None, override: dict | None = None):
        """The cylinders of the zones in the 3D view, and for a zone the preview has turned a ghost of what is inside it, turned. `override`: {index: zone}
        drawn in place of the stored one (a handle is being dragged: where the zone would be)."""
        v = self.panel.view
        if v is None:
            return
        zs = list(self.panel.engine.state().get("moving_zones") or [])
        for k, z in (override or {}).items():
            if 0 <= k < len(zs):
                zs[k] = z
        ghosts = {}
        for i, z in enumerate(zs):
            if self.turn.get(i) and not (override and i in override):
                ids = [fid for b in self.where(i, z) if b["where"] == zone_tools.INSIDE for fid in b["faces"]]
                if ids:
                    ghosts[i] = zone_tools.triangles_of(self.pr.faces(), ids)
        v.zone_view.set(zs, active if active is not None else (self.handle_zone if self.handles.showing else None), self.turn, ghosts)
        if override is None:
            self.handles.sync()

    def show_numbers(self, i: int, z: dict):
        """The numbers of the zone's card, as the zone would be (a handle is being dragged): nothing is stored."""
        f = self.forms.get(i)
        if f is None:
            return
        for k, v in (("cx", z["center"][0] * 1000), ("cy", z["center"][1] * 1000), ("cz", z["center"][2] * 1000), ("radius", z["radius"] * 1000),
                     ("length", z["length"] * 1000), ("axis", z["axis"])):
            f.show_value(k, round(v, 3) if not isinstance(v, str) else v)

    def set_turn(self, i: int, deg: float):
        """Preview the zone turned by deg degrees: the parts inside it turn about its axis in the 3D view (nothing is stored)."""
        zs = self.panel.engine.state().get("moving_zones") or []
        if not 0 <= i < len(zs):
            raise ValueError(f"No moving zone {i + 1} (there are {len(zs)})")
        self.turn[i] = float(deg) % 360.0
        self.draw_zones(i)
        s = self.sliders.get(i)
        try:
            if s is not None and not s[0].isSliderDown() and s[0].value() != int(round(self.turn[i])):
                s[0].blockSignals(True)
                s[0].setValue(int(round(self.turn[i])))
                s[0].blockSignals(False)
            if s is not None:
                s[1].setText(self.turn_text(zs[i], self.turn[i]))
        except RuntimeError:
            self.sliders.pop(i, None)

    def turn_text(self, z: dict, deg: float) -> str:
        fd = zone_tools.frame_degrees(self.panel.engine.state().get("settings"))
        t = f"{fmt_num(round(deg, 1))}°"
        if fd > 0:
            t += f" = {fmt_num(round(deg / fd, 1))} saved frames of {fmt_num(fd)}°"
        if z.get("rpm"):
            t += f" = {fmt_num(round(deg / zone_tools.degrees_per_second(z) * 1000, 2))} ms at {fmt_num(z['rpm'])} rpm"
        return t

    def play(self, i: int, on: bool = True):
        """Turn the preview round and round (a few degrees at a time) or stop it."""
        self.playing = i if on else None
        if on:
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self):
        zs = self.panel.engine.state().get("moving_zones") or []
        i = self.playing
        if i is None or not 0 <= i < len(zs):
            self._timer.stop()
            return
        fd = zone_tools.frame_degrees(self.panel.engine.state().get("settings"))
        step = fd if fd > 0 else 6.0
        self.set_turn(i, self.turn.get(i, 0.0) + (step if (zs[i].get("rpm") or 1) >= 0 else -step))

    def stop_preview(self):
        """Back to the model as it is (leaving the step): the turn is zero, the play stops, the cylinders go."""
        self._timer.stop()
        self.playing = None
        self.turn = {}
        self.sliders = {}
        self.handles.hide()
        v = self.panel.view
        if v is not None:
            v.zone_view.clear()

    # ------------------------------------------------------------------ a centre of rotation picked on the model
    def start_pick(self, kind: str, i: int, on: bool = True):
        p = self.panel
        p.axis_pick = (kind, i) if on else None
        if on:
            p.active = None
            p.sym.picking = False
            self.oblique = None
            p.view.last_hit = None
            p.say("Click a cylindrical face or near a circular edge in the 3D view: the centre of rotation is put there.")
        p.redo()

    def picked(self, fid: int, point=None):
        p = self.panel
        kind, i = p.axis_pick
        p.axis_pick = None
        try:
            info = self.pr.axis_info(int(fid), point)
        except Exception as e:  # noqa: BLE001
            p.say(f"No round feature at face {fid}: {e}. Press ⌖ again and click a cylinder or near a circular edge.")
            p.redo()
            return None
        c = [float(x) for x in info["center"]]
        where = f"({', '.join(fmt_num(_mm(x)) for x in c)}) mm"
        if kind == "zone":
            if info["angle_deg"] > 0.5:
                self.oblique = dict(info, index=i)
                p.redo()
                return info
            zs = self.zones()
            z = zs[i]
            letter = info["nearest"][1]
            z["axis"] = z["axis"] if z["axis"][1] == letter else "+" + letter    # (keeps the sense of rotation on the same line)
            z["center"] = c
            self._save_zones(zs, f"{z['name']}: centre {where}, axis {z['axis']} - from the {info['kind']} (radius {fmt_num(_mm(info['radius']))} mm)")
        else:
            rot = self.rotors()
            r = rot[i]
            cur = r.get("axis") or [0, 0, 1]
            flip = sum(cur[k] * info["axis"][k] for k in range(3)) < 0
            r["center"] = c
            r["axis"] = [-x for x in info["axis"]] if flip else list(info["axis"])
            self._do(lambda: self.pr.set_rotors(rot), f"Rotor {i + 1}: centre {where}")
        return info

    def turn_model(self):
        o = self.oblique
        self.oblique = None
        if not o:
            return None
        D = orient.align_matrix(o["axis"], studies.AXES[o["nearest"]])
        return self._do(lambda: self.pr.rotate_geometry(matrix=D.tolist(), snap_zones=True),
                        "Turning the model… then pick the cylinder or edge again: the zone will follow it")

    def keep_oblique(self):
        self.oblique = None
        self.panel.redo()

    # ------------------------------------------------------------------ the blocks
    def build_rotors(self, st, busy) -> list:
        out = [_h("Rotors"), _muted("Actuator discs where the propellers turn. Blank thrust = the rotors together carry the weight.")]
        why = "A job is running: wait until it has finished" if busy else ""
        ap = self.panel.axis_pick
        for i, r in enumerate(st.get("rotors") or []):
            card = _card()
            v = QVBoxLayout(card)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(6)
            head = QHBoxLayout()
            head.addWidget(_swatch("#ff8a00", "A rotor disc"))
            t = QLabel(f"Rotor {i + 1}")
            t.setStyleSheet("font-weight: 600;")
            head.addWidget(t)
            ctr = wid(QLabel(f"({', '.join(fmt_num(round(_mm(x))) for x in r['center'])}) mm"), f"setup.rotor.{i}.center")
            ctr.setObjectName("muted")
            ctr.setToolTip("The centre of the disc (x, y, z)")
            head.addWidget(ctr, 1)
            d = _btn("✕", f"setup.rotor.{i}.delete", why or "Delete this rotor", "ghost")
            d.setAccessibleName(f"Delete rotor {i + 1}")
            d.setEnabled(not busy)
            d.clicked.connect(lambda _=False, i=i: self.delete_rotor(i))
            head.addWidget(d)
            v.addLayout(head)
            f = ParamForm([{"key": "radius", "label": "Radius", "unit": "mm", "min": 0, "help": "The radius of the propeller disc"},
                           {"key": "thrust_N", "label": "Thrust", "unit": "N", "placeholder": "auto", "blank": True,
                            "help": "The thrust of this rotor. Empty = automatic: the rotors together carry the weight at the set pitch"}],
                          {"radius": _mm(r["radius"]), "thrust_N": r.get("thrust_N")}, f"setup.rotor.{i}")
            f.setEnabled(not busy)
            f.changed.connect(lambda k, val, i=i: self.set_rotor(i, **{k: val / 1000 if k == "radius" else val}))
            v.addWidget(f)
            pk = _btn("⌖ Centre on a round edge…", f"setup.rotor.{i}.pick_axis", why or PICK_TIP, "toggle")
            pk.setCheckable(True)
            pk.setChecked(ap == ("rotor", i))
            pk.setEnabled(not busy)
            pk.toggled.connect(lambda on, i=i: self.start_pick("rotor", i, on))
            v.addWidget(pk, 0, Qt.AlignLeft)
            out.append(card)
        row = QWidget()
        hl = FlowLayout(row)
        det = _btn("✨ Detect on motor tops", "setup.rotor.detect", why or "Find the round flat tops of the motors and put a disc above each")
        det.setEnabled(not busy)
        det.clicked.connect(self.detect)
        hl.addWidget(det)
        add = _btn("＋ Add rotor", "setup.rotor.add", why or "Add a disc above the parts, then centre it on a motor (⌖) and set its radius")
        add.setEnabled(not busy)
        add.clicked.connect(self.add_rotor)
        hl.addWidget(add)
        any_set = any(r.get("thrust_N") is not None for r in st.get("rotors") or [])
        at = _btn("Reset thrusts to automatic", "setup.rotor.auto_thrust",
                  why or ("Thrusts carry the weight at the set pitch" if any_set else "Every thrust is automatic already (an empty Thrust field)"))
        at.setEnabled(any_set and not busy)
        at.clicked.connect(self.auto_thrust)
        hl.addWidget(at)
        out.append(row)
        return out

    def weight_note(self, st):
        s, rot = st["settings"], st.get("rotors") or []
        if not rot:
            return None
        import math
        W = float(s.get("mass_kg") or 0) * 9.81
        cth = max(math.cos(math.radians(float(s.get("pitch_deg") or 0))), 0.2)
        tot = sum(r["thrust_N"] if r.get("thrust_N") is not None else W / cth / len(rot) for r in rot)
        lab = wid(QLabel(f"Weight {fmt_num(round(W, 3))} N · rotor thrust {fmt_num(round(tot, 3))} N"), "setup.rotor.weight")
        lab.setWordWrap(True)
        lab.setObjectName("muted")
        lab.setToolTip("The weight of the vehicle (mass × 9.81) and the thrust of all rotors together")
        return lab

    def _parts_note(self, i: int, z: dict) -> QLabel:
        """Which parts are inside the zone (they turn with it), which are outside, and a warning for a part the zone's boundary cuts through."""
        try:
            rows = self.where(i, z)
        except Exception:  # noqa: BLE001
            rows = []
        ins = [b["name"] for b in rows if b["where"] == zone_tools.INSIDE]
        part = [b for b in rows if b["where"] == zone_tools.PARTLY]
        out = [b["name"] for b in rows if b["where"] == zone_tools.OUTSIDE]
        text = "Inside (turns with the zone): " + (", ".join(ins) if ins else "no part yet - fit the zone round the rotating part") + ". "
        if out:
            text += "Outside: " + ", ".join(out) + ". "
        if part:
            text += ("⚠ The zone's boundary cuts through " + ", ".join(f"{b['name']} ({round(b['share'] * 100)} % inside)" for b in part)
                     + ": the mesh cannot match there. Make the zone larger, or smaller, so that every part is wholly inside or outside.")
        lab = wid(QLabel(text), f"setup.zone.{i}.parts")
        lab.setWordWrap(True)
        if part:
            lab.setStyleSheet(f"color: {theme.tokens()['warn_text']};")
        else:
            lab.setObjectName("muted")
        return lab

    def _turn_row(self, i: int, z: dict) -> QHBoxLayout:
        """Preview the turn: a slider over one revolution and a play button; the parts inside the zone turn in the 3D view before any solver time is spent."""
        row = QHBoxLayout()
        row.addWidget(QLabel("Preview the turn"))
        play = wid(QPushButton("▶"), f"setup.zone.{i}.turn_play")
        play.setObjectName("toggle")
        play.setCheckable(True)
        play.setChecked(self.playing == i)
        play.setToolTip("Turn the zone and what is inside it round and round in the 3D view (a preview: nothing is stored)")
        play.toggled.connect(lambda on, i=i: self.play(i, on))
        row.addWidget(play)
        sl = wid(QSlider(Qt.Horizontal), f"setup.zone.{i}.turn")
        sl.setRange(0, 360)
        sl.setValue(int(round(self.turn.get(i, 0.0))))
        sl.setToolTip("Turn the parts inside the zone by this angle, to see how they sit in it and where they would pass the boundary")
        row.addWidget(sl, 1)
        lab = wid(QLabel(self.turn_text(z, self.turn.get(i, 0.0))), f"setup.zone.{i}.turn_text")
        lab.setObjectName("muted")
        row.addWidget(lab)
        sl.valueChanged.connect(lambda val, i=i: self.set_turn(i, float(val)))
        self.sliders[i] = (sl, lab)
        return row

    def build_zones(self, st, busy) -> list:
        aero = st["study"] == "external_aero"
        why = "A job is running: wait until it has finished" if busy else ""
        out = [_h("Moving zones"),
               _muted("A cylinder of air that turns with a propeller, fan, rotor or wheel inside it. Put the whole rotating part inside the zone, "
                      "and keep it clear of the parts that do not rotate. Use ⌖ Centre on a cylinder or circular edge to put its centre and "
                      "axis on a shaft, a hub bore or a round edge.")]
        if self.oblique:
            o = self.oblique
            box = _card()
            v = QVBoxLayout(box)
            v.setContentsMargins(10, 8, 10, 8)
            msg = wid(QLabel(f"⚠ The {o['kind']} you picked has its axis {fmt_num(round(o['angle_deg'], 2))}° away from the {o['nearest'][1]} axis, and "
                             f"a rotating zone has to lie along x, y or z. Turn the whole model so that this axis lies along {o['nearest']}?"),
                      "setup.zone.oblique")
            msg.setWordWrap(True)
            msg.setStyleSheet(f"color: {theme.tokens()['warn_text']};")
            v.addWidget(msg)
            r = FlowLayout()
            t = _btn("Turn the model", "setup.zone.turn", why or "Turn the model so the picked axis lies along the nearest axis (the geometry is rebuilt)", "primary")
            t.setEnabled(not busy)
            t.clicked.connect(self.turn_model)
            r.addWidget(t)
            k = _btn("Leave it", "setup.zone.keep", "Keep the model as it is")
            k.clicked.connect(self.keep_oblique)
            r.addWidget(k)
            v.addLayout(r)
            out.append(box)
        parts = sorted({f.get("part") or "(body)" for f in self.pr.faces() if f.get("kind") not in ("farfield", "enclosure")})
        ap = self.panel.axis_pick
        for i, z in enumerate(st.get("moving_zones") or []):
            card = _card()
            v = QVBoxLayout(card)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(6)
            head = QHBoxLayout()
            head.addWidget(_swatch("#9b6cff", "A rotating zone"))
            nm = wid(QLineEdit(z["name"]), f"setup.zone.{i}.name")
            nm.setToolTip("Rename this zone (letters, digits and _)")
            nm.editingFinished.connect(lambda i=i, w=nm, old=z["name"]: self.set_zone(i, name=w.text().strip()) if w.text().strip() not in ("", old) else None)
            head.addWidget(nm, 1)
            sp = QLabel(f"{fmt_num(z['rpm'])} rpm")
            sp.setObjectName("muted")
            head.addWidget(sp)
            d = _btn("✕", f"setup.zone.{i}.delete", why or "Delete this zone", "ghost")
            d.setAccessibleName(f"Delete {z['name']}")
            d.setEnabled(not busy)
            d.clicked.connect(lambda _=False, i=i: self.delete_zone(i))
            head.addWidget(d)
            v.addLayout(head)
            methods = ["mrf", "sliding"] if aero else ["mrf"]
            params = [
                {"key": "method", "label": "Method", "options": methods, "option_labels": {"mrf": "MRF (steady)", "sliding": "Sliding mesh (transient)"},
                 "option_help": {m: MZ_HELP[m] for m in methods}},
                {"key": "rpm", "label": "Speed (+ = right-hand about the axis)", "unit": "rpm"},
                {"key": "axis", "label": "Axis", "options": list(studies.AXES), "help": "The axis the zone turns about"},
                {"key": "cx", "label": "Centre x", "unit": "mm"}, {"key": "cy", "label": "Centre y", "unit": "mm"},
                {"key": "cz", "label": "Centre z", "unit": "mm"},
                {"key": "radius", "label": "Radius", "unit": "mm", "min": 0}, {"key": "length", "label": "Length along the axis", "unit": "mm", "min": 0},
                {"key": "cell_size", "label": "Cell size inside", "unit": "mm", "placeholder": "auto", "blank": True, "min": 0,
                 "help": "Finer cells round the rotating part (blades, tip vortices). Empty = automatic"},
            ]
            vals = {"method": z.get("method", "mrf"), "rpm": z["rpm"], "axis": z["axis"], "cx": _mm(z["center"][0]), "cy": _mm(z["center"][1]),
                    "cz": _mm(z["center"][2]), "radius": _mm(z["radius"]), "length": _mm(z["length"]),
                    "cell_size": _mm(z["cell_size"]) if z.get("cell_size") else None}
            f = ParamForm(params, vals, f"setup.zone.{i}")
            f.setEnabled(not busy)
            f.changed.connect(lambda k, val, i=i: self.set_zone(i, **{k: val}))
            v.addWidget(f)
            self.forms[i] = f
            hb = wid(QCheckBox("Show handles in the 3D view"), f"setup.zone.{i}.handles")
            hb.setChecked(self.handle_zone == i)
            hb.setToolTip("Drag this zone in the 3D view: the arrows and the ball move it, the square-ended arrows resize it, the rings turn its axis "
                          "(a quarter turn at a time: a zone lies along x, y or z). The numbers above follow the handles and the handles follow the numbers")
            hb.toggled.connect(lambda on, i=i: self.set_handle_zone(i, on))
            v.addWidget(hb)
            row = QHBoxLayout()
            row.addWidget(QLabel("Fit around part"))
            fit = wid(QComboBox(), f"setup.zone.{i}.fit")
            fit.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            fit.setMinimumContentsLength(8)
            fit.addItem("choose…", None)
            for n in parts:
                fit.addItem(n, n)
            fit.setToolTip(why or "Size and place the zone round a part (the propeller): its axis through the part's centre, a small gap all round")
            fit.setEnabled(not busy)
            fit.currentIndexChanged.connect(lambda _=0, i=i, c=fit: self.fit_zone(i, c.currentData()) if c.currentData() else None)
            row.addWidget(fit, 1)
            v.addLayout(row)
            pk = _btn("⌖ Centre on a round edge…", f"setup.zone.{i}.pick_axis", why or PICK_TIP, "toggle")
            pk.setCheckable(True)
            pk.setChecked(ap == ("zone", i))
            pk.setEnabled(not busy)
            pk.toggled.connect(lambda on, i=i: self.start_pick("zone", i, on))
            v.addWidget(pk, 0, Qt.AlignLeft)
            v.addWidget(self._parts_note(i, z))
            v.addLayout(self._turn_row(i, z))
            out.append(card)
        add = _btn("＋ Add rotating zone", "setup.zone.add", why or "A cylinder of air turning about the up axis, in the middle of the parts")
        add.setEnabled(not busy)
        add.clicked.connect(self.add_zone)
        out.append(add)
        if st.get("moving_zones"):
            out.append(_muted("Changing where a zone is or its size needs a new mesh (its boundary is meshed exactly); a new speed or method does not."))
        return out
