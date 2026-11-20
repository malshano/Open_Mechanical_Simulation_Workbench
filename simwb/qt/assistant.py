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
    """Runs ChatSession on a private asyncio loop (a thread of its own) and turns its events into Qt signals."""
    event = Signal(dict)
    auth = Signal(dict)

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

    def shutdown(self):
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


class AssistantPanel(QWidget):
    """The chat. toolDone is emitted when Claude has used a workbench tool, so the window can refresh what it shows."""
    toolDone = Signal(str, bool)

    def __init__(self, bridge: ChatBridge, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.bridge = bridge
        self.pid: str | None = None
        self.log: dict[str, list] = {}       # per study: [(role, text)]
        self.busy = False
        self.pending_tools: list = []
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(8)
        top = QHBoxLayout()
        self.who = wid(QLabel("Checking the Claude login…"), "assistant.login_status")
        self.who.setObjectName("muted")
        self.who.setWordWrap(True)
        self.btn_login = wid(QPushButton("Log in"), "assistant.login")
        self.btn_logout = wid(QPushButton("Log out"), "assistant.logout")
        self.btn_new = wid(QPushButton("New chat"), "assistant.new_chat")
        for b in (self.btn_login, self.btn_logout, self.btn_new):
            b.setObjectName("ghostBorder")
        top.addWidget(self.who, 1)
        top.addWidget(self.btn_login)
        top.addWidget(self.btn_logout)
        top.addWidget(self.btn_new)
        v.addLayout(top)
        self.view = wid(QTextBrowser(), "assistant.transcript")
        self.view.setOpenExternalLinks(True)
        self.view.setPlaceholderText("Ask Claude to set up, mesh, run or explain this study.")
        v.addWidget(self.view, 1)
        self.chips = FlowLayout(spacing=6)                  # (wraps: the panel need not be as wide as three chips)
        v.addLayout(self.chips)
        self.status = wid(QLabel(""), "assistant.status")
        self.status.setObjectName("muted")
        v.addWidget(self.status)
        row = QHBoxLayout()
        self.input = wid(_Input(), "assistant.input")
        self.input.setPlaceholderText("Ask Claude…  (Enter sends, Shift+Enter for a new line)")
        self.input.setFixedHeight(64)
        self.btn_send = wid(QPushButton("Send"), "assistant.send")
        self.btn_send.setObjectName("primary")
        row.addWidget(self.input, 1)
        row.addWidget(self.btn_send)
        v.addLayout(row)

        self.btn_send.clicked.connect(self._send_clicked)
        self.input.send.connect(self._send_clicked)
        self.btn_new.clicked.connect(self.new_chat)
        self.btn_login.clicked.connect(self._login)
        self.btn_logout.clicked.connect(self.bridge.logout)
        bridge.event.connect(self._on_event)
        bridge.auth.connect(self._on_auth)
        bridge.check_login()
        self._chips("any")
        self._enable()

    # ------------------------------------------------------------------ study and login
    def set_project(self, pid: str | None):
        self.pid = pid or None
        self._render()
        self._enable()

    def set_context(self, step: str):
        self._chips("results" if step == "results" else "setup" if step == "setup" else "any")

    def _on_auth(self, st: dict):
        ok = bool(st.get("loggedIn"))
        if ok:
            self.who.setText("Logged in" + (f" as {st['email']}" if st.get("email") else "")
                             + (f" ({st['subscriptionType']})" if st.get("subscriptionType") else ""))
        else:
            self.who.setText("Not logged in to Claude. Press Log in (a terminal opens), then log in in the browser.")
        self.btn_login.setVisible(not ok)
        self.btn_logout.setVisible(ok and st.get("authMethod") != "api_key")
        self._logged_in = ok
        self._enable()

    def _login(self):
        self.status.setText(self.bridge.login())
        self.bridge.check_login()

    def _enable(self):
        self.input.setEnabled(bool(self.pid))
        self.btn_send.setText("Stop" if self.busy else "Send")
        self.btn_send.setEnabled(bool(self.pid))

    def _chips(self, kind: str):
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, text in enumerate(SUGGESTIONS[kind][:3]):
            b = wid(QPushButton(text), f"assistant.chip.{i}")
            b.setObjectName("chip")
            b.clicked.connect(lambda _=False, t=text: self.ask(t))
            self.chips.addWidget(b)

    # ------------------------------------------------------------------ conversation
    def _send_clicked(self):
        if self.busy:
            self.bridge.stop(self.pid)
            return
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self.ask(text)

    def ask(self, text: str):
        if not self.pid or self.busy:
            return
        history = [{"role": r, "text": t} for r, t in self.log.get(self.pid, []) if r in ("user", "assistant")]
        self._add("user", text)
        self.busy = True
        self.status.setText("Claude is working…")
        self._enable()
        self.bridge.ask(self.pid, text, history if len(history) > 0 and self.pid not in self.bridge.sessions else None)

    def new_chat(self):
        if self.pid:
            self.bridge.reset(self.pid)
            self.log[self.pid] = []
        self.busy = False
        self.status.setText("")
        self._render()
        self._enable()

    def _add(self, role: str, text: str):
        if self.pid:
            self.log.setdefault(self.pid, []).append((role, text))
            self._render()

    def _on_event(self, ev: dict):
        if ev.get("project") not in (None, self.pid):
            return
        t = ev.get("type")
        if t == "chat_status":
            self.status.setText(ev.get("text", ""))
        elif t == "chat_text":
            self.status.setText("")
            self._add("assistant", ev.get("text", ""))
        elif t == "chat_tool":
            self.status.setText(f"Using {ev.get('name')}…")
            self._add("tool", f"{ev.get('name')}")
        elif t == "chat_tool_result":
            self.toolDone.emit(ev.get("name", ""), bool(ev.get("ok")))
        elif t == "chat_done":
            self.busy = False
            c = ev.get("cost_usd")
            self.status.setText(f"Done{f' (cost about ${c:.3f})' if isinstance(c, (int, float)) else ''}")
            self._enable()
        elif t == "chat_error":
            self.busy = False
            self._add("error", ev.get("text", "Something went wrong"))
            self.status.setText("")
            self._enable()

    def _render(self):
        parts = []
        for role, text in self.log.get(self.pid or "", []):
            if role == "user":
                parts.append(f"**You**\n\n{text}")
            elif role == "assistant":
                parts.append(f"**Claude**\n\n{text}")
            elif role == "tool":
                parts.append(f"*↳ {text}*")
            else:
                parts.append(f"**⚠ {text}**")
        self.view.setMarkdown("\n\n".join(parts))
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())
