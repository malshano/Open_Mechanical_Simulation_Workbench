# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately through GitHub:
**Security** tab of this repository > **Report a vulnerability**
(<https://github.com/malshano/Open_Mechanical_Workbench/security/advisories/new>).

Include what you found, how to reproduce it, and what an attacker could do with it. You should get an answer within
two weeks. Fixes are released as soon as they are ready, and you are credited in the advisory unless you ask not to be.

Only the latest release and the `main` branch are supported.

## How the workbench is meant to be used

The workbench is a desktop application: a local web server that only one person on the computer uses, through the
browser on the same computer.

- The server listens on `127.0.0.1` only and answers only to the host names `127.0.0.1`, `localhost` and `[::1]`
  (this blocks DNS-rebinding attacks from web pages). Requests that change anything need the `X-SimWB: 1` header and a
  local `Origin`, so other web pages cannot drive it.
- Do **not** expose the port to a network (port forwarding, `0.0.0.0` proxies, tunnels). There is no login: anyone who
  can reach the port can run simulations and read your studies.
- Any program already running under your account can use the local API. This is the same trust level as your own
  files.
- The built-in Claude assistant gets only the workbench's own tools: no shell, no file access, no web access. The
  Claude terminal in the GUI accepts only a fixed list of commands.
- **Study folders from other people.** An OpenFOAM case can run programs (`#codeStream`, `coded` conditions,
  `systemCall`, libraries loaded by path). The workbench refuses these in edited case files and checks every file of
  the written case before a run. Still, treat a study folder or STEP file from an unknown source like any other
  downloaded file.
- Downloads made by the installers (uv, OpenFOAM, MS-MPI, micromamba for CalculiX) are pinned to exact versions and
  checked against SHA-256 checksums before they are run. CalculiX packages come from conda-forge through micromamba.

## Scope

In scope: the code in this repository (server, GUI, solvers integration, installers).
Out of scope: vulnerabilities in OpenFOAM, gmsh, CalculiX, Python packages or Claude Code themselves (report them to
their projects), and attacks that need the server to be exposed to a network against the guidance above.
