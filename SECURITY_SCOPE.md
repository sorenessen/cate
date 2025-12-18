# CATE Security Assessment Scope

## Purpose
CATE is used to validate defensive controls on systems owned or explicitly authorized by the operator.
All activity is intended to observe, measure, and document defensive behavior — not to exploit systems or exfiltrate data.

## Authorized Environment
- Localhost
- Containerized test environments
- Privately owned domains and infrastructure (e.g. delphonix.com)
- Systems not exposed to the public internet unless explicitly configured

## Explicitly Out of Scope
CATE will NOT be used to:
- Exfiltrate data
- Perform SQL injection, XSS, RCE, or privilege escalation
- Bypass authentication to access protected user data
- Persist access or modify production systems

## Defensive Layers Observed
CATE may interact with the following defensive layers **only to observe responses**:

- Edge / CDN
- Web Application Firewall (WAF)
- Rate limiting
- Application authentication
- Application input validation

## Expected Outcomes
For each flow, CATE should be able to show:
- Which defensive layer responded
- How the request was blocked, throttled, or challenged
- What headers, cookies, or tokens were present at the time
- A non-destructive record of the response (headers, status, optional body capture)

## Intent
CATE is designed to help defenders understand:
- How far a request can progress before being stopped
- Which controls are effective
- Where configuration or visibility gaps exist
