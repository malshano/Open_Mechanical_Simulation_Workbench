"""The materials library: built-in liquids, the user's own materials (checked, saved, carried by a study) and the fluid choices of the studies."""
import json

import pytest

from simwb import materials, studies


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    """An empty workspace for the user's library; the built-in tables are put back afterwards."""
    import simwb.project as P
    monkeypatch.setattr(P, "WORKSPACE", tmp_path)
    solids, fluids = dict(materials.SOLIDS), dict(materials.FLUIDS)
    yield tmp_path
    materials.SOLIDS.clear(), materials.SOLIDS.update(solids)
    materials.FLUIDS.clear(), materials.FLUIDS.update(fluids)
    materials._changed()


def _fluid_param(study):
    return next(p for p in studies.STUDIES[study]["settings"] if p["key"] == "fluid")


def test_the_library_has_liquids_and_gases():
    kinds = {k: f["kind"] for k, f in materials.FLUIDS.items()}
    assert kinds["air"] == "gas" and kinds["water"] == "liquid"
    assert {"seawater", "glycol_50", "engine_oil", "transformer_oil"} <= set(materials.fluid_keys(["liquid"]))
    assert all(f["rho"] > 0 and f["mu"] > 0 and f["Pr"] > 0 for f in materials.FLUIDS.values())


def test_both_flow_studies_offer_every_fluid():
    for st in ("external_aero", "cht_enclosure"):
        assert set(_fluid_param(st)["options"]) == set(materials.FLUIDS)


def test_a_custom_fluid_is_saved_chosen_by_the_studies_and_removed(lib):
    key = materials.add_fluid(label="Brine", kind="liquid", rho=1180, mu=1.6e-3, cp=3300, k=0.55)
    assert materials.is_custom("fluid", key) and key in _fluid_param("external_aero")["options"]
    assert _fluid_param("external_aero")["option_labels"][key] == "Brine"
    assert json.loads((lib / "materials.json").read_text())["fluids"][key]["rho"] == 1180
    assert abs(materials.FLUIDS[key]["Pr"] - 1.6e-3 * 3300 / 0.55) < 1e-9          # (worked out when not given)
    materials.remove_custom("fluid", key)
    assert key not in materials.FLUIDS and key not in _fluid_param("external_aero")["options"]


def test_wrong_values_and_names_are_refused_with_the_reason(lib):
    with pytest.raises(ValueError, match="Density"):
        materials.add_solid(label="Bad", k=1, rho=-5, cp=500, E=1e9, nu=0.3)
    with pytest.raises(ValueError, match="name"):
        materials.add_fluid(label="  ", kind="liquid", rho=1000, mu=1e-3, cp=4000, k=0.6)
    with pytest.raises(ValueError, match="already a material"):
        materials.add_fluid(label="water (20 c)", kind="liquid", rho=1000, mu=1e-3, cp=4000, k=0.6)
    with pytest.raises(ValueError, match="number"):
        materials.add_solid(label="X", k="abc", rho=1000, cp=500, E=1e9, nu=0.3)


def test_built_in_materials_cannot_be_changed_or_removed(lib):
    with pytest.raises(ValueError):
        materials.remove_custom("solid", "steel")
    with pytest.raises(ValueError):
        materials.update_custom("fluid", "water", rho=1)


def test_a_study_carries_its_custom_materials_to_another_machine(lib):
    key = materials.add_solid(label="Test alloy", k=120, rho=2800, cp=900, E=71e9, nu=0.33, Sy=500e6)
    data = {"bodies": {"1": {"material": key}}, "settings": {"fluid": "air"}}
    blob = materials.used_by(data)
    assert list(blob["solids"]) == [key] and not blob["fluids"]
    materials.SOLIDS.pop(key)                                   # (this machine does not have it)
    assert materials.adopt(blob) == 1 and materials.get_solid(key)["rho"] == 2800


def test_a_material_in_use_cannot_be_removed(lib, monkeypatch):
    key = materials.add_fluid(label="Used coolant", kind="liquid", rho=1000, mu=1e-3, cp=4000, k=0.6)
    import simwb.project as P
    monkeypatch.setattr(P, "list_projects", lambda include_hidden=False: [{"id": "p1", "name": "My study", "path": str(lib / "p1")}])
    (lib / "p1").mkdir()
    (lib / "p1" / "project.json").write_text(json.dumps({"settings": {"fluid": key}, "bodies": {}}))
    with pytest.raises(ValueError, match="My study"):
        materials.remove_custom("fluid", key)


def test_a_liquid_is_not_a_perfect_gas():
    assert materials.is_gas(materials.FLUIDS["air"]) and not materials.is_gas(materials.FLUIDS["water"])
