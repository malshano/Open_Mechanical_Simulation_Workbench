# Contributing

Thank you for helping. Bug reports, physics checks against known results, and pull requests are all welcome.

## Before you start

- Read [docs/TECHNICAL_MANUAL.md](docs/TECHNICAL_MANUAL.md): the code map, data flow, conventions and the pitfalls that
  already happened.
- The workbench is a **general** tool: changes must work for any model a user imports, not be tuned to one example.
- For a larger change, open an issue first so we can agree on the approach.
- Security problems: see [SECURITY.md](SECURITY.md), not the issue tracker.

## Development setup (Windows)

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m simwb.server
```

OpenFOAM is installed into the repository folder on the first start (`openfoam/`, ignored by git).

## Tests

```bat
.venv\Scripts\python -m pytest tests -q
```

All tests must pass. Add a test for every bug fix and every new feature. Tests that need CalculiX are skipped when it is
not installed. Physics changes should come with a check against an analytical result or a reference case.

## Pull requests

- One topic per pull request, with a clear description of what changed and why, and how you verified it (tests,
  screenshots of the GUI, a solver run).
- Keep the style of the surrounding code; update the user and technical manuals when behaviour changes.
- Do not commit generated files, studies, solver installations, or CAD you do not have the right to share.

## Licence and sign-off

The project is licensed under the **GNU GPL v3.0 or later** (see [LICENSE](LICENSE)). By contributing you agree that
your contribution is licensed under the same terms.

Sign off every commit to certify the [Developer Certificate of Origin](https://developercertificate.org/) (that you
wrote the change or otherwise have the right to submit it under this licence):

```
git commit -s -m "Fix the inlet arrow on half models"
```
