"""Unit tests for the integration engine's pure logic (codecs + HL7v2->FHIR).
These need no MLX, no database, and no running services — so they run in CI on Linux."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from engine import codecs, hl7map


def test_mllp_roundtrip():
    payload = b"MSH|^~\\&|A|B|C|D|||ADT^A01|123|P|2.5\rPID|||MRN1||Doe^John||19800101|M\r"
    msgs, leftover = codecs.mllp_extract(bytearray(codecs.mllp_wrap(payload)))
    assert msgs == [payload]
    assert leftover == bytearray()


def test_mllp_partial_frame_is_buffered():
    half = bytes([codecs.VT]) + b"MSH|partial"   # no FS/CR terminator yet
    msgs, leftover = codecs.mllp_extract(bytearray(half))
    assert msgs == []
    # the buffered remainder keeps the VT so the next read can complete the frame
    assert bytes(leftover) == bytes([codecs.VT]) + b"MSH|partial"


def test_raw_newline_extract():
    msgs, left = codecs.raw_newline_extract(bytearray(b"alpha\nbravo\ncharl"))
    assert msgs == [b"alpha", b"bravo"]
    assert bytes(left) == b"charl"


def test_length_prefix_extract():
    body = b"hello"
    framed = len(body).to_bytes(4, "big") + body
    msgs, left = codecs.length_prefix_extract(bytearray(framed))
    assert msgs == [body] and left == bytearray()


def test_hl7_adt_to_patient_components():
    raw = ("MSH|^~\\&|ADT|H|MAC|M|20260606||ADT^A04|9001|P|2.5\r"
           "PID|||MRN-9001^^^H^MR||Hyrule^Zelda^A||19910312|F|||1 Rd^^City^ST^00001||555-0102\r")
    m = hl7map.parse(raw)
    assert hl7map.message_type(m) == "ADT^A04"
    assert hl7map.control_id(m) == "9001"
    bundle = hl7map.to_fhir_bundle(m)
    pat = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")
    assert pat["name"][0]["family"] == "Hyrule"
    assert pat["name"][0]["given"] == ["Zelda"]
    assert pat["gender"] == "female"
    assert pat["birthDate"] == "1991-03-12"
    assert pat["identifier"][0]["value"] == "MRN-9001"      # component, not the whole field
    assert pat["telecom"][0]["value"] == "555-0102"          # single-value field, not just "5"
    assert pat["address"][0]["city"] == "City"


def test_adt_uses_put_oru_uses_ifnoneexist():
    adt = hl7map.parse("MSH|^~\\&|A|B|C|D|||ADT^A01|1|P|2.5\rPID|||M1^^^H^MR||A^B\r")
    oru = hl7map.parse("MSH|^~\\&|A|B|C|D|||ORU^R01|2|P|2.5\rPID|||M1^^^H^MR||A^B\rOBX|1|NM|x||5|u\r")
    adt_req = adt_pat_req(adt)
    oru_req = adt_pat_req(oru)
    assert adt_req["method"] == "PUT"               # ADT is authoritative
    assert oru_req["method"] == "POST" and "ifNoneExist" in oru_req   # ORU must not clobber


def adt_pat_req(msg):
    b = hl7map.to_fhir_bundle(msg)
    return next(e["request"] for e in b["entry"] if e["resource"]["resourceType"] == "Patient")


def test_hl7_ack():
    m = hl7map.parse("MSH|^~\\&|A|B|C|D|||ADT^A01|55|P|2.5\rPID|||X||Y^Z\r")
    assert "MSA|AA|55" in hl7map.make_ack(m, "AA")
    assert "MSA|AE|55" in hl7map.make_ack(m, "AE")
