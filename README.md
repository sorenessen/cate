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

## Safety flags

| Flag                  | Description                                       |
|----------------------|---------------------------------------------------|
| `--env dev`          | Environment selection                              |
| `--i-understand-prod`| Required to run against prod                       |
| `--max-rps`          | Requests-per-second governor                        |
| `--stop-on-error-rate` | Auto-shutdown on high error rate                 |

---

## Project structure

```text
cate/
  cli.py            # CLI + profiles integration
  engine.py         # Async workers, RPS governor
  models.py         # Target, JobConfig, Result models
  logging_utils.py  # JSONL logging
  profiles.py       # TOML profile loader
  tests/
    test_wordlist.txt
profiles.toml        # Profile definitions
logs/                # JSONL outputs
```

---

## Roadmap

### v0.2  
Richer logging, timing histograms, HTML/Markdown reports.

### v0.3  
Stateful flows (login → action → logout), cookie/session support.

### v0.4  
Distributed workers, queues, dashboard.

### v0.5  
Integration with Calypso Labs / CopyCat / Integrity Suite.

---

## Maintainer

**Soren Essen**  
Principal Engineer & Product Architect, Calypso Labs
