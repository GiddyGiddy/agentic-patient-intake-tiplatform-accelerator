from app.agents.state import IntakeState
from app.core.audit import audit
from app.core.epa_client import fetch_patient_summary


IAM_MESSAGE = "Bitte geben Sie den Zugriff auf Ihre ePA-Daten über Ihre Gesundheits-ID frei."


def consent_node(state: IntakeState) -> IntakeState:
    """Gatekeeper for TI access (ePA medication plan, KIS, KIM).

    Without consent the workflow stops and the frontend must obtain approval via Gesundheits-ID.
    """
    if state.get("consent_granted"):
        pid = state.get("patient_id", "")
        summary = fetch_patient_summary(state.get("kvnr"), state.get("access_token"))
        audit(
            "epa_read",
            pid,
            resource="summary",
            medication_count=len(summary.medication_plan),
            allergy_count=len(summary.allergies),
            condition_count=len(summary.conditions),
            status=summary.status,
            errors=summary.errors,
            reachable=summary.available,
        )
        return {
            "iam_required": False,
            "medication_plan": summary.medication_plan,
            "allergies": summary.allergies,
            "conditions": summary.conditions,
            "epa_errors": summary.errors,
            "epa_status": summary.status,
            "epa_available": summary.available,
            "next_agent": "SchedulingAgent",
        }
    return {
        "iam_required": True,
        "iam_message": IAM_MESSAGE,
        "next_agent": "ConsentAgent",
    }
