from typing import Optional, TypedDict

# Prototype urgency labels, ordered from most to least urgent; not validated MTS.
TRIAGE_LEVELS = ["Rot", "Orange", "Gelb", "Grün", "Blau"]


class IntakeState(TypedDict, total=False):
    patient_id: str
    messages: list[str]
    intake_questions: dict[str, str]  # question preceding each answer, keyed by zero-based answer index
    consent_granted: bool
    # patient's Gesundheits-ID token, forwarded to the ePA; never returned to the client
    access_token: str

    # ConsentAgent: data read from the ePA after consent
    medication_plan: list[str]
    epa_available: bool

    # IntakeAgent
    anamnesis_summary: str
    triage_score: str  # one of TRIAGE_LEVELS
    reply: str
    intake_stopped: bool

    # routing includes "IntakeUnavailable", which ends the run without completing intake
    next_agent: str

    # ConsentAgent
    iam_required: bool
    iam_message: str

    # SchedulingAgent
    appointment_id: Optional[str]
    slot_start: Optional[str]
    scheduling_error: str  # GATEWAY_TIMEOUT | GATEWAY_CONNECTION_FAILED | GATEWAY_AUTH_ERROR | GATEWAY_ERROR
    scheduling_failed: bool  # set by the ErrorHandler

    # KIMAgent
    letter: str
    kim_sent: bool
