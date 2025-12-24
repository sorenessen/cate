# CATE Formal Invariants

**Version:** 0.4.x  
**Scope:** All CATE execution modes (`http-fuzz`, `http-flow`, future modes)  
**Purpose:** Define the non-negotiable reasoning constraints that govern how CATE observes, interprets, and reports system behavior.

These invariants are architectural guarantees, not implementation details.  
They exist to ensure determinism, explainability, and trust.

---

## Invariant I — Observation Only

**Statement**

CATE derives conclusions exclusively from behaviors it directly observes during execution.

**Implications**

- CATE does not infer intent, identity, or motivation.
- CATE does not speculate beyond recorded HTTP responses, timings, and explicit errors.
- CATE does not classify actors (e.g. “attacker”, “user”, “bot”).

**Rationale**

Observed behavior is verifiable.  
Inferred behavior is disputable.

By constraining itself to observation, CATE remains auditable and defensible.

---

## Invariant II — One-Way Reasoning

**Statement**

CATE’s reasoning flows strictly forward:

```
Results → Summary → Signals → Verdict
```

No stage may influence or reinterpret an earlier stage.

**Implications**

- Raw execution results are immutable once recorded.
- Summaries are descriptive, not interpretive.
- Signals may only reference summary fields.
- Verdicts may only reference signals.

**Rationale**

Bidirectional reasoning creates bias and post-hoc justification.  
One-way reasoning preserves causal integrity.

---

## Invariant III — Deterministic Output

**Statement**

Given identical inputs and execution conditions, CATE produces identical summaries, signals, and verdicts.

**Implications**

- No randomness in signal or verdict generation.
- No time-based branching logic.
- No environment-specific heuristics that alter reasoning.

**Rationale**

Determinism enables:
- reproducibility
- comparison across runs
- reliable automation
- trustworthy demos

If a result cannot be reproduced, it cannot be trusted.

---

## Invariant IV — Evidence-Bound Severity

**Statement**

Severity may increase only when additional evidence is present.

Missing, partial, or ambiguous data may reduce confidence, but must not escalate severity.

**Implications**

- Severity is monotonic with evidence.
- Context alone does not raise severity.
- Absence of data never implies risk.

**Rationale**

Over-escalation erodes credibility faster than missed alerts.  
CATE prefers restraint over speculation.

---

## Invariant V — Silence Is a Valid Outcome

**Statement**

CATE explicitly allows outcomes where no noteworthy signals are produced.

**Implications**

- A clean run is a meaningful result.
- “No issues detected” is not a failure state.
- Lack of findings does not require justification.

**Rationale**

Systems that always report findings train users to ignore them.  
Silence preserves signal integrity.

---

## Invariant VI — Subject-Agnostic Language

**Statement**

CATE describes behavior without assigning identity or intent.

**Implications**

- Outputs reference “requests”, “steps”, “actors”, or “sources”, not people.
- No anthropomorphic or adversarial language appears in reasoning layers.
- Higher-level attribution is delegated to downstream systems.

**Rationale**

Separating behavior from identity keeps CATE composable and neutral.

---

## Invariant VII — Explainability by Construction

**Statement**

Every verdict produced by CATE must be traceable to concrete signals, which themselves must be traceable to summary fields.

**Implications**

- No verdict exists without supporting signals.
- No signal exists without supporting summary data.
- Traceability does not rely on prose explanation alone.

**Rationale**

Explainability is not a feature added later; it is a property of the system’s construction.

---

## Enforcement Philosophy

These invariants are enforced through:

- architectural separation of stages
- limited data access at each layer
- explicit documentation
- selective runtime assertions during development

They are intentionally simple to reason about and difficult to violate accidentally.

---

## Non-Goals

CATE explicitly does **not** attempt to:

- identify malicious actors
- predict intent
- assign blame
- replace human judgment

Those responsibilities belong to downstream systems and analysts.

---

**End of Invariants**
