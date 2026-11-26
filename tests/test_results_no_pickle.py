"""Result files are plain data: written without pickles, read without ever running code from the file, and older files (whose text arrays are
pickled) still open - through a reader that refuses anything but the numpy types such an array is made of."""
import io
import os
import pickle
import zipfile
from pathlib import Path

import numpy as np
import pytest

from simwb.results import ResultSet, UnsafeResultFile, read_npz


def small(times=False):
    nodes = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], float)
    tets = np.array([[0, 1, 2, 3], [1, 2, 3, 4]])
    f = {"Temperature": ("node", np.arange(5, dtype=float), "C"), "Von Mises": ("cell", np.array([1.0, 2.0]), "MPa")}
    tris = np.array([[0, 2, 1], [1, 2, 4]])
    kw = {}
    if times:
        kw = dict(times=[0.0, 1.0], frames={"Temperature": [np.zeros(5, np.float32), np.ones(5, np.float32)]})
    return ResultSet(nodes, tets, np.zeros(2, int), ["Plate", "Resistor"], f, tris, np.array([0, 1]), np.array([0, 1]), ["Top", "Side"],
                     {"Von Mises": np.array([1.0, 2.0])}, **kw)


def legacy_save(rs, path):
    """The writer of the previous versions: text arrays as pickled object arrays, meta as repr."""
    d = dict(nodes=rs.nodes, tets=rs.tets, tet_region=rs.tet_region, surf_tris=rs.surf_tris, surf_cell=rs.surf_cell, surf_group=rs.surf_group,
             regions=np.array(rs.regions, dtype=object), groups=np.array(rs.groups, dtype=object))
    meta = {}
    for k, (loc, arr, unit) in rs.fields.items():
        d["f_" + k] = arr
        meta[k] = (loc, unit)
    for k, arr in rs.surf_values.items():
        d["s_" + k] = arr
    if rs.times:
        d["times"] = np.asarray(rs.times, float)
        d["frame_names"] = np.array(list(rs.frames), dtype=object)
        for j, (k, arrs) in enumerate(rs.frames.items()):
            d[f"t{j}"] = np.stack(arrs).astype(np.float32)
    d["meta"] = np.array([repr(meta)], dtype=object)
    np.savez_compressed(path, **d)


def same(a, b):
    assert a.regions == b.regions and a.groups == b.groups and set(a.fields) == set(b.fields)
    assert np.array_equal(a.nodes, b.nodes) and np.array_equal(a.tets, b.tets)
    for k in a.fields:
        assert a.fields[k][0] == b.fields[k][0] and a.fields[k][2] == b.fields[k][2] and np.array_equal(a.fields[k][1], b.fields[k][1])
    assert a.times == b.times and set(a.frames) == set(b.frames)


@pytest.mark.parametrize("times", (False, True))
def test_new_files_hold_no_pickles_and_round_trip(tmp_path, times):
    rs = small(times)
    p = tmp_path / "r.npz"
    rs.save(p)
    z = np.load(p, allow_pickle=False)                       # numpy itself accepts it without pickles
    assert all(z[k].dtype != object for k in z.files)
    same(rs, ResultSet.load(p))


@pytest.mark.parametrize("times", (False, True))
def test_older_files_still_open(tmp_path, times):
    rs = small(times)
    p = tmp_path / "old.npz"
    legacy_save(rs, p)
    with pytest.raises(ValueError):                          # (numpy refuses them without pickles: the reader has to handle them)
        np.load(p, allow_pickle=False)["regions"]
    same(rs, ResultSet.load(p))


class Boom:
    def __reduce__(self):
        return (os.system, (f'echo owned > "{Boom.marker}"',))


def evil_npz(tmp_path, key="regions"):
    """A results file whose text array carries a pickle that would run a command."""
    Boom.marker = tmp_path / "owned.txt"
    good = tmp_path / "good.npz"
    legacy_save(small(), good)
    out = tmp_path / "evil.npz"
    buf = io.BytesIO()
    np.lib.format.write_array_header_1_0(buf, {"descr": "|O", "fortran_order": False, "shape": (1,)})
    pickle.dump(np.array([Boom()], dtype=object), buf, protocol=3)
    with zipfile.ZipFile(good) as zi, zipfile.ZipFile(out, "w") as zo:
        for n in zi.namelist():
            zo.writestr(n, buf.getvalue() if n == key + ".npy" else zi.read(n))
    return out


@pytest.mark.parametrize("key", ("regions", "groups", "meta"))
def test_a_file_that_asks_for_code_is_refused_and_nothing_runs(tmp_path, key):
    p = evil_npz(tmp_path, key)
    with pytest.raises(UnsafeResultFile, match="refused"):
        ResultSet.load(p)
    assert not Boom.marker.exists()                          # the command never ran


def test_a_text_array_holding_something_else_than_text_is_refused(tmp_path):
    good = tmp_path / "g.npz"
    legacy_save(small(), good)
    buf = io.BytesIO()
    np.lib.format.write_array_header_1_0(buf, {"descr": "|O", "fortran_order": False, "shape": (2,)})
    pickle.dump(np.array([1.5, {"a": 1}], dtype=object), buf, protocol=3)
    out = tmp_path / "odd.npz"
    with zipfile.ZipFile(good) as zi, zipfile.ZipFile(out, "w") as zo:
        for n in zi.namelist():
            zo.writestr(n, buf.getvalue() if n == "regions.npy" else zi.read(n))
    with pytest.raises(UnsafeResultFile, match="something other than text"):
        ResultSet.load(out)


def test_read_npz_reads_plain_files_and_nothing_in_the_code_base_unpickles():
    buf = io.BytesIO()
    np.savez(buf, a=np.arange(3), b=np.array(["x", "yy"]))
    buf.seek(0)
    z = read_npz(buf)
    assert z["a"].tolist() == [0, 1, 2] and z["b"].tolist() == ["x", "yy"]
    root = Path(__file__).resolve().parents[1] / "simwb"
    offenders = [str(p.relative_to(root)) for p in root.rglob("*.py") if "allow_pickle=True" in p.read_text(encoding="utf-8", errors="ignore")]
    assert offenders == []
