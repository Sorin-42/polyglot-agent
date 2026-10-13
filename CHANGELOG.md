# Changelog

All notable changes to Polyglot Agent. Versions follow [Semantic Versioning](https://semver.org/).

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
