# CATE — Calypso Automated Testing Engine

Local-first HTTP fuzzing, brute-force simulation, and behavioral response analysis.  
Part of the Calypso Integrity Platform.

CATE is a lightweight, safety-gated HTTP fuzzing engine for controlled security testing of your own systems.

It currently supports:

- GET and POST fuzzing  
- Payload injection via `{payload}` placeholders  
- Body templating  
- Custom headers  
- Rate limiting  
- Error-rate shutdown  
- Profile-based execution via `profiles.toml`  
- Response grouping and outlier detection  

This is v0.1, intentionally simple, fast, and local-first.

## Table of Contents
- [Features](#features)
  - [Safe by default](#safe-by-default)
  - [GET / POST fuzzing](#get--post-fuzzing)
  - [Custom headers](#custom-headers)
  - [Rate limiting and error controls](#rate-limiting-and-error-controls)
- [Profiles (`profiles.toml`)](#profiles-profilestoml)
- [Output format (JSONL)](#output-format-jsonl)
- [Logging and reports (v0.2)](#logging-and-reports-v02)
  - [Example](#example)
- [Stateful flows (v0.3)](#stateful-flows-v03)
  - [Flow definitions (`flows.toml`)](#flow-definitions-flowstoml)
  - [Optional assertions (v0.3.0)](#optional-assertions-v030)
  - [Running a flow](#running-a-flow)
  - [Flow logs & Markdown reports](#flow-logs--markdown-reports)
  - [Flow variables and interpolation (v0.3.1)](#flow-variables-and-interpolation-v031)
- [Safety flags](#safety-flags)
- [Project structure](#project-structure)
- [Roadmap](#roadmap)
- [Maintainer](#maintainer)


---

## Features

### Safe by default

- Refuses to run against `env=prod` unless `--i-understand-prod` is provided.  
- Designed for local and dev environments first.

---

### GET / POST fuzzing

Inject `{payload}` into URLs:

```text
https://example.com/?q={payload}
```

Use body templates for POST:

```text
user=admin&pass={payload}
```

---

### Custom headers

```bash
python -m cate.cli http-fuzz \
  --url "https://example.com/api" \
  --method GET \
  --header "Authorization: Bearer TOKEN" \
  --header "X-Test: 123" \
  --wordlist ./wordlist.txt
```

---

### Rate limiting and error controls

`--max-rps` sets a global requests-per-second limit.

`--stop-on-error-rate` stops the run if recent errors exceed a threshold.

---

## Profiles (`profiles.toml`)

You can define reusable configurations:

```toml
[profiles.delphonix-login-dev]
url = "https://delphonix.com/login.php"
method = "POST"
wordlist = "./cate/tests/test_wordlist.txt"
body_template = "user=admin&pass={payload}"
placeholder = "{payload}"
concurrency = 2
timeout = 10.0
max_rps = 0.5
stop_on_error_rate = 0.5
env = "dev"
```

Run the profile:

```bash
python -m cate.cli http-fuzz \
  --profile delphonix-login-dev \
  --output ./logs/delph-login.jsonl
```

---

## Output format (JSONL)

Each line contains one result:

```json
{
  "payload": "password",
  "status_code": 200,
  "elapsed_ms": 103.4,
  "content_length": 2512,
  "error": null,
  "timestamp": "2025-12-06T21:59:31.471253Z"
}
```

This can be consumed by Elastic, Splunk, Datadog, Loki, pandas, etc.

---

## Logging and reports (v0.2)

When you pass `--output`, CATE writes a structured JSONL log for every payload, and also generates two summary artifacts:

- `run.jsonl` – one JSON object per payload  
- `run.summary.json` – machine-readable rollup (totals, error rate, latency stats, status counts, sample errors)  
- `run.summary.md` – human-friendly Markdown report  

### Example

```bash
python -m cate.cli http-fuzz \
  --profile delphonix-login-dev \
  --output ./logs/delphonix-login.jsonl
```

Creates:

- `logs/delphonix-login.jsonl`  
- `logs/delphonix-login.summary.json`  
- `logs/delphonix-login.summary.md`

### `*.summary.json` example

```json
{
  "generated_at": "2025-12-07T02:03:36.782954Z",
  "target": {
    "method": "POST",
    "url": "https://delphonix.com/login.php"
  },
  "env": "dev",
  "wordlist": "./cate/tests/test_wordlist.txt",
  "concurrency": 2,
  "timeout_seconds": 10.0,
  "max_rps": 0.5,
  "stop_on_error_rate": 0.5,
  "total_payloads": 4,
  "error_count": 0,
  "error_rate": 0.0,
  "status_counts": {
    "200": 4
  },
  "latency": {
    "count": 4,
    "min_ms": 100.43,
    "max_ms": 930.23,
    "mean_ms": 350.12,
    "p50_ms": 210.77,
    "p90_ms": 880.54,
    "p99_ms": 925.00
  },
  "error_examples": []
}
```

### `*.summary.md` includes:

- Target details  
- Totals and error rate  
- Status code table  
- Latency statistics  
- Error/anomaly examples  

---

## Stateful flows (v0.3)

CATE supports **multi-step, stateful HTTP flows** using `flows.toml` and the `http-flow` command.

Example flow:

```toml
[flows.delphonix-login-sequence]
description = "Login as admin, then fetch about page."
steps = ["login", "about"]

[flows.delphonix-login-sequence.login]
method = "POST"
url = "https://delphonix.com/login.php"
body_template = "user=admin&pass={password}"
capture_cookies = true

[flows.delphonix-login-sequence.about]
method = "GET"
url = "https://delphonix.com/about.html"
expect_status = 200
```

### Optional flow assertions

```toml
max_latency_ms = 10.0              # Fails if greater than 10.0
body_must_contain = "about"        # Fails if value is absent
```

---

## Running a flow

Dry run:

```bash
python -m cate.cli http-flow \
  --flow delphonix-login-sequence \
  --dry-run
```

Full run:

```bash
python -m cate.cli http-flow \
  --flow delphonix-login-sequence \
  --output logs/delphonix-flow
```

Produces:

- `logs/delphonix-flow.jsonl`  
- `logs/delphonix-flow.summary.md`

## Flow logs & Markdown reports

```markdown
# ✅ Flow Passed

## Overview

| Metric    | Value      |
|-----------|------------|
| Steps     | 2          |
| Failures  | 0          |
| Avg latency | 259.79 ms |

## All Steps

| Step  | Method | URL                                  | Status | OK  | Latency (ms) | Bytes | Error |
|-------|--------|---------------------------------------|--------|-----|--------------:|------:|-------|
| login | POST   | https://delphonix.com/login.php       | 200    | ✅  | 421.0         | 2512  |       |
| about | GET    | https://delphonix.com/about.html      | 200    | ✅  | 98.6          | 19298 |       |

_Report generated by **CATE v0.3**_
```
---

## Listing available flows

To see what flows are defined in `flows.toml`:

```bash
python -m cate.cli http-flow --list
```
#### Example output:

```[CATE] Available flows:
  - delphonix-login-sequence: Login as admin, then fetch about page.
  - delphonix-login-sequence-vars: Login as admin, then fetch about page (with variable extraction demo).
```
## Using a custom flows.toml file

By default, CATE looks for a file named flows.toml in the current working directory.

You can override this with `--flows-file`:

### List flows from a specific file
`python -m cate.cli http-flow --flows-file ci/flows-ci.toml --list`

### Run a specific flow from that file
```
python -m cate.cli http-flow \
  --flows-file flows.toml \
  --flow delphonix-login-sequence-vars \
  --output logs/dev-flow-safe-flowsfile
```

This is useful when you:

- Keep separate flow definitions for local vs CI.

- Want to ship example flows with the repo, but use a different file in your own environment.

---

## Flow variables and interpolation (v0.3.1)

Flows can extract values from one step and reuse them later using simple
**regex capture + `{name}` interpolation**.

Example:

```toml
[flows.delphonix-login-sequence-vars]
description = "Login as admin, then fetch about page (with variable extraction demo)."
steps = ["login", "about", "echo"]

[flows.delphonix-login-sequence-vars.login]
method = "POST"
url = "https://delphonix.com/login.php"
body_template = "user=admin&pass={password}"
capture_cookies = true

# Pull a marker out of the login page and store it as 'marker'
extract_regex = "Login or Sign Up"
store_as = "marker"
require_extracted = true

[flows.delphonix-login-sequence-vars.about]
method = "GET"
url = "https://delphonix.com/about.html?m={marker}"
expect_status = 200
body_must_contain = "About"

[flows.delphonix-login-sequence-vars.echo]
method = "GET"
url = "https://delphonix.com/about.html?marker={marker}&step=echo"
expect_status = 200
```
### Example Run (3-Step Variable Flow)
#### Below is a real execution of the variable-enabled flow:
```
python -m cate.cli http-flow \
  --flow delphonix-login-sequence-vars \
  --output logs/delphonix-flow-vars
```
#### Produces console output:
```
[CATE] Loaded flow 'delphonix-login-sequence-vars'
[CATE] Description: Login as admin, then fetch about page (with variable extraction demo).
[CATE] Steps:
  1. login -> POST https://delphonix.com/login.php       (capture_cookies=True, expect_status=None)
  2. about -> GET  https://delphonix.com/about.html?m={marker}
  3. echo  -> GET  https://delphonix.com/about.html?marker={marker}&step=echo

[CATE] Executing flow (v0.3.0 stateful HTTP run) in env=dev (timeout=10.0s, max_rps=2.0)…

[CATE] Flow results:
[OK] login: POST https://delphonix.com/login.php
     → status=200, 355.9 ms, 2512 bytes

[OK] about: GET https://delphonix.com/about.html?m=Login or Sign Up
     → status=200, 92.5 ms, 19298 bytes

[OK] echo: GET https://delphonix.com/about.html?marker=Login or Sign Up&step=echo
     → status=200, 101.7 ms, 19298 bytes

[CATE] Flow logs written to:
  logs/delphonix-flow-vars.jsonl
  logs/delphonix-flow-vars.summary.md

[CATE] Flow completed successfully.
```
#### And the generated Markdown report (delphonix-flow-vars.summary.md) looks like:

✅ Flow Passed

## Overview

| Metric | Value    |
|--------|----------|
| Steps  | 3        |
| Failures | 0      |
| Avg latency | 399.60 ms |

## All Steps

| Step  | Method | URL                                                                | Status | OK  | Latency (ms) | Bytes | Error |
|-------|--------|--------------------------------------------------------------------|--------|-----|--------------:|-------:|-------|
| login | POST   | https://delphonix.com/login.php                                   | 200    | ✅  | 1008.6       | 2512  |       |
| about | GET    | https://delphonix.com/about.html?m=Login or Sign Up               | 200    | ✅  |   88.6       | 19298 |       |
| echo  | GET    | https://delphonix.com/about.html?marker=Login or Sign Up&step=echo | 200    | ✅  |  101.7       | 19298 |       |

_Report generated by **CATE v0.3 — Calypso Automated Testing Engine**_


How it works:

- extract_regex is run against the response body of login.

- The first match is stored under the key given by store_as
(here: marker).

- Later steps can use {marker} anywhere in url or body_template.

- If require_extracted = true and no match is found, the step fails.

This lets you chain flows like:

1. Log in and grab a CSRF token, marker, or one-time link.

2. Feed that value into a follow-up request.

3. Assert that the flow as a whole behaved as expected.

---



---

## Safety flags

| Flag | Description |
|------|-------------|
| `--env dev` | Environment selection |
| `--i-understand-prod` | Required to run on prod |
| `--max-rps` | Requests-per-second governor |
| `--stop-on-error-rate` | Auto-shutdown on high error rate |

---

## Project structure

```text
cate/
  cli.py
  engine.py
  models.py
  logging_utils.py
  profiles.py
  flows.py
  tests/
profiles.toml
flows.toml
logs/
```

---

## Roadmap

### v0.2  
Richer logging & Markdown reports.

### v0.3  
Stateful flows, cookie/session support.

### v0.4  
Distributed workers, queues, GUI dashboard.

### v0.5  
Calypso Labs ecosystem integration.

---

## Maintainer

**Soren Essen**  
Principal Engineer & Product Architect, Calypso Labs
