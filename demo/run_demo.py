import sys
import os
import time

# Add the project root to sys.path so we can import lib and agents, and pin the
# working directory to the repo root so agents resolve their relative
# data/<incident_id>/ paths regardless of where the demo is launched from.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(REPO_ROOT)
os.chdir(REPO_ROOT)

# Findings contain unicode (e.g. "pool.maxSize: 50 → 10", "Exception×5"). On
# Windows the default console/pipe codec is cp1252, which cannot encode those
# and would crash the demo mid-run. Force UTF-8 with graceful replacement.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from lib.band_client import BandClientWrapper
from lib.models import IncidentAlert, TriageTask, Finding, Severity
from lib import scenario_loader

# Import agent modules
import agents.commander.main as commander_agent
import agents.metrics_agent.main as metrics_agent
import agents.logs_agent.main as logs_agent
import agents.change_agent.main as change_agent
import agents.runbook_agent.main as runbook_agent

# Console coloring utilities
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"
RESET = "\033[0m"

# Persisted across stage headers so the active scenario stays visible throughout.
ACTIVE_SCENARIO = ""


def _sev_color(sev_label):
    return RED if sev_label == "SEV-1" else YELLOW if sev_label == "SEV-2" else GREEN


def print_header(title):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{MAGENTA} {title} {RESET}")
    if ACTIVE_SCENARIO:
        print(f"{CYAN} scenario: {ACTIVE_SCENARIO}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}")


def print_agent_log(agent_name, message, color=GREEN):
    print(f"{BOLD}{color}[{agent_name}]{RESET} {message}")


# ─── Scenario selection ──────────────────────────────────────────────────────

def _print_scenario_menu(infos):
    print(f"\n{BOLD}Select an incident scenario:{RESET}")
    for idx, info in enumerate(infos, 1):
        sev_color = _sev_color(info.sev_label)
        print(
            f"  {BOLD}[{idx}]{RESET} {info.title} "
            f"{sev_color}({info.sev_label}){RESET} {CYAN}- {info.service}{RESET}"
        )
        print(f"      {info.description}")


def _resolve_choice(raw, infos):
    """Map a raw answer (index like '2' or id like 'inc-002') to a scenario id."""
    raw = (raw or "").strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        n = int(raw)
        return infos[n - 1].id if 1 <= n <= len(infos) else None
    for info in infos:
        if info.id.lower() == raw:
            return info.id
    return None


def select_scenario(argv):
    """Resolve which scenario to run from argv, an interactive prompt, or a
    sensible default for non-interactive runs."""
    infos = scenario_loader.list_scenarios()
    if not infos:
        return None

    # Command-line override: `python demo/run_demo.py inc-002` or `... 2`.
    if len(argv) > 1:
        chosen = _resolve_choice(argv[1], infos)
        if chosen:
            return chosen
        print(f"{RED}Unknown scenario '{argv[1]}'. Showing picker instead.{RESET}")

    _print_scenario_menu(infos)

    if not sys.stdin.isatty():
        print(f"{YELLOW}(non-interactive input — defaulting to [1] {infos[0].title}){RESET}")
        return infos[0].id

    while True:
        try:
            raw = input(f"\n{BOLD}Enter choice (1-{len(infos)}, default 1): {RESET}")
        except EOFError:
            return infos[0].id
        if not raw.strip():
            return infos[0].id
        chosen = _resolve_choice(raw, infos)
        if chosen:
            return chosen
        print(f"{RED}Invalid choice '{raw.strip()}'. Try again.{RESET}")


def main(scenario_id=None):
    global ACTIVE_SCENARIO

    if scenario_id is None:
        scenario_id = select_scenario(sys.argv)
    if scenario_id is None:
        print(f"{RED}No scenarios found under data/. Nothing to run.{RESET}")
        return

    try:
        scenario = scenario_loader.load_scenario(scenario_id)
    except FileNotFoundError as exc:
        print(f"{RED}Error: {exc}{RESET}")
        return

    alert = scenario.alert
    info = scenario.info
    ACTIVE_SCENARIO = f"[{info.sev_label}] {info.title} ({scenario_id})"

    # 1. Initialize Shared Band Client
    shared_band = BandClientWrapper()

    # 2. Inject shared band client into all agents
    commander_agent.band = shared_band
    metrics_agent.band = shared_band
    logs_agent.band = shared_band
    change_agent.band = shared_band
    runbook_agent.band = shared_band

    # Fresh commander state for this run.
    commander_agent.incident_cache.clear()

    # 3. Subscribe agent handlers to the shared channels
    shared_band.subscribe("incident-events", commander_agent.handle_alert)
    shared_band.subscribe("triage-tasks", metrics_agent.handle_task)
    shared_band.subscribe("triage-tasks", logs_agent.handle_task)
    shared_band.subscribe("triage-tasks", change_agent.handle_task)
    shared_band.subscribe("triage-tasks", runbook_agent.handle_task)
    shared_band.subscribe("triage-findings", commander_agent.handle_finding)

    print_header("THE WAR ROOM - MULTI-AGENT INCIDENT RESPONSE SIMULATION")

    # 4. Present the loaded Incident Alert
    sev_color = RED if alert.severity >= Severity.HIGH else YELLOW
    print(f"{BOLD}Incident Alert Detected:{RESET}")
    print(f"  - {BOLD}ID:{RESET} {alert.id}")
    print(f"  - {BOLD}Title:{RESET} {YELLOW}{alert.title}{RESET}")
    print(f"  - {BOLD}Service:{RESET} {alert.service}")
    print(f"  - {BOLD}Description:{RESET} {alert.description}")
    print(
        f"  - {BOLD}Severity:{RESET} {sev_color}{alert.severity.name} "
        f"({_sev_color(info.sev_label)}{info.sev_label}{sev_color}){RESET}"
    )
    time.sleep(1)

    # 5. Trigger the Commander
    print_header("STAGE 1: INCIDENT COMMANDER TRIAGE ALLOCATION")
    print_agent_log("Commander", "Received alert. Creating Incident Space & assigning tasks...", BLUE)

    # Publish incident alert (mode="json" so the Severity enum serializes to int)
    shared_band.publish("incident-events", alert.model_dump(mode="json"), "alert-system")

    # Poll incident events so the commander receives the alert and fans out tasks
    shared_band.poll("incident-events")
    time.sleep(1)

    # Print out assignments
    triage_tasks_queue = shared_band.message_queue.get("triage-tasks", [])
    print(f"\n{BOLD}Commander Published Triage Tasks ({len(triage_tasks_queue)}):{RESET}")
    for idx, envelope in enumerate(triage_tasks_queue):
        payload = envelope["payload"]
        print(f"  [{idx + 1}] Assigned to: {GREEN}{payload['assigned_to']}{RESET}")
        print(f"      Description: {payload['description']}")
    time.sleep(1.5)

    # 6. Execute Triage Agents
    print_header("STAGE 2: MULTI-AGENT PARALLEL DOMAIN TRIAGE")

    # Poll triage-tasks so each agent processes all tasks
    while shared_band.message_queue.get("triage-tasks"):
        shared_band.poll("triage-tasks")

    time.sleep(1)

    # 7. Collect Findings
    findings_queue = shared_band.message_queue.get("triage-findings", [])
    deliberation_queue = shared_band.message_queue.get("deliberation", [])

    print_agent_log("Metrics Agent", "Analyzing CPU, Memory & Latency telemetry...", GREEN)
    print_agent_log("Logs Agent", "Searching through application exceptions and tracing IDs...", YELLOW)
    print_agent_log("Change Agent", "Checking deployment pipelines and configurations...", CYAN)
    print_agent_log("Runbook Agent", "Querying matching runbooks and mitigation runsheets...", MAGENTA)
    time.sleep(1.5)

    print_header("STAGE 3: TRIAGE FINDINGS REPORTED")

    # Let's deduplicate or group findings by agent to display them clearly
    findings_by_agent = {}
    for envelope in findings_queue:
        payload = envelope["payload"]
        agent = payload["agent"]
        if agent not in findings_by_agent:
            findings_by_agent[agent] = Finding(**payload)

    for agent, finding in findings_by_agent.items():
        color = GREEN if "metrics" in agent else YELLOW if "logs" in agent else CYAN if "change" in agent else MAGENTA
        print(f"\n{BOLD}{color}>>> {agent.upper()} Finding Details:{RESET}")
        print(f"  - {BOLD}Finding ID:{RESET} {finding.finding_id}")
        print(f"  - {BOLD}Type:{RESET} {finding.finding_type}")
        print(f"  - {BOLD}Signal:{RESET} {finding.signal}")
        print(f"  - {BOLD}Value:{RESET} {finding.value}")
        print(f"  - {BOLD}Confidence:{RESET} {color}{finding.confidence:.2f}{RESET}")
        print(f"  - {BOLD}Hypothesis:{RESET} {finding.hypothesis}")
        print(f"  - {BOLD}Summary:{RESET} {finding.summary}")
        if finding.supporting_data:
            print(f"  - {BOLD}Supporting Data:{RESET} {finding.supporting_data}")
        time.sleep(1)

    # 8. Show Deliberations (if any)
    if deliberation_queue:
        print_header("STAGE 4: INCIDENT WAR ROOM DELIBERATION CHAT")
        shown_messages = set()
        for envelope in deliberation_queue:
            payload = envelope["payload"]
            sender = payload["sender"]
            content = payload["content"]
            msg_key = f"{sender}:{content}"
            if msg_key not in shown_messages:
                shown_messages.add(msg_key)
                color = GREEN if "metrics" in sender else YELLOW if "logs" in sender else CYAN if "change" in sender else MAGENTA
                print(f"[CHAT] {BOLD}{color}{sender}:{RESET} \"{content}\"")
                time.sleep(1)
    else:
        print(f"\n{BOLD}{YELLOW}No deliberations reported (Incident severity requires direct Commander verdict).{RESET}\n")

    # 9. Formulate Verdict
    print_header("STAGE 5: INCIDENT VERDICT & RESOLUTION COORDINATION")

    print_agent_log("Commander", "Synthesizing triage reports and runbook recommendations...", BLUE)

    # Poll triage-findings so the commander processes them and generates a verdict
    while shared_band.message_queue.get("triage-findings"):
        shared_band.poll("triage-findings")

    time.sleep(1.5)

    verdict_queue = shared_band.message_queue.get("commander-verdict", [])
    if verdict_queue:
        payload = verdict_queue[0]["payload"]
        verdict_desc = payload["verdict"]
        actions = payload["remediation"]

        status = payload["status"]
        status_color = GREEN if status == "resolved" else YELLOW if status == "mitigating" else RED

        print(f"\n{BOLD}{GREEN}[VERDICT] Commander Verdict Formulated:{RESET}")
        print(f"  - {BOLD}Incident ID:{RESET} {payload['incident_id']}")
        print(f"  - {BOLD}Status:{RESET} {status_color}{status.upper()}{RESET} (confidence={payload['confidence']:.2f})")
        print(f"  - {BOLD}Severity:{RESET} {payload['severity']}")
        print(f"  - {BOLD}Verdict:{RESET} {YELLOW}{verdict_desc}{RESET}")
        print(f"  - {BOLD}Root Cause:{RESET} {payload['root_cause']}")
        print(f"  - {BOLD}Actions Recommended:{RESET}")
        for idx, act in enumerate(actions):
            print(f"    {idx+1}. {act}")
        print(f"  - {BOLD}Evidence IDs:{RESET} {', '.join(payload['evidence_ids'])}")
        print(f"  - {BOLD}Deliberation Summary:{RESET} {payload['deliberation_summary']}")

        print_header("STAGE 6: GENERATED ARTIFACTS")
        print(f"{BOLD}{CYAN}-- Status Page Update --{RESET}")
        print(payload["status_page"])
        print(f"\n{BOLD}{CYAN}-- Draft Postmortem --{RESET}")
        print(payload["draft_postmortem"])
    else:
        print(f"\n{BOLD}{RED}No verdict published by the Commander.{RESET}")

    print(f"\n{BOLD}{GREEN}=== SIMULATION COMPLETE ({scenario_id}) ==={RESET}\n")


if __name__ == "__main__":
    main()
