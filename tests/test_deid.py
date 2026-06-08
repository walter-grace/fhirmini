"""De-identification tests — prove no direct identifiers survive and linkage is preserved.
Pure (no services/network), runs in CI."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pipeline.deidentify import Deidentifier

PATIENT = {
    "resourceType": "Patient", "id": "patient-123",
    "meta": {"versionId": "7", "lastUpdated": "2026-06-01T10:00:00Z", "tag": [{"code": "x"}]},
    "identifier": [{"system": "urn:mrn", "value": "MRN-9001"}, {"system": "ssn", "value": "123-45-6789"}],
    "name": [{"family": "Hyrule", "given": ["Zelda", "A"]}],
    "telecom": [{"system": "phone", "value": "555-0102"}],
    "gender": "female", "birthDate": "1991-03-12",
    "address": [{"line": ["77 Kokiri Forest"], "city": "Hyrule", "state": "HW", "postalCode": "00002"}],
    "photo": [{"url": "http://x/photo.jpg"}],
    "text": {"status": "generated", "div": "<div>Zelda Hyrule, 555-0102, born 1991</div>"},
}
OBS = {"resourceType": "Observation", "id": "obs-1", "status": "final",
       "effectiveDateTime": "2026-05-20T14:30:00Z",
       "subject": {"reference": "Patient/patient-123"},
       "valueQuantity": {"value": 72, "unit": "bpm"}}

D = Deidentifier(salt="unit-test-salt")
P = D.deidentify(PATIENT)
O = D.deidentify(OBS)
BLOB = json.dumps([P, O])


def test_no_direct_identifiers_survive():
    for leak in ("Hyrule", "Zelda", "555-0102", "MRN-9001", "123-45-6789",
                 "Kokiri", "00002", "photo.jpg"):
        assert leak not in BLOB, f"PII leaked: {leak}"


def test_narrative_dropped():
    assert "div" not in P.get("text", {})


def test_dates_generalized_to_year():
    assert P["birthDate"] == "1991"
    assert O["effectiveDateTime"] == "2026"
    assert "lastUpdated" not in P["meta"]      # date stripped from meta
    assert "versionId" not in P["meta"]


def test_geography_reduced_to_state():
    a = P["address"][0]
    assert a == {"state": "HW"}, f"address not reduced: {a}"


def test_age_over_89_aggregated():
    old = Deidentifier(salt="s").deidentify({"resourceType": "Patient", "birthDate": "1930-01-01"})
    assert "birthDate" not in old           # >89 -> dropped


def test_linkage_preserved_via_stable_pseudonym():
    # the Observation's subject ref must point at the de-identified Patient's new id
    assert O["subject"]["reference"] == f"Patient/{P['id']}"
    assert P["id"] != "patient-123"          # but it's pseudonymized
    # deterministic for the same salt
    assert Deidentifier(salt="s").pseudo("x") == Deidentifier(salt="s").pseudo("x")
    assert Deidentifier(salt="a").pseudo("x") != Deidentifier(salt="b").pseudo("x")


def test_clinical_value_retained():
    assert O["valueQuantity"]["value"] == 72   # the signal we train on survives
    assert P["gender"] == "female"
