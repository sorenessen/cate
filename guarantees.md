# CATE Execution & Reporting Guarantees

CATE guarantees the following for every execution:

1. A report is always generated.
   - Markdown report is always written.
   - HTML report is always written.
   - Reports render even if some artifacts are missing.

2. Verdicts are explainable.
   - Verdicts derive only from signals.
   - Signals derive only from observed behavior.
   - Reports never infer data that was not observed.

3. Artifacts degrade gracefully.
   - Missing summary.json does not block reports.
   - Missing JSONL does not block reports.
   - Partial runs still produce reports.

4. Reports are source-of-truth.
   - If a report says PASS, the embedded data supports it.
   - If data is missing, the report states that explicitly.

These guarantees are considered part of the public contract.
