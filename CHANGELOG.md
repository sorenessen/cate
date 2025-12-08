# Changelog

All notable changes to **CATE — Calypso Automated Testing Engine** are documented in this file.

The version numbers below match the informal milestones we’ve been using in the code and README (`v0.1`, `v0.2`, `v0.3`).

---

## [Unreleased]

- Per-step fuzz payloads in flows (shared wordlists across flow steps)
- TUI / GUI front-end for non-CLI users
- Multi-worker / distributed execution
- CI integration examples and GitHub Actions recipes

---

## [0.3.0] – 2025-12-08

**Stateful flows, assertions, and variable extraction.**

### Added

- New `http-flow` subcommand:
  - Runs **multi-step, stateful HTTP flows** defined in `flows.toml`.
  - Maintains a shared cookie jar across steps (e.g., login → about page).
- `flows.toml` support with:
  - Flow-level metadata: `description`, ordered `steps = [...]`.
  - Step definitions under `[flows.<name>.<step>]` tables.
- Per-step **assertions**:
  - `max_latency_ms` – fail a step if latency exceeds this threshold.
  - `body_must_contain` – fail if response body does **not** contain a string.
  - `body_must_not_contain` – fail if response body **does** contain a string.
- **Variable extraction and interpolation**:
  - `extract_regex` – pull a value out of a response body.
  - `store_as` – save the extracted value into a named variable.
  - `require_extracted` – fail the step if extraction fails.
  - Use stored vars in later steps via `{var_name}` placeholders in `url` and `body_template`.
- Flow-level logging:
  - `<prefix>.jsonl` – one JSON object per step with timing, size, ok/error status, and assertion messages.
  - `<prefix>.summary.md` – human-readable Markdown summary:
    - Overall pass/fail status
    - Steps, failures, average latency
    - Per-step table with status, latency, bytes, and errors.

### Changed

- Flow execution safety:
  - `http-flow` now respects `--env` and `--i-understand-prod`, mirroring `http-fuzz`:
    - Refuses to run with `--env prod` unless `--i-understand-prod` is present.
- README:
  - Added **Stateful flows (v0.3)** section with examples for `flows.toml`, variable extraction, and flow reports.
  - Documented example run for `delphonix-login-sequence` and `delphonix-login-sequence-vars`.

---

## [0.2.0] – 2025-12-07

**Richer logging and Markdown summaries.**

### Added

- Summary generation when `--output` is provided to `http-fuzz`:
  - `<name>.jsonl` – one JSON object per payload (unchanged core format).
  - `<name>.summary.json` – machine-readable rollup including:
    - Target (method, URL)
    - Environment and runtime config
    - Total payloads, error counts, error rate
    - Status code histogram
    - Latency stats (min / max / mean / p50 / p90 / p99)
    - Sample error payloads (when any exist).
  - `<name>.summary.md` – Markdown report suitable for GitHub / VS Code:
    - Target details (method, URL, env, wordlist)
    - Totals and error rate
    - Status code table
    - Latency table
    - Error / anomaly samples list.
- `write_run_summaries(...)` helper to keep summary generation logic centralized.

### Changed

- `logging_utils.write_results_jsonl`:
  - Uses `dataclasses.asdict` for `Result`.
  - Ensures timestamps are serialized as ISO 8601 UTC strings.
- README:
  - Added **Logging and reports (v0.2)** section with example `*.summary.json` and `*.summary.md` outputs.
  - Documented how to use `--output` to drive reporting.

---

## [0.1.0] – 2025-12-06

**Initial HTTP fuzzing engine.**

### Added

- `cate` CLI with `http-fuzz` subcommand:
  - Target URL with `{payload}` placeholder support.
  - Supports `GET` and `POST` (and arbitrary HTTP methods).
- Core fuzzing capabilities:
  - Wordlist-driven payload injection.
  - `{payload}` substitution in URL query string or path.
  - `--body-template` to inject `{payload}` into POST bodies
    (e.g., `user=admin&pass={payload}`).
  - `--header` repeated flag for arbitrary HTTP headers.
  - `--concurrency` for async parallel requests.
  - `--timeout` per-request timeout controls.
- Safety features:
  - `--max-rps` global requests-per-second governor.
  - `--stop-on-error-rate` to stop when recent error fraction is too high.
  - `--env` label (`dev`, `stage`, `prod`), with a guardrail:
    - Refuses to run against `--env prod` unless `--i-understand-prod` is provided.
- Profile system (`profiles.toml`):
  - Named profiles under `[profiles.<name>]` with URL, method, wordlist, body template, etc.
  - `--profile` flag to run fuzz jobs from a profile instead of raw CLI flags.
- JSONL output:
  - One line per payload with:
    - `payload`, `status_code`, `elapsed_ms`, `content_length`, `error`, `timestamp`.
- CLI summary:
  - Groups responses by `(status_code, content_length)`.
  - Prints samples and highlights “rare” response shapes as potential outliers.

---

## Versioning

CATE currently uses **semantic-style version labels** (e.g., `0.1.0`, `0.2.0`, `0.3.0`) with:

- **0.x** – early development / experimental
- **PATCH** – bugfixes and minor internal improvements
- **MINOR** – new features and behavior, backwards compatible where possible

