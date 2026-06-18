"""Generate ``ui/scenarios.js`` from the *real* agent pipeline.

The static dashboard (``ui/dashboard.html``) can't run Python, so instead of
hardcoding agent findings we run every scenario through the actual Commander +
4-agent pipeline and serialize the results to a JS file the dashboard reads via
``<script src="scenarios.js">``. This keeps the dashboard faithful to what the
agents truly produce — re-run this whenever scenarios or agent logic change:

    python demo/gen_dashboard_data.py
"""
import json
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(REPO_ROOT)
os.chdir(REPO_ROOT)

import logging
logging.disable(logging.CRITICAL)  # silence per-message Band logs while generating

from lib.band_client import BandClientWrapper
from lib import scenario_loader
from lib.artifact_generator import STATUS_LABELS
import agents.commander.main as commander
import agents.metrics_agent.main as metrics_agent
import agents.logs_agent.main as logs_agent
import agents.change_agent.main as change_agent
import agents.runbook_agent.main as runbook_agent

OUTPUT = os.path.join(REPO_ROOT, "ui", "scenarios.js")

# agent id -> (display key, display name, render order)
AGENT_DISPLAY = {
    "metrics-agent": ("metrics", "Metrics", 0),
    "logs-agent": ("logs", "Logs", 1),
    "change-agent": ("change", "Change", 2),
    "runbook-agent": ("runbook", "Runbook", 3),
}

# signal -> short human-readable headline for the agent card
SIGNAL_SUMMARY = {
    "connection_pool_exhaustion": "Connection Pool Exhausted",
    "latency_spike": "Latency Anomaly",
    "elevated_latency": "Elevated Latency",
    "total_outage": "Total Outage",
    "cpu_saturation": "CPU Saturation",
    "slow_queries": "Slow Queries",
    "elevated_errors": "Elevated Errors",
    "no_errors": "No Errors Found",
    "null_reference_crash": "Null Reference Crash",
    "oom_crash": "Out Of Memory",
    "timeout": "Timeouts",
    "process_crash": "Process Crash",
    "high_impact_deploy": "High-Impact Deploy",
    "recent_deploy": "Recent Deploy",
    "no_recent_changes": "No Recent Changes",
    "runbook_stale": "Stale Runbook",
    "runbook_current": "Runbook Current",
}


def _humanize(signal: str) -> str:
    return SIGNAL_SUMMARY.get(signal, signal.replace("_", " ").title())


def _run_pipeline(scenario):
    """Run one scenario through Commander + 4 agents on a fresh Band bus and
    return (ordered findings, deliberation messages, verdict payload)."""
    band = BandClientWrapper()
    for mod in (commander, metrics_agent, logs_agent, change_agent, runbook_agent):
        mod.band = band
    commander.incident_cache.clear()

    band.subscribe("incident-events", commander.handle_alert)
    for agent in (metrics_agent, logs_agent, change_agent, runbook_agent):
        band.subscribe("triage-tasks", agent.handle_task)
    band.subscribe("triage-findings", commander.handle_finding)

    band.publish("incident-events", scenario.alert.model_dump(mode="json"), "alert-system")
    band.poll("incident-events")
    while band.message_queue.get("triage-tasks"):
        band.poll("triage-tasks")
    while band.message_queue.get("triage-findings"):
        band.poll("triage-findings")

    state = commander.incident_cache.get(scenario.info.id, {})
    findings = state.get("findings", [])
    deliberation = [e["payload"] for e in band.message_queue.get("deliberation", [])]
    verdicts = band.message_queue.get("commander-verdict", [])
    verdict = verdicts[0]["payload"] if verdicts else None
    return findings, deliberation, verdict


def _agent_card(finding):
    key, name, order = AGENT_DISPLAY.get(finding.agent, (finding.agent, finding.agent.title(), 99))
    return {
        "key": key,
        "name": name,
        "order": order,
        "findingType": finding.finding_type,
        "signal": finding.signal,
        "summary": _humanize(finding.signal),
        "detail": finding.value,
        "hypothesis": finding.hypothesis,
        "confidence": round(finding.confidence, 2),
    }


def build_scenario(info):
    scenario = scenario_loader.load_scenario(info.id)
    findings, deliberation, verdict = _run_pipeline(scenario)

    agents = sorted((_agent_card(f) for f in findings), key=lambda c: c["order"])
    status = verdict["status"] if verdict else "unknown"

    return {
        "id": info.id,
        "alertId": scenario.alert.id,
        "title": info.title,
        "description": info.description,
        "service": info.service,
        "severity": int(info.severity),
        "sevLabel": info.sev_label,
        "sevClass": info.sev_label.lower(),  # "sev-1" / "sev-2" / "sev-3"
        "timeWindow": info.time_window,
        "status": status,
        "statusLabel": STATUS_LABELS.get(status, status.title()),
        "confidence": verdict["confidence"] if verdict else 0.0,
        "verdict": verdict["verdict"] if verdict else "",
        "rootCause": verdict["root_cause"] if verdict else "",
        "remediation": verdict["remediation"] if verdict else [],
        "agents": agents,
        "deliberation": [
            {"sender": m.get("sender", "agent"), "content": m.get("content", "")}
            for m in deliberation
        ],
    }


def main():
    infos = scenario_loader.list_scenarios()
    scenarios = {info.id: build_scenario(info) for info in infos}
    payload = {"order": [info.id for info in infos], "scenarios": scenarios}

    body = json.dumps(payload, indent=2, ensure_ascii=False)
    banner = (
        "// AUTO-GENERATED by demo/gen_dashboard_data.py — do not edit by hand.\n"
        "// Produced by running each scenario through the real Commander + agent\n"
        "// pipeline so the dashboard reflects genuine, data-driven findings.\n"
    )
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(banner)
        f.write("window.WAR_ROOM_SCENARIOS = ")
        f.write(body)
        f.write(";\n")

    print(f"Wrote {len(scenarios)} scenarios to {os.path.relpath(OUTPUT, REPO_ROOT)}")
    for sid in payload["order"]:
        s = scenarios[sid]
        print(f"  {sid}: {s['sevLabel']} {s['status']} (conf={s['confidence']}) — {len(s['agents'])} agents")


if __name__ == "__main__":
    main()
