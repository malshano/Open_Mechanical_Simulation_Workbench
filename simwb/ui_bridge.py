"""The line between the tools (Claude's, the MCP server's, the REST API) and the desktop window.

The desktop application (simwb.qt) installs a handler here when it starts. The tools in simwb.tools call `call(op, args)`; when no
window is running (the web server alone, the MCP server on its own) they get a clear error instead. Nothing in this module needs Qt."""
from __future__ import annotations

HANDLER = None   # set by the desktop application: handler(op: str, args: dict) -> JSON-able


class NoWindow(RuntimeError):
    pass


def available() -> bool:
    return HANDLER is not None


def call(op: str, args: dict | None = None):
    if HANDLER is None:
        raise NoWindow("The desktop window is not running (start it with run_workbench_qt.bat or python -m simwb.qt): there is no "
                       "user interface to inspect or drive.")
    return HANDLER(op, args or {})
