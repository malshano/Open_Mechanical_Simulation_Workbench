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
  barely moves after about 300 iterations. (2) Cf is already steady there. The test that means something is on the 6 rule-off runs (to 1500): Cf at
  iteration 500 differs from Cf at 1500 by at most 0.012 %. (For the runs that stopped at about 505 the same comparison
  is trivially zero; for those: Cf at iteration 300 is within 0.053 % of the final value in all 24 runs of both files,
  and Cf over the last 100 iterations changed by at most 0.025 % with the rule on and 0.075 % with it off.) The rule-off
  check is only a consistency check (the same Cf series qualifies the run in mesh_analyze.py and is then reported as
  steady: partly circular; Cf is stored every 25 iterations to 7 digits, so many drifts read exactly 0.0). Rule-off runs
  are flagged not converged and not valid by design (the app only calls a run converged when the residual targets or
  the stop rule say so). The rule's first test is at iteration 500, so a case that
  converges more slowly is not covered: one laminar plate, tet mesher, two coarse presets. (3) The spread between
  repeats is not explained by the stop rule: with the rule off it is as large as with it on (quick preset, max-min over
  the mean of 4 runs each: 2.9 % off, 3.6 % at 0.02 %, 1.9 % at 0.2 %; coarse, 2 runs each: 0.6 %, 1.3 %, 1.0 %), and
  four runs cannot rank those ranges. (4) Accuracy: this configuration (5 layers, no edge refinement) is the LEAST
  accurate of the plate data: Cf is 17.2 % to 22.5 % below Blasius in all 18 rows (quick mean -19.9 %, coarse mean
  -18.2 %). The older 12-layer data are far better (edge x6: -5.7 % to -3.7 % on quick). These files show steadiness
  and repeat spread only, not mesh accuracy.

- `flat_plate_fixed_mesh_and_12layers_2026-11-27.jsonl` (issue 14, second round): (a) ONE mesh solved again with the
  same settings (`solves=`; rows with `solve_repeats`): mesh 1 solved 4 times on the automatic core count and 3 more
  times on 2 cores (`procs2=`), mesh 2 solved 4 times. (b) The issue's own configuration: 12 layers, edge refinement
  factor 6 (`n=12,edge=6` through the existing `edge_refinement` mesh setting), quick preset, default rule, 4 runs that
  rebuild the mesh. `git` is 28cb12e+uncommitted for the first row (the working tree of the monitor change) and 53094ec
  for the others (same `simwb/` code as that commit).

  Result. Fixed mesh: Cf is the same to 6 digits in every solve: max-min over the mean 0.0001 % (mesh 1, 4 solves),
  0.0000 % (mesh 1 on 2 cores), 0.0001 % (mesh 2); auto against 2 cores differ by 0.0004 %. The solves are not
  bit-identical (the 7th-8th digit changes) but the solver and the decomposition add about 1e-6 of Cf. The final cell
  count is identical in every solve of one mesh (182,225 and 181,860). Each solve of this pipeline rewrites the case and runs
  the layer step again (I did not verify this from the logs), so the layer step is reproducible on a given mesh too.
  The two meshes differ from each other by 1.4 % in Cf (0.0023638 and 0.0023964). Rebuilding the mesh each time, the
  quick 5-layer runs span 5.5 % in Cf (14 runs, -22.5 % to -18.0 % against Blasius) and the base (gmsh) cell count
  alone varies 98,921-100,275. So the run to run variation of the earlier replicate files lives in the building of the
  mesh, not in the stop rule, the solver or the number of cores: it is at least 3 orders of magnitude larger than the
  fixed-mesh solver variation. Which property of the mesh drives Cf is not identified: correlations of Cf with the base
  cell count, the final cell count, the largest non-orthogonality, the largest skewness, the layer thickness share and
  the mean number of layers (14 runs) are all between -0.09 and +0.09. Issue configuration (12 layers, edge x6, 4 runs): the
  stop is at iteration 501-507, converged, 93.5 % of the layer thickness, 310,405-313,972 final cells, Cf error
  -4.3 % to -3.6 % (mean -4.1 %), spread of the 4 rebuilds 0.73 % (max-min over the mean). Here too the stop is not the
  limit; the spread is smaller than with 5 layers but 4 runs cannot say whether that is real.
