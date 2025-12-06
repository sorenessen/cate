CATE — Calypso Automated Testing Engine
Local-first HTTP fuzzing, brute-force simulation, and behavioral response analysis.

Part of the Calypso Integrity Platform.

CATE is a lightweight, safety-gated HTTP fuzzing engine for controlled security testing of your own systems.
It supports:

GET + POST fuzzing

Payload injection ({payload})

Body templating

Custom headers

Rate limiting

Error-rate shutdown

Profile-based execution (profiles.toml)

Response grouping + outlier detection

This is v0.1, intentionally simple, fast, and local-first.

🚀 Features (v0.1)
✔ Safe by default

Requires explicit acknowledgement to run against env=prod

Built for local + dev testing flow

✔ GET / POST fuzzing

Inject {payload} into URLs:

https://example.com/?q={payload}


Or POST bodies:

--body-template "user=admin&pass={payload}"

✔ Custom headers
--header "X-Test: Hello" \
--header "Authorization: Bearer TOKEN"

✔ Rate limiting & error controls

--max-rps to avoid overwhelming services

--stop-on-error-rate to halt at suspicious error spikes

✔ Profiles system

Never retype long commands again — store fuzzing configs in profiles.toml.

Example:

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


Run it with:

python -m cate.cli http-fuzz --profile delphonix-login-dev

✔ Response grouping + outlier detection

CATE groups responses by (status_code, content_length) so you can spot:

auth success/failure

redirects

error pages

content-length anomalies

timing differences

This is the heart of “behavioral fuzzing.”

📦 Installation
git clone https://github.com/yourname/cate
cd cate
pip install -r requirements.txt


(Poetry support coming in later versions.)

🏁 Quick Start
1. GET fuzz
python -m cate.cli http-fuzz \
  --url "https://example.com/?q={payload}" \
  --wordlist ./cate/tests/test_wordlist.txt

2. POST fuzz
python -m cate.cli http-fuzz \
  --url "https://example.com/login" \
  --method POST \
  --body-template "user=admin&pass={payload}" \
  --wordlist ./cate/tests/test_wordlist.txt

3. With headers
python -m cate.cli http-fuzz \
  --url "https://example.com/api" \
  --method GET \
  --header "Authorization: Bearer test123" \
  --wordlist ./wordlist.txt

4. Using profiles
python -m cate.cli http-fuzz \
  --profile delphonix-login-dev \
  --output ./logs/login-profile.jsonl

🗂 profiles.toml Format
[profiles.example]
url = "https://example.com/?q={payload}"
method = "GET"
wordlist = "./cate/tests/test_wordlist.txt"
placeholder = "{payload}"
concurrency = 5
timeout = 10.0
max_rps = 1.0
stop_on_error_rate = 0.5
env = "dev"

📄 Output Format (JSONL)

Each result is a separate JSON line:

{
  "payload": "password",
  "status_code": 200,
  "elapsed_ms": 103.4,
  "content_length": 2512,
  "error": null,
  "timestamp": "2025-12-06T21:59:31.471253Z"
}


Ideal for:

Elastic / Kibana

Splunk

Datadog

Loki / Grafana

Pandas analysis

🔒 Safety Controls
Flag	Purpose
`--env dev	stage
--i-understand-prod	Required for production fuzzing
--max-rps	Global rate limit
--stop-on-error-rate	Auto-shutdown on error spikes

CATE is secure-by-default and refuses production fuzzing unless explicitly acknowledged.

📁 Project Structure
cate/
  cli.py              # CLI parser + profiles integration
  engine.py           # async workers, RPS governor
  models.py           # Target, JobConfig, Result
  logging_utils.py    # JSONL writer
  profiles.py         # TOML profiles loader
  tests/
    test_wordlist.txt

profiles.toml         # your reusable profiles
logs/                 # output JSONL

🗺 Roadmap
v0.2

Request/response event logs

Timing histograms

Summary HTML reports

v0.3

Stateful flows (login → action → logout)

Cookie/session support

Multi-step fuzzing plans

v0.4

Distributed workers

Redis queue

Dashboard UI

v0.5

Integration with Calypso Labs: CopyCat metadata, Integrity Suite workflows

👤 Maintainer

Soren Essen
Principal Engineer & Product Architect
Calypso Labs
