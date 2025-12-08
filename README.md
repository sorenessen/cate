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
max_latency_ms = 10.0
body_must_contain = "about"
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

### Example flow summary

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
