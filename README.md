# Polyglot Agent

A local computer-use assistant for Windows that you can talk to in any language.

- **Main agent:** `claude-opus-5-5` at **high** effort, with adaptive thinking (summaries shown in the UI).
- **Your language, chosen by you:** on first launch you pick your language from a list. The app never detects or guesses it from your text, browser or Windows settings. Change it any time in the top bar.
- **Translation layer:** `claude-haiku-5-5` translates your message into English before the agent sees it, and translates the agent's English reply back into your language while it streams, one paragraph at a time.
- **Tools:** computer control (`computer_toolset_20260801`: screenshots, zoom, mouse, keyboard), PowerShell, file view/edit, web search, and a persistent **memory** (`memory_20250818`).
- **Memory:** the agent saves what it learns about you and your projects (PCB boards, codebases, lab setups, preferences) as it works. After each request, a background reviewer also updates `/memories`. You can browse and edit everything in the **Memory** panel. Memory is stored in English; click **Translate** there to read it in your language (read-only, cached; **Show original** to edit). **Tidy up** has an AI pass merge duplicates and drop outdated lines; **Clear all** deletes every memory file. Both save a backup first (in `data/memories-backups`, which the agent can't see), and **Undo** puts it back.
- **Projects and chats:** each chat is either inside a project or a random chat. A project has a name, optional instructions for the agent, and a list of folders the agent may read. Chats are saved and can be reopened from the sidebar.
- **Folder permission:** the first time the agent reads a file in a new folder, you choose *Allow this folder* (you can edit the folder, for example widening it to the project root), *Just this file* or *Deny*. After that, reads in that folder never ask again, in this chat and in every later chat of the same project.
- **Token usage:** the header meter shows how much of the context window is used and how much is left. Click it for a per-chat breakdown (agent, translator and memory review: input, cache hits, output, estimated cost). A one-line summary also follows every request.

```
 you (中文 / 日本語 / Español / …)
        │  Haiku: to English (code, paths and literal strings kept as-is)
        ▼
 Opus 5.5 agent ── thinks, acts and writes memory in English only
        │  streamed English reply
        ▼
 Haiku: back to your language, paragraph by paragraph ──► UI
```

Because the agent only ever sees English, its reasoning, tool calls and memory stay in one language. The agent is not told that a translation layer exists or which language you use: from its side, you simply write English. The UI still shows you both versions: click **Show English** under any reply, or turn on *Show English originals* in Settings.

### Prompt cache stays intact

Translation happens only at the edges. The agent's transcript is append-only, and the translator never edits or re-sends it. Each translator call contains just the one message being translated. Within a chat, the system prompt, the tool list and the thinking mode are frozen at the first request and saved with the chat. Changing language, toggling settings or reopening the chat after a restart therefore doesn't change the cached prefix. `tests/test_cache_and_projects.py` checks this byte for byte. Changing the model or effort mid-chat does start a new cache.

### What the agent receives from your PC

- **System prompt:** today's date, the project's name, instructions and readable folders, and a memory snapshot. Nothing about your language or the translation layer. It contains no OS version, hardware or screen details.
- **Screen-sharing switch** (above the message box): **Active app** (the default) or **Full screen**. If the agent needs the full screen while you're on Active app, it has to ask and you approve or deny. Switching back to Active app never asks.
- **Screenshots (default):** only the focused window, plus any menu or dropdown it has open. The taskbar is never included. When the chat window itself is focused, no image is sent at all. The agent switches apps with a small `windows` tool (list/focus) instead of screenshotting the taskbar. *Screen* mode (in Settings, or requested by the agent for drag-and-drop) captures the whole screen minus the taskbar.
- **Files:** only after you allow the folder, as described above.
- **PowerShell output:** only for commands you approve (in the default *ask* mode).

## Quick start (Windows)

1. Unzip the folder anywhere, for example `Documents\polyglot-agent`.
2. Double-click **`PolyglotAgent.exe`**.
   - **First launch only:** a console window installs the app's components into a private `.venv` folder, which takes about a minute. This needs Python 3.10 or newer, and the exe finds it automatically (python.org install, `py` launcher, PATH, or Anaconda/Miniconda). If none is found, it offers to open the Python download page.
   - **Every launch after that:** no console. The app opens in its own window (Edge or Chrome app mode, no tabs or address bar).
   - Closing the window quits the app. Launching it again while it's open brings the existing window to the front.
3. Choose your language in the picker that appears (you can't send messages until you do).
4. Open ⚙ Settings and paste your API key from https://platform.claude.com. Alternatively, set the `ANTHROPIC_API_KEY` environment variable before launching.

Windows SmartScreen may warn the first time, because the exe isn't code-signed: click **More info → Run anyway**. You can avoid that by right-clicking the zip → **Properties** → **Unblock** before you unzip it.

Keep `PolyglotAgent.exe` inside the folder: it runs the app files next to it. To create a desktop shortcut, right-click → *Send to* → *Desktop*. If something breaks after an update, delete the `.venv` folder and launch again to rebuild it.

### Standalone exe (optional, no Python needed)

To get a single `PolyglotAgent.exe` you can copy to any PC without Python, build it once on a Windows machine that has Python:

```bat
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
```

The result is `dist\PolyglotAgent.exe`, roughly 40–60 MB. The standalone exe keeps its data in `%LOCALAPPDATA%\PolyglotAgent`. You can also push this folder to GitHub and run the included **build-exe** Actions workflow, which builds the exe on a Windows runner.

### For developers

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m app.main          REM browser tab + console logs
.venv\Scripts\pythonw -m app.desktop      REM same as the exe
```

Your language is the one you chose in the top bar; the translator is told that language and never detects it. Replies come back in that language.

## Safety: it controls your real mouse and keyboard

- **Stop** button: halts the current action immediately, including mid-typing and mid-scroll.
- **Fail-safe:** move the mouse into any screen corner and the next action aborts.
- **PowerShell commands** and **file edits** need your approval by default (*ask*). You can set them to *auto* or *off*.
- *Ask before every mouse/keyboard action* gives you step-by-step approval.
- The agent is told to ask before anything consequential (sending, buying, deleting, installing, entering passwords), and to treat text on screen as data, not instructions. Anthropic's prompt-injection classifiers also scan screenshots.
- The server listens on 127.0.0.1 only. Each launch generates a random token, and the WebSocket rejects other origins, so other websites can't drive it.
- Windows blocks input into elevated (admin) windows and the UAC prompt unless this app is also run as administrator. Don't run it as administrator unless you need to.

## Settings worth knowing

| Setting | Default | Notes |
|---|---|---|
| Model / Effort | `claude-opus-5-5` / `high` | Opus 5.5 defaults to *medium* when effort is omitted, so this app sets *high* explicitly. Use *xhigh* or *max* for hard tasks. |
| Translator | `claude-haiku-5-5`, effort `low`, thinking off | Fast and cheap. Use `claude-sonnet-5-5` for harder technical prose. |
| Screenshot long edge | 1568 px | Within the toolset limits (≤ 2000 px per side, ≤ 4784 visual tokens). Coordinates are scaled back to physical pixels. The agent can `zoom` to read small text. |
| Screenshots | window | *window* = focused window only; *screen* = whole screen. The taskbar is excluded in both. |
| Monitor | 1 | Which display to control. 1 is the primary. |
| Clear old tool results after | 100k input tokens | Server-side context editing removes old screenshots. Pruning on the client would invalidate Opus 5.5 thinking blocks. Memory results are never cleared. |
| Memory reviewer | `claude-haiku-5-5` | Runs after each request. Turn it off to save tokens. |

Tool and system-prompt changes take effect in the **next new chat**. Model and effort changes take effect immediately.

## Files

```
PolyglotAgent.exe       windowed launcher (first run sets up .venv, then starts the app silently)
launcher/               launcher source (C, built with MinGW: launcher/build.sh) and app icon
tools/bootstrap.py      first-run setup used by the launcher
tools/build_exe.ps1     builds the standalone single-file exe (PolyglotAgent.spec, PyInstaller)
app/desktop.py          desktop entry: single instance, own app window, quits when closed
app/main.py             developer entry (browser tab, console logs)
app/server.py           FastAPI + WebSocket, token/origin checks, settings & memory REST
app/agent.py            session: translate in → Opus loop with tools → translate out → memory pass,
                        folder permissions, token accounting, chat persistence
app/store.py            projects and chats on disk (exact transcript, so resumed chats hit the cache)
app/api.py              raw streaming Messages API client (keeps thinking/toolset blocks byte-exact)
app/translator.py       Haiku translator + paragraph-streaming reply translation
app/computer.py         Windows controller (mss screenshots, pyautogui, SendInput Unicode typing/wheel)
app/keys.py             xdotool-style key names → Windows keys
app/memory_store.py     /memories backend with path-traversal protection
app/local_tools.py      PowerShell + text editor tools
app/prompts.py          agent / translator / memory-review prompts
app/static/             web UI (vendored marked + DOMPurify, works offline)
data/                   settings.json, memories/, projects/, chats/ (created at runtime)
tests/                  pytest suite (mocked API and computer)
```

Run the tests with `pip install pytest` and then `python -m pytest -q`.

## Version control

The project is a git repository. `main` holds released states, tagged `v0.1.0`, `v0.2.0`, …; new work goes on a `feat/…` or `fix/…` branch and is merged into `main` when it passes the tests. Changes are listed in [CHANGELOG.md](CHANGELOG.md). Runtime data (`data/`, which holds your API key, chats and memory) and `.venv/` are in `.gitignore` and never committed.

## Notes and limits

- Logs from the windowed app go to `data/app.log`.
- Chats are saved in `data/chats/` with their full transcript, including screenshots the agent took, so they can be resumed. Deleting a chat in the sidebar removes its file. Deleting a project keeps its chats as random chats.
- The cost shown in the header is an estimate based on list prices.
- Unicode typing works in normal Windows apps. A few games and remote-desktop windows ignore Unicode input. For those, ask the agent to put the text on the clipboard with PowerShell (`Set-Clipboard`) and paste it with ctrl+v.
- Web search results include source links under the reply.
