# Working in this repository

Notes for Claude Code (and anyone else working here). Read `CONTRIBUTING.md` for the project rules; this file is about how work moves through the branches.

## Branch flow

```
feature / fix / docs branch  ->  staging  ->  main
```

- **Every branch goes through `staging` before it reaches `main`.** Nothing is merged or pushed to `main` directly, and nothing skips `staging`.
- **Everything in `staging` is a beta release.** `staging` always carries a beta version (`0.4.0bN` in `simwb/__init__.py`), and what is in it is what beta testers get. Merge only what you are willing to ship as a beta.
- **`main` is the stable line.** It only receives what has been through `staging` and accepted as a beta, by a pull request from `staging` (see "Rules for main" in `CONTRIBUTING.md`).
- Work branches are tested on their own, then merged into `staging`.

## Before merging into `staging`

- The new tests and the existing tests that touch the changed code pass, run with the project's Python environment (the system Python has no PySide6 or VTK and skips the Qt tests).
- User-visible changes are in the user manual (and `docs/QT_PARITY.md` / the technical manual where relevant), and the issue numbers are in the commit messages (`Fixes #N`).
- Raise the beta number and add the notes to `docs/BETA_0.4.md` when a beta is cut.

## Working practice

- Do feature work in its own git worktree on its own branch (`git worktree add ../MechanicalSimulationWorkbench-<name> -b <branch> <base>`), never in the main folder: other people and agents edit it. Do not revert or stash anything in a folder you did not start.
- Push to the `public` remote only. `origin` (the old private repository) is archived.
- Commit and push only when asked. Deleting a branch on a remote is a separate request.
- Keep the benchmark data in its own data-only branch (`topopt/literature-benchmark` for the topology optimisation benchmark); code and tests go through `staging` like everything else.
