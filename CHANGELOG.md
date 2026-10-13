# Changelog

All notable changes to Polyglot Agent. Versions follow [Semantic Versioning](https://semver.org/).

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
