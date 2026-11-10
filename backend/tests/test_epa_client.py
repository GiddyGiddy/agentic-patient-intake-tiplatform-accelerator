import httpx
import pytest

from app.core import epa_client
from config import settings


def _bundle(*resources):
    return {"entry": [{"resource": resource} for resource in resources]}


class _Resp:
    def __init__(self, payload, status=200, url="http://epa.test"):
        self._payload = payload
        self.status = status
        self.request = httpx.Request("GET", url)

    def raise_for_status(self):
        if self.status >= 400:
            response = httpx.Response(self.status, request=self.request)
            raise httpx.HTTPStatusError("request failed", request=self.request, response=response)

    def json(self):
        return self._payload


def test_reads_medication_allergies_and_conditions_with_patient_token(monkeypatch):
    seen = {}
    payloads = {
        "MedicationStatement": _bundle(
            {"medicationCodeableConcept": {"text": "Ramipril 5 mg"}, "dosage": [{"text": "1-0-0"}]},
            {"medicationCodeableConcept": {"coding": [{"display": "Metformin"}]}},
            {"status": "active"},
        ),
        "AllergyIntolerance": _bundle({"code": {"text": "Penicillin-Allergie"}}),
        "Condition": _bundle({"code": {"coding": [{"display": "Hypertonie"}]}}),
    }

    def fake_get(url, params, headers, timeout):
        assert timeout == settings.epa_timeout_s
        resource_type = url.rsplit("/", 1)[-1]
        seen[resource_type] = {"url": url, "params": params, "headers": headers}
        return _Resp(payloads[resource_type], url=url)

    monkeypatch.setattr(epa_client.httpx, "get", fake_get)
    result = epa_client.fetch_patient_summary("X110000001", "tok")

    assert result.medication_plan == ["Ramipril 5 mg (1-0-0)", "Metformin"]
    assert result.allergies == ["Penicillin-Allergie"]
    assert result.conditions == ["Hypertonie"]
    assert result.status == "available"
    assert result.available is True
    assert seen["MedicationStatement"]["url"] == f"{settings.epa_base_url}/MedicationStatement"
    patient_identifier = f"{epa_client.KVNR_IDENTIFIER_SYSTEM}|X110000001"
    assert seen["MedicationStatement"]["params"]["subject.identifier"] == patient_identifier
    assert seen["AllergyIntolerance"]["params"]["patient.identifier"] == patient_identifier
    assert seen["Condition"]["params"]["subject.identifier"] == patient_identifier
    assert seen["Condition"]["params"]["clinical-status"] == "active"
    assert seen["MedicationStatement"]["headers"]["Authorization"] == "Bearer tok"


def test_unreachable_epa_is_reported_not_raised(monkeypatch):
    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(epa_client.httpx, "get", boom)
    result = epa_client.fetch_patient_summary("X110000001", "tok")
    assert result.status == "unavailable"
    assert result.errors == {
        "medication_plan": "unavailable",
        "allergies": "unavailable",
        "conditions": "unavailable",
    }
    assert result.available is False


@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_error"),
    [(401, "unauthorized", "unauthorized"), (403, "forbidden", "forbidden")],
)
def test_authorization_failures_are_distinguished(
    monkeypatch, status_code, expected_status, expected_error
):
    monkeypatch.setattr(
        epa_client.httpx,
        "get",
        lambda url, **_kwargs: _Resp({}, status=status_code, url=url),
    )
    result = epa_client.fetch_patient_summary("X110000001", "tok")
    assert result.status == expected_status
    assert set(result.errors.values()) == {expected_error}


def test_partial_resource_failure_keeps_successful_data(monkeypatch):
    def fake_get(url, **_kwargs):
        if url.endswith("/Condition"):
            return _Resp({}, status=403, url=url)
        if url.endswith("/MedicationStatement"):
            return _Resp(_bundle({"medicationCodeableConcept": {"text": "Ramipril"}}), url=url)
        return _Resp(_bundle({"code": {"text": "Penicillin"}}), url=url)

    monkeypatch.setattr(epa_client.httpx, "get", fake_get)
    result = epa_client.fetch_patient_summary("X110000001", "tok")

    assert result.status == "partial"
    assert result.medication_plan == ["Ramipril"]
    assert result.allergies == ["Penicillin"]
    assert result.conditions == []
    assert result.errors == {"conditions": "forbidden"}
    assert result.available is True


def test_empty_resources_are_successful(monkeypatch):
    monkeypatch.setattr(epa_client.httpx, "get", lambda url, **_kwargs: _Resp({}, url=url))
    result = epa_client.fetch_patient_summary("X110000001", None)
    assert result.status == "available"
    assert result.medication_plan == result.allergies == result.conditions == []


def test_missing_kvnr_does_not_send_requests(monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("FHIR request should not be sent without a verified KVNR")

    monkeypatch.setattr(epa_client.httpx, "get", unexpected_request)
    result = epa_client.fetch_patient_summary(None, "tok")
    assert result.status == "missing_kvnr"
    assert result.available is False
    assert set(result.errors.values()) == {"missing_kvnr"}
