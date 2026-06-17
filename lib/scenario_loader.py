"""Scenario loader — discovers and loads incident scenarios from ``data/``.

The CLI demo (interactive picker) and the dashboard data generator both use
this module so that every surface reads the *same* on-disk scenarios instead of
hardcoding incident details. A scenario is any ``data/<id>/`` directory that
contains an ``alert.json``; its domain data (metrics, logs, changes, runbook)
lives in sibling sub-directories using the Phase 5 layout.

Paths are resolved relative to the repository root (via ``__file__``) so the
loader works regardless of the caller's current working directory.
"""
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from lib.models import IncidentAlert, Severity
from lib.artifact_generator import map_severity

__all__ = ["ScenarioInfo", "Scenario", "list_scenarios", "load_scenario", "DATA_DIR"]

# data/ sits next to lib/ at the repo root. Resolve absolutely so discovery
# does not depend on the process working directory.
DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"


@dataclass
class ScenarioInfo:
    """Lightweight summary of a scenario, for menus and sidebars."""

    id: str
    title: str
    description: str
    severity: Severity
    service: str = "unknown-service"
    sev_label: str = "SEV-3"  # SEV-1 / SEV-2 / SEV-3, derived from severity
    time_window: str = ""


@dataclass
class Scenario:
    """A fully-loaded scenario: the alert plus all raw domain data files."""

    info: ScenarioInfo
    alert: IncidentAlert
    metrics: Dict[str, Any] = field(default_factory=dict)
    logs: List[Dict[str, Any]] = field(default_factory=list)
    changes: Dict[str, Any] = field(default_factory=dict)
    runbook: str = ""


# ─── internal helpers ────────────────────────────────────────────────────────

def _resolve_base(data_dir: Optional[str | pathlib.Path]) -> pathlib.Path:
    return pathlib.Path(data_dir) if data_dir else DATA_DIR


def _load_alert(scenario_dir: pathlib.Path) -> IncidentAlert:
    data = json.loads((scenario_dir / "alert.json").read_text(encoding="utf-8"))
    return IncidentAlert(**data)


def _info_from_alert(scenario_id: str, alert: IncidentAlert) -> ScenarioInfo:
    return ScenarioInfo(
        id=scenario_id,
        title=alert.title,
        description=alert.description,
        severity=alert.severity,
        service=alert.service,
        sev_label=map_severity(alert.severity),
        time_window=alert.time_window,
    )


def _read_json(path: pathlib.Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _read_jsonl(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _read_runbook(runbook_dir: pathlib.Path, service: str) -> str:
    """Read the runbook markdown for a service, mirroring the runbook agent's
    file-discovery order."""
    if not runbook_dir.exists():
        return ""
    for candidate in (f"{service}.md", f"{service}-runbook.md", "runbook.md"):
        p = runbook_dir / candidate
        if p.exists():
            return p.read_text(encoding="utf-8")
    md_files = sorted(runbook_dir.glob("*.md"))
    return md_files[0].read_text(encoding="utf-8") if md_files else ""


# ─── public API ──────────────────────────────────────────────────────────────

def list_scenarios(data_dir: Optional[str | pathlib.Path] = None) -> List[ScenarioInfo]:
    """Return summaries for every scenario found under ``data/``.

    A directory qualifies as a scenario only if it contains an ``alert.json``.
    Results are sorted by scenario id for stable menu ordering.
    """
    base = _resolve_base(data_dir)
    if not base.exists():
        return []

    infos: List[ScenarioInfo] = []
    for scenario_dir in sorted(base.iterdir()):
        if not scenario_dir.is_dir() or not (scenario_dir / "alert.json").exists():
            continue
        try:
            alert = _load_alert(scenario_dir)
        except (json.JSONDecodeError, OSError, ValueError):
            # Skip malformed scenarios rather than failing the whole listing.
            continue
        infos.append(_info_from_alert(scenario_dir.name, alert))
    return infos


def load_scenario(
    scenario_id: str, data_dir: Optional[str | pathlib.Path] = None
) -> Scenario:
    """Load all data for a single scenario.

    Raises ``FileNotFoundError`` if the scenario directory has no ``alert.json``.
    Missing domain files (metrics/logs/changes/runbook) degrade gracefully to
    empty containers so a partially-populated scenario still loads.
    """
    base = _resolve_base(data_dir)
    scenario_dir = base / scenario_id
    if not (scenario_dir / "alert.json").exists():
        raise FileNotFoundError(
            f"No scenario '{scenario_id}' under {base} (missing alert.json)"
        )

    alert = _load_alert(scenario_dir)
    return Scenario(
        info=_info_from_alert(scenario_id, alert),
        alert=alert,
        metrics=_read_json(scenario_dir / "metrics" / "snapshots.json", default={}),
        logs=_read_jsonl(scenario_dir / "logs" / "events.jsonl"),
        changes=_read_json(scenario_dir / "changes" / "deploys.json", default={}),
        runbook=_read_runbook(scenario_dir / "runbooks", alert.service),
    )
