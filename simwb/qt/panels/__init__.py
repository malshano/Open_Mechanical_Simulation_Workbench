"""The panel of each step (Geometry, Setup, Mesh, Solve, Results): one module each, so that they can be worked on side by side.

A panel shows what the study holds (refresh) and tells the window when a choice changes what the 3D view should draw (changed).
The study itself is only changed through the engine."""
from .common import KPI, _Panel, _h, _muted, _seg  # noqa: F401
from .geometry import GeometryPanel
from .mesh import MeshPanel
from .results import ResultsPanel
from .setup import SetupPanel
from .solve import SolvePanel

__all__ = ["GeometryPanel", "SetupPanel", "MeshPanel", "SolvePanel", "ResultsPanel", "KPI"]
