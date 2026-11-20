# Benchmark results

One JSON line per case (see benchmarks/mesh_benchmark.py). Read them with `python benchmarks/mesh_analyze.py <file>`.

- `sharp_plate_tet_2026-11-20.jsonl`: laminar flow over a sharp-edged plate, tetrahedral mesh, 12 layers. Rows with
  `edge_field = v1-superseded` come from the FIRST version of the sharp-edge refinement (a size field that jumped to
  the far-field size within 12 edge cells). That version was replaced (it broke the layer step as the mesh was refined,
  and its zone was accidentally razor thin); its rows are kept only as history and must not be read as results of
  the current method. Their layer coverage (84-94 %) also predates the layer-table metric.
