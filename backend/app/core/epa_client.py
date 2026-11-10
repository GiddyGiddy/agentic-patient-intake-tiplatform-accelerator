from dataclasses import dataclass, field
from typing import Literal

import httpx

from config import settings

EPAStatus = Literal[
    "available", "partial", "unauthorized", "forbidden", "unavailable", "missing_kvnr"
]
_RESOURCES = ("medication_plan", "allergies", "conditions")
KVNR_IDENTIFIER_SYSTEM = "http://fhir.de/sid/gkv/kvid-10"


@dataclass
class EPAReadResult:
    medication_plan: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    status: EPAStatus = "available"
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return "medication_plan" not in self.errors


def _describe(statement: dict) -> str | None:
    concept = statement.get("medicationCodeableConcept") or {}
    name = concept.get("text") or next(
        (c.get("display") for c in concept.get("coding", []) if c.get("display")), None
    )
    if not name:
        name = (statement.get("medicationReference") or {}).get("display")
    if not name:
        return None
    dosage = next((d.get("text") for d in statement.get("dosage", []) if d.get("text")), None)
    return f"{name} ({dosage})" if dosage else name


def _describe_code(resource: dict) -> str | None:
    concept = resource.get("code") or {}
    name = concept.get("text") or next(
        (
            coding.get("display") or coding.get("code")
            for coding in concept.get("coding", [])
            if coding.get("display") or coding.get("code")
        ),
        None,
    )
    return name


def _bundle_resources(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        raise ValueError("FHIR response is not a Bundle")
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        raise ValueError("FHIR Bundle entry is not a list")
    return [
        entry["resource"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)
    ]


def fetch_patient_summary(kvnr: str | None, access_token: str | None) -> EPAReadResult:
    """Reads medication, allergy, and condition summaries from the ePA.

    The KVNR comes from the verified Gesundheits-ID token claim, never from client input. The
    patient's bearer token is forwarded to the configured TI gateway, which adds the institution's
    SMC-B assertion. A failed resource read is recorded separately so one unsupported endpoint
    does not discard successfully read data or block appointment scheduling.
    """
    if not kvnr:
        return EPAReadResult(
            status="missing_kvnr",
            errors={resource: "missing_kvnr" for resource in _RESOURCES},
        )

    headers = {"Accept": "application/fhir+json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    patient_identifier = f"{KVNR_IDENTIFIER_SYSTEM}|{kvnr}"
    requests = {
        "medication_plan": (
            "MedicationStatement",
            {"subject.identifier": patient_identifier, "status": "active", "_count": 100},
        ),
        "allergies": (
            "AllergyIntolerance",
            {"patient.identifier": patient_identifier, "_count": 100},
        ),
        "conditions": (
            "Condition",
            {
                "subject.identifier": patient_identifier,
                "clinical-status": "active",
                "_count": 100,
            },
        ),
    }
    result = EPAReadResult()
    for key, (resource_type, params) in requests.items():
        try:
            response = httpx.get(
                f"{settings.epa_base_url}/{resource_type}",
                params=params,
                headers=headers,
                timeout=settings.epa_timeout_s,
            )
            response.raise_for_status()
            resources = _bundle_resources(response.json())
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            result.errors[key] = (
                "unauthorized" if code == 401 else "forbidden" if code == 403 else "unavailable"
            )
            continue
        except (httpx.HTTPError, ValueError):
            result.errors[key] = "unavailable"
            continue

        if key == "medication_plan":
            result.medication_plan = [value for item in resources if (value := _describe(item))]
        elif key == "allergies":
            result.allergies = [value for item in resources if (value := _describe_code(item))]
        else:
            result.conditions = [value for item in resources if (value := _describe_code(item))]

    if result.errors:
        if len(result.errors) == len(_RESOURCES) and set(result.errors.values()) == {"unauthorized"}:
            result.status = "unauthorized"
        elif len(result.errors) == len(_RESOURCES) and set(result.errors.values()) == {"forbidden"}:
            result.status = "forbidden"
        elif len(result.errors) == len(_RESOURCES):
            result.status = "unavailable"
        else:
            result.status = "partial"
    return result
