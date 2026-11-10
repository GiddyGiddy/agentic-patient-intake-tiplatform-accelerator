import json
import logging

from app.agents import intake_agent, kim_agent, scheduling_agent
from app.core import audit as audit_mod
from tests.test_scheduling_agent import FakeClient, FakeResource


def test_llm_followup_question_is_used_when_anamnesis_incomplete(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda text: intake_agent.FollowupResponse(complete=False, question="Nehmen Sie BlutverdÃ¼nner?"))
    r = intake_agent.intake_node({"messages": ["Husten", "seit zwei Tagen"]})
    assert r["reply"] == "Nehmen Sie BlutverdÃ¼nner?"
    assert r["next_agent"] == "END"
    assert "triage_score" not in r


def test_followups_stop_at_the_message_cap(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda text: intake_agent.FollowupResponse(complete=False, question="noch eine Frage?"))
    msgs = ["Husten"] * intake_agent.MAX_PATIENT_MESSAGES
    r = intake_agent.intake_node({"messages": msgs})
    assert r["next_agent"] == "IntakeUnavailable"
    assert "triage_score" not in r


def test_red_flag_skips_followups(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda text: "Frage?")
    r = intake_agent.intake_node({"messages": ["starke Brustschmerzen"]})
    assert r["triage_score"] == "Rot"


def test_kim_failure_does_not_fail_the_run(monkeypatch):
    def down(*a, **k):
        raise ConnectionRefusedError()

    monkeypatch.setattr("app.core.kim_outbox.send_kim_message", down)
    r = kim_agent.kim_node({"patient_id": "X1", "triage_score": "Gelb"})
    assert r["kim_sent"] is False
    assert r["next_agent"] == "END"


def test_appointment_references_existing_patient(monkeypatch):
    client = FakeClient([FakeResource(id="s1", start="2030-01-01T10:00:00Z", end="2030-01-01T10:20:00Z")])
    monkeypatch.setattr(scheduling_agent, "SyncFHIRClient", lambda url, **kw: client)
    scheduling_agent.scheduling_node({"patient_id": "X1", "triage_score": "Gelb"})
    actor = client.created[0]["participant"][0]["actor"]
    assert actor["reference"] == "Patient/X1"


def test_audit_event_is_json_and_pseudonymised(capsys, monkeypatch):
    monkeypatch.setattr(audit_mod, "_logger", logging.getLogger("audit-test"))
    audit_mod._logger.handlers.clear()
    audit_mod._logger.setLevel(logging.INFO)
    audit_mod._logger.propagate = False
    lines = []

    class H(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    audit_mod._logger.addHandler(H())
    audit_mod.audit("consent_granted", "X110000001", ttl_s=5)
    event = json.loads(lines[0])
    assert event["event"] == "consent_granted"
    assert "X110000001" not in lines[0]
    assert event["patient"] == audit_mod.pseudonym("X110000001")
