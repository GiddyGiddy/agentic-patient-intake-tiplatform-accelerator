import logging
from datetime import datetime, timedelta, timezone

import requests
from fhirpy import SyncFHIRClient
from fhirpy.base.exceptions import AuthorizationError, BaseFHIRError, ForbiddenError

from app.agents.state import IntakeState
from app.core.audit import audit
from config import settings

log = logging.getLogger(__name__)

# Demo slot mapping: Rot/Orange/Gelb get acute slots, Grün/Blau standard ones.
_EARLIEST = {
    "Rot": timedelta(0),
    "Orange": timedelta(minutes=10),
    "Gelb": timedelta(hours=1),
    "Grün": timedelta(days=2),
    "Blau": timedelta(days=7),
}


def _find_free_slot(client: SyncFHIRClient, earliest: datetime):
    # errors propagate: an outage must not look like "no free slot"
    slots = (
        client.resources("Slot")
        .search(status="free", start__ge=earliest.isoformat())
        .sort("start")
        .limit(1)
        .fetch()
    )
    return slots[0] if slots else None


def _patient_actor(client: SyncFHIRClient, patient_id: str) -> dict:
    """Links the Appointment to the KIS Patient when it exists (HAPI enforces referential integrity)."""
    actor = {"display": f"Patient {patient_id}"}
    try:
        if client.resources("Patient").search(_id=patient_id).limit(1).fetch():
            actor["reference"] = f"Patient/{patient_id}"
    except Exception:
        pass
    return actor


def _book(state: IntakeState) -> IntakeState:
    client = SyncFHIRClient(
        settings.fhir_base_url,
        requests_config={"timeout": (settings.fhir_connect_timeout_s, settings.fhir_read_timeout_s)},
    )
    score = state.get("triage_score", "Grün")
    earliest = datetime.now(timezone.utc) + _EARLIEST.get(score, timedelta(days=2))

    slot = _find_free_slot(client, earliest)
    if slot is not None:
        start, end = slot["start"], slot["end"]
        slot_ref = {"reference": f"Slot/{slot.id}"}
    else:
        # no free slot published by the KIS: request the earliest allowed time
        start_dt = earliest.replace(second=0, microsecond=0)
        start, end = start_dt.isoformat(), (start_dt + timedelta(minutes=20)).isoformat()
        slot_ref = None

    data = {
        "status": "booked" if slot_ref else "proposed",
        "priority": 1 if score in ("Rot", "Orange") else 5,
        "description": state.get("anamnesis_summary", "")[:200],
        "start": start,
        "end": end,
        "participant": [
            {"actor": _patient_actor(client, state["patient_id"]), "status": "accepted"}
        ],
    }
    if slot_ref:
        data["slot"] = [slot_ref]
    appt = client.resource("Appointment", **data)
    appt.save()

    if slot_ref:
        slot["status"] = "busy"
        slot.save()

    audit(
        "appointment_created",
        state["patient_id"],
        appointment_id=appt.id,
        status=data["status"],
        triage=score,
    )
    return {"appointment_id": appt.id, "slot_start": start, "next_agent": "KIMAgent"}


def _error_code(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout):
        return "GATEWAY_TIMEOUT"
    if isinstance(exc, (requests.ConnectionError, OSError)):
        return "GATEWAY_CONNECTION_FAILED"
    if isinstance(exc, (AuthorizationError, ForbiddenError)):
        return "GATEWAY_AUTH_ERROR"  # e.g. SMC-B certificate invalid/expired
    if isinstance(exc, (BaseFHIRError, requests.RequestException)):
        return "GATEWAY_ERROR"
    raise exc  # programming errors are not infrastructure failures


def scheduling_node(state: IntakeState) -> IntakeState:
    """Books the appointment. Infrastructure failures (KIS/TI gateway down, timeout, SMC-B/certificate
    errors) do not crash the run: they are recorded in the state and routed to the ErrorHandler."""
    try:
        return _book(state)
    except Exception as exc:
        code = _error_code(exc)
        log.warning("scheduling failed: %s (%s)", code, type(exc).__name__)
        return {"scheduling_error": code, "next_agent": "ErrorHandler"}


FALLBACK_MESSAGE = (
    "Aufgrund einer vorübergehenden technischen Störung an den TI-Schnittstellen konnten wir Ihren "
    "Termin nicht automatisch eintragen. Das Praxisteam wurde informiert und bucht den Termin manuell. "
    "Sie können es auch später erneut versuchen."
)


def error_handler_node(state: IntakeState) -> IntakeState:
    """Controlled fallback: friendly message to the patient (no technical details), alert for the clinic IT."""
    code = state.get("scheduling_error", "UNKNOWN")
    audit("scheduling_failed", state["patient_id"], error=code, triage=state.get("triage_score"), alert=True)
    return {"reply": FALLBACK_MESSAGE, "scheduling_failed": True, "next_agent": "END"}
