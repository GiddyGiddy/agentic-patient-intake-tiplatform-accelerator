import json
import logging

import httpx

from app.agents.state import IntakeState
from app.core.kim_outbox import enqueue_and_send
from app.core.llm_client import generate
from config import settings

log = logging.getLogger(__name__)


def _epa_error_text(error: str) -> str:
    return {
        "missing_kvnr": "mangels KVNR nicht abrufbar",
        "unauthorized": "wegen einer nicht autorisierten Anfrage nicht abrufbar (HTTP 401)",
        "forbidden": "wegen fehlender Berechtigung nicht abrufbar (HTTP 403)",
        "unavailable": "technisch nicht abrufbar",
    }.get(error, "nicht abrufbar")


def _meds_text(state: IntakeState) -> str:
    meds = state.get("medication_plan") or []
    if meds:
        return "Medikationsplan (ePA): " + "; ".join(meds)
    error = state.get("epa_errors", {}).get("medication_plan")
    if error:
        return (
            f"Medikationsplan (ePA): {_epa_error_text(error)}; "
            "Einträge sind damit nicht ausgeschlossen."
        )
    if state.get("epa_status") and "medication_plan" in state:
        return "Medikationsplan (ePA): keine aktiven Einträge in den abgerufenen Daten."
    if state.get("epa_available") is False:
        return "Medikationsplan: ePA nicht erreichbar."
    return "Medikationsplan (ePA): keine aktiven Eintraege."


def _epa_context_text(state: IntakeState) -> str:
    if not state.get("epa_status") and "allergies" not in state and "conditions" not in state:
        return ""
    errors = state.get("epa_errors", {})
    sections = ["Weitere ePA-Angaben (nicht unabhängig verifiziert):"]
    for key, label in (("allergies", "Allergien/Unverträglichkeiten"), ("conditions", "Vorerkrankungen")):
        values = state.get(key, [])
        error = errors.get(key)
        if error:
            sections.append(
                f"{label} (ePA): {_epa_error_text(error)}; "
                "Einträge sind damit nicht ausgeschlossen."
            )
        elif values:
            sections.append(f"{label} (ePA): {'; '.join(values)}")
        else:
            sections.append(f"{label} (ePA): keine Einträge in den abgerufenen Daten.")
    return "\n".join(sections)


def _template_letter(state: IntakeState) -> str:
    sections = [
        "Sehr geehrte Kollegin, sehr geehrter Kollege,\n\n"
        "wir übersenden Ihnen die Aufnahmeinformationen zu Ihrem Patienten "
        f"(ID {state['patient_id']}). Die nicht ärztlich geprüfte automatische "
        f"Ersteinschätzung ergab die Dringlichkeit {state.get('triage_score')}.\n\n"
        f"Anamnese: {state.get('anamnesis_summary', '')}\n\n"
        f"{_meds_text(state)}"
    ]
    epa_context = _epa_context_text(state)
    if epa_context:
        sections.append(epa_context)
    sections.append(
        f"Termin: {state.get('slot_start')} (Appointment/{state.get('appointment_id')}).\n\n"
        "Mit freundlichen kollegialen Grüßen"
    )
    return "\n\n".join(sections)


def _llm_letter(state: IntakeState) -> str | None:
    if not settings.llm_base_url:
        return None
    prompt = (
        "Schreibe einen formellen medizinischen Arztbrief als Fließtext auf Deutsch "
        "(Anrede, Anamnese, Einschätzung, Termin, Grußformel). Erfinde keine Befunde.\n"
        f"Anamnese: {state.get('anamnesis_summary')}\n"
        f"Automatische Ersteinschätzung (nicht ärztlich geprüft): {state.get('triage_score')}\n"
        f"{_meds_text(state)}\n"
        "Weitere ePA-Angaben werden nach dem generierten Text separat und unverändert angefügt.\n"
        f"Termin: {state.get('slot_start')}"
    )
    try:
        letter = generate(prompt, timeout=120)
        epa_context = _epa_context_text(state)
        return f"{letter.rstrip()}\n\n{epa_context}" if epa_context else letter
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("LLM letter generation failed (%s); using template", type(exc).__name__)
        return None


def kim_node(state: IntakeState) -> IntakeState:
    letter = _llm_letter(state) or _template_letter(state)
    triage_record = {
        "patient_id": state["patient_id"],
        "triage_system": "unvalidated-prototype",
        "triage_score": state.get("triage_score"),
        "appointment_id": state.get("appointment_id"),
        "slot_start": state.get("slot_start"),
        "medication_plan": state.get("medication_plan", []),
    }
    # stored in the outbox first: if the KIM client module is down, the retry worker delivers it later
    sent = enqueue_and_send(
        state["patient_id"],
        "Arztbrief - Patientenaufnahme",
        letter,
        [("triage.json", json.dumps(triage_record, ensure_ascii=False))],
    )
    return {"letter": letter, "kim_sent": sent, "next_agent": "END"}
