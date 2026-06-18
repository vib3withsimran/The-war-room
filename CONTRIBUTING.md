# Contributing to The War Room

Thank you for contributing to **The War Room**! To maintain code quality and ensure the multi-agent incident response simulation runs correctly, please follow these guidelines.

---

## 🛠️ Development Setup

### 1. Prerequisites

- **Python:** Version `3.10` or higher.
- **Node.js & NPM:** (Optional, for Prettier formatting).

### 2. Environment Installation

Initialize and activate your virtual environment:

```bash
# Create virtual env
python -m venv .venv

# Activate (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# Activate (Unix/macOS)
source .venv/bin/activate

# Install dependencies
pip install pydantic pytest
```

---

## 🎨 Code Style Guidelines

To keep the codebase clean and formatted:

1. **Python:** Keep code PEP 8 compliant. Use type annotations for shared models and utility libraries.
2. **Web Assets (HTML, CSS, JS, Markdown):** Format using **Prettier** with the configuration in `.prettierrc`. You can format all web assets using:
   ```bash
   npx prettier --write "ui/**/*" "demo/**/*.md" "README.md" "CONTRIBUTING.md"
   ```

---

## 🧪 Testing Requirements

Before pushing any code, verify that all tests pass. All test modules live in the `tests/` directory and are configured with pytest.

Run the test suite:

```bash
python -m pytest
```

- **Adding Features:** If you add new agents, scorers, or remediation capabilities, you **must** write corresponding tests in the `tests/` directory (e.g. `test_new_feature.py`).
- **Postmortems:** Do not commit temporary postmortem outputs created by test cases.

---

## 📂 Creating and Compiling Incident Scenarios

The system is data-driven; it loads scenarios dynamically from `data/` instead of hardcoding incident data.

### 1. Scenario Directory Structure

To add a new scenario, create a folder under `data/<incident_id>/` (e.g. `data/inc-004/`) with the following files:

```text
data/inc-004/
├── alert.json                  # Target alert (service, severity, description)
├── changes/
│   └── deploys.json            # Deployment log diffs and timestamps
├── logs/
│   └── events.jsonl            # JSON lines representing log logs/errors
├── metrics/
│   └── snapshots.json          # Metric snapshots (p99 latency, CPU, pool size)
└── runbooks/
    └── <service-name>.md       # Markdown resolution guidelines
```

### 2. Compile Scenario Data for the Web Dashboard

Since the static HTML web dashboard cannot execute Python code directly, you must run the data generator script after adding or modifying any scenario. This executes the incident response agents over the new data and saves the output to `ui/scenarios.js`:

```bash
python demo/gen_dashboard_data.py
```

---

## 🚨 Git-Ops & Remediation Features

1. **Remediation Plans:** All simulated actions should map to the details in `lib/remediation.py` or inherit the default auto-command fallback. Ensure that the duration values are kept small enough to run simulations smoothly.
2. **Postmortem Auto-Commit:** The Incident Commander automatically commits the generated markdown report to Git on resolution. Ensure your local Git configure has a default user name and email configured:
   ```bash
   git config user.name "Your Name"
   git config user.email "your.email@example.com"
   ```
