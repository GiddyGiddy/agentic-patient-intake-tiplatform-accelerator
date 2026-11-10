import json

import pytest

from app.agents import intake_agent
from app.agents.intake_agent import intake_node


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Ich habe starke Brustschmerzen", "Rot"),
        ("starke Schmerzen im Bein", "Orange"),
        ("Fieber seit gestern", "Gelb"),
        ("Husten", "Grün"),
        ("Ich brauche ein Rezept", "Blau"),
    ],
)
def test_keyword_score(text, expected):
    assert intake_agent._keyword_score(text) == expected


def test_red_triggers_immediately_and_routes_to_consent(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Emergency response must not call the LLM")

    monkeypatch.setattr(intake_agent, "_llm_followup", forbidden)
    monkeypatch.setattr(intake_agent, "_llm_assess", forbidden)
    r = intake_node({"messages": ["Ich habe Brustschmerzen"]})
    assert r["triage_score"] == "Rot"
    assert r["next_agent"] == "ConsentAgent"
    assert "112" in r["reply"]


def test_non_red_asks_followup_until_enough_info(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda t: intake_agent.FollowupResponse(complete=True))
    r = intake_node({"messages": ["Husten"]})
    assert "triage_score" not in r
    assert r["next_agent"] == "END"


def test_non_red_completes_after_enough_messages(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda t: intake_agent.FollowupResponse(complete=True))
    monkeypatch.setattr(intake_agent, "_llm_assess", lambda t: {"summary": "Husten.", "triage_score": "Blau"})
    r = intake_node({"messages": ["Husten", "seit drei Tagen"]})
    assert r["triage_score"] == "Grün"
    assert r["anamnesis_summary"]
    assert r["next_agent"] == "ConsentAgent"


def test_llm_can_raise_but_not_lower_urgency(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda t: intake_agent.FollowupResponse(complete=True))
    monkeypatch.setattr(
        intake_agent, "_llm_assess", lambda t: {"summary": "s", "triage_score": "Orange"}
    )
    assert intake_node({"messages": ["Husten", "stark"]})["triage_score"] == "Orange"

    monkeypatch.setattr(
        intake_agent, "_llm_assess", lambda t: {"summary": "s", "triage_score": "Blau"}
    )
    assert intake_node({"messages": ["Brustschmerzen"]})["triage_score"] == "Rot"


def test_llm_failure_does_not_assign_keyword_urgency(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda t: intake_agent.FollowupResponse(complete=True))
    monkeypatch.setattr(intake_agent, "_llm_assess", lambda t: None)
    r = intake_node({"messages": ["Fieber", "seit 2 Tagen"]})
    assert "triage_score" not in r
    assert r["reply"] == intake_agent.UNAVAILABLE_REPLY
    assert r["next_agent"] == "IntakeUnavailable"


def test_followup_failure_is_not_treated_as_completion(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda t: None)
    monkeypatch.setattr(intake_agent, "_llm_assess", lambda t: pytest.fail("Assessment must not run"))
    r = intake_node({"messages": ["Husten", "seit 2 Tagen"]})
    assert "triage_score" not in r
    assert r["reply"] == intake_agent.UNAVAILABLE_REPLY
    assert intake_node({"messages": ["Husten"]})["reply"] == intake_agent.UNAVAILABLE_REPLY


HEADACHE_QUESTION = (
    "Haben Sie in den letzten Tagen oder Wochen Kopfschmerzen ohne eine klare Ursache "
    "oder sind sie Teil eines größeren Symptomkomplexes?"
)


@pytest.mark.parametrize("repeated", [
    HEADACHE_QUESTION,
    "  HABEN SIE in den letzten Tagen oder Wochen Kopfschmerzen ohne eine klare Ursache "
    "oder sind sie Teil eines größeren Symptomkomplexes?!  ",
    "Haben Sie in den letzten Tagen oder Wochen Kopfschmerzen ohne klare Ursache "
    "oder sind sie Teil eines größeren Symptomkomplexes?",
])
def test_repeated_headache_question_is_retried_with_dialog(monkeypatch, repeated):
    monkeypatch.setattr(intake_agent.settings, "llm_base_url", "http://example.test")
    prompts = []
    responses = iter([
        {"complete": False, "question": repeated},
        {"complete": False, "question": "Seit wann haben Sie Kopfschmerzen?"},
    ])

    def generate(prompt, **kwargs):
        prompts.append(prompt)
        return json.dumps(next(responses), ensure_ascii=False)

    monkeypatch.setattr(intake_agent, "generate", generate)
    result = intake_node({
        "messages": ["ich habe Kopfschmerzen", "ohne eine klare Ursache"],
        "intake_questions": {"1": HEADACHE_QUESTION},
    })
    assert result["reply"] == "Seit wann haben Sie Kopfschmerzen?"
    assert result["next_agent"] == "END"
    assert len(prompts) == 2
    assert "ohne eine klare Ursache" in prompts[1]
    assert "andere" in prompts[1].lower()


@pytest.mark.parametrize("retry", [
    {"complete": False, "question": HEADACHE_QUESTION},
    {"complete": True, "question": None},
    {"complete": False, "question": ""},
])
def test_duplicate_retry_failure_stops_without_assessment(monkeypatch, retry):
    monkeypatch.setattr(intake_agent.settings, "llm_base_url", "http://example.test")
    responses = iter([{"complete": False, "question": HEADACHE_QUESTION}, retry])
    monkeypatch.setattr(intake_agent, "generate", lambda *a, **k: json.dumps(next(responses)))
    monkeypatch.setattr(intake_agent, "_llm_assess", lambda t: pytest.fail("Must not assess"))
    result = intake_node({
        "messages": ["ich habe Kopfschmerzen", "ich weiß es nicht"],
        "intake_questions": {"1": HEADACHE_QUESTION},
    })
    assert result["reply"] == intake_agent.REPEATED_REPLY
    assert result["next_agent"] == "IntakeUnavailable"
    assert "triage_score" not in result


def test_short_distinct_questions_are_not_rejected():
    assert not intake_agent._repeats_question("Seit wann?", ["Wie stark?"])
    assert intake_agent._repeats_question(" SEIT  WANN?! ", ["Seit wann?"])


def test_question_compared_to_all_preceding_questions(monkeypatch):
    monkeypatch.setattr(intake_agent.settings, "llm_base_url", "http://example.test")
    responses = iter([
        {"complete": False, "question": "Seit wann?"},
        {"complete": False, "question": "Haben Sie Fieber?"},
    ])
    monkeypatch.setattr(intake_agent, "generate", lambda *a, **k: json.dumps(next(responses)))
    result = intake_node({
        "messages": ["Kopfschmerzen", "gestern", "mäßig"],
        "intake_questions": {"1": "Seit wann?", "2": "Wie stark?"},
    })
    assert result["reply"] == "Haben Sie Fieber?"


def test_retry_provider_failure_is_safe_and_does_not_log_patient_text(monkeypatch, caplog):
    monkeypatch.setattr(intake_agent.settings, "llm_base_url", "http://example.test")
    responses = iter([
        json.dumps({"complete": False, "question": HEADACHE_QUESTION}),
        "invalid JSON",
    ])
    monkeypatch.setattr(intake_agent, "generate", lambda *a, **k: next(responses))
    result = intake_node({
        "messages": ["Kopfschmerzen", "synthetische persönliche Angabe"],
        "intake_questions": {"1": HEADACHE_QUESTION},
    })
    assert result["reply"] == intake_agent.REPEATED_REPLY
    assert "triage_score" not in result
    assert "synthetische persönliche Angabe" not in caplog.text
    assert HEADACHE_QUESTION not in caplog.text


@pytest.mark.parametrize("data", [
    {"complete": "false", "question": "Seit wann?"},
    {"complete": False},
    {"complete": False, "question": "  "},
    {"complete": False, "question": 42},
    [],
])
def test_followup_schema_rejects_invalid_decisions(monkeypatch, data):
    monkeypatch.setattr(intake_agent.settings, "llm_base_url", "http://example.test")
    monkeypatch.setattr(intake_agent, "generate", lambda *a, **k: json.dumps(data))
    assert intake_agent._llm_followup("[]") is None


def test_summary_prompt_preserves_question_answer_context(monkeypatch):
    seen = []
    summary = (
        "Die Person berichtet über plötzlichen Gewichtsverlust und Durchfall. "
        "Sie gibt an, weniger Appetit zu haben. Weitere Symptome und Medikamente "
        "mit Einfluss auf den Appetit werden verneint."
    )
    monkeypatch.setattr(intake_agent.settings, "llm_base_url", "http://example.test")

    def generate(prompt, **kwargs):
        seen.append((prompt, kwargs))
        return json.dumps({"summary": summary, "triage_score": "Blau"}, ensure_ascii=False)

    monkeypatch.setattr(intake_agent, "generate", generate)
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda t: intake_agent.FollowupResponse(complete=True))
    state = {
        "messages": [
            "ich bin plötzlich abgenommen",
            "Gewicht und Körpermasse",
            "Durchfall habe ich bemerkt",
            "ich habe nicht mehr zu viel Appetit als zuvor",
            "nein",
            "nein",
        ],
        "intake_questions": {
            "1": "Was genau hast du abgenommen?",
            "2": "Haben Sie Durchfall bemerkt?",
            "3": "Haben Sie Änderungen in Ihrem Appetit bemerkt?",
            "4": "Haben Sie auch andere Symptome wie Müdigkeit, Fieber oder Schmerzen?",
            "5": "Haben Sie Medikamente eingenommen, die Ihren Appetit beeinflussen könnten?",
        },
    }
    result = intake_node(state)
    prompt, options = seen[0]
    dialog = json.loads(prompt.split("\n\nDialog:\n", 1)[1])
    assert dialog[-4:] == [
        {"role": "assistant", "text": state["intake_questions"]["4"]},
        {"role": "patient", "text": "nein"},
        {"role": "assistant", "text": state["intake_questions"]["5"]},
        {"role": "patient", "text": "nein"},
    ]
    assert options["json_mode"] is True
    assert result["anamnesis_summary"].startswith(
        f"Automatische Zusammenfassung (nicht ärztlich geprüft):\n{summary}\n\n"
    )
    assert "Frage: Haben Sie Medikamente eingenommen, die Ihren Appetit beeinflussen könnten?\nAntwort: nein" in result["anamnesis_summary"]


def test_valid_summary_is_not_discarded_when_triage_is_invalid(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda t: intake_agent.FollowupResponse(complete=True))
    monkeypatch.setattr(intake_agent.settings, "llm_base_url", "http://example.test")
    monkeypatch.setattr(
        intake_agent, "generate", lambda *a, **k: '{"summary": "Husten seit drei Tagen.", "triage_score": "?"}'
    )
    result = intake_node({"messages": ["Husten", "seit drei Tagen"]})
    assert "Husten seit drei Tagen." in result["anamnesis_summary"]
    assert "triage_score" not in result
    assert result["reply"] == intake_agent.UNAVAILABLE_REPLY


def test_invalid_summary_fallback_preserves_negation_context(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda t: intake_agent.FollowupResponse(complete=True))
    monkeypatch.setattr(intake_agent, "_llm_assess", lambda text: {"summary": None, "triage_score": "Blau"})
    result = intake_node({
        "messages": ["Gewichtsverlust", "nein"],
        "intake_questions": {"1": "Nehmen Sie Medikamente?"},
    })
    assert result["anamnesis_summary"] == (
        "Automatische Zusammenfassung nicht verfügbar.\n\nGesprächsangaben zur Überprüfung:\n"
        "Antwort: Gewichtsverlust\nFrage: Nehmen Sie Medikamente?\nAntwort: nein"
    )


def test_old_sessions_without_questions_keep_answers_without_inventing_context(monkeypatch):
    monkeypatch.setattr(intake_agent, "_llm_followup", lambda t: intake_agent.FollowupResponse(complete=True))
    monkeypatch.setattr(intake_agent, "_llm_assess", lambda text: None)
    result = intake_node({"messages": ["Husten", "nein"]})
    assert result["anamnesis_summary"].endswith("Antwort: Husten\nAntwort: nein")
