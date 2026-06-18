"""Tests for the scenario loader that powers the CLI picker and the dashboard
data generator (Wow Feature 3 — interactive simulation scenarios)."""
import pytest

from lib import scenario_loader
from lib.models import IncidentAlert, Severity


# ─── Discovery ───────────────────────────────────────────────────────────────

class TestListScenarios:
    def test_finds_the_three_phase5_scenarios(self):
        ids = [info.id for info in scenario_loader.list_scenarios()]
        assert {"inc-001", "inc-002", "inc-003"}.issubset(set(ids))

    def test_results_are_sorted_by_id(self):
        ids = [info.id for info in scenario_loader.list_scenarios()]
        assert ids == sorted(ids)

    def test_titles_come_from_alert_json_not_hardcoded(self):
        by_id = {i.id: i for i in scenario_loader.list_scenarios()}
        assert by_id["inc-001"].title == "API Gateway Latency Spike"
        assert by_id["inc-002"].title == "User Service Elevated Latency"
        assert by_id["inc-003"].title == "Payment Service Full Outage"

    def test_severity_labels_are_distinct_and_correct(self):
        by_id = {i.id: i for i in scenario_loader.list_scenarios()}
        assert by_id["inc-001"].sev_label == "SEV-2"  # HIGH
        assert by_id["inc-002"].sev_label == "SEV-3"  # MEDIUM
        assert by_id["inc-003"].sev_label == "SEV-1"  # CRITICAL

    def test_severity_is_an_enum(self):
        by_id = {i.id: i for i in scenario_loader.list_scenarios()}
        assert by_id["inc-003"].severity == Severity.CRITICAL
        assert by_id["inc-002"].severity == Severity.MEDIUM

    def test_missing_data_dir_returns_empty(self, tmp_path):
        assert scenario_loader.list_scenarios(tmp_path / "nope") == []


# ─── Loading ─────────────────────────────────────────────────────────────────

class TestLoadScenario:
    def test_loads_all_domain_data_for_inc001(self):
        s = scenario_loader.load_scenario("inc-001")
        assert isinstance(s.alert, IncidentAlert)
        assert s.alert.service == "api-gateway"
        assert len(s.metrics.get("snapshots", [])) > 0
        assert len(s.logs) > 0
        assert len(s.changes.get("deploys", [])) > 0
        assert "API Gateway" in s.runbook

    def test_scenarios_have_distinct_data(self):
        a = scenario_loader.load_scenario("inc-001")
        b = scenario_loader.load_scenario("inc-002")
        # Different services, runbooks, and metric profiles — not copies.
        assert a.alert.service != b.alert.service
        assert a.runbook != b.runbook
        assert a.metrics != b.metrics

    def test_logs_are_parsed_as_dicts(self):
        s = scenario_loader.load_scenario("inc-003")
        assert all(isinstance(row, dict) for row in s.logs)
        assert any(row.get("level") in ("ERROR", "FATAL") for row in s.logs)

    def test_info_matches_alert(self):
        s = scenario_loader.load_scenario("inc-002")
        assert s.info.id == "inc-002"
        assert s.info.title == s.alert.title
        assert s.info.description == s.alert.description

    def test_unknown_scenario_raises(self):
        with pytest.raises(FileNotFoundError):
            scenario_loader.load_scenario("inc-does-not-exist")
