"""The engine guide (simwb/guide.py): rows, platform support, installed flags, apply_guides, markdown."""
import sys

import pytest

from simwb import guide


def fake_studies():
    return {
        "heat_conduction": {"label": "Heat", "guide": {
            "problem": "Temperatures", "engines": ["builtin"], "fidelity": "f", "cost": "seconds", "needs": "nothing"}},
        "vibration_modal": {"label": "Vibration", "guide": {
            "problem": "Modes", "engines": [{"id": "builtin", "role": "default"}, "calculix"], "fidelity": "f",
            "cost": "seconds", "needs": "nothing"}},
        "no_guide": {"label": "Nothing to say"},
    }


def by_study(rs):
    return {r["study"]: r for r in rs}


def test_rows_follow_the_studies_with_a_guide():
    rs = guide.rows(fake_studies(), platform="win32", include_planned=False)
    assert [r["study"] for r in rs] == ["heat_conduction", "vibration_modal"]
    r = rs[1]
    assert r["label"] == "Vibration" and r["problem"] == "Modes" and r["exists"] and not r["planned"]
    assert [e["id"] for e in r["engines"]] == ["builtin", "calculix"]
    assert r["engines"][0]["role"] == "default" and r["engines"][1]["role"] is None
    assert set(r) >= {"study", "problem", "fidelity", "cost", "needs", "engines"}
    assert set(r["engines"][0]) == {"id", "name", "installed", "offered", "reason", "role"}


def test_planned_rows_appear_unless_the_study_exists():
    rs = by_study(guide.rows(fake_studies(), platform="win32"))
    for k in ("vibro_acoustics", "nonlinear_thermomechanics", "aeroelastic_flutter_transonic"):
        assert rs[k]["planned"] and not rs[k]["exists"]
    studies = fake_studies()
    studies["vibro_acoustics"] = {"label": "Vibro-acoustics", "guide": dict(guide.GUIDE_ENTRIES["vibro_acoustics"])}
    rs = by_study(guide.rows(studies, platform="win32"))
    assert rs["vibro_acoustics"]["exists"] and not rs["vibro_acoustics"]["planned"]
    assert rs["vibro_acoustics"]["label"] == "Vibro-acoustics"
    assert len([r for r in guide.rows(studies) if r["study"] == "vibro_acoustics"]) == 1


def test_moose_not_offered_on_windows_but_on_linux():
    win = by_study(guide.rows({}, platform="win32"))["nonlinear_thermomechanics"]["engines"][0]
    assert win["id"] == "moose" and win["offered"] is False and win["installed"] is False
    assert "no native Windows build" in win["reason"] and "WSL" in win["reason"]
    for plat in ("linux", "darwin"):
        e = by_study(guide.rows({}, platform=plat))["nonlinear_thermomechanics"]["engines"][0]
        assert e["offered"] is True and e["reason"]  # offered; the reason says it is planned
        assert "Planned" in e["reason"]
    assert by_study(guide.rows({}, platform="win32"))["vibro_acoustics"]["engines"][0]["offered"] is True  # openCFS runs on Windows


def test_platform_names_are_normalised():
    assert guide.platform_family("win32") == "win32" and guide.platform_family("cygwin") == "win32"
    assert guide.platform_family("linux") == "linux" and guide.platform_family("darwin") == "darwin"
    assert guide.platform_family(None) in ("win32", "linux", "darwin")


def test_installed_flag_comes_from_status():
    studies = fake_studies()
    rs = guide.rows(studies, status={"calculix": True}, platform="win32")
    e = {x["id"]: x for x in by_study(rs)["vibration_modal"]["engines"]}
    assert e["calculix"]["installed"] is True and e["calculix"]["reason"] is None
    assert e["builtin"]["installed"] is True  # always, even when status does not say
    rs = guide.rows(studies, status={"calculix": False}, platform="win32")
    e = {x["id"]: x for x in by_study(rs)["vibration_modal"]["engines"]}
    assert e["calculix"]["installed"] is False and "Not installed" in e["calculix"]["reason"]
    rs = guide.rows(studies, status=None, platform="win32")  # unknown
    e = {x["id"]: x for x in by_study(rs)["vibration_modal"]["engines"]}
    assert e["calculix"]["installed"] is None and e["calculix"]["offered"] is True and e["calculix"]["reason"] is None
    e = {x["id"]: x for x in by_study(guide.rows(studies, status={"calculix": None}, platform="win32"))["vibration_modal"]["engines"]}
    assert e["calculix"]["installed"] is None


def test_unknown_engine_is_an_error():
    with pytest.raises(KeyError):
        guide.rows({"x": {"guide": {"problem": "p", "engines": ["nope"]}}})


def test_apply_guides_sets_only_existing_keys_and_copies():
    studies = {"heat_conduction": {"label": "Heat"}, "other": {"label": "Other"}}
    guide.apply_guides(studies)
    assert studies["heat_conduction"]["guide"]["engines"] == ["builtin"]
    assert "guide" not in studies["other"] and "vibro_acoustics" not in studies
    studies["heat_conduction"]["guide"]["engines"].append("x")
    assert guide.GUIDE_ENTRIES["heat_conduction"]["engines"] == ["builtin"]  # a copy, not shared
    assert guide.apply_guides({}) == {}


def test_guide_markdown_names_every_study_and_engine():
    studies = guide.apply_guides({k: {"label": k.replace("_", " ")} for k in guide.GUIDE_ENTRIES})
    rs = guide.rows(studies, platform="win32")
    md = guide.guide_markdown(rs)
    for r in rs:
        assert f"`{r['study']}`" in md
    for k in list(guide.GUIDE_ENTRIES) + ["nonlinear_thermomechanics", "aeroelastic_flutter_transonic"]:
        assert f"`{k}`" in md
    assert "openCFS" in md and "MOOSE" in md and "not available on this platform" in md
    assert md.count("\n| ") == len(rs)  # one line per row after the header and the separator


def test_every_engine_in_the_entries_is_known():
    entries = [*guide.GUIDE_ENTRIES.values(), *[p["guide"] for p in guide.PLANNED.values() if "guide" in p]]
    for g in entries:
        assert g["engines"] and g["problem"] and g["cost"] and g["needs"] and g["fidelity"]
        for e in g["engines"]:
            assert (e["id"] if isinstance(e, dict) else e) in guide.ENGINES
    for k in guide.PLANNED:
        assert k in guide.GUIDE_ENTRIES or "guide" in guide.PLANNED[k]
    for meta in guide.ENGINES.values():
        assert meta["name"] and meta["platforms"]


def test_engine_status_tolerates_everything():
    st = guide.engine_status(["builtin", "moose", "opencfs"])
    assert st["builtin"] is True
    assert set(st) == {"builtin", "moose", "opencfs"} and all(v in (True, False, None) for v in st.values())
    assert st["moose"] is not True or sys.platform != "win32"  # (no native Windows build)


def test_real_studies_all_have_a_guide_once_any_has():
    from simwb.studies import STUDIES

    have = [k for k, s in STUDIES.items() if s.get("guide")]
    if not have:
        pytest.skip("apply_guides is not wired into studies.py yet")
    assert [k for k, s in STUDIES.items() if not s.get("guide")] == []
    guide.rows(STUDIES)  # every engine id is known
