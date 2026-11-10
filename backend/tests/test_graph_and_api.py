from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from langgraph.graph import END

import app.agents.graph as graph_mod
from app.agents.graph import route_next_step
from app.core.db import ConsentRow, IntakeSessionRow, db_session, utcnow
from app.core.session import store
from config import settings
from main import app


def test_route_next_step_maps_agents():
    assert route_next_step({"next_agent": "SchedulingAgent"}) == "scheduling"
    assert route_next_step({"next_agent": "KIMAgent"}) == "kim"
    assert route_next_step({"next_agent": "END"}) == END
    assert route_next_step({}) == END


def test_pending_iam_stops_the_run():
    assert route_next_step({"next_agent": "ConsentAgent", "iam_required": True}) == END


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def stub_ti(monkeypatch):
    """Replaces scheduling/KIM so the full graph runs without TI services."""
    calls = []
    monkeypatch.setattr(
        graph_mod,
        "scheduling_node",
        lambda s: calls.append("scheduling")
        or {"appointment_id": "a1", "slot_start": "t", "next_agent": "KIMAgent"},
    )
    monkeypatch.setattr(
        graph_mod, "kim_node", lambda s: calls.append("kim") or {"kim_sent": True, "next_agent": "END"}
    )
    monkeypatch.setattr("app.api.endpoints.intake_graph", graph_mod.build_graph())
    return calls


def _chat(client, msg):
    return client.post("/api/chat", json={"message": msg})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "database": True}


def test_chat_without_consent_stops_and_keeps_state_server_side(client, stub_ti):
    body = _chat(client, "Ich habe starke Brustschmerzen").json()
    assert body["triage_score"] == "Rot"
    assert body["iam_required"] is True
    assert body["consent_granted"] is False
    assert body["kim_sent"] is False
    assert stub_ti == []


def test_client_cannot_inject_consent_or_triage(client, stub_ti):
    body = client.post(
        "/api/chat",
        json={"message": "Husten", "consent_granted": True, "triage_score": "Rot"},
    ).json()
    assert body["consent_granted"] is False
    assert body["triage_score"] is None
    assert stub_ti == []


def test_consent_grant_resumes_through_epa_scheduling_and_kim(client, stub_ti, monkeypatch):
    from app.agents import consent_agent
    from app.core.epa_client import EPAReadResult

    seen_kvnrs = []
    monkeypatch.setattr(
        consent_agent,
        "fetch_patient_summary",
        lambda kvnr, token: seen_kvnrs.append(kvnr)
        or EPAReadResult(medication_plan=["Ramipril 5 mg"], status="available"),
    )
    _chat(client, "Ich habe starke Brustschmerzen")
    body = client.post(
        "/api/consent",
        json={"granted": True, "kvnr": "attacker-controlled-value"},
    ).json()
    assert stub_ti == ["scheduling", "kim"]
    assert seen_kvnrs == [settings.dev_kvnr]
    assert body["appointment_id"] == "a1"
    assert body["kim_sent"] is True
    assert body["iam_required"] is False
    assert body["consent_granted"] is True
    assert body["epa_status"] == "available"
    assert body["medication_plan"] == ["Ramipril 5 mg"]
    assert body["epa_available"] is True
    assert "kvnr" not in body


def test_consent_grant_is_idempotent_after_completion(client, stub_ti):
    _chat(client, "Brustschmerzen")
    client.post("/api/consent", json={"granted": True})
    again = client.post("/api/consent", json={"granted": True}).json()
    assert again["appointment_id"] == "a1"
    assert stub_ti == ["scheduling", "kim"]  # not booked twice


def test_consent_without_intake_is_rejected(client, stub_ti):
    assert client.post("/api/consent", json={"granted": True}).status_code == 409


def test_pending_consent_does_not_rerun_intake(client, stub_ti):
    first = _chat(client, "Brustschmerzen").json()
    again = _chat(client, "noch eine Nachricht").json()
    assert again["triage_score"] == first["triage_score"]
    assert again["iam_required"] is True


def test_followup_question_persists_with_its_answer_and_survives_resume(client, stub_ti, monkeypatch):
    from app.agents import intake_agent

    observed = []
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda text: intake_agent.FollowupResponse(complete=True))
    monkeypatch.setattr(intake_agent, "_llm_assess", lambda text: observed.append(text) or {"summary": None, "triage_score": "Blau"})
    first = _chat(client, "Husten").json()
    assert first["reply"] == intake_agent.FALLBACK_QUESTION
    with db_session() as db:
        assert db.get(IntakeSessionRow, "dev-patient").result["intake_questions"] == {"1": first["reply"]}
    second = _chat(client, "seit drei Tagen").json()
    assert second["anamnesis_summary"] == (
        "Automatische Zusammenfassung nicht verfügbar.\n\nGesprächsangaben zur Überprüfung:\n"
        f"Antwort: Husten\nFrage: {first['reply']}\nAntwort: seit drei Tagen"
    )
    assert first["reply"] in observed[0]
    completed = client.post("/api/consent", json={"granted": True}).json()
    assert completed["anamnesis_summary"] == second["anamnesis_summary"]
    assert client.delete("/api/session").status_code == 204
    with db_session() as db:
        assert db.get(IntakeSessionRow, "dev-patient") is None


def test_new_red_flag_while_waiting_for_consent_is_not_ignored(client, stub_ti, monkeypatch):
    from app.agents import intake_agent

    monkeypatch.setattr(intake_agent, "_llm_followup", lambda text: intake_agent.FollowupResponse(complete=True))
    monkeypatch.setattr(intake_agent, "_llm_assess", lambda text: {"summary": "Husten", "triage_score": "Blau"})
    _chat(client, "Husten")
    assert _chat(client, "seit gestern").json()["triage_score"] == "Grün"
    monkeypatch.setattr(intake_agent, "_llm_assess", lambda text: pytest.fail("LLM called for emergency"))
    body = _chat(client, "Jetzt habe ich Atemnot").json()
    assert body["triage_score"] == "Rot"
    assert "112" in body["reply"]
    assert stub_ti == []


def test_unavailable_followup_does_not_save_advice_as_question(client, monkeypatch):
    from app.agents import intake_agent

    responses = iter([intake_agent.FollowupResponse(complete=False, question="Seit wann?"), None])
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda text: next(responses))
    first = _chat(client, "Husten").json()
    second = _chat(client, "seit gestern").json()
    assert second["triage_score"] is None
    assert second["iam_required"] is False
    assert "nicht verfügbar" in second["reply"]
    with db_session() as db:
        assert db.get(IntakeSessionRow, "dev-patient").result["intake_questions"] == {"1": first["reply"]}


def test_repeated_question_stops_without_saving_advice_as_question(client, monkeypatch):
    import json
    from app.agents import intake_agent

    question = "Haben Sie Kopfschmerzen ohne eine klare Ursache?"
    responses = iter([{"complete": False, "question": question}] * 3)
    monkeypatch.setattr(settings, "llm_base_url", "http://example.test")
    monkeypatch.setattr(intake_agent, "generate", lambda *a, **k: json.dumps(next(responses)))
    first = _chat(client, "ich habe Kopfschmerzen").json()
    second = _chat(client, "ohne eine klare Ursache").json()
    assert first["reply"] == question
    assert second["reply"] == intake_agent.REPEATED_REPLY
    assert second["triage_score"] is None
    assert second["iam_required"] is False
    assert second["intake_stopped"] is True
    assert _chat(client, "noch eine Antwort").json()["intake_stopped"] is True
    with db_session() as db:
        row = db.get(IntakeSessionRow, "dev-patient")
        assert row.result["intake_questions"] == {"1": question}
        assert row.messages == ["ich habe Kopfschmerzen", "ohne eine klare Ursache"]
    emergency = _chat(client, "Jetzt habe ich Atemnot").json()
    assert emergency["triage_score"] == "Rot"
    assert "112" in emergency["reply"]
    assert emergency["intake_stopped"] is False


def test_stopped_intake_can_be_reset(client, monkeypatch):
    import json
    from app.agents import intake_agent

    question = "Seit wann?"
    monkeypatch.setattr(settings, "llm_base_url", "http://example.test")
    monkeypatch.setattr(intake_agent, "generate", lambda *a, **k: json.dumps({"complete": False, "question": question}))
    _chat(client, "Husten")
    assert _chat(client, "gestern").json()["intake_stopped"]
    assert client.delete("/api/session").status_code == 204
    assert not _chat(client, "Husten").json()["intake_stopped"]


def test_revoked_consent_blocks_further_ti_access(client, stub_ti):
    _chat(client, "Husten")  # follow-up question, intake not finished
    body = client.post("/api/consent", json={"granted": False}).json()
    assert body["consent_granted"] is False


def test_chat_after_completion_is_rejected(client, stub_ti):
    _chat(client, "Brustschmerzen")
    client.post("/api/consent", json={"granted": True})
    assert _chat(client, "nochmal").status_code == 409


def test_reset_session_starts_over(client, stub_ti):
    _chat(client, "Brustschmerzen")
    assert client.delete("/api/session").status_code == 204
    body = _chat(client, "Husten").json()
    assert body["triage_score"] is None


def test_ti_failure_returns_502_and_allows_retry(client, monkeypatch):
    def boom(state):
        raise ConnectionError("kis down")

    monkeypatch.setattr(graph_mod, "scheduling_node", boom)
    monkeypatch.setattr("app.api.endpoints.intake_graph", graph_mod.build_graph())
    _chat(client, "Brustschmerzen")
    assert client.post("/api/consent", json={"granted": True}).status_code == 502

    monkeypatch.setattr(
        graph_mod, "scheduling_node", lambda s: {"appointment_id": "a2", "slot_start": "t", "next_agent": "KIMAgent"}
    )
    monkeypatch.setattr(
        graph_mod, "kim_node", lambda s: {"kim_sent": True, "next_agent": "END"}
    )
    monkeypatch.setattr("app.api.endpoints.intake_graph", graph_mod.build_graph())
    assert client.post("/api/consent", json={"granted": True}).json()["appointment_id"] == "a2"


def test_message_length_and_rate_limit(client, stub_ti, monkeypatch):
    assert _chat(client, "x" * (settings.max_message_chars + 1)).status_code == 422
    monkeypatch.setattr(settings, "rate_limit_per_min", 2)
    assert _chat(client, "a").status_code == 200
    assert _chat(client, "b").status_code == 200
    assert _chat(client, "c").status_code == 429


def test_chat_requires_token_when_auth_enabled(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_disabled", False)
    assert _chat(client, "hi").status_code == 401
    assert client.post("/api/consent", json={"granted": True}).status_code == 401
    assert client.delete("/api/session").status_code == 401


def test_consent_expires(client, stub_ti, monkeypatch):
    _chat(client, "Brustschmerzen")
    client.post("/api/consent", json={"granted": True})
    with db_session() as s:  # pretend the booking did not finish and the consent is long expired
        s.get(IntakeSessionRow, "dev-patient").result = {}
        for row in s.query(ConsentRow):
            row.expires_at = utcnow() - timedelta(hours=1)
    body = _chat(client, "und noch").json()
    assert body["iam_required"] is True
