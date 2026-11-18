import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_worker(task, job, tmp):
    jp = Path(tmp) / f"{task}.json"
    jp.write_text(json.dumps(job))
    r = subprocess.run([sys.executable, "-m", "simwb.gmsh_worker", task, str(jp)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return r


@pytest.fixture
def box_step(tmp_path):
    import gmsh
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.clear()
    gmsh.model.occ.addBox(0, 0, 0, 100, 20, 20)  # mm
    gmsh.model.occ.synchronize()
    p = tmp_path / "box.step"
    gmsh.write(str(p))
    gmsh.finalize()
    return p
