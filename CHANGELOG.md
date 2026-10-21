# Changelog

All notable changes to Polyglot Agent. Versions follow [Semantic Versioning](https://semver.org/).

## [0.8.0] - 2026-10-20

### Added
- **Mac app.** "Polyglot Agent.app" in a `.dmg` (drag to Applications), for Apple Silicon and Intel Macs:
  no Python or Terminal needed, and it opens in its own window (WebKit, through pywebview). The `.dmg`
  includes an "Open me first" guide for the one-time approval macOS asks for, since the app isn't
  registered with Apple. Data defaults to `~/Library/Application Support/PolyglotAgent` (asked on first start).
  Built with `PolyglotAgent-mac.spec` / `tools/build_mac.sh` on GitHub's Mac machines.
- **GitHub workflow builds and checks every download.** On `feat/`/`fix/` pushes and version tags it builds
  the Windows exe, the Mac apps and the Python pack, then starts each one, checks it answers and quits it
  (`tools/smoke_test.py`); the pack is started on Ubuntu and macOS. A version tag publishes a GitHub release
  with all files and a "which file do I need" note.
- **Python pack (Linux, or any computer with Python).** `python tools/build_pack.py` builds `dist/PolyglotAgent-<version>-python.zip`,
  which runs on any computer with Python 3.10+: `start.command` (macOS, double-click), `./start.sh` (Linux)
  or `start.bat` (Windows). `start.py` sets up a private `.venv` on the first run and reinstalls only when
  `requirements.txt` changes; data stays in `data/` inside the folder.
- On macOS and Linux the agent runs commands with bash through a `shell` tool (same approval prompts),
  and its prompt names the right system. Stopping a command ends everything it started.
- Folder picker on macOS (the system's "choose folder" window) and Linux (zenity, kdialog, then Tk). Without
  any of them, the app asks for the path instead.
- On macOS the app opens in its own window (pywebview is now installed there too); on Linux in a Chrome,
  Edge or Chromium app window when one is installed, otherwise a browser tab. A file lock keeps it to one
  copy at a time; Settings → Data folder → Open uses Finder or
  the file manager.

### Changed
- Computer control is Windows-only and greyed out elsewhere; the screen settings are hidden there.
- `mss` and `pyautogui` are installed on Windows only, `pywebview` on Windows and macOS.
- First-run setup can put pip into an environment created without it (e.g. Ubuntu without `python3-venv`),
  using the system's pip, and explains what to install when that isn't possible.
- Settings no longer saves fields that are greyed out, so a data folder shared between a Mac and a Windows
  PC keeps the Windows computer-control setting.

## [0.7.0] - 2026-10-15

### Added
- **Choose your data folder.** On first start the standalone exe asks where to keep your memory, chats,
  projects and settings (including the API key), before the language picker. It suggests
  `%LOCALAPPDATA%\PolyglotAgent`; **Browse…** opens the Windows folder picker. A folder that already holds
  other files gets a `Polyglot Agent` folder inside it, and a folder that already holds this app's data is
  used as it is. If the chosen folder goes missing later (e.g. an unplugged drive), the app asks again.
- **Settings → Data folder** shows the folder, opens it in Explorer, and can change it: the current data is
  copied to the new folder (the old one is kept), or a folder that already has data is used as it is.
- **Folder picker instead of typed paths.** Project folders are added with **Add folder…**, and the
  "Allow reading files here?" card has **Change…** to pick a wider folder. Both open the native Windows
  folder dialog. A folder that doesn't contain the file is refused with a note.

### Changed
- **Computer control is off by default.** Turn it on in Settings → Tools (it applies to the next new
  chat). Settings saved by an earlier version keep the value they had.
- "Random chats" in the sidebar is now **Chats** (对话 in Chinese).
- **Display to control** replaces the Monitor number box: a list of the connected displays with their
  size and position (main display, on the left, on the right, above, below). It is hidden when only one
  display is connected. New settings default to the main display, found by its position rather than
  assumed to be first in Windows' list; a display that's been unplugged also falls back to it.
- Screen capture uses `mss.MSS()` where available; `mss.mss()` is deprecated in mss 10.2 and will be
  removed.
- This PC's app state (log, single-instance lock, window cache, and the note of where the data folder is)
  stays in `%LOCALAPPDATA%\PolyglotAgent` for the standalone exe, separate from your data. The folder
  version keeps everything in `data\` as before.

## [0.6.0] - 2026-10-14

### Added
- **Auto compact.** Once a chat's context reaches 150k tokens (Settings → Context), the API
  summarizes the older messages (server-side compaction, `compact-2026-01-12`) and the agent continues
  from the summary. The summary prompt asks it to keep the task state: requests, decisions, paths, results
  and next steps. A divider marks the spot in the chat; click it to read the summary (in English). The
  status pill shows "Compacting context" while it is written, and its token cost is listed as
  *Auto compact* in the usage panel. Messages before the summary are dropped from later requests, since
  the API ignores them anyway. If the model or account doesn't support compaction, the app says so once
  and continues without it.

### Changed
- **The "Claude is using your computer" border and pointer are always on.** The setting that turned
  them off is gone, and a saved "off" from an older version is ignored.
- **The Settings panel is translated.** Every label, note and option (model, effort, max tokens /
  reply, approval modes, screenshot range, and so on) follows the app language. In Chinese, effort
  levels and approval modes show Chinese names; the values sent to the API are unchanged.

## [0.5.1] - 2026-10-13

### Changed
- Scrollbars now match Claude's style: thin, transparent track, a soft thumb (the border colour at 35%,
  a little stronger while the pointer is over the area), in light and dark mode.

### Fixed
- The message box showed a scrollbar even for one line: its auto-height left out the 2 px border. It now
  grows with the text and only scrolls once it reaches 40% of the window height.
- After an update, the app window could keep running the old interface from its cache. The page now
  loads its CSS, JS and icons with a version stamp that changes whenever those files change.

## [0.5.0] - 2026-10-13

### Added
- **"Claude is using your computer" signal.** While the agent acts on the desktop, an orange border glows
  around the controlled monitor with a label at the top (in Chinese when the app language is Chinese).
- **Claude's pointer.** An orange ring with a "Claude" tag stays where the agent last pointed and pulses
  when it clicks, so its pointer can be told apart from yours.
- **Esc stops the agent.** A physical Esc press stops it like the Stop button and is not passed on to the
  app. Esc keys the agent sends itself are injected input and are ignored.
- The overlays are click-through, never take focus, are left out of the agent's window list and are
  excluded from screen capture, so the agent never sees them. Settings → Tools can turn the visuals off;
  Esc-to-stop stays on.

## [0.4.0] - 2026-10-13

### Added
- **Built-in app window (WebView2).** The app now opens in its own window on WebView2, the Chromium
  engine that ships with Windows 10/11, with its own taskbar button and icon. No Edge/Chrome needed,
  and no browser profile folder in `data/`.
- **Window choice** in Settings → Window: built-in window, Microsoft Edge window, Google Chrome window,
  or a default-browser tab (e.g. Firefox). Applies at the next launch. Edge and Chrome each get their
  own private profile. Falls back to Edge/Chrome when WebView2 or pywebview is unavailable.
- `tools/make_icons.py` and `tools/set_exe_icon.py` to regenerate the icon and update the launcher exe.

### Changed
- **Sharper icon.** Every size from 16 to 256 px is drawn on its own pixel grid, so small icons
  (taskbar, title bar, Explorer) are crisp instead of blurry. The page uses PNG favicons and the
  top-bar logo uses the same icon.
- The page is marked `notranslate`, so Chrome/Edge no longer offer to translate the app.
- The standalone exe includes the WebView2 window. `tools/build_exe.ps1` picks Python 3.13/3.12/3.14
  through the `py` launcher instead of whatever `python` is on PATH.
- New dependency: `pywebview` (the first launch after updating reinstalls components once).

## [0.3.0] - 2026-10-13

### Added
- **Memory clean-up** in the Memory panel:
  - **Tidy up**: the memory reviewer model merges duplicate facts and files, drops outdated,
    contradicted or one-off lines and any secrets, and keeps everything still useful. The result
    summary is shown in your language. If it fails part-way, memory is restored automatically.
  - **Clear all** (with a confirm click) deletes every memory file.
  - **Undo** restores memory from before the last tidy-up or clear. Backups are kept in
    `data/memories-backups` (the newest 10), outside the folder the agent can see.
  - Clean-up is refused while the agent is working or updating memory, and new messages wait while a
    tidy-up runs. Its token cost counts toward the session total.

## [0.2.2] - 2026-10-13

### Fixed
- **Reopening the app showed "127.0.0.1 refused to connect".** Closing the window could leave the old
  process alive with its server already stopped: the web server waited forever for a dropped connection
  to close. The process kept the single-instance lock, so every relaunch reopened the dead address.
  Shutdown now gives connections 3 seconds, the process is always ended (a 15-second watchdog covers
  any other hang), and a relaunch during shutdown waits for the old copy and then starts normally.
- A second launch no longer wipes `data/app.log` of the running copy.

## [0.2.1] - 2026-10-13

### Changed
- The main agent no longer knows about the translation layer. The system prompt's `<language>` section
  and the "English only" memory rule are gone, so the agent is never told that another language or a
  translator exists. A test checks that the system prompt and tool list never mention translation.
  Chats started before this keep their original prompt (it is frozen per chat for the prompt cache).

## [0.2.0] - 2026-10-13

### Added
- **One-click memory translation.** The Memory panel has a **Translate** button that shows the open
  memory file in your language (read-only Markdown). It stays on while you browse files, until you
  click **Show original**. Translations are cached, and memory files on disk stay English.
- **Language picker.** On first launch, or if your settings still say "auto", the app asks you to
  choose your language. You can't send a message until you have chosen one.
- Git repository with `.gitattributes`, this changelog and release tags.

### Changed
- The app never detects your language. The "Auto-detect" option, the English-detection heuristic
  and the translator's language detection are gone. The translator is told which language you write in.
- The interface language follows your choice instead of the browser/Windows locale.
- Settings reject unknown language names.

## [0.1.0] - 2026-10-13

- Baseline as received: Opus 5.5 agent with a Haiku translation layer, computer use, PowerShell,
  file tools, web search, persistent memory, projects and chats, token usage meter.
