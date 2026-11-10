from datetime import datetime, timedelta, timezone

import pytest

from app.agents import scheduling_agent
from app.agents.scheduling_agent import scheduling_node
from app.core.epa_client import KVNR_IDENTIFIER_SYSTEM

KVNR = "X110000001"


class FakeResource(dict):
    saved = False

    def __init__(self, id=None, **data):
        super().__init__(**data)
        self.id = id

    def save(self):
        self.saved = True
        self.id = self.id or "appt-1"


class FakeSearch:
    def __init__(self, results, searches=None):
        self.results = results
        self.searches = searches

    def search(self, **kw):
        if self.searches is not None:
            self.searches.append(kw)
        return self

    def sort(self, *a):
        return self

    def limit(self, *a):
        return self

    def fetch(self):
        return self.results

    def __iter__(self):
        return iter(self.results)


class FakeClient:
    def __init__(self, slots, patients=None):
        self.slots = slots
        self.patients = patients if patients is not None else [
            FakeResource(id="kis-p1", identifier=[{"system": KVNR_IDENTIFIER_SYSTEM, "value": KVNR}])
        ]
        self.created = []
        self.patient_searches = []

    def resources(self, name):
        if name == "Patient":
            return FakeSearch(self.patients, self.patient_searches)
        return FakeSearch(self.slots)

    def resource(self, name, **data):
        r = FakeResource(**data)
        self.created.append(r)
        return r


def _use(monkeypatch, slots):
    client = FakeClient(slots)
    monkeypatch.setattr(scheduling_agent, "SyncFHIRClient", lambda url, **kw: client)
    return client


def test_books_free_slot_and_marks_it_busy(monkeypatch):
    slot = FakeResource(id="s1", start="2030-01-01T10:00:00Z", end="2030-01-01T10:20:00Z", status="free")
    client = _use(monkeypatch, [slot])

    r = scheduling_node({"patient_id": "pairwise-sub", "kvnr": KVNR, "triage_score": "Gelb", "anamnesis_summary": "x"})

    appt = client.created[0]
    assert appt["status"] == "booked"
    assert appt["slot"] == [{"reference": "Slot/s1"}]
    assert appt["start"] == slot["start"]
    assert appt["participant"][0]["actor"] == {"reference": "Patient/kis-p1"}
    assert client.patient_searches == [{"identifier": f"{KVNR_IDENTIFIER_SYSTEM}|{KVNR}"}]
    assert slot["status"] == "busy" and slot.saved
    assert r == {"appointment_id": "appt-1", "slot_start": slot["start"], "next_agent": "KIMAgent"}


def test_without_free_slot_proposes_appointment(monkeypatch):
    client = _use(monkeypatch, [])
    scheduling_node({"patient_id": "p1", "kvnr": KVNR, "triage_score": "Grün"})
    appt = client.created[0]
    assert appt["status"] == "proposed"
    assert "slot" not in appt


@pytest.mark.parametrize(
    "score,priority,min_delay",
    [
        ("Rot", 1, timedelta(0)),
        ("Orange", 1, timedelta(minutes=9)),
        ("Gelb", 5, timedelta(minutes=59)),
        ("Grün", 5, timedelta(days=1, hours=23)),
        ("Blau", 5, timedelta(days=6, hours=23)),
    ],
)
def test_acuity_drives_priority_and_earliest_start(monkeypatch, score, priority, min_delay):
    client = _use(monkeypatch, [])
    scheduling_node({"patient_id": "p1", "kvnr": KVNR, "triage_score": score})
    appt = client.created[0]
    assert appt["priority"] == priority
    start = datetime.fromisoformat(appt["start"])
    assert start >= datetime.now(timezone.utc) + min_delay - timedelta(minutes=1)


def test_acute_slot_is_earlier_than_standard(monkeypatch):
    client = _use(monkeypatch, [])
    scheduling_node({"patient_id": "p", "kvnr": KVNR, "triage_score": "Gelb"})
    scheduling_node({"patient_id": "p", "kvnr": KVNR, "triage_score": "Grün"})
    gelb, gruen = (datetime.fromisoformat(a["start"]) for a in client.created)
    assert gelb < gruen


@pytest.mark.parametrize(
    "kvnr,patients,expected",
    [
        (None, [], "PATIENT_IDENTITY_MISSING"),
        ("bad", [], "PATIENT_IDENTITY_MISSING"),
        (KVNR, [], "PATIENT_NOT_FOUND"),
        (KVNR, [FakeResource(id="p1"), FakeResource(id="p2")], "PATIENT_AMBIGUOUS"),
        (KVNR, [FakeResource(id="p1", identifier=[{"system": KVNR_IDENTIFIER_SYSTEM, "value": "X110000002"}])], "PATIENT_IDENTITY_MISMATCH"),
        (KVNR, [FakeResource(identifier=[{"system": KVNR_IDENTIFIER_SYSTEM, "value": KVNR}])], "PATIENT_IDENTITY_MISMATCH"),
        (KVNR, [FakeResource(id="bad/id", identifier=[{"system": KVNR_IDENTIFIER_SYSTEM, "value": KVNR}])], "PATIENT_IDENTITY_MISMATCH"),
    ],
)
def test_missing_or_unreliable_identity_never_books(monkeypatch, kvnr, patients, expected):
    slot = FakeResource(id="s1", status="free", start="2030-01-01T10:00:00Z", end="2030-01-01T10:20:00Z")
    client = FakeClient([slot], patients)
    monkeypatch.setattr(scheduling_agent, "SyncFHIRClient", lambda url, **kw: client)

    result = scheduling_node({"patient_id": "pairwise-sub", "kvnr": kvnr})

    assert result == {"scheduling_error": expected, "next_agent": "ErrorHandler"}
    assert not client.created
    assert slot["status"] == "free" and not slot.saved


def test_patient_lookup_network_error_is_not_swallowed(monkeypatch):
    import requests

    class Down(FakeClient):
        def resources(self, name):
            if name == "Patient":
                raise requests.ConnectionError()
            return super().resources(name)

    client = Down([])
    monkeypatch.setattr(scheduling_agent, "SyncFHIRClient", lambda url, **kw: client)
    assert scheduling_node({"patient_id": "p", "kvnr": KVNR}) == {
        "scheduling_error": "GATEWAY_CONNECTION_FAILED", "next_agent": "ErrorHandler",
    }
    assert not client.created


def test_patient_lookup_checks_next_page_for_duplicates(monkeypatch):
    from fhirpy import SyncFHIRClient

    client = SyncFHIRClient("http://mock/fhir")
    pages = [
        {
            "resourceType": "Bundle",
            "entry": [{"resource": {"resourceType": "Patient", "id": "p1",
                                   "identifier": [{"system": KVNR_IDENTIFIER_SYSTEM, "value": KVNR}]}}],
            "link": [{"relation": "next", "url": "http://mock/fhir/Patient?page=2"}],
        },
        {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Patient", "id": "p2"}}]},
    ]
    monkeypatch.setattr(client, "_fetch_resource", lambda *args, **kwargs: pages.pop(0))
    with pytest.raises(scheduling_agent.PatientMappingError, match="PATIENT_AMBIGUOUS"):
        scheduling_agent._patient_actor(client, KVNR)
    assert not pages
