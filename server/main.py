"""
FastAPI server — wraps the War Room agent pipeline as a REST API.

Serves the static dashboard UI and provides live triage/remediation/postmortem
endpoints so the dashboard calls real agents instead of canned data.

Usage:
    python -m server.main              # localhost:8000
    python -m server.main --port 8080  # custom port
"""
import sys
import os
import time
import json
from pathlib import Path

# Ensure project root is on sys.path so agent imports resolve correctly.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(str(REPO_ROOT))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lib.band_client import BandClientWrapper
from lib.models import Finding, CommanderVerdict
from lib import scenario_loader

# Agent modules — we override their module-level `band` per request.
import agents.commander.main as commander_agent
import agents.metrics_agent.main as metrics_agent
import agents.logs_agent.main as logs_agent
import agents.change_agent.main as change_agent
import agents.runbook_agent.main as runbook_agent

AGENT_MODULES = {
    "commander": commander_agent,
    "metrics": metrics_agent,
    "logs": logs_agent,
    "change": change_agent,
    "runbook": runbook_agent,
}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="War Room API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory result store — each triage run stores its output keyed by
# incident_id so remediation/postmortem can reference it.
# ---------------------------------------------------------------------------

_results: dict[str, dict] = {}


def _build_agent_entry(finding: Finding) -> dict:
    """Convert a Finding model to the dict shape the dashboard expects."""
    sd = finding.supporting_data or {}
    return {
        "key": finding.agent.replace("-agent", "").split("-")[0],
        "name": finding.agent.replace("-agent", "").title(),
        "order": 0,
        "findingType": finding.finding_type,
        "signal": finding.signal,
        "summary": finding.summary,
        "detail": finding.value,
        "hypothesis": finding.hypothesis,
        "confidence": round(finding.confidence, 2),
        "supporting_data": sd,
    }


def _build_triage_response(scenario_id: str) -> dict:
    """Return canned scenario metadata for the dashboard's initial load."""
    infos = scenario_loader.list_scenarios()
    for info in infos:
        if info.id == scenario_id:
            sev_map = {1: "SEV-3", 2: "SEV-3", 3: "SEV-2", 4: "SEV-1"}
            status_map = {1: "mitigating", 2: "mitigating", 3: "resolved", 4: "resolved"}
            return {
                "id": info.id,
                "title": info.title,
                "description": info.description,
                "service": info.service,
                "severity": int(info.severity),
                "sevLabel": sev_map.get(int(info.severity), "SEV-3"),
                "timeWindow": info.time_window,
                "statusLabel": status_map.get(int(info.severity), "mitigating"),
                "agents": [],
                "verdict": "",
                "rootCause": "",
                "remediation": [],
                "deliberation": [],
                "confidence": 0.0,
                "status": "pending",
            }
    raise HTTPException(status_code=404, detail=f"Scenario '{scenario_id}' not found")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/scenarios")
def api_list_scenarios():
    """Return all available scenarios with their metadata."""
    infos = scenario_loader.list_scenarios()
    sev_map = {1: "SEV-3", 2: "SEV-3", 3: "SEV-2", 4: "SEV-1"}
    result = []
    for info in infos:
        result.append({
            "id": info.id,
            "title": info.title,
            "description": info.description,
            "service": info.service,
            "severity": int(info.severity),
            "sevLabel": sev_map.get(int(info.severity), "SEV-3"),
            "timeWindow": info.time_window,
        })
    return {"scenarios": result}


@app.get("/api/scenarios/{scenario_id}")
def api_get_scenario(scenario_id: str):
    """Return full scenario data including canned agent info."""
    return _build_triage_response(scenario_id)


@app.post("/api/triage/{scenario_id}")
def api_run_triage(scenario_id: str):
    """Run the full agent triage pipeline for a scenario and return results."""
    # 1. Load scenario
    try:
        scenario = scenario_loader.load_scenario(scenario_id)
    except FileNotFoundError:
        raise HTTPException(404, detail=f"Scenario '{scenario_id}' not found")

    alert = scenario.alert
    info = scenario.info

    # 2. Create fresh BandClientWrapper for this run
    fresh_band = BandClientWrapper()

    # 3. Inject into all agent modules
    for mod in AGENT_MODULES.values():
        mod.band = fresh_band

    # Clear commander cache
    commander_agent.incident_cache.clear()

    # 4. Subscribe handlers
    fresh_band.subscribe("incident-events", commander_agent.handle_alert)
    fresh_band.subscribe("triage-tasks", metrics_agent.handle_task)
    fresh_band.subscribe("triage-tasks", logs_agent.handle_task)
    fresh_band.subscribe("triage-tasks", change_agent.handle_task)
    fresh_band.subscribe("triage-tasks", runbook_agent.handle_task)
    fresh_band.subscribe("triage-findings", commander_agent.handle_finding)

    # 5. Publish alert → triggers Commander → fan-out → agents → findings
    fresh_band.publish("incident-events", alert.model_dump(mode="json"), "alert-system")
    fresh_band.poll("incident-events")
    time.sleep(0.5)

    # 6. Process triage tasks (agents publish findings into the queue)
    while fresh_band.message_queue.get("triage-tasks"):
        fresh_band.poll("triage-tasks")
        time.sleep(0.2)

    # 7. Snapshot findings from queue BEFORE commander consumes them
    raw_findings = list(fresh_band.message_queue.get("triage-findings", []))

    # 8. Now feed findings to commander (poll triggers handle_finding → verdict)
    for _ in raw_findings:
        fresh_band.poll("triage-findings")

    time.sleep(0.5)

    # 9. Collect results
    deliberation_queue = fresh_band.message_queue.get("deliberation", [])
    verdict_queue = fresh_band.message_queue.get("commander-verdict", [])

    # Deduplicate findings by agent
    findings_by_agent: dict[str, Finding] = {}
    for envelope in raw_findings:
        payload = envelope["payload"]
        agent = payload["agent"]
        if agent not in findings_by_agent:
            findings_by_agent[agent] = Finding(**payload)

    # Build agent entries
    agent_entries = []
    for agent_key in ["metrics-agent", "logs-agent", "change-agent", "runbook-agent"]:
        if agent_key in findings_by_agent:
            entry = _build_agent_entry(findings_by_agent[agent_key])
            agent_entries.append(entry)

    # Build deliberation entries
    delib_entries = []
    for envelope in deliberation_queue:
        payload = envelope["payload"]
        delib_entries.append({
            "sender": payload["sender"],
            "content": payload["content"],
        })

    # Build verdict
    sev_map = {1: "SEV-3", 2: "SEV-3", 3: "SEV-2", 4: "SEV-1"}

    if verdict_queue:
        vp = verdict_queue[0]["payload"]
        verdict_text = vp["verdict"]
        root_cause = vp["root_cause"]
        remediation = vp["remediation"]
        confidence = vp["confidence"]
        status = vp["status"]
        sev_label = vp["severity"]
        incident_id = vp["incident_id"]
        status_label = status.title() if status else "Resolved"
    else:
        # Fallback — commander didn't produce a verdict
        verdict_text = f"Triage complete for {alert.title}"
        root_cause = "No root cause determined"
        remediation = ["Monitor service closely"]
        confidence = 0.5
        status = "mitigating"
        sev_label = sev_map.get(int(info.severity), "SEV-3")
        incident_id = scenario_id
        status_label = "Investigating"

    triage_config_map = {
        "metrics": {"name": "Metrics", "findingType": "anomaly"},
        "logs": {"name": "Logs", "findingType": "log_anomaly"},
        "change": {"name": "Change", "findingType": "change_correlation"},
        "runbook": {"name": "Runbook", "findingType": "runbook_match"},
    }

    for entry in agent_entries:
        cfg = triage_config_map.get(entry["key"], {})
        if cfg:
            entry["name"] = cfg["name"]
            if not entry["findingType"] or entry["findingType"] == "observation":
                entry["findingType"] = cfg["findingType"]

    result = {
        "id": scenario_id,
        "incident_id": incident_id,
        "title": alert.title,
        "description": alert.description,
        "service": info.service,
        "severity": int(info.severity),
        "sevLabel": sev_label,
        "sevClass": f"sev-{int(info.severity)}",
        "timeWindow": info.time_window,
        "status": status,
        "statusLabel": status_label,
        "confidence": round(float(confidence), 2),
        "verdict": verdict_text,
        "rootCause": root_cause,
        "remediation": remediation,
        "agents": agent_entries,
        "deliberation": delib_entries,
        "evidence_ids": vp.get("evidence_ids", []) if verdict_queue else [],
    }

    # Store for subsequent remediation/postmortem calls
    _results[incident_id] = result

    return result


@app.post("/api/remediate/{incident_id}")
def api_run_remediation(incident_id: str):
    """Simulate remediation and return updated state."""
    result = _results.get(incident_id)
    if not result:
        raise HTTPException(404, detail=f"No triage results for '{incident_id}'. Run triage first.")

    from lib.remediation import RemediationEngine

    engine = RemediationEngine.from_recommendations(incident_id, result["remediation"])
    engine.execute()  # synchronous simulation

    result["status"] = "resolved"
    result["statusLabel"] = "Resolved"
    result["mttr"] = engine.plan.total_mttr

    return {"status": "completed", "mttr": engine.plan.total_mttr, "actions_count": len(engine.plan.actions)}


@app.post("/api/postmortem/{incident_id}")
def api_generate_postmortem(incident_id: str):
    """Generate and commit postmortem. Returns commit URL."""
    result = _results.get(incident_id)
    if not result:
        raise HTTPException(404, detail=f"No triage results for '{incident_id}'. Run triage first.")

    from lib.postmortem_generator import generate_postmortem
    from lib.git_ops import commit_postmortem, get_commit_url
    from lib.models import CommanderVerdict

    # Build a CommanderVerdict from the stored result
    verdict = CommanderVerdict(
        incident_id=incident_id,
        status=result["status"],
        verdict=result["verdict"],
        root_cause=result["rootCause"],
        severity=result["sevLabel"],
        confidence=result["confidence"],
        remediation=result["remediation"],
        draft_postmortem="",
        status_page="",
        evidence_ids=result.get("evidence_ids", []),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    postmortem_md = generate_postmortem(
        verdict=verdict,
        findings=[],
        deliberation_log=result.get("deliberation", []),
        total_mttr=result.get("mttr", 0.0),
    )

    success, commit_hash = commit_postmortem(incident_id, postmortem_md)
    if success and commit_hash:
        url = get_commit_url(commit_hash)
        return {"committed": True, "hash": commit_hash, "url": url}
    elif success:
        return {"committed": False, "hash": "", "url": "", "detail": "Saved to disk only (git unavailable)"}
    else:
        raise HTTPException(500, detail="Failed to write postmortem")


# ---------------------------------------------------------------------------
# Static file mount — MUST be after API routes so /api/* matches first.
# ---------------------------------------------------------------------------

UI_DIR = REPO_ROOT / "ui"
if UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main():
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
    print(f"War Room API running at http://localhost:{port}")
    print(f"  Dashboard:  http://localhost:{port}/")
    print(f"  API docs:   http://localhost:{port}/docs")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
