# 🚀 CATE — Combined Release Notes (v0.1 → v0.3.0)

This release bundles the **entire evolution of CATE** — from a minimal local fuzzing engine to a fully featured, safety-gated, stateful flow runner with structured logging, Markdown analytics, and variable-aware multi-step orchestration.

It represents the first complete, stable milestone of the **Calypso Automated Testing Engine**, suitable for internal engineering use, CI experimentation, and integration into Calypso Labs tooling.

---

## 📌 **Highlights Across All Versions**

### **✔ v0.1 — Baseline Fuzzing Engine**
Initial launch of CATE as a local-first, safety-gated fuzzing tool.

**Core Features Added**
- GET & POST fuzzing  
- `{payload}` placeholder injection  
- Body templating  
- Custom headers  
- Concurrency control  
- Request timeout handling  
- `--max-rps` rate limiting  
- `--stop-on-error-rate` adaptive safety cutoff  
- Profiles (`profiles.toml`) for reproducible job definitions  
- Basic grouping of responses by status & content length  
- Local-first, dev-safe execution rules

---

### **✔ v0.2 — Structured Logging + Human-Friendly Reports**
This version introduced **professional-grade observability**, enabling CATE to function as a real debugging and analysis tool.

**New Artifacts**
- `*.jsonl` — one JSON object per payload  
- `*.summary.json` — machine-readable rollups  
- `*.summary.md` — GitHub-friendly Markdown analytics  

**Added Capabilities**
- Timing distribution analysis (min/max/mean/p50/p90/p99)  
- Status code aggregation  
- Error sample collection  
- Safer UTC timestamping  
- Cleaner terminal output  
- Summary tables (latency, errors, statuses)  
- Pretty printed grouped payload results  
- Outlier detection for rare response shapes

---

### **✔ v0.3.0 — Stateful Flows + Assertions + Variable Extraction**
The largest upgrade so far: multi-step test orchestration with shared state.

**Major Additions**
#### 1. 🧩 **Flow Engine (`http-flow`)**
- Define flows in `flows.toml`
- Ordered steps (`steps = ["login", "about", "echo"]`)
- Per-step configuration (method, url, body_template, headers, expectations)
- Automatic cookie/session handling between steps
- DRY-RUN preview mode

#### 2. 🧠 **Variable Extraction & Reuse**
- Extract dynamic values from responses using `extract_regex`
- Store them to named variables with `store_as`
- Interpolate anywhere using `{varname}` syntax
  - Works in URLs  
  - Works in body templates  
  - Works in headers  

#### 3. 🛡 **Assertion System**
Per-step validation support:
- `expect_status = 200`
- `max_latency_ms = 500.0`
- `body_must_contain = "About"`
- `body_must_not_contain = "Dashboard"`
- `stop_on_fail = true`

Assertions propagate into logs + Markdown summaries with pass/fail metadata.

#### 4. 🚫 **Production Safety Gate**
- Cannot run against `env=prod` unless  
  `--i-understand-prod` is explicitly provided  

#### 5. 📊 **Flow Summary Reports**
Each flow execution now generates:
- `<name>.jsonl` — one line per step  
- `<name>.summary.md` — status table + errors + timings  

#### 6. 🧱 Internal Improvements
- Unified parser in `flows.py`
- Added missing header support
- State dictionary (`state["vars"]`) passed across steps
- Better error printing & fail propagation
- Stronger type hints
- Dynamic template interpolation

---

## 🗂 **Project Structure Recap**

```
cate/
  cli.py
  engine.py
  flows.py
  models.py
  logging_utils.py
  profiles.py
  tests/
profiles.toml
flows.toml
logs/
```

---

## 🧭 Recommended Next Steps

### **v0.4 – Distributed Workers**
- Multi-worker cluster execution  
- Redis queue  
- Multi-process parallelism  

### **v0.5 – UI + Integrity Suite Integration**
- Real dashboard  
- Session playback  
- CopyCat & GhostLine integration  

---

## ✔ Stability + Compatibility
- Fully backward compatible with v0.1 and v0.2  
- Flow + profile formats now stable  

---

## 🎉 Summary
CATE is now a **modular testing engine** capable of:
- scripted multi-step authentication flows  
- variable-driven workflows  
- real-time assertion enforcement  
- structured analytics  
- safe execution modes  

This release establishes the baseline foundation for the **Calypso Integrity Platform**.

