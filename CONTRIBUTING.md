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

## Branch flow: staging before main

Work branches are merged into the **`staging`** branch first; `main` receives only what has been through `staging`.
Everything in `staging` is a **beta release** (version `0.4.0bN`), so merge only what you are willing to ship as a
beta. See [CLAUDE.md](CLAUDE.md) for the details.

## Rules for main

`main` is protected on GitHub:

- Changes go in through a **pull request** from a branch or a fork. Direct pushes, force-pushes and deleting the branch
  are blocked.
- The **tests** check (`pytest` on Windows) must pass on the latest commit, and the branch must be up to date with
  `main`.
- One **approving review** from the maintainer (see [.github/CODEOWNERS](.github/CODEOWNERS)) is needed, after the last
  push, and every review conversation must be resolved.
- Pull requests are merged with **squash** (preferred) or a merge commit.
- The first workflow run of a new contributor waits for the maintainer's approval (a GitHub default for forks).
- Changes to the security checks (case-file check, local-host and CSRF guards, pinned downloads with checksums) get an
  extra careful review: say so in the pull request.

## Licence and sign-off

The project is licensed under the **GNU GPL v3.0 or later** (see [LICENSE](LICENSE)). By contributing you agree that
your contribution is licensed under the same terms.

Sign off every commit to certify the [Developer Certificate of Origin](https://developercertificate.org/) (that you
wrote the change or otherwise have the right to submit it under this licence):

```
git commit -s -m "Fix the inlet arrow on half models"
```
