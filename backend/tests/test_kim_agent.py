import email
import json
from email import policy

from app.agents import kim_agent
from app.agents.kim_agent import kim_node
from app.core import kim_client

STATE = {
    "patient_id": "p1",
    "triage_score": "Gelb",
    "anamnesis_summary": "Husten seit 3 Tagen",
    "appointment_id": "a1",
    "slot_start": "2030-01-01T10:00:00Z",
}


def test_kim_node_sends_letter_with_machine_readable_triage(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        "app.core.kim_outbox.send_kim_message",
        lambda subject, body, **kw: sent.update(subject=subject, body=body, **kw),
    )

    r = kim_node(STATE)

    assert r["kim_sent"] is True and r["next_agent"] == "END"
    assert "Husten seit 3 Tagen" in sent["body"]
    ((name, data),) = sent["attachments"]
    assert name == "triage.json"
    record = json.loads(data)
    assert record["triage_score"] == "Gelb"
    assert record["triage_system"] == "unvalidated-prototype"
    assert "nicht ärztlich geprüfte" in sent["body"]
    assert record["appointment_id"] == "a1"


def test_send_kim_message_builds_smtp_mail_with_attachment(monkeypatch):
    captured = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def send_message(self, msg):
            captured.append(msg)

    monkeypatch.setattr(kim_client.smtplib, "SMTP", FakeSMTP)
    kim_client.send_kim_message("Betreff", "Text", attachments=[("triage.json", b'{"a": 1}')])

    msg = email.message_from_bytes(captured[0].as_bytes(), policy=policy.default)
    assert msg["Subject"] == "Betreff"
    attachment = next(msg.iter_attachments())
    assert attachment.get_filename() == "triage.json"
    assert json.loads(attachment.get_content()) == {"a": 1}


def test_template_letter_contains_medication_plan():
    text = kim_agent._template_letter(
        {"patient_id": "X1", "medication_plan": ["Ramipril 5 mg"], "epa_available": True}
    )
    assert "Ramipril 5 mg" in text
    assert "nicht erreichbar" in kim_agent._template_letter({"patient_id": "X1", "epa_available": False})
