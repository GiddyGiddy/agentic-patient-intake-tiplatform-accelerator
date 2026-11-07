import logging

import httpx

from app.agents.state import TRIAGE_LEVELS, IntakeState
from app.core.llm_client import LLMResponseError, generate, parse_json_response
from config import settings

log = logging.getLogger(__name__)
RED = ("brustschmerz", "atemnot", "bewusstlos", "lähmung", "krampfanfall", "chest pain", "unconscious")
ORANGE = ("starke schmerzen", "blutung", "severe pain", "bleeding")
YELLOW = ("fieber", "erbrechen", "fever", "vomiting")
GREEN = ("schmerz", "husten", "pain", "cough")

MIN_PATIENT_MESSAGES = 2  # non-red cases need at least this many answers before triage
MAX_PATIENT_MESSAGES = 6  # stop asking follow-up questions after this many answers
FALLBACK_QUESTION = "Danke. Seit wann bestehen die Beschwerden und wie stark sind sie?"


def _keyword_score(text: str) -> str:
    t = text.lower()
    for level, words in (("Rot", RED), ("Orange", ORANGE), ("Gelb", YELLOW), ("Grün", GREEN)):
        if any(w in t for w in words):
            return level
    return "Blau"


def _more_urgent(a: str, b: str) -> str:
    return min(a, b, key=TRIAGE_LEVELS.index)


def assessment_prompt(text: str) -> str:
    return (
        "Du bist ein medizinischer Anamnese-Assistent. Fasse die Angaben des Patienten "
        "als Anamnese zusammen und vergib eine Dringlichkeit nach dem Manchester-Triage-System "
        '(Rot, Orange, Gelb, Grün, Blau). Antworte nur als JSON: '
        '{"summary": "...", "triage_score": "..."}\n\nPatientenangaben:\n' + text
    )


def _llm_assess(text: str) -> dict | None:
    if not settings.llm_base_url:
        return None
    try:
        data = parse_json_response(generate(assessment_prompt(text), timeout=120, json_mode=True))
        if (
            isinstance(data, dict)
            and data.get("triage_score") in TRIAGE_LEVELS
            and isinstance(data.get("summary"), str)
            and data["summary"].strip()
        ):
            return data
        log.warning("LLM assessment response did not match the expected schema")
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("LLM assessment failed (%s); using deterministic fallback", type(exc).__name__)
    return None


def followup_prompt(text: str) -> str:
    return (
        "Du fuehrst eine strukturierte Anamnese (Beschwerden, Beginn, Staerke, Vorerkrankungen, "
        "Medikamente). Entscheide anhand der bisherigen Patientenangaben, ob noch eine wichtige "
        "Information fehlt. Antworte nur als JSON: "
        '{"complete": true|false, "question": "eine kurze Frage auf Deutsch"}\n\nPatientenangaben:\n'
        + text
    )


def _llm_followup(text: str) -> str | None:
    """Returns the next anamnesis question, or None if the LLM considers the anamnesis complete/unavailable."""
    if not settings.llm_base_url:
        return None
    try:
        data = parse_json_response(generate(followup_prompt(text), timeout=60, json_mode=True))
        if not isinstance(data, dict) or not isinstance(data.get("complete"), bool):
            raise LLMResponseError("Invalid follow-up schema")
        question = data.get("question")
        if data.get("complete") is False and isinstance(question, str) and question.strip():
            return question.strip()
        if data["complete"] is False:
            raise LLMResponseError("Missing follow-up question")
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("LLM follow-up failed (%s); using fallback", type(exc).__name__)
    return None


def intake_node(state: IntakeState) -> IntakeState:
    messages = state.get("messages", [])
    text = " ".join(messages)
    keyword = _keyword_score(text)

    if keyword != "Rot":
        question = None
        if len(messages) < MIN_PATIENT_MESSAGES:
            question = _llm_followup(text) or FALLBACK_QUESTION
        elif len(messages) < MAX_PATIENT_MESSAGES:
            question = _llm_followup(text)
        if question:
            return {"reply": question, "next_agent": "END"}
    llm = _llm_assess(text)
    # the LLM may raise but never lower what the safety keywords flagged
    score = _more_urgent(keyword, llm["triage_score"]) if llm else keyword
    summary = llm["summary"] if llm else f"Patientenangaben: {text}"

    reply = (
        "Ihre Beschwerden sind möglicherweise lebensbedrohlich. Bitte rufen Sie sofort den Notruf 112."
        if score == "Rot"
        else f"Danke, die Anamnese ist vollständig (Dringlichkeit: {score})."
    )
    return {
        "anamnesis_summary": summary,
        "triage_score": score,
        "reply": reply,
        "next_agent": "ConsentAgent",
    }
