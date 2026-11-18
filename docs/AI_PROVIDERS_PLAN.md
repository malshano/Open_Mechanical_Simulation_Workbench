---
title: Plan - other AI providers (API keys, Ollama) with a sandbox
purpose: Step-by-step plan to implement yourself. Each step names the files, what to write, how to test it, and when it is done.
---

# Other AI providers with a sandbox: step-by-step plan

Goal: besides Claude (Agent SDK, `simwb/agent.py`), the side-panel chat can use **Ollama** (local models) and any
**API-key provider** (OpenAI, Gemini, Mistral, Groq, OpenRouter, ...), and the model can only touch the study folder
it was opened for.

## 0. What the code does today (read this first)

| Piece | Where | What matters for you |
|---|---|---|
| Chat session | `simwb/agent.py` `ChatSession` | One per study per websocket. `ask(text, history)` streams events through `send`. Claude-specific: `ClaudeSDKClient`, `create_sdk_mcp_server`. |
| Tools | `simwb/tools.py` `TOOLS` = `(name, description, JSON schema, handler)` | Provider-neutral already. `call(name, project, args)` validates the arguments and returns text (`"ERROR: ..."` on failure). |
| Events to the page | `chat_status`, `chat_text`, `chat_tool`, `chat_tool_result`, `chat_done`, `chat_error` | The page (`app.js`, `chat_*`) renders these; a new provider only has to emit the same events. |
| Websocket | `simwb/server.py` `/ws` | Creates `agent.ChatSession(pid, psend)` on the first `chat` message. This is where the provider is chosen. |
| System prompt | `tools.SYSTEM_PROMPT` | Shared by all providers. |
| HTTP client | `httpx` is already in `requirements.txt` | Enough for Ollama and every OpenAI-compatible API. No new dependency needed. |

Design in one sentence: **keep `ChatSession` as the Claude implementation, add a small `Provider` interface that
turns "messages + tools" into "text or tool calls", and write one generic tool-calling loop that works for every
provider.** The sandbox is not the model's good behaviour but what the tool list lets it do.

## 1. The sandbox: three layers

**Layer A - capability (the one that matters).** The model can only call what is in the tool list it is given. Today
there is no shell and no file tool, which is the strongest sandbox there is. Keep it that way: do not add "read
file" or "run command" tools. If you ever need file access, see step 7.

**Layer B - scope.** Even workbench tools reach outside the open study. Found in the code:

| Tool | Problem for a restricted model | Fix (step 2) |
|---|---|---|
| `import_step(step_path)`, `create_project(step_path)` | Takes **any path on the disk** from the model: it can copy any `.step/.stl/.obj` the user can read into a study. | Allow only paths inside the study folder (or the bundled examples). |
| `list_projects`, `create_project`, `rename_project` | Reach other studies / create new ones in the workspace. | Not offered to restricted providers. |
| `install_openfoam` | Downloads and installs software. | Not offered; or ask for confirmation. |
| `run_simulation`, `cancel_job`, `delete_bc` | Long or destructive. | Confirmation for non-Claude providers (step 6). |

**Layer C - data and keys.**
- Anything the model sees (part names, face names, results) goes to the provider. With Ollama it stays on the
  machine; with a cloud API it does not: show which one is active and ask once per provider.
- **API keys never go in the study folder or `project.json`.** The study folder is exactly what you are
  sandboxing to; a key stored there is readable by anything that gets file access later, and travels with a copied
  study. Store keys in the OS credential store (step 1).
- Part names, face names and STEP text are untrusted input (prompt injection): treat model output that follows
  "instructions" found in a tool result with suspicion, which is what the confirmation in step 6 is for.

## 2. Step 1 - provider settings and key storage

Files: new `simwb/providers/__init__.py`, `simwb/providers/config.py`; `requirements.txt` (`keyring>=25`).

1. Provider table (code, not a file the user edits):

   ```python
   PROVIDERS = {
       "claude":     {"label": "Claude (Claude Code login)", "kind": "claude"},
       "ollama":     {"label": "Ollama (local)", "kind": "ollama", "base_url": "http://127.0.0.1:11434"},
       "openai":     {"label": "OpenAI", "kind": "openai", "base_url": "https://api.openai.com/v1", "key": True},
       "openrouter": {"label": "OpenRouter", "kind": "openai", "base_url": "https://openrouter.ai/api/v1", "key": True},
       "gemini":     {"label": "Gemini", "kind": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "key": True},
       "custom":     {"label": "Other (OpenAI-compatible)", "kind": "openai", "key": True},   # user types base_url
   }
   ```
   Most cloud providers speak the OpenAI chat-completions format, so **two implementations cover everything**:
   `ollama` and `openai` (+ the existing Claude one). Verify each provider's base URL in its docs when you add it.

2. Non-secret settings (provider, model, base_url) in `~/.simwb/ai.json` (user level, **not** per study).
3. Keys with `keyring.set_password("simwb", provider, key)` / `get_password`. On Windows this is the Credential
   Manager. Fallback if keyring has no backend: environment variable (`OPENAI_API_KEY` ...), never a plain file in
   the workbench or study folder.
4. Never log a key, never return it from an API route (only `{"has_key": true}`), never put it in an exception text.

Test: `tests/test_providers.py` - save a key with a fake keyring backend (`keyring.backends.null` /
an in-memory class), read it back, check that `ai.json` and the response of the settings route do not contain it.

## 3. Step 2 - make the tool layer restricted-safe

Files: `simwb/tools.py`; test in `tests/test_api.py`.

1. Add `SANDBOX_TOOLS`: the tools that act on the open study only (everything except `list_projects`,
   `create_project`, `rename_project`, `install_openfoam`, and the MCP-only extras). Provide
   `tools_for(provider_kind)`; Claude keeps the full list.
2. Path guard used by `import_step` when called by a restricted provider:

   ```python
   def inside(base: Path, p: str | Path) -> Path:
       base = base.resolve()
       q = (base / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()
       if q != base and base not in q.parents:          # resolve() follows symlinks and '..'
           raise ValueError(f"Only files inside the study folder can be used: {q}")
       return q
   ```
   Call it with `pr.dir`. Note `Path.resolve()` handles `..` and symlinks; on Windows also reject UNC paths
   (`str(q).startswith("\\\\")`) and drive-relative forms. Check the extension list (`.step .stp .stl .obj`) after
   resolving, and a size cap (e.g. 200 MB).
3. Pass a context to `call(name, pr, args, restricted=True)` so handlers can apply the guard.

Test: ask `import_step` for `..\\..\\Windows\\x.step`, an absolute path outside, a symlink pointing out
(skip on Windows without privilege), and a valid file in the study folder; only the last works.

## 4. Step 3 - provider interface and the generic tool loop

Files: `simwb/providers/base.py`, `simwb/providers/loop.py`.

```python
@dataclass
class Reply:
    text: str                      # assistant text (may be empty)
    tool_calls: list[dict]         # [{"id": str, "name": str, "args": dict}]

class Provider(Protocol):
    async def chat(self, messages: list[dict], tools: list[dict], system: str) -> Reply: ...
```

The loop (one place, used by every non-Claude provider):

```python
async def run_turn(provider, messages, tools, system, project, send, restricted, max_steps=12, confirm=None):
    for step in range(max_steps):                         # a hard cap: a weak model may loop forever
        reply = await provider.chat(messages, tools, system)
        if reply.text:
            await send({"type": "chat_text", "text": reply.text})
        if not reply.tool_calls:
            return
        messages.append(assistant_message(reply))         # provider-specific shape: see steps 4 and 5
        for tc in reply.tool_calls:
            await send({"type": "chat_tool", "name": tc["name"], "input": tc["args"]})
            if confirm and needs_confirmation(tc["name"]) and not await confirm(tc):
                text = "ERROR: the user declined this action"
            else:
                text = await asyncio.to_thread(call, tc["name"], project, tc["args"], restricted=True)
            await send({"type": "chat_tool_result", "name": tc["name"], "ok": not text.startswith("ERROR"), "preview": text[:300]})
            messages.append(tool_message(tc, text[:20000]))   # cap what goes back into the context
    await send({"type": "chat_text", "text": "(stopped: too many steps in one turn)"})
```

Rules: always cap steps; cap tool-result size (small models have small context); a tool error is returned to the
model as text, never raised (`call` already does this); validate with `call`'s own argument checks - local models
often invent argument names, and the error message that lists the allowed ones lets them correct themselves.

Test: a `FakeProvider` that returns scripted replies (tool call, then text) - assert the events emitted, that
the step cap stops a provider that always asks for a tool, and that a declined confirmation gives the error text.

## 5. Step 4 - Ollama provider

File: `simwb/providers/ollama.py`. Ollama runs on `http://127.0.0.1:11434` once installed (`ollama serve`; the
desktop app starts it).

- Request: `POST /api/chat` with `{"model": m, "messages": [...], "tools": [...], "stream": false}`.
- Tool definition format (from the workbench tuple):
  `{"type": "function", "function": {"name": name, "description": desc, "parameters": schema}}`.
- Response: `message.content` (text) and `message.tool_calls = [{"function": {"name": ..., "arguments": {...}}}]`
  (`arguments` is already an object here, unlike OpenAI where it is a JSON string).
- Send the result back as `{"role": "tool", "tool_name": name, "content": text}` after appending the assistant
  message that contained the calls. Check the current Ollama API docs for the exact field names of your version.
- List models with `GET /api/tags` for a dropdown; check tool support with `POST /api/show` (`capabilities`
  contains `"tools"` on recent versions) and tell the user when a model cannot call tools.
- Use `httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5))`: the first call loads the model and can take
  minutes. A connect failure means "Ollama is not running": say that in plain words with the start command.
- Context size: set `"options": {"num_ctx": 16384}` - the default is small, and this prompt plus 40 tool
  definitions is already several thousand tokens. This is the most common reason a local model "ignores" tools.
- Model advice for the settings text: use a model with tool support (Qwen 2.5/3 7B or larger, Llama 3.1 8B or larger,
  Mistral Nemo, ...). Expect small models to fail on multi-step setups: that is why steps 6 and 8 exist.
- Start with `stream: false`. Add streaming later (`stream: true` gives newline-delimited JSON) once the loop works.

Test: with `httpx.MockTransport` return a canned tool-call response, then a text response; assert `Reply`.
Manual test with a real model: "show me the project state", then "add a fixed support on the bottom face".

## 6. Step 5 - OpenAI-compatible provider

File: `simwb/providers/openai_compat.py`.

- `POST {base_url}/chat/completions`, header `Authorization: Bearer <key>`,
  body `{"model", "messages", "tools", "tool_choice": "auto"}`.
- Tools: same `{"type": "function", "function": {...}}` shape.
- Reply: `choices[0].message.content` and `.tool_calls = [{"id", "function": {"name", "arguments": "<JSON string>"}}]`:
  `json.loads` the arguments and **handle invalid JSON** (return an error text to the model).
- The system prompt goes as the first message, `{"role": "system", ...}`.
- Tool results: `{"role": "tool", "tool_call_id": id, "content": text}`; the assistant message with `tool_calls`
  must precede them.
- The same class serves Ollama's OpenAI-compatible endpoint (`http://127.0.0.1:11434/v1`, any key), which gives you
  a second way to test without a cloud account.
- Errors: 401 = wrong key, 429 = rate limit / no credit, 404 = wrong model name: translate each to a sentence.
- Show the user what a turn costs only where the API returns `usage`; do not guess prices.

## 7. Step 6 - wire it into the chat (server and page)

Files: `simwb/agent.py`, `simwb/server.py`, `simwb/web/app.js`, `simwb/web/index.html`.

1. `agent.py`: add `ProviderChatSession` next to `ChatSession`, same public methods (`ask`, `interrupt`, `close`).
   It keeps `self.messages` (the conversation), builds the system prompt exactly like `_connect` does, and calls
   `run_turn`. `interrupt` cancels the running task. The page's `history` argument seeds `messages` after a reload.
2. `server.py` `/ws`: when the page sends `chat`, include `provider`; create `ChatSession` for `claude`, else
   `ProviderChatSession`. Add routes: `GET /api/ai` (providers, active one, models, `has_key` flags - no keys),
   `POST /api/ai` (choose provider/model/base_url), `POST /api/ai/key` (save a key), `DELETE /api/ai/key/{provider}`.
   Mutating routes already need `X-SimWB`.
3. Page: a provider dropdown and model field in the chat header, a "Key..." button that stores a key through the
   route (a password input; clear it after saving), and a badge "stays on this computer" (Ollama) or "sent to
   <provider>" (cloud) with a one-time consent dialog per cloud provider.
4. Confirmation: add a `chat_confirm` event (`{id, name, input}`) and a websocket reply `chat_confirm_reply`
   `{id, ok}`; the loop awaits a future that the websocket handler resolves. Needs confirmation (for non-Claude
   providers): `run_simulation`, `generate_mesh` on big meshes, `cancel_job`, `delete_bc`, `import_step`. Show the
   arguments in the dialog.
5. `chat_error` texts must stay plain sentences ("Ollama is not running: start it with `ollama serve`").

Test: websocket test (like `test_websocket_rejects_foreign_origin`) with a fake provider: send `chat`, receive
`chat_text`; send a tool call that needs confirmation, receive `chat_confirm`, reply `ok: false`.

## 8. Step 7 - only if you really need file access (optional, do last)

Prefer not to. If a model must read the study's own files (for example a solver log):

1. One read-only tool `read_study_file(relative_path)` using the `inside()` guard of step 2, an extension allow-list
   (`.log .txt .json .md .csv`), a size cap (100 kB, with the tail for logs), and a deny-list for
   `project.json` keys that could hold paths or tokens (there should be none: keep it so).
2. No write tool. Results of the model's work go through the existing tools, which validate their input.
3. Never expose the user's home folder, the workbench folder, or `~/.simwb` (keys' non-secret settings).
4. OS-level isolation (a subprocess in a restricted token / Windows Job Object / container) is the next level, but
   it is a project of its own; with no shell and no write tool it is not needed.

## 9. Step 8 - quality of local models (what to expect, what to add)

- Small models pick wrong tools or invent arguments. Mitigations that fit this code: the argument errors from
  `call` (they list what is allowed); a shorter system prompt variant for local models; offering fewer tools
  (`tools_for` by study type); a "plan" tool-free first turn is not worth the complexity.
- Add a per-provider "tool mode": `auto` (tools) or `off` (plain chat that can still explain results: the app
  inserts `summary_text()` into the prompt). A model without tool support still gives useful explanations.
- Keep a log (`~/.simwb/ai.log`, no keys, truncated results) for debugging model behaviour.

## 10. Test checklist (all without network)

| Test | Proves |
|---|---|
| Fake keyring | Keys stored and read; not in `ai.json`, not in any API response, not in a study folder. |
| `inside()` | `..`, absolute paths, symlinks and UNC paths are refused; a file in the study works. |
| `tools_for` | Restricted providers do not get `list_projects`, `create_project`, `rename_project`, `install_openfoam`. |
| Fake provider loop | Events in order; step cap; result-size cap; declined confirmation. |
| Ollama / OpenAI `MockTransport` | Request bodies (tool format, system message, `tool_call_id`), reply parsing, invalid JSON arguments, 401 / 429 / 404 / connection refused texts. |
| Websocket | Provider chosen per message; `chat_stop` cancels; foreign origin refused (exists). |
| Manual | Ollama with a tool-capable model: project state, add a support, run a small steady study. |

## 11. Suggested order and size

1. Steps 1-2 (settings, keys, restricted tools): about a day. Nothing visible, everything testable.
2. Steps 3-4 (loop, Ollama): about a day. First end-to-end chat with a local model from a script.
3. Step 5 (OpenAI-compatible): half a day. Check with Ollama's `/v1` endpoint first, then a real key.
4. Step 6 (server + page + confirmation): one to two days. This is most of the UI work.
5. Steps 7-8 (optional file tool, local-model quality): as needed.

Do not start with the page: get `python -c` with `OllamaProvider` + `run_turn` + the real `TOOLS` calling
`get_project_state` on a test study working first. Everything after that is wiring.
