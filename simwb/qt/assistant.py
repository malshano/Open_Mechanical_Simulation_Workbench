"""The Claude assistant of the Qt window: the same conversation engine as the web version (simwb.agent.ChatSession, with the
workbench tools of simwb.tools - Claude gets no shell and no file access), shown in a dock. The conversation runs on its own
thread with an asyncio loop; its events come back as Qt signals."""
from __future__ import annotations

import asyncio
import subprocess
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from .. import agent
from .flow import FlowLayout
from .ids import wid

SUGGESTIONS = {
    "any": ["Summarise this study for me", "What should I do next?", "Is the mesh fine enough?"],
    "results": ["Summarise these results for me", "Is the safety factor acceptable?", "Where is the worst spot and why?"],
    "setup": ["Help me set up the supports and loads", "Which material fits this part?"],
}


class ChatBridge(QObject):
    """Runs ChatSession on a private asyncio loop (a thread of its own) and turns its events into Qt signals. It also runs the claude
    command line of the chat box (/login, /status ...: agent.TERMINAL) and turns its output into signals."""
    event = Signal(dict)
    auth = Signal(dict)
    term_out = Signal(str, str)            # study id, a line of output
    term_done = Signal(str, int, bool)     # study id, exit code, was it a login / logout (the assistant must reconnect)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop = asyncio.new_event_loop()
        self.sessions: dict[str, agent.ChatSession] = {}
        threading.Thread(target=self.loop.run_forever, daemon=True, name="claude-assistant").start()

    async def _send(self, ev: dict):
        self.event.emit(dict(ev))

    def _session(self, pid: str) -> agent.ChatSession:
        s = self.sessions.get(pid)
        if s is None:
            s = self.sessions[pid] = agent.ChatSession(pid, self._send)
        return s

    def ask(self, pid: str, text: str, history: list | None = None):
        asyncio.run_coroutine_threadsafe(self._session(pid).ask(text, history or []), self.loop)

    def stop(self, pid: str):
        if pid in self.sessions:
            asyncio.run_coroutine_threadsafe(self.sessions[pid].interrupt(), self.loop)

    def reset(self, pid: str):
        s = self.sessions.pop(pid, None)
        if s is not None:
            asyncio.run_coroutine_threadsafe(s.close(), self.loop)

    def check_login(self):
        threading.Thread(target=lambda: self.auth.emit(agent.auth_status()), daemon=True).start()

    def login(self) -> str:
        return agent.start_login()

    def logout(self):
        def run():
            try:
                subprocess.run([agent._cli(), "auth", "logout"], capture_output=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            finally:
                self.auth.emit(agent.auth_status())
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ the claude command line of the chat box
    def _term_event(self, ev: dict):
        """Called from the thread that reads the command's output (any thread): becomes a signal for the window's thread."""
        if ev.get("type") == "term_out":
            self.term_out.emit(str(ev.get("project")), str(ev.get("text", "")))
        elif ev.get("type") == "term_done":
            self.term_done.emit(str(ev.get("project")), int(ev.get("code") or 0), bool(ev.get("auth")))

    def term_run(self, pid: str, line: str) -> list:
        """Run one checked line ("status", '-p "question"') as arguments of the claude CLI in the study's folder; returns the arguments."""
        args = agent.terminal_line_args(line)
        agent.TERMINAL.run(pid, args, self._term_event)
        return args

    def term_write(self, pid: str, text: str) -> bool:
        return agent.TERMINAL.write(pid, text)

    def term_running(self, pid: str | None) -> bool:
        return bool(pid) and agent.TERMINAL.running(pid)

    def term_stop(self, pid: str):
        agent.TERMINAL.stop(pid)

    def shutdown(self):
        try:
            for pid in list(agent.TERMINAL.procs):
                agent.TERMINAL.stop(pid)
        except Exception:  # noqa: BLE001
            pass
        for s in list(self.sessions.values()):
            try:
                asyncio.run_coroutine_threadsafe(s.close(), self.loop).result(timeout=3)
            except Exception:  # noqa: BLE001
                pass
        self.loop.call_soon_threadsafe(self.loop.stop)


class _Input(QPlainTextEdit):
    """Enter sends, Shift+Enter makes a new line."""
    send = Signal()

    def keyPressEvent(self, e: QKeyEvent):  # noqa: N802
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.send.emit()
            return
        super().keyPressEvent(e)


TOOL_LABELS = {
    "get_project_state": "Reading study", "find_faces": "Finding faces", "highlight_faces": "Highlighting faces", "set_body": "Setting part properties",
    "add_boundary_condition": "Adding boundary condition", "update_boundary_condition": "Updating boundary condition",
    "delete_boundary_condition": "Deleting boundary condition", "set_settings": "Changing settings", "generate_mesh": "Generating mesh",
    "run_simulation": "Running simulation", "get_results": "Reading results", "check_setup": "Checking setup", "list_options": "Looking up options",
    "auto_inlet_outlet": "Detecting inlet/outlet", "detect_rotors": "Placing rotors", "set_rotors": "Setting rotors", "set_mesh_settings": "Mesh settings",
    "get_run_status": "Checking run status", "set_domain": "Rebuilding domain", "openfoam_status": "Checking OpenFOAM",
    "probe_results": "Probing results", "wait_for_run": "Waiting for the run", "cancel_job": "Cancelling job", "rename_project": "Renaming study",
}
KEEP = 120            # messages kept per study (the web version keeps the same)


class AssistantPanel(QWidget):
    """The chat. toolDone is emitted when Claude has used a workbench tool, so the window can refresh what it shows.

    A line that starts with / runs the claude command line in the study's folder (/login /logout /status /ask /version /help: the same as the
    web version); anything else goes to the assistant. The conversation of each study is kept (in the window's settings) and shown again
    after the window is restarted."""
    toolDone = Signal(str, bool)
    notice = Signal(str, str)          # text, kind (info | ok | warn | error): the window shows it as a toast

    def __init__(self, bridge: ChatBridge, parent=None, settings=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.bridge = bridge
        self.settings = settings
        self.pid: str | None = None
        self.step = "any"
        self.log: dict[str, list] = {}       # per study: [(role, text)]  roles: user assistant tool term error
        self._busy: dict = {}                # study id -> Claude is answering there
        self.pending_tools: list = []
        self._tool_at: dict = {}             # (study, tool name) -> index of its line in the log, until its result comes
        self._logged_in = False
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(8)
        self.who = wid(QLabel("Checking the Claude login…"), "assistant.login_status")
        self.who.setObjectName("muted")
        self.who.setWordWrap(True)
        v.addWidget(self.who)
        top = FlowLayout(spacing=6)                       # (the buttons wrap: the dock need not be wide)
        self.btn_login = wid(QPushButton("Log in"), "assistant.login")
        self.btn_logout = wid(QPushButton("Log out"), "assistant.logout")
        self.btn_code = wid(QPushButton("Claude Code"), "assistant.claude_code")
        self.btn_new = wid(QPushButton("New chat"), "assistant.new_chat")
        for b in (self.btn_login, self.btn_logout, self.btn_code, self.btn_new):
            b.setObjectName("ghostBorder")
            top.addWidget(b)
        self.btn_login.setToolTip("Log in to Claude: runs /login here; open the link it prints and log in in the browser")
        self.btn_logout.setToolTip("Log out of Claude (/logout); log in again as someone else with Log in")
        self.btn_new.setToolTip("Start a new conversation about this study (the old one is forgotten)")
        v.addLayout(top)
        self.view = wid(QTextBrowser(), "assistant.transcript")
        self.view.setOpenExternalLinks(True)
        self.view.setPlaceholderText("Ask Claude to set up, mesh, run or explain this study.\nType /help for the commands of the Claude command line.")
        v.addWidget(self.view, 1)
        self.chips = FlowLayout(spacing=6)                  # (wraps: the panel need not be as wide as three chips)
        v.addLayout(self.chips)
        self.status = wid(QLabel(""), "assistant.status")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        row = QHBoxLayout()
        self.input = wid(_Input(), "assistant.input")
        self.input.setPlaceholderText("Ask Claude…  (Enter sends, Shift+Enter for a new line; / starts a command)")
        self.input.setFixedHeight(64)
        self.btn_send = wid(QPushButton("Send"), "assistant.send")
        self.btn_send.setObjectName("primary")
        self.btn_stop = wid(QPushButton("Stop"), "assistant.stop")
        self.btn_stop.setObjectName("ghostBorder")
        side = QVBoxLayout()
        side.setSpacing(4)
        side.addWidget(self.btn_send)
        side.addWidget(self.btn_stop)
        row.addWidget(self.input, 1)
        row.addLayout(side)
        v.addLayout(row)

        self.btn_send.clicked.connect(self._send_clicked)
        self.btn_stop.clicked.connect(self.stop)
        self.input.send.connect(self._send_clicked)
        self.btn_new.clicked.connect(self.new_chat)
        self.btn_login.clicked.connect(lambda: self.command("/login"))
        self.btn_logout.clicked.connect(lambda: self.command("/logout"))
        self.btn_code.clicked.connect(self.open_claude_code)
        bridge.event.connect(self._on_event)
        bridge.auth.connect(self._on_auth)
        bridge.term_out.connect(self._term_out)
        bridge.term_done.connect(self._term_done)
        bridge.check_login()
        self._chips("any")
        self._enable()

    # ------------------------------------------------------------------ study, login, history
    def set_project(self, pid: str | None):
        self.pid = pid or None
        if self.pid and self.pid not in self.log:
            self.log[self.pid] = self._load(self.pid)
        self._render()
        self._enable()
        self.status.setText("Claude is working…" if self.busy else "")

    @property
    def busy(self) -> bool:
        return bool(self._busy.get(self.pid))

    @busy.setter
    def busy(self, on: bool):
        if self.pid:
            self._busy[self.pid] = bool(on)

    def _key(self, pid: str) -> str:
        return "chat/" + "".join(c if c.isalnum() or c in "-_." else "_" for c in pid)

    def _load(self, pid: str) -> list:
        """The conversation kept for this study (what was said before the window was closed)."""
        if self.settings is None:
            return []
        try:
            import json
            data = json.loads(self.settings.value(self._key(pid), "[]", type=str) or "[]")
            return [(str(r), str(t)) for r, t in data if r in ("user", "assistant", "tool", "term", "error")]
        except Exception:  # noqa: BLE001
            return []

    def _save(self, pid: str | None = None):
        pid = pid or self.pid
        if self.settings is None or not pid:
            return
        try:
            import json
            msgs = [list(m) for m in self.log.get(pid, [])][-KEEP:]
            self.settings.setValue(self._key(pid), json.dumps(msgs))
            self.settings.sync()
        except Exception:  # noqa: BLE001
            pass

    def set_context(self, step: str):
        self.step = step
        self._chips("results" if step == "results" else "setup" if step == "setup" else "any")

    def _on_auth(self, st: dict):
        ok = bool(st.get("loggedIn"))
        if ok:
            if st.get("authMethod") == "api_key":
                self.who.setText("Using ANTHROPIC_API_KEY")
            else:
                self.who.setText("Logged in" + (f" as {st['email']}" if st.get("email") else "")
                                 + (f" ({st['subscriptionType']})" if st.get("subscriptionType") else ""))
        else:
            self.who.setText("Not logged in to Claude. Press Log in (or type /login), open the link it shows and log in in the browser.")
        self.btn_login.setVisible(not ok)
        self.btn_logout.setVisible(ok and st.get("authMethod") != "api_key")
        self._logged_in = ok
        self._enable()

    def _enable(self):
        has = bool(self.pid)
        term = self.term_busy()
        self.input.setEnabled(has)
        self.btn_send.setEnabled(has and (term or not self.busy))
        self.btn_send.setToolTip("Send the message (Enter)" if not self.busy else
                                 "Send this line to the running command (a login code, for example)" if term else
                                 "Claude is answering: wait, or press Stop")
        if not has:
            self.btn_send.setToolTip("Open or create a study to chat with Claude about it")
        self.btn_stop.setEnabled(self.busy or term)
        self.btn_stop.setToolTip("Stop the running command" if term else "Stop Claude's answer" if self.busy else
                                 "Nothing to stop: Claude is not answering and no command is running")
        self.btn_code.setEnabled(has)
        self.btn_code.setToolTip("Open Claude Code in a terminal window, in this study's folder (it finds a CLAUDE.md and the workbench tools there)"
                                 if has else "Open or create a study first: Claude Code starts in the study's folder")
        self.btn_new.setEnabled(has)
        self.btn_login.setEnabled(not term)
        self.btn_logout.setEnabled(not term)

    def _chips(self, kind: str):
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, text in enumerate(SUGGESTIONS[kind][:3]):
            b = wid(QPushButton(text), f"assistant.chip.{i}")
            b.setObjectName("chip")
            b.setToolTip(text)
            b.clicked.connect(lambda _=False, t=text: self.ask(t))
            self.chips.addWidget(b)

    # ------------------------------------------------------------------ the claude command line (/login /status ...)
    def term_busy(self) -> bool:
        return self.bridge.term_running(self.pid)

    def command(self, text: str):
        """A line that starts with /: run it as a claude command in the study's folder, show what it prints in the conversation."""
        if not self.pid:
            return
        kind, line = agent.slash_command(text)
        self._add("term", f"› {text}")
        if kind == "help":
            self._term_append(agent.TERM_HELP)
            self._save()
            return
        if kind == "unknown":
            self._term_append("Unknown command. Type /help")
            return
        try:
            self.bridge.term_run(self.pid, line)
        except Exception as e:  # noqa: BLE001
            self._term_append(str(e))
            self.notice.emit(str(e), "error")
        self._enable()
        self.status.setText("Running the command…")

    def _term_append(self, text: str):
        """Output for the last command line of the conversation."""
        msgs = self.log.setdefault(self.pid or "", [])
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i][0] == "term":
                msgs[i] = ("term", msgs[i][1] + "\n" + text)
                break
        self._render()

    def _term_out(self, pid: str, text: str):
        if pid == self.pid:
            self._term_append(text)

    def _term_done(self, pid: str, code: int, auth: bool):
        if pid == self.pid:
            self._term_append(f"[exit {code}]")
            self.status.setText("")
            self._save(pid)
        if auth:                                   # (the assistant reconnects with the new login)
            self.bridge.reset(pid)
            self.bridge.check_login()
        self._enable()

    def open_claude_code(self):
        """Claude Code (the interactive command line) in a terminal window, started in the study's folder."""
        if not self.pid:
            return
        try:
            import os

            from .. import project as P
            pr = P.open_project(self.pid)
            P.write_claude_files(pr.dir, int(os.environ.get("SIMWB_PORT", "8765")))
            msg = agent.open_in_claude_code(pr.dir)
        except Exception as e:  # noqa: BLE001
            self.notice.emit(f"Cannot open Claude Code: {e}", "error")
            return
        self.status.setText(msg)
        self.notice.emit(msg, "info")

    def stop(self):
        """Stop the answer (or the running command)."""
        if not self.pid:
            return
        if self.term_busy():
            self.bridge.term_stop(self.pid)
            self.status.setText("Stopping the command…")
        elif self.busy:
            self.bridge.stop(self.pid)
            self.status.setText("Stopping…")

    # ------------------------------------------------------------------ conversation
    def _send_clicked(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        if self.pid and self.term_busy():           # (the input of the running command: a login code)
            self.input.clear()
            self.bridge.term_write(self.pid, text)
            self._term_append(text)
            return
        if text.startswith("/"):
            self.input.clear()
            self.command(text)
            return
        if self.busy:
            return
        self.input.clear()
        self.ask(text)

    def ask(self, text: str):
        if not self.pid or self.busy:
            return
        history = [{"role": r, "text": t[:2000]} for r, t in self.log.get(self.pid, []) if r in ("user", "assistant")][-20:]
        self._add("user", text)
        self.busy = True
        self.status.setText("Claude is working…")
        self._enable()
        ctx = f'[GUI context: user is on the "{self.step}" step] ' if self.step != "any" else ""
        self.bridge.ask(self.pid, ctx + text, history if history and self.pid not in self.bridge.sessions else None)

    def new_chat(self):
        if self.pid:
            self.bridge.reset(self.pid)
            self.log[self.pid] = []
            self._save()
        self.busy = False
        self._tool_at = {k: v for k, v in self._tool_at.items() if k[0] != self.pid}
        self.status.setText("")
        self._render()
        self._enable()

    def _add(self, role: str, text: str, pid: str | None = None):
        pid = pid or self.pid
        if pid:
            self.log.setdefault(pid, []).append((role, text))
            self._save(pid)
            if pid == self.pid:
                self._render()

    def _on_event(self, ev: dict):
        pid = ev.get("project") or self.pid
        if not pid:
            return
        here = pid == self.pid
        say = self.status.setText if here else (lambda _t: None)
        t = ev.get("type")
        if t == "chat_status":
            say(ev.get("text", ""))
        elif t == "chat_text":
            say("")
            self._add("assistant", ev.get("text", ""), pid)
        elif t == "chat_tool":
            name = ev.get("name") or ""
            say(f"Using {TOOL_LABELS.get(name, name)}…")
            self._add("tool", TOOL_LABELS.get(name, name), pid)
            self._tool_at[(pid, name)] = len(self.log.get(pid, [])) - 1
        elif t == "chat_tool_result":
            name = ev.get("name", "")
            i = self._tool_at.pop((pid, name), None)
            msgs = self.log.get(pid, [])
            if i is not None and i < len(msgs) and msgs[i][0] == "tool":
                ok = bool(ev.get("ok"))
                extra = "" if ok else " – " + str(ev.get("preview") or "").replace("ERROR: ", "")[:200]
                msgs[i] = ("tool", msgs[i][1] + extra + (" ✓" if ok else " !"))
                self._save(pid)
                if here:
                    self._render()
            if here:
                self.toolDone.emit(name, bool(ev.get("ok")))
        elif t == "chat_done":
            self._busy[pid] = False
            c = ev.get("cost_usd")
            say(f"Done{f' (cost about ${c:.3f})' if isinstance(c, (int, float)) else ''}")
            self._enable()
        elif t == "chat_error":
            self._busy[pid] = False
            self._add("error", ev.get("text", "Something went wrong"), pid)
            say("")
            self._enable()
            self.bridge.check_login()

    def _render(self):
        parts = []
        for role, text in self.log.get(self.pid or "", []):
            if role == "user":
                parts.append(f"**You**\n\n{text}")
            elif role == "assistant":
                parts.append(f"**Claude**\n\n{text}")
            elif role == "tool":
                parts.append(f"*↳ {text}*")
            elif role == "term":
                parts.append("```\n" + text.replace("```", "'''") + "\n```")
            else:
                parts.append(f"**⚠ {text}**")
        self.view.setMarkdown("\n\n".join(parts))
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())
