# Changelog

All notable changes to this project are documented here. Format: Keep a Changelog.

## [Unreleased]

## [0.2.0] - 2026-04-21

### BREAKING CHANGES

- All action endpoints (`/dom`, `/execute`, `/click`, `/type`, `/select`) now require a `tab` parameter in the form `br-XXXX:N` (query param on GET, body field on POST).
- All CLI action commands (`dom`, `click`, `type`, `select`, `exec`) now require a `--tab br-XXXX:N` flag.
- `SaidkickClient` action methods now require `tab` as their first argument.
- Extension ↔ server protocol adds a `HELLO` handshake frame; the extension must be reinstalled from this version of the repo.
- The old tab-selection heuristic inside `background.js` (active tab → localhost:8000/8088 → first non-chrome tab) has been removed.

### Features

- `GET /tabs` endpoint aggregates tabs across all connected browsers. Optional `?active=true` filter.
- `saidkick tabs` CLI command; `--active` filter for the currently-focused tab.
- Multi-browser support: server assigns an ephemeral `br-XXXX` ID on each WS connection and tracks them in a dict keyed by `browser_id`.
- `/console` and `saidkick logs` support a `browser` / `--browser` filter. Every stored log entry is stamped with its source `browser_id`.

## [0.1.0]

- Initial release: FastAPI server, Chrome MV3 extension, Typer CLI, Python client for remote browser inspection and automation.
