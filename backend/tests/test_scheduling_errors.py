import pytest
import requests
from fhirpy.base.exceptions import ForbiddenError

import app.agents.graph as graph_mod
from app.agents import scheduling_agent
from app.core.db import AuditRow, db_session
from fastapi.testclient import TestClient
from main import app
from tests.test_scheduling_agent import FakeClient


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize(
    "exc, code",
    [
        (requests.Timeout(), "GATEWAY_TIMEOUT"),
        (requests.ConnectionError(), "GATEWAY_CONNECTION_FAILED"),
        (ForbiddenError("SMC-B certificate invalid"), "GATEWAY_AUTH_ERROR"),
    ],
)
def test_infrastructure_errors_route_to_error_handler(monkeypatch, exc, code):
    def boom(state):
        raise exc

    monkeypatch.setattr(scheduling_agent, "_book", boom)
    r = scheduling_agent.scheduling_node({"patient_id": "X1"})
    assert r == {"scheduling_error": code, "next_agent": "ErrorHandler"}


def test_programming_errors_are_not_swallowed(monkeypatch):
    monkeypatch.setattr(scheduling_agent, "_book", lambda s: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        scheduling_agent.scheduling_node({"patient_id": "X1"})


def test_slot_search_outage_is_not_reported_as_no_slot():
    class Down:
        def resources(self, *_):
            raise requests.ConnectionError()

    with pytest.raises(requests.ConnectionError):
        scheduling_agent._find_free_slot(Down(), scheduling_agent.datetime.now())


def test_error_handler_gives_friendly_message_and_audits_alert():
    r = scheduling_agent.error_handler_node(
        {"patient_id": "X1", "triage_score": "Gelb", "scheduling_error": "GATEWAY_TIMEOUT"}
    )
    assert r["scheduling_failed"] is True and r["next_agent"] == "END"
    assert "technischen Störung" in r["reply"] and "GATEWAY" not in r["reply"]
    with db_session() as d:
        row = d.query(AuditRow).one()
        assert row.event == "scheduling_failed"


def test_identity_mapping_error_requests_manual_contact_without_claiming_notification():
    r = scheduling_agent.error_handler_node(
        {"patient_id": "pairwise-sub", "scheduling_error": "PATIENT_AMBIGUOUS"}
    )
    assert r["scheduling_failed"] is True
    assert "kein Termin gebucht" in r["reply"]
    assert "wenden Sie sich" in r["reply"]
    assert "informiert" not in r["reply"]


def test_missing_kis_patient_stops_graph_before_booking_and_kim(client, monkeypatch):
    fake_kis = FakeClient([], [])
    kim_calls = []
    monkeypatch.setattr(scheduling_agent, "SyncFHIRClient", lambda url, **kw: fake_kis)
    monkeypatch.setattr(graph_mod, "kim_node", lambda s: kim_calls.append(s) or {"next_agent": "END"})
    monkeypatch.setattr("app.api.endpoints.intake_graph", graph_mod.build_graph())

    client.post("/api/chat", json={"message": "Brustschmerzen"})
    response = client.post("/api/consent", json={"granted": True})

    assert response.status_code == 200
    body = response.json()
    assert body["scheduling_failed"] is True
    assert body["appointment_id"] is None
    assert "kein Termin gebucht" in body["reply"]
    assert fake_kis.created == []
    assert kim_calls == []


def test_gateway_failure_ends_run_without_kim_and_allows_retry(client, monkeypatch):
    kim_calls = []
    monkeypatch.setattr(
        graph_mod, "scheduling_node", lambda s: {"scheduling_error": "GATEWAY_TIMEOUT", "next_agent": "ErrorHandler"}
    )
    monkeypatch.setattr(graph_mod, "kim_node", lambda s: kim_calls.append(1) or {"kim_sent": True, "next_agent": "END"})
    monkeypatch.setattr("app.api.endpoints.intake_graph", graph_mod.build_graph())

    client.post("/api/chat", json={"message": "Brustschmerzen"})
    body = client.post("/api/consent", json={"granted": True}).json()
    assert body["scheduling_failed"] is True
    assert "technischen Störung" in body["reply"]
    assert body["appointment_id"] is None and kim_calls == []

    monkeypatch.setattr(
        graph_mod, "scheduling_node", lambda s: {"appointment_id": "a9", "slot_start": "t", "next_agent": "KIMAgent"}
    )
    monkeypatch.setattr("app.api.endpoints.intake_graph", graph_mod.build_graph())
    retry = client.post("/api/consent", json={"granted": True}).json()
    assert retry["appointment_id"] == "a9" and retry["scheduling_failed"] is False
