<div align="center">

# 🔵 MARCS
### Multi-Agent Code Review System

**Crash-Safe • Journaled • Deterministic • AI-Ready**

<br/>

<img src="data/marcs_branding_kit/marcs-mark.svg" width="100" alt="MARCS Logo"/>

<br/>

[![Tests](https://img.shields.io/badge/tests-179%20passed-brightgreen?style=for-the-badge)](.)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?style=for-the-badge)](.)
[![License](https://img.shields.io/badge/license-MIT-purple?style=for-the-badge)](LICENSE)
[![Status](https://img.shields.io/badge/status-Complete-success?style=for-the-badge)](.)

<br/>

**MARCS is a crash-safe, deterministic multi-agent code review system that integrates LLM reasoning with journaled patch application, safety analysis, and fully reproducible workflows.**

</div>

---

## 🚀 Overview

MARCS is a production-grade, LLM-powered code review orchestrator with crash-safe patch application, structured journaling, deterministic execution, safety scanning, and full HTML artifact reporting.

Inspired by CI/CD systems, database recovery mechanisms, and multi-agent tooling — MARCS brings *reliability* to AI-driven code modification.

---

## 🎯 Use Cases

- Automated PR review for high-volume engineering teams
- Enterprise code quality pipelines
- Safe LLM-generated patch application
- Security-aware code transformations
- Deterministic AI agents (no nondeterministic runs)
- Research environment for multi-agent development workflows

---

## 🧰 Tech Stack

**Python · FastAPI · AsyncIO · Structured Logging · LLM Integration · Unified Diff Engine · Crash-Safe Journaling**

---

## ✨ Key Features

- ✅ **Multi-Agent Reviewer** (LLM-backed or demo)
- ✅ **Crash-Safe Patch Application** (journal + rollback + resume)
- ✅ **Deterministic Execution** (no "LLM randomness corruption")
- ✅ **Safety Engine** (detects eval/exec/os.system/shell=True/etc.)
- ✅ **Unified Diff Previews**
- ✅ **HTML Artifact Reports**
- ✅ **Full Backup Layer**
- ✅ **Webhook Integration** (GitHub-compatible)
- ✅ **179 Tests** — end-to-end coverage
- ✅ **Minimal Dependencies** & clean architecture
- ✅ **Structured Logging & Tracing** (trace IDs & spans)

---

## 📂 Project Structure

```
marcs/
├── api/                 # FastAPI routes & webhook handlers
├── core/                # Logging, tracing, env checks, reporting
├── orchestration/       # Orchestrator, safety engine
├── services/            # Reviewer + patch applier
├── worker/              # Async queue worker
├── journals/            # Crash-safe execution logs
├── backups/             # Automatic file backups
├── artifacts/           # HTML + JSON outputs
├── scripts/             # CLI tools & demos
└── tests/               # 179 tests with full coverage
```

---

## 🖼️ HTML Report Preview

<div align="center">
  <img src="data/html_report.png" width="900" alt="MARCS HTML Report"/>
  <p><em>Auto-generated HTML report with findings, patches, and safety warnings</em></p>
</div>

---

## 🧱 Architecture

```
             ┌──────────────────────┐
             │   GitHub Webhooks    │
             └──────────┬───────────┘
                        │
                 Normalization
                        ▼
             ┌──────────────────────┐
             │       Queue          │
             └──────────┬───────────┘
                        │
                 Worker (Async)
                        ▼
   ┌────────────────────────────────────────┐
   │           MARCS Orchestrator           │
   │                                        │
   │  • Runs Reviewer (LLM or Demo)         │
   │  • Generates Patch Suggestions         │
   │  • Safety Scan (eval/exec/etc)         │
   │  • Preview Rendering                   │
   │  • Crash-Safe Apply (Journal)          │
   │  • Backup + Commit                     │
   │  • Artifact Writer (JSON + HTML)       │
   └────────────────────┬───────────────────┘
                        │
           Artifacts / Backups / Journal
                        ▼
     • artifacts/<event_id>/report.html  
     • journals/<event_id>.jsonl  
     • backups/<event_id>/filename.bak  
```

---

## 🏁 Quickstart

### Requirements

**Python 3.9+**  
macOS / Linux recommended

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Initialize MARCS

```bash
./scripts/init_marcs.sh
```

### 3. Create Demo Repository

```bash
mkdir demo-repo
echo "print('hello')" > demo-repo/hello.py
```

### 4. Create Review File (review.json)

```json
{
  "findings": [],
  "suggested_patches": {
    "hello.py": "--- a/hello.py\n+++ b/hello.py\n@@ -1,1 +1,2 @@\n print('hello')\n+print('world')\n"
  }
}
```

---

## 🖥️ Usage Examples

### Preview Only

```bash
./scripts/demo_orchestrator.py \
  --event-id evt-preview \
  --repo-root demo-repo \
  --review-json review.json \
  --preview-only
```

### Dry-Run Apply

```bash
./scripts/demo_orchestrator.py \
  --event-id evt-dry \
  --repo-root demo-repo \
  --review-json review.json \
  --yes \
  --dry-run
```

### Real Apply (Crash-Safe Commit)

```bash
./scripts/demo_orchestrator.py \
  --event-id evt-real \
  --repo-root demo-repo \
  --review-json review.json \
  --yes
```

**Output example:**

```
[orchestrator] review_loaded_from_event
[orchestrator] apply_success → applied: hello.py
[orchestrator] report_generated → artifacts/evt-demo/report.html

✓ Done — safe, deterministic, crash-safe apply complete.
```

---

## 🛡️ Safety Engine

**Detects:**

- `os.system()`
- `eval()`
- `exec()`
- `subprocess.run(..., shell=True)`
- Large patches (> 200 lines)
- Sensitive files (requirements.txt, setup.py, etc.)

**Warnings appear in:**

- CLI output
- `review.json`
- HTML report

---

## 🔄 Crash Recovery

```bash
python3 scripts/run_inspector.py --dry-run
python3 scripts/run_inspector.py
```

**Inspector can:**

- Resume partially applied operations
- Roll back corrupted operations
- Validate journal integrity

---

## 🌐 Webhooks (GitHub-Compatible)

Send demo webhooks:

```bash
./scripts/send_webhook_demo.sh
```

**Outputs:**

- Normalized event
- Summary
- Priority
- Trace IDs

---

## 📁 Artifacts Produced

```
artifacts/<event_id>/
  ├── review.json
  ├── apply_result.json
  └── report.html

backups/<event_id>/
  └── *.bak

journals/
  └── <event_id>.jsonl
```

All runs are traceable, reproducible, and auditable.

---

## 🧪 Tests

```bash
pytest -q
```

**✅ 179 tests passed**

**Covers:**

- Patch conflict resolution
- Safety engine
- Resume/rollback
- HTML reports
- End-to-end orchestration
- Worker + queue
- Deterministic patch application

---

## 🎬 Demo (12s)

<div align="center">
  <img src="data/demo.gif" width="900" alt="MARCS Demo"/>
</div>

---

## 🎨 Branding

Located in `data/marcs_branding_kit/`:

- `marcs-logo.svg`
- `marcs-mark.svg`
- `marcs-social-card.svg`
- `brand-guidelines.json`

**Style:**

- Primary — `#0b67ff`
- Accent — `#4cc1ff`
- Font — Inter / system-ui

---

## 🤝 Contributing

**Fork → Branch → Commit → Push → PR**

Contributions welcome!

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

MIT License — see [LICENSE](LICENSE).

---

## 👤 Author

**Aklesh Mishra**

AI/ML/GenAI Engineer • Multi-Agent Systems • LLM Infrastructure

- GitHub: [@Coder-12](https://github.com/Coder-12)
- Twitter: [@iminevitable10](https://x.com/iminevitable10)

---

<div align="center">

## ⭐ If MARCS impressed you, please star the repo!

**Built with precision for robustness, safety, determinism, and correctness.**

Made with ❤️ for the future of AI-powered development tools.

</div>