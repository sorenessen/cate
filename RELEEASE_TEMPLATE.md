# CATE {{VERSION}} — {{TITLE}}

## 🧭 Overview
{{OVERVIEW}}

Provide a high-level summary (2–4 sentences) of what this release focuses on.  
Examples: new HTTP-flow capabilities, richer assertions, better logging, safer prod-guards, etc.

---

## ✨ Highlights

### 🌊 Flows / Assertions / Includes
- {{FLOW_FEATURE_1}}
- {{FLOW_FEATURE_2}}
- {{FLOW_FEATURE_3}}

### 🛠️ Engine / HTTP Fuzzing / Logging
- {{ENGINE_CHANGE_1}}
- {{ENGINE_CHANGE_2}}
- {{ENGINE_CHANGE_3}}

### 📚 Docs / Ops
- Updated CHANGELOG / README where applicable
- New or modified runtime config / profiles / flows documented
- Added or updated release scripts / developer workflow notes

---

## 🧪 Verification Checklist

- ✅ `python -m cate.cli --version` reports **{{VERSION}}**
- ✅ Sample `http-fuzz` run (with a dev profile) completes and writes JSONL + summaries
- ✅ Sample `http-flow` (e.g. `delphonix-login-sequence-vars`) passes in **dev**
- ✅ `--stop-on-fail` stops the flow on first failure (and respects per-step `stop_on_fail = true`)
- ✅ `--continue-on-fail` runs through all steps even when some fail
- ✅ `--vars-dump` prints extracted variables from `extract_regex` / `store_as` steps
- ✅ `--save-body` writes failing response bodies (`.txt` and `.html` when applicable)
- ✅ `include = ["flows/demo-flows.toml"]` and other includes resolve without cycles or missing files
- ✅ No unexpected exceptions or tracebacks in typical flows / fuzz runs

---

## 🧩 Technical Details

### Commands / Flags Added or Updated
- {{COMMAND_OR_FLAG_1}}
- {{COMMAND_OR_FLAG_2}}
- {{COMMAND_OR_FLAG_3}}

### Compatibility Notes
- Backward compatible with all v0.3.x CATE configs unless explicitly noted.
- No breaking CLI changes unless specified below.
- Any changes to JSONL / summary schema are documented here.

### Deprecated / Removed
- {{DEPRECATED_OR_REMOVED}}

---

## 🧾 Meta

- **Date:** {{DATE}}
- **Tag:** {{TAG}}
- **Branch:** {{BRANCH}}
- **Commit Range:** {{COMMIT_RANGE}}

---

## 🔍 What’s Changed
{{GIT_COMMITS}}

Full changelog: `{{PREV_TAG}}...{{TAG}}`

---

## 👥 Contributors
{{CONTRIBUTORS}}

---

## 📦 Assets
- Source code (zip)
- Source code (tar.gz)
- Release notes: `release-notes-v{{VERSION}}.md`
