## Overview

**Focus:** Flows engine + includes + template functions  
**Range:** `v0.3.0` → `HEAD`  
**Commits:** 15

## Highlights

### Flows / Assertions / Includes
- 🌊 v0.3.1: polish http-flow, logs, and includes (`8584a3f`)
- 🌊 added JS basics for interpolation with flows (`66be749`)
- 🌊 added flow file inclusion to root flows.toml file (`498f1b9`)
- 🌊 flow vars logs created - assertion break downs visible (`25106c6`)
- 🌊 add assertion logs flows (`d1713bf`)
- 🌊 added global stop on fail for flows (`05b4c53`)
- 🌊 added flows files (`3258fb7`)
- 🌊 added listing flows capability (`901317c`)
- 🌊 added listing flows capability (`8d653dd`)

### Engine / HTTP fuzzing / Logging
- (no engine / fuzzing changes; internal refactors only)

### Docs / Ops
- 📚 added versioning and make_release_notes.sh script (`2da11e5`)
- 📚 Added release_notes (`d9c335f`)

### Other
- 🧩 up version (`8ce5271`)
- 🧩 fixed duplicate interpolation functions (`6ef0c1c`)
- 🧩 added save body function as text and html when failure and html only if html is recognized as present (`52a349c`)
- 🧩 added global continue on failedd step rule (`0ef0252`)

## Verification Checklist

- [x] `python -m cate.cli --version` reports v0.3.1
- [x] Sample `http-fuzz` run (with a dev profile) completes and writes JSONL + summaries
- [x] Sample `http-flow` (e.g. delphonix-login-sequence-vars) passes in dev with logs written
- [x] `--stop-on-fail` + per-step `stop_on_fail = true` behave as expected
- [x] `--continue-on-fail` executes all steps even when earlier ones fail
- [x] `--vars-dump` prints extracted variables correctly
- [x] `--save-body` writes failing response bodies (.txt / .html) next to logs
- [x] `include = ["flows/demo-flows.toml"]` and other includes load without errors
- [x] No unexpected exceptions in common flows / fuzz runs

<details>
<summary><strong>Technical Details</strong></summary>

- Product: CATE (Calypso Automated Testing Engine)
- Date: 2025-12-10
- Tag: `v0.3.1`
- Branch: `main`
- Commit range: `v0.3.0..HEAD`

</details>

<details>
<summary><strong>What's Changed (commits)</strong></summary>

- `8584a3f` — v0.3.1: polish http-flow, logs, and includes
- `8ce5271` — up version
- `6ef0c1c` — fixed duplicate interpolation functions
- `66be749` — added JS basics for interpolation with flows
- `498f1b9` — added flow file inclusion to root flows.toml file
- `52a349c` — added save body function as text and html when failure and html only if html is recognized as present
- `25106c6` — flow vars logs created - assertion break downs visible
- `0ef0252` — added global continue on failedd step rule
- `d1713bf` — add assertion logs flows
- `05b4c53` — added global stop on fail for flows
- `3258fb7` — added flows files
- `901317c` — added listing flows capability
- `8d653dd` — added listing flows capability
- `2da11e5` — added versioning and make_release_notes.sh script
- `d9c335f` — Added release_notes

</details>

## Contributors

- @sorenessen
