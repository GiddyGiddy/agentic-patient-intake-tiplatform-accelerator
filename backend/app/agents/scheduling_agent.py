import logging
import re
from datetime import datetime, timedelta, timezone
from itertools import islice

import requests
from fhirpy import SyncFHIRClient
from fhirpy.base.exceptions import AuthorizationError, BaseFHIRError, ForbiddenError

from app.agents.state import IntakeState
from app.core.audit import audit
from app.core.epa_client import KVNR_IDENTIFIER_SYSTEM
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


class PatientMappingError(Exception):
    """A verified identity could not be linked uniquely to a KIS patient."""


def _patient_actor(client: SyncFHIRClient, kvnr: str | None) -> dict:
    if not isinstance(kvnr, str) or not re.fullmatch(r"[A-Z][0-9]{9}", kvnr):
        raise PatientMappingError("PATIENT_IDENTITY_MISSING")

    patients = client.resources("Patient").search(
        identifier=f"{KVNR_IDENTIFIER_SYSTEM}|{kvnr}"
    ).limit(2)
    matches = list(islice(patients, 2))
    if len(matches) != 1:
        raise PatientMappingError("PATIENT_NOT_FOUND" if not matches else "PATIENT_AMBIGUOUS")
    patient = matches[0]
    identifiers = patient.get("identifier")
    if (
        not isinstance(patient.id, str)
        or not re.fullmatch(r"[A-Za-z0-9.-]{1,64}", patient.id)
        or not isinstance(identifiers, list)
        or not any(
            isinstance(item, dict)
            and item.get("system") == KVNR_IDENTIFIER_SYSTEM
            and item.get("value") == kvnr
            for item in identifiers
        )
    ):
        raise PatientMappingError("PATIENT_IDENTITY_MISMATCH")
    return {"reference": f"Patient/{patient.id}"}


def _book(state: IntakeState) -> IntakeState:
    client = SyncFHIRClient(
        settings.fhir_base_url,
        requests_config={"timeout": (settings.fhir_connect_timeout_s, settings.fhir_read_timeout_s)},
    )
    score = state.get("triage_score", "Grün")
    earliest = datetime.now(timezone.utc) + _EARLIEST.get(score, timedelta(days=2))

    actor = _patient_actor(client, state.get("kvnr"))
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
        "participant": [{"actor": actor, "status": "accepted"}],
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
    if isinstance(exc, PatientMappingError):
        return str(exc)
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

PATIENT_MAPPING_MESSAGE = (
    "Ihre Identität konnte nicht eindeutig einer Patientenakte zugeordnet werden. "
    "Es wurde kein Termin gebucht. Bitte wenden Sie sich zur Terminvereinbarung an das Praxisteam."
)


def error_handler_node(state: IntakeState) -> IntakeState:
    """Controlled fallback: friendly message to the patient (no technical details), alert for the clinic IT."""
    code = state.get("scheduling_error", "UNKNOWN")
    audit("scheduling_failed", state["patient_id"], error=code, triage=state.get("triage_score"), alert=True)
    message = PATIENT_MAPPING_MESSAGE if code.startswith("PATIENT_") else FALLBACK_MESSAGE
    return {"reply": message, "scheduling_failed": True, "next_agent": "END"}
