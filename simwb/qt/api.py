"""The HTTP side of the desktop application: the workbench's REST API (everything the web version could do) plus /api/ui/*, the control
layer of the window (state, actions, widgets, screenshot, log, wait, eval), served from inside the running application.

Local only: it listens on 127.0.0.1, and the server's own guards (local Host header, local Origin, the X-SimWB header on every
change) apply. /api/ui/eval also needs the per-run token written to <workspace>/.ui/control.json and the debug switch.

    curl http://127.0.0.1:8765/api/ui/state
    curl -X POST -H "X-SimWB: 1" -H "Content-Type: application/json" -d '{"action":"step.go","args":{"step":"mesh"}}' http://127.0.0.1:8765/api/ui/do
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time

from fastapi import APIRouter, Body, Header
from fastapi.responses import FileResponse

from .. import project as P


def _free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def make_router(control, token: str) -> APIRouter:
    r = APIRouter(prefix="/api/ui", tags=["desktop window"])

    @r.get("/state")
    def state():
        """Everything the window shows now."""
        return control.handle("state", {})

    @r.get("/actions")
    def actions():
        return control.handle("actions", {})

    @r.post("/do")
    def do(body: dict = Body(...)):
        """{"action": "mesh.generate", "args": {...}}: one operation of the user interface."""
        return control.handle("do", {"action": body.get("action", ""), "args": body.get("args") or {}})

    @r.get("/widgets")
    def widgets(filter: str | None = None, visible_only: bool = False):
        return control.handle("widgets", {"filter": filter, "visible_only": visible_only})

    @r.post("/widget")
    def widget(body: dict = Body(...)):
        """{"id": "results.colormap", "op": "set", "value": "viridis"}"""
        return control.handle("widget", {"id": body.get("id", ""), "op": body.get("op", "get"), "value": body.get("value")})

    @r.get("/screenshot")
    def screenshot(what: str = "window"):
        d = control.handle("screenshot", {"what": what})
        return FileResponse(d["path"], media_type="image/png", headers={"X-Path": d["path"]})

    @r.get("/log")
    def log(after: int = 0, level: str | None = None, source: str | None = None, contains: str | None = None, limit: int = 200):
        return control.handle("log", {"after": after, "level": level, "source": source, "contains": contains, "limit": limit})

    @r.post("/wait")
    def wait(body: dict = Body(default={})):
        return control.handle("wait", {"timeout_s": float(body.get("timeout_s", 120))})

    @r.get("/coverage")
    def coverage():
        """Controls without an id (the list must be empty)."""
        return control.handle("coverage", {})

    @r.post("/eval")
    def eval_(body: dict = Body(...), authorization: str | None = Header(default=None)):
        """Python run inside the application. Debug mode and the token are both required (checked by the control layer itself)."""
        given = (authorization or "").removeprefix("Bearer ").strip()
        return control.handle("eval", {"code": body.get("code", ""), "token": given})

    return r


def start(control, port: int | None = None, debug: bool = False) -> dict:
    """Serve the REST API and the control layer on 127.0.0.1 in a thread of this process. The first free port from SIMWB_PORT / 8765."""
    import uvicorn

    from .. import server

    want = int(port or os.environ.get("SIMWB_PORT", "8765"))
    use = next((p for p in range(want, want + 20) if _free(p)), None)
    if use is None:
        raise RuntimeError(f"No free port from {want} to {want + 19} for the API")
    os.environ["SIMWB_PORT"] = str(use)       # (the studies' .mcp.json files name this port)
    token = control.token
    server.app.include_router(make_router(control, token))
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=use, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True, name="simwb-api").start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    info = {"url": f"http://127.0.0.1:{use}", "port": use, "pid": os.getpid(), "token": token, "debug": bool(debug),
            "started": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": "desktop"}
    d = P.WORKSPACE / ".ui"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "control.json"
    f.write_text(json.dumps(info, indent=1))
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass
    control.log.add("info", "api", f"API on {info['url']} (control file {f})")
    info["server"] = srv
    return info
