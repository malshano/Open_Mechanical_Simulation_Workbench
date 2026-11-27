# Benchmark results

One JSON line per case (see benchmarks/mesh_benchmark.py). Read them with `python benchmarks/mesh_analyze.py <file>`.

- `sharp_plate_tet_2026-11-20.jsonl`: laminar flow over a sharp-edged plate, tetrahedral mesh, 12 layers. Rows with
  `edge_field = v1-superseded` come from the FIRST version of the sharp-edge refinement (a size field that jumped to
  the far-field size within 12 edge cells). That version was replaced (it broke the layer step as the mesh was refined,
  and its zone was accidentally razor thin); its rows are kept only as history and must not be read as results of
  the current method. Their layer coverage (84-94 %) also predates the layer-table metric.

- `flat_plate_stopping_rule_2026-11-27.jsonl` (issue 14, stopping rule): the laminar sharp plate (tet, 5 layers, growth
  1.2, 3 m/s) with the app's stop rule at three settings (`stop=` in mesh_benchmark.py = the study setting
  "Stop when forces settle", % of the total load): 0.2 (the default), 0.02 (tight) and 0 (off: runs to the 1500
  iteration limit). `quick` preset: 4 repeats of each; `coarse` preset: 2 of each. Every row has `stop_tol_percent`,
  `stopped_at` (last iteration), and `cf_history` (Cf from the solver's viscous x force every 25 iterations), plus
  `cf_drift_last100_percent` (change of Cf over the last 100 iterations). `git` is the code that made the row
  (`d74e84f` rows: the code of the stop setting; later rows `833d4e0`: the same `simwb/` code, tests and docs added).
  Read with `python benchmarks/mesh_analyze.py <file>`: the first table is the stop-rule table. Rows with the rule off
  are never called converged by the app (it only calls a run converged when the residual targets or the stop rule
  say so); the analysis accepts them as steady when Cf changed by less than 0.1 % over the last 100 iterations.

  Result. (1) The default rule stops these runs at iteration 501-510 (tight 0.02 %: 502-512), because the total force
  barely moves after about 300 iterations. (2) Cf is already steady there: in all 18 runs Cf at iteration 500 differs
  from Cf at 1500 (or at the stop) by at most 0.012 %, and Cf over the last 100 iterations changed by at most 0.075 %
  (rule off) and 0.017 % (rule on). So the stop rule is not what limits the benchmark. (3) The spread between repeats
  is the same with the rule off: quick preset 2.9 % (rule off), 3.6 % (0.02 %), 1.9 % (0.2 %); coarse preset 0.6 %,
  1.3 %, 1.0 % (max-min over the mean of 2-4 runs). Repeats of one spec do not build the same mesh (final cells
  179,929-182,265 on quick, 231,470-235,105 on coarse), and Cf follows the mesh, not the stop rule. The 2-point
  replicate spread of the earlier files is therefore mesher (and layer step) noise, not monitor tolerance. Same-mesh
  noise cannot be separated from this: no run repeats the identical mesh.
