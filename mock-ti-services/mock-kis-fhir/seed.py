"""Seeds the HAPI mock (acts as KIS and as ePA) with demo data. Idempotent: uses PUT with fixed ids."""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.environ.get("FHIR_BASE_URL", "http://localhost:8080/fhir")


def put(resource_type: str, rid: str, body: dict) -> None:
    body = {**body, "resourceType": resource_type, "id": rid}
    url = f"{BASE}/{resource_type}/{rid}"
    last: urllib.error.HTTPError | None = None
    for attempt in range(1, 5):
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            method="PUT",
            headers={"Content-Type": "application/fhir+json"},
        )
        try:
            urllib.request.urlopen(req, timeout=30).read()
            return
        except urllib.error.HTTPError as err:
            last = err
            print(f"PUT {url} -> {err.code} (attempt {attempt}): {err.read()[:300]!r}", flush=True)
            if err.code < 500:
                break
            time.sleep(3 * attempt)
    # A re-seed that hits a server error is harmless if the resource is already there.
    try:
        urllib.request.urlopen(url, timeout=10).read()
        print(f"{resource_type}/{rid} already present, keeping it", flush=True)
    except Exception:
        raise last


def wait_for_server() -> None:
    for _ in range(90):
        try:
            urllib.request.urlopen(f"{BASE}/metadata", timeout=5).read()
            return
        except Exception:
            time.sleep(4)
    raise SystemExit("FHIR server not reachable")


def med(pid: str, rid: str, name: str, dosage: str) -> None:
    put(
        "MedicationStatement",
        rid,
        {
            "status": "active",
            "subject": {"reference": f"Patient/{pid}"},
            "medicationCodeableConcept": {"text": name},
            "dosage": [{"text": dosage}],
        },
    )


def main() -> None:
    wait_for_server()
    # Patient ids equal the `sub` of the mock Gesundheits-ID tokens
    put("Patient", "X110000001", {"name": [{"family": "Mustermann", "given": ["Max"]}]})
    put("Patient", "X110000002", {"name": [{"family": "Musterfrau", "given": ["Erika"]}]})
    med("X110000001", "med-1", "Ramipril 5 mg", "1-0-0")
    med("X110000001", "med-2", "Metformin 500 mg", "1-0-1")
    # patient 2 intentionally has no medication plan

    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for i in range(1, 41):
        start = base + timedelta(hours=i)
        put(
            "Slot",
            f"slot-{i}",
            {
                "status": "free",
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=20)).isoformat(),
                "schedule": {"display": "Notaufnahme" if i < 10 else "Ambulanz"},
            },
        )
    print("seeded", flush=True)


if __name__ == "__main__":
    main()
