# Changelog

All notable changes to VERONICA (control plane) are documented here.

## [0.8.1] -- 2026-03-11 -- PyPI Initial Release

First publication to PyPI as `veronica-cp`.

### Changed

- PyPI package name: `veronica` -> `veronica-cp` (the `veronica` name on PyPI belongs to an unrelated project)
- README: rewritten to reflect control plane reality, removed "Execution OS" overclaim
- All `pip install veronica[extra]` references updated to `pip install veronica-cp[extra]`
- GitHub About and pyproject.toml description aligned with control plane scope
- Python import remains `import veronica` (unchanged)

### Added

- Install section in README with namespace conflict warning
- CI: GitHub Actions publish workflow (on release -> PyPI)
- CHANGELOG.md

---

## [0.8.0] -- 2026-03-11 -- Initial Public Release

Full control-plane implementation: HTTP API, dashboard UI, deployment stack,
tenant hierarchy, rollout pipeline, incident replay, and design partner docs.

### Added

- **Phase 1+2**: Kernel E2E integration + HTTP API (FastAPI, OpenAPI, API key auth)
- **Phase 3**: Dashboard UI -- policy editor, event log, incident detail, replay viewer
- **Phase 4**: Docker Compose deployment, Grafana dashboards, backup/restore, fail-closed startup
- **Phase 5**: Tenant hierarchy (org -> team -> chain), rollout pipeline (DRAFT -> ACTIVE), incident replay
- **Phase 6 (Goal 6)**: Design partner readiness -- onboarding guide, 3 runbooks, deployment checklist, case study template
- PostgreSQL event store backend
- HMAC-SHA256 signed policy bundles (immutable config mode)
- Redis Arbiter Lua nil guards for corrupted state recovery
- 105 adversarial tests across Redis Arbiter, Policy Distributor, Rollout Registry
- Shared `_validators.py` for route validation constants
- Shared `utils.js` (escHtml) across 7 UI pages

### Fixed

- 8 rounds of F.R.I.D.A.Y. review-fix loop (XSS hardening, HMAC, credential leak, NaN budget corruption, race conditions)
- 25-round security hardening v2 (thread safety, input validation, resource leaks)
- Pagination dead zone on boundary page counts
- `simulate()` atomicity -- state transition + result set in single operation
- `since`/`until` validation order in event queries
- Tracked `__pycache__` removed from git

### Stats

- 1197 tests, 3 skipped
- ruff clean (0 errors)

---

## [0.7.1] -- 2026-03-10 -- Thread Safety Hardening

### Fixed

- Thread-safe `_total_spent_usd` and per-chain spend tracking
- `VeronicaOS.close()` and context manager support
- `_step_counter` moved from module-level to instance (concurrent OS instances)
- Log warning when `PolicyConfig.expires_at` is in the past
- `_tighten_factor` fallback to signal severity for non-halt_tighten signals

### Added

- 37 adversarial/boundary/concurrency tests for OrgPolicy
- Docstrings clarifying intentional design constraints (store, redis_arbiter, types)

---

## [0.7.0] -- 2026-03-10 -- Org Policy Engine

### Added

- `OrgPolicy` dataclass with `validate()` and `clamp()` methods
- `OrgPolicyDenied` exception and `StepContext._check_denial` guard
- OrgPolicy validate/clamp integrated into `before_step` pipeline
- `veronica_denied_total` Prometheus metric and `step_denied` log event
- `OrgPolicy` and `OrgPolicyDenied` exported from veronica package

---

## [0.6.0] -- 2026-03-10 -- LLM Integration Adapter

### Added

- `StepContext` class with kind-dispatched `run()` / `run_llm()` / `run_tool()`
- `VeronicaOS.step()` context manager with guaranteed `after_step`
- `_normalize_intent` and `run_step` sugar for 1-line LLM execution
- `_make_fallback_snapshot` for defensive `ContextSnapshot` creation
- `StepContext` exported from veronica package
- LLM integration adapter design doc

---

## [0.5.0] and earlier

See git log for prior history.
