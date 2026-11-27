"""In-app Claude chat, powered by the Claude Agent SDK.

Authentication uses the local Claude Code login (claude.ai subscription or Console), or
ANTHROPIC_API_KEY if set. Claude only gets the workbench tools - no shell or file access.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import daemonthread
from . import project as P
from .tools import SYSTEM_PROMPT, TOOLS, call

MODEL = os.environ.get("SIMWB_CLAUDE_MODEL", "claude-opus-5-5")


def _cli() -> str:
    import claude_agent_sdk

    b = Path(claude_agent_sdk.__file__).parent / "_bundled" / ("claude.exe" if sys.platform == "win32" else "claude")
    return str(b) if b.exists() else "claude"


def auth_status() -> dict:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return {"loggedIn": True, "authMethod": "api_key"}
    try:
        r = subprocess.run([_cli(), "auth", "status"], capture_output=True, text=True, timeout=20)
        d = json.loads(r.stdout)
        return {k: d.get(k) for k in ("loggedIn", "authMethod", "email", "orgName", "subscriptionType")}
    except Exception as e:  # noqa: BLE001
        return {"loggedIn": False, "error": str(e)}


def open_in_claude_code(folder: Path) -> str:
    """Open Claude Code (the interactive command line) in a terminal window, in the study's folder. The folder has a
    CLAUDE.md and a .mcp.json (project.write_claude_files), so it knows the study. Nothing else is passed to it."""
    import shutil

    cli = shutil.which("claude") or _cli()
    if sys.platform == "win32":
        # no shell: the folder goes in as the working directory, never onto a command line (a folder name with & or ^ in it)
        subprocess.Popen([cli], cwd=str(folder), creationflags=subprocess.CREATE_NEW_CONSOLE)
        return f"Claude Code opened in a terminal window, in {folder}"
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["open", "-a", "Terminal"]):
        try:
            subprocess.Popen(term + [cli], cwd=str(folder))
            return f"Claude Code opened in a terminal window, in {folder}"
        except FileNotFoundError:
            continue
    return f"Run this in a terminal: cd \"{folder}\" && {cli}"


def start_login() -> str:
    """Open the Claude login flow in a terminal window (browser-based OAuth)."""
    cli = _cli()
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "Claude login", "cmd", "/k", cli, "auth", "login"])
        return "A terminal window opened - follow the browser login, then press 'Refresh'."
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["open", "-a", "Terminal"]):
        try:
            subprocess.Popen(term + [cli, "auth", "login"])
            return "A terminal window opened - follow the browser login, then press 'Refresh'."
        except FileNotFoundError:
            continue
    return f"Run this in a terminal: {cli} auth login"


# terminal escape sequences in the CLI output (colours, cursor moves, clickable-link markers)
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][A-Z0-9]|\x1b.")


class Terminal:
    """The Claude terminal of a project: runs the Claude Code CLI (the one the assistant uses) in the folder the
    project is saved in. Output goes to the page line by line; lines typed while it runs go to its stdin (a login
    that waits for a code). Only the claude CLI runs here - not a general shell."""

    def __init__(self):
        self.procs: dict[str, subprocess.Popen] = {}

    def run(self, pid: str, args: list[str], emit) -> str:
        import threading

        pr = P.open_project(pid)
        if pid in self.procs and self.procs[pid].poll() is None:
            raise ValueError("A command is still running in this terminal (Stop it first, or type its input)")
        env = dict(os.environ)
        env["NO_COLOR"] = "1"
        proc = subprocess.Popen([_cli(), *args], cwd=str(pr.dir), env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", bufsize=1,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.procs[pid] = proc

        def pump():
            for line in proc.stdout:
                emit({"type": "term_out", "project": pid, "text": _ANSI.sub("", line.rstrip("\r\n"))})
            emit({"type": "term_done", "project": pid, "code": proc.wait(), "auth": bool(args) and args[0] == "auth"})

        threading.Thread(target=pump, daemon=True).start()
        return str(pr.dir)

    def write(self, pid: str, text: str) -> bool:
        proc = self.procs.get(pid)
        if proc is None or proc.poll() is not None:
            return False
        try:
            proc.stdin.write(text + "\n")
            proc.stdin.flush()
        except OSError:
            return False
        return True

    def running(self, pid: str) -> bool:
        proc = self.procs.get(pid)
        return proc is not None and proc.poll() is None

    def stop(self, pid: str):
        proc = self.procs.get(pid)
        if proc is not None and proc.poll() is None:
            proc.terminate()


TERMINAL = Terminal()


TERMINAL_ALLOWED = {("auth", "login"), ("auth", "logout"), ("auth", "status"), ("--help",), ("-h",), ("--version",),
                    ("-v",), ("mcp", "list")}

TERM_HELP = "\n".join(["Commands (they run the claude command line in the study folder):", "/login   log in to Claude", "/logout  log out",
                          "/status  who is logged in", "/ask question   one plain question to Claude (no study tools, no shell)", "/version, /help",
                          "Anything without a / goes to the assistant, which can set up and run your study."])


def terminal_args(args: list) -> list:
    """The arguments the Claude terminal may pass to the claude CLI: a fixed list of commands, or one question
    (-p "text") that runs without file-editing or shell tools. Any other flag is refused: flags such as
    --dangerously-skip-permissions, --allowedTools, --mcp-config, --settings or --add-dir would turn the terminal
    into a way to run commands."""
    if tuple(args) in TERMINAL_ALLOWED:
        return list(args)
    if len(args) == 2 and args[0] in ("-p", "--print") and args[1] and not args[1].startswith("-"):
        return ["-p", args[1], "--permission-mode", "default", "--disallowedTools", "Bash,Edit,Write,NotebookEdit,WebFetch"]
    raise ValueError("This terminal runs: login, logout, status, --help, --version, mcp list, or one question as "
                     "-p \"question\" (without shell or file-editing tools)")


def terminal_line_args(line: str) -> list:
    """One line typed in the Claude terminal ("login", "status", "-p \"question\"") -> the checked arguments for the claude CLI."""
    import shlex

    args = shlex.split(line.strip(), posix=False)
    args = [a[1:-1] if len(a) > 1 and a[0] == a[-1] and a[0] in "\"'" else a for a in args]
    if args and args[0].lower() in ("claude", "claude.exe"):
        args = args[1:]
    short = {"login": ["auth", "login"], "logout": ["auth", "logout"], "status": ["auth", "status"]}
    if len(args) == 1 and args[0] in short:
        args = short[args[0]]
    return terminal_args(args)


def slash_command(text: str) -> tuple[str, str | None]:
    """A chat line that starts with / ("/status", "/ask what is Q?", "/help") -> (kind, line): ("run", the line for terminal_line_args),
    ("help", None) or ("unknown", None). The same commands as the web version's chat box."""
    cmd, *rest = text[1:].strip().split()
    arg = " ".join(rest)
    if cmd in ("help", "?"):
        return "help", None
    if cmd == "ask":
        return ("run", '-p "%s"' % arg.replace('"', "'")) if arg else ("unknown", None)
    if cmd in ("login", "logout", "status"):
        return "run", cmd
    if cmd == "version":
        return "run", "--version"
    return "unknown", None


class ChatSession:
    """One conversation bound to a project; streams events to `send` (async callable)."""

    def __init__(self, project_id: str, send):
        self.project_id = project_id
        self.send = send
        self.client = None
        self.lock = asyncio.Lock()

    def _project(self):
        return P.open_project(self.project_id)

    def _build_server(self):
        from claude_agent_sdk import create_sdk_mcp_server, tool

        sdk_tools = []
        for name, desc, schema, _ in TOOLS:
            def make(name=name):
                async def handler(args):
                    await self.send({"type": "chat_tool", "name": name, "input": args})
                    text = await daemonthread.to_thread(call, name, self._project(), args)
                    await self.send({"type": "chat_tool_result", "name": name,
                                     "ok": not text.startswith("ERROR"), "preview": text[:300]})
                    return {"content": [{"type": "text", "text": text}], "is_error": text.startswith("ERROR")}
                return handler
            sdk_tools.append(tool(name, desc, schema)(make()))
        return create_sdk_mcp_server("simwb", "1.0.0", sdk_tools)

    async def _connect(self):
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        pr = self._project()
        opts = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT + f"\n\nThe user has project '{pr.data['name']}' open (study: "
                                          f"{pr.spec['label']}), saved in the folder {pr.dir} (its files: project.json, geometry, case, "
                                          f"results, videos).",
            mcp_servers={"simwb": self._build_server()},
            tools=[],
            allowed_tools=[f"mcp__simwb__{t[0]}" for t in TOOLS],
            model=MODEL,
            max_turns=40,
            cwd=str(pr.dir),
            setting_sources=[],
        )
        self.client = ClaudeSDKClient(options=opts)
        await self.client.connect()

    async def ask(self, text: str, history: list | None = None):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

        async with self.lock:
            try:
                if self.client is None:
                    await self.send({"type": "chat_status", "text": "Connecting to Claude…"})
                    await self._connect()
                    if history:
                        # the page was reloaded: give the new session the conversation the user can still see
                        lines = [f"{m['role']}: {m['text']}" for m in history if m.get("text")]
                        text = ("[Earlier conversation in this project, for context]\n" + "\n".join(lines)
                                + "\n[End of earlier conversation]\n\n" + text)
                await self.client.query(text)
                async for msg in self.client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for b in msg.content:
                            if isinstance(b, TextBlock) and b.text.strip():
                                await self.send({"type": "chat_text", "text": b.text})
                    elif isinstance(msg, ResultMessage):
                        await self.send({"type": "chat_done", "cost_usd": getattr(msg, "total_cost_usd", None),
                                         "is_error": getattr(msg, "is_error", False)})
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "login" in msg.lower() or "auth" in msg.lower() or "401" in msg:
                    msg += "\nYou may need to log in to Claude (button at the top of the chat panel)."
                await self.send({"type": "chat_error", "text": msg})
                await self.close()

    async def interrupt(self):
        if self.client:
            try:
                await self.client.interrupt()
            except Exception:  # noqa: BLE001
                pass

    async def close(self):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.client = None
