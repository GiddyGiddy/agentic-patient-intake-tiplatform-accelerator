from app.agents import consent_agent
from app.agents.consent_agent import consent_node
from app.core.epa_client import EPAReadResult


def test_without_consent_requests_iam_and_points_to_itself(monkeypatch):
    called = []
    monkeypatch.setattr(consent_agent, "fetch_patient_summary", lambda *args: called.append(1))
    result = consent_node({"consent_granted": False})
    assert result["iam_required"] is True
    assert result["iam_message"]
    assert result["next_agent"] == "ConsentAgent"
    assert called == []


def test_missing_consent_flag_is_treated_as_denied():
    assert consent_node({})["iam_required"] is True


def test_with_consent_reads_epa_with_token_and_routes_to_scheduling(monkeypatch):
    seen = {}

    def fake(kvnr, token):
        seen.update(kvnr=kvnr, token=token)
        return EPAReadResult(
            medication_plan=["Ramipril 5 mg"],
            allergies=["Penicillin"],
            conditions=["Hypertonie"],
            status="available",
        )

    monkeypatch.setattr(consent_agent, "fetch_patient_summary", fake)
    result = consent_node(
        {
            "consent_granted": True,
            "patient_id": "internal-sub",
            "kvnr": "X110000001",
            "access_token": "tok",
        }
    )
    assert result["iam_required"] is False
    assert result["next_agent"] == "SchedulingAgent"
    assert result["medication_plan"] == ["Ramipril 5 mg"]
    assert result["allergies"] == ["Penicillin"]
    assert result["conditions"] == ["Hypertonie"]
    assert result["epa_available"] is True
    assert result["epa_status"] == "available"
    assert seen == {"kvnr": "X110000001", "token": "tok"}


def test_epa_permission_denial_does_not_block_scheduling(monkeypatch):
    monkeypatch.setattr(
        consent_agent,
        "fetch_patient_summary",
        lambda *args: EPAReadResult(
            status="forbidden",
            errors={
                "medication_plan": "forbidden",
                "allergies": "forbidden",
                "conditions": "forbidden",
            },
        ),
    )
    result = consent_node(
        {
            "consent_granted": True,
            "patient_id": "internal-sub",
            "kvnr": "X110000001",
            "access_token": "tok",
        }
    )
    assert result["epa_status"] == "forbidden"
    assert result["epa_available"] is False
    assert result["next_agent"] == "SchedulingAgent"


def test_missing_kvnr_skips_epa_and_still_routes_to_scheduling(monkeypatch):
    monkeypatch.setattr(
        consent_agent,
        "fetch_patient_summary",
        lambda kvnr, token: EPAReadResult(
            status="missing_kvnr",
            errors={
                "medication_plan": "missing_kvnr",
                "allergies": "missing_kvnr",
                "conditions": "missing_kvnr",
            },
        ),
    )
    result = consent_node({"consent_granted": True, "patient_id": "internal-sub"})
    assert result["epa_status"] == "missing_kvnr"
    assert result["epa_available"] is False
    assert result["next_agent"] == "SchedulingAgent"
