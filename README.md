CATE — Calypso Automated Testing Engine

Local-first HTTP fuzzing, brute-force simulation, and behavioral response analysis.
Part of the Calypso Integrity Platform.

CATE is a lightweight, safety-gated HTTP fuzzing engine designed for controlled security testing of your own services. It supports GET/POST templating, payload injection, request shaping, rate limiting, response grouping, and profile-based execution.

CATE is intentionally simple in v0.1 — fast, local-first, and predictable — making it ideal for:

Testing login endpoints

Checking for authentication bypasses

Detecting behavioral differences across payloads

Studying timing patterns, size differences, and redirects

Reproducing attack-surface scenarios safely

Running quick brute-force simulations against your own infrastructure

Features (v0.1)
✔ Local-only safety design

CATE will not run against env=prod unless explicitly acknowledged with --i-understand-prod.

✔ GET and POST request fuzzing

Inject {payload} into URLs or request bodies.

✔ Body templating

Example:

--method POST
--body-template "user=admin&pass={payload}"

✔ Custom headers

Provide headers multiple times:

--header "X-Test: 123"
--header "Authorization: Bearer TOKEN"

✔ Rate limiting (max RPS)

Controls request per second globally (--max-rps).

✔ Error-rate trigger

Stops execution if recent failures exceed a threshold (--stop-on-error-rate).

✔ Profiles system (profiles.toml)

Define reusable fuzzing targets:

[profiles.delphonix-login-dev]
url = "https://delphonix.com/login.php"
method = "POST"
wordlist = "./cate/tests/test_wordlist.txt"
body_template = "user=admin&pass={payload}"
placeholder = "{payload}"
concurrency = 2
timeout = 10.0
max_rps = 0.5
stop_on-error-rate = 0.5
env = "dev"


Then run:

python -m cate.cli http-fuzz --profile delphonix-login-dev

✔ Summary + Outlier Detection

After each run, CATE groups all responses by:

HTTP status code

Content length

This makes it easy to spot meaningful differences such as:

Authentication success vs failure

Redirects vs direct responses

Error pages

Behavioral anomalies

Installation

Clone the repo:

git clone https://github.com/yourname/cate
cd cate


Install dependencies:

pip install -r requirements.txt


(Poetry support will be added in later versions.)

Quick Start
1. Basic GET fuzz
python -m cate.cli http-fuzz \
  --url "https://example.com/?q={payload}" \
  --wordlist ./cate/tests/test_wordlist.txt

2. POST fuzz
python -m cate.cli http-fuzz \
  --url "https://example.com/login" \
  --method POST \
  --body-template "username=admin&password={payload}" \
  --wordlist ./cate/tests/test_wordlist.txt

3. With headers
python -m cate.cli http-fuzz \
  --url "https://example.com/api" \
  --method GET \
  --header "Authorization: Bearer test123" \
  --wordlist ./wordlist.txt

4. Using a profile

Profiles live in profiles.toml.

Run with:

python -m cate.cli http-fuzz \
  --profile delphonix-login-dev \
  --output logs/delph-login.jsonl

Profiles (profiles.toml)

Example structure:

[profiles.example]
url = "https://example.com/?q={payload}"
method = "GET"
wordlist = "./cate/tests/test_wordlist.txt"
placeholder = "{payload}"
concurrency = 5
timeout = 10.0
max_rps = 1.0
env = "dev"

Output Format (JSONL)

Each result is a JSON line:

{
  "payload": "password123",
  "status_code": 200,
  "elapsed_ms": 102.33,
  "content_length": 2512,
  "error": null,
  "timestamp": "2025-12-06T21:59:31.471253Z"
}


Perfect for ingestion into:

ELK/Elastic

Splunk

Datadog

Grafana Loki

Python pandas

Safety Controls

CATE includes built-in safety mechanisms to prevent misuse:

env flag
--env dev | stage | prod

Production acknowledgment
--i-understand-prod

Rate limiting
--max-rps 0.5

Error-rate cutoff
--stop-on-error-rate 0.5

Local-only default

CATE will refuse production-level fuzzing unless explicitly overridden.

Project Structure
cate/
  cli.py              ← CLI + argument parsing + profile merging
  engine.py           ← Async worker pool + RPS governor + execution
  models.py           ← Pydantic-like model classes (JobConfig, Target, Result)
  logging_utils.py    ← JSONL writer
  profiles.py         ← Profiles loader + validation
  tests/
    test_wordlist.txt
profiles.toml         ← Your reusable test configurations
logs/                 ← Output directory for JSONL logs

Roadmap
v0.2 — Logging, Metrics, and Visualization

Workers → structured log events

Progress bar

Histogram output for timing

Optional HTML / terminal report

v0.3 — Session & State

Cookie jar support

Stateful sequences (login → action → logout)

Chained fuzzing profiles

v0.4 — Distributed

Multiple workers over network

Redis queue support

Dashboard

v0.5 — Calypso Labs Integration

CopyCat metadata injection

API-based workflows

Multi-target test plans

License

Private, © Calypso Labs 2025.

Maintainer

Soren Essen
Principal Engineer & Product Architect
Calypso Labs