"""Tiny client for the workbench HTTP API (server on 127.0.0.1:8766), used by the showcase validation scripts."""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8766"
HDR = {"Content-Type": "application/json", "X-SimWB": "1", "Origin": BASE}


def call(method, path, body=None, timeout=900):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=HDR)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode()[:600]}")


def tool(pid, tool_name, **args):
    """Run a workbench tool (same as the assistant's): returns the text it prints."""
    return call("POST", f"/api/tools/{tool_name}", {"project_id": pid, "args": args})["text"]


def tool_json(pid, tool_name, **args):
    t = tool(pid, tool_name, **args)
    try:
        return json.loads(t)
    except Exception:
        return t


def wait_job(job, timeout=900):
    """Poll a job dict ({'id': ...}) until it is no longer running."""
    jid = job["id"] if isinstance(job, dict) and "id" in job else job
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = call("GET", f"/api/jobs/{jid}")
        if j["status"] != "running":
            return j
        time.sleep(1.0)
    raise TimeoutError(jid)
