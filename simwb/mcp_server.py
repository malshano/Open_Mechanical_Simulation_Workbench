"""Stdio MCP server: drive the workbench from Claude Desktop / Claude Code.

Register with Claude Code:
    claude mcp add simwb -- <repo>/.venv/Scripts/python.exe -m simwb.mcp_server
Project tools take `project_id`; it may only be left out while there is a single project.
"""
from __future__ import annotations

import asyncio
import copy

import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

import os

from . import project as P
from . import tools as T
from .tools import MCP_EXTRA, PROJECT_FREE, PROJECT_OPTIONAL, SYSTEM_PROMPT, TOOLS, call

T.REMOTE_GUI = os.environ.get("SIMWB_GUI_URL", f"http://127.0.0.1:{os.environ.get('SIMWB_PORT', '8765')}")

ALL = TOOLS + MCP_EXTRA


def _schema(name, schema):
    s = copy.deepcopy(schema)
    if name not in PROJECT_FREE or name in PROJECT_OPTIONAL:
        s.setdefault("properties", {})["project_id"] = {
            "type": "string", "description": "Project id (required when several projects exist)"}
    return s


async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[
        types.Tool(name=n, description=d, input_schema=_schema(n, s)) for n, d, s, _ in ALL])


def _via_gui(name, pid, args):
    """If the workbench GUI server is running, run the tool there: one process owns jobs and project state,
    and the user sees the changes live. Returns None when the GUI is not reachable."""
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"{T.REMOTE_GUI}/api/tools/{name}",
                                 data=json.dumps({"project_id": pid, "args": args}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-SimWB": "1"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            d = json.loads(r.read())
            pr = d.get("project")
            return (f"[project: {pr['name']} (id {pr['id']})]\n" if pr else "") + d["text"]
    except urllib.error.HTTPError as e:
        try:
            return "ERROR: " + json.loads(e.read()).get("error", str(e))
        except Exception:  # noqa: BLE001
            return f"ERROR: {e}"
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return None


def _err(text):
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


async def call_tool(ctx, params):
    name = params.name
    args = dict(params.arguments or {})
    pid = args.pop("project_id", None)
    # the running GUI owns projects, jobs and its workspace: forward there first
    text = await asyncio.to_thread(_via_gui, name, pid, args) if T.REMOTE_GUI else None
    if text is not None:
        body = text.split("\n", 1)[1] if text.startswith("[project:") and "\n" in text else text
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                    is_error=body.startswith("ERROR"))
    # GUI not running: work on the local workspace in this process
    pr = None
    if name not in PROJECT_FREE or (name in PROJECT_OPTIONAL and pid):
        if not pid:
            projects = P.list_projects()
            if not projects:
                return _err("No projects yet - call create_project first.")
            if len(projects) > 1:
                return _err("ERROR: several projects exist: pass project_id (one of "
                            + ", ".join(p["id"] for p in projects[:20]) + ").")
            pid = projects[0]["id"]
        try:
            pr = P.open_project(pid)
        except KeyError as e:
            return _err(f"ERROR: {e.args[0] if e.args else e}")
    text = await asyncio.to_thread(call, name, pr, args)
    is_error = text.startswith("ERROR")
    if pr is not None:
        text = f"[project: {pr.data.get('name')} (id {pr.id})]\n" + text
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=is_error)


server = Server("simwb", version="0.1.0", instructions=SYSTEM_PROMPT, on_list_tools=list_tools,
                on_call_tool=call_tool)


async def main():
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
