# Changelog

All notable changes to Quorum. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-15

First production release.

### Added
- Causal memory on FalkorDB: quarantine a poisoned fact and all of its descendants, with restore and supersede.
- Four-layer defense: quorum gate, atomic execute guard, quarantine cascade, and damage control with outbox-driven compensation.
- Embedded MCP server with per-person and per-agent tokens, rate limits, and a plain-English audit log with CSV export.
- Console: labeled navigation on every tab, live **AI at work** feed, in-app **Guide** (press `G`) with an A to Z glossary, plain-language status chips, a labeled phone tab bar.
- GitHub Actions CI: frontend `verify` (types, lint, 114 tests, contrast, contract check, builds) and backend ruff, mypy and pytest against a real FalkorDB.
- Project docs: documentation index, contributing guide, security policy, archived planning notes under `docs/archive/`.

### Fixed
- Removed unused imports and placeholder-less f-strings so `ruff check .` passes repo-wide.
- Header no longer overflows or overlaps at 1440px; phone navigation labels are readable.

### Known limits
- Supported deployment is one API writer process with embedded MCP. Separate API workers or standalone MCP writers are unsupported (see [docs/AUDIT_REMEDIATION.md](docs/AUDIT_REMEDIATION.md)).
