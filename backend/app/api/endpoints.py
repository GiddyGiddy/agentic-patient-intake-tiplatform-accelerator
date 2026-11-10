import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from app.agents.consent_agent import IAM_MESSAGE
from app.agents.graph import intake_graph
from app.agents.intake_agent import REPEATED_REPLY, _keyword_score
from app.core.audit import audit
from app.core.security import _bearer, get_current_user
from app.core.session import IntakeSession, store
from config import settings

router = APIRouter()
log = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=settings.max_message_chars)


class ConsentRequest(BaseModel):
    granted: bool


def _view(session: IntakeSession, reply: str | None = None, iam_message: str | None = None) -> dict:
    """API response built from the server-side session (the client holds no authoritative state)."""
    waiting = bool(session.triage_score) and not session.consent_active and not session.completed
    r = session.result
    return {
        "reply": reply,
        "intake_stopped": bool(r.get("intake_stopped")),
        "triage_score": session.triage_score,
        "anamnesis_summary": session.anamnesis_summary,
        "iam_required": waiting,
        "iam_message": (iam_message or IAM_MESSAGE) if waiting else None,
        "consent_granted": session.consent_active,
        "appointment_id": r.get("appointment_id"),
        "slot_start": r.get("slot_start"),
        "kim_sent": r.get("kim_sent", False),
        "medication_plan": r.get("medication_plan", []),
        "epa_available": r.get("epa_available"),
        "epa_status": r.get("epa_status"),
        "scheduling_failed": False,
    }


def _run(sub: str, token: str, session: IntakeSession, kvnr: str | None = None) -> dict:
    state = {
        "patient_id": sub,
        "kvnr": kvnr,
        "messages": list(session.messages),
        "intake_questions": dict(session.result.get("intake_questions", {})),
        "consent_granted": session.consent_active,
        "access_token": token,
    }
    if session.triage_score:
        state["triage_score"] = session.triage_score
        state["anamnesis_summary"] = session.anamnesis_summary or ""
    try:
        result = intake_graph.invoke(state)
    except Exception as exc:
        # FHIR/ePA unreachable etc.: nothing was committed to the session, the client may retry
        log.exception("agent graph failed")
        audit("run_failed", sub, error=type(exc).__name__)
        raise HTTPException(502, "Ein angebundener TI-Dienst ist nicht erreichbar.") from exc

    session.triage_score = result.get("triage_score") or session.triage_score
    session.anamnesis_summary = result.get("anamnesis_summary") or session.anamnesis_summary
    if result.get("intake_stopped"):
        session.result["intake_stopped"] = True
    elif session.triage_score == "Rot":
        session.result.pop("intake_stopped", None)
    if not session.triage_score and result.get("next_agent") == "END" and result.get("reply"):
        questions = dict(session.result.get("intake_questions", {}))
        questions[str(len(session.messages))] = result["reply"]
        session.result["intake_questions"] = questions
    for key in (
        "appointment_id",
        "slot_start",
        "kim_sent",
        "medication_plan",
        "epa_available",
        "epa_status",
    ):
        if key in result:
            session.result[key] = result[key]
    view = _view(session, result.get("reply"), result.get("iam_message"))
    view["scheduling_failed"] = bool(result.get("scheduling_failed"))
    return view


def _token(creds: HTTPAuthorizationCredentials | None) -> str:
    return creds.credentials if creds else ""


@router.post("/chat")
def chat(
    req: ChatRequest,
    user: dict = Depends(get_current_user),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
):
    """Adds a patient message and runs one pass of the agent graph."""
    sub = user["sub"]
    if store.rate_limited(sub):
        raise HTTPException(429, "Zu viele Anfragen.")
    with store.locked(sub) as session:
        if session.completed:
            raise HTTPException(409, "Die Aufnahme ist bereits abgeschlossen.")
        if session.result.get("intake_stopped") and _keyword_score(" ".join([*session.messages, req.message])) != "Rot":
            return _view(session, REPEATED_REPLY)
        if len(session.messages) >= settings.max_session_messages:
            raise HTTPException(409, "Maximale Gesprächslänge erreicht.")
        session.messages.append(req.message)
        audit("chat_message", sub, count=len(session.messages))
        if session.triage_score and not session.consent_active:
            if session.triage_score != "Rot" and _keyword_score(" ".join(session.messages)) == "Rot":
                return _run(sub, _token(creds), session, user.get("kvnr"))
            # Intake is finished and waiting for consent.
            return _view(session)
        return _run(sub, _token(creds), session, user.get("kvnr"))


@router.post("/consent")
def set_consent(
    req: ConsentRequest,
    user: dict = Depends(get_current_user),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
):
    """Records the patient's decision. A grant resumes the workflow (ePA read, booking, KIM)."""
    sub = user["sub"]
    if store.rate_limited(sub):
        raise HTTPException(429, "Zu viele Anfragen.")
    with store.locked(sub) as session:
        if not req.granted:
            session.revoke()
            audit("consent_revoked", sub)
            return _view(session)
        if not session.triage_score:
            raise HTTPException(409, "Es gibt keine laufende Aufnahme.")
        if session.completed:
            return _view(session)
        session.grant(user.get("jti"))
        audit("consent_granted", sub, ttl_s=settings.consent_ttl_s)
        return _run(sub, _token(creds), session, user.get("kvnr"))


@router.delete("/session", status_code=204)
def reset_session(user: dict = Depends(get_current_user)):
    """Starts over: drops conversation, triage, consent and results."""
    store.reset(user["sub"])
    audit("session_reset", user["sub"])
    return Response(status_code=204)
