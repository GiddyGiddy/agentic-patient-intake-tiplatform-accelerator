import importlib.util
from pathlib import Path


def test_reference_patient_seed_has_unique_linked_resources(monkeypatch):
    seed_path = Path(__file__).resolve().parents[2] / "mock-ti-services" / "mock-kis-fhir" / "seed.py"
    spec = importlib.util.spec_from_file_location("mock_kis_seed", seed_path)
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    resources = {}
    monkeypatch.setattr(seed, "put", lambda kind, rid, body: resources.update({(kind, rid): body}))

    seed.seed_reference_patient()

    patient = resources[("Patient", "X110411675")]
    assert patient["identifier"] == [{"system": seed.KVNR_SYSTEM, "value": "X110411675"}]
    assert resources[("MedicationStatement", "ref-110411675-med-1")]["subject"]["reference"] == "Patient/X110411675"
    assert resources[("AllergyIntolerance", "ref-110411675-allergy-1")]["patient"]["reference"] == "Patient/X110411675"
    assert resources[("Condition", "ref-110411675-condition-1")]["subject"]["reference"] == "Patient/X110411675"
    assert len(resources) == 4
