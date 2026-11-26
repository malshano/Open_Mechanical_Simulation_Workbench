"""Topology optimisation (SIMP on tetrahedral meshes): `problem.build` makes the structural problem from a project's mesh, materials and conditions,
`simp.Optimiser` finds the stiffest shape for a mass target. See docs/TOPOLOGY_OPTIMISATION.md."""
from .problem import Problem, ProblemError, build  # noqa: F401
from .simp import Optimiser, Settings, default_radius, estimate  # noqa: F401
