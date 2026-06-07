"""HL7 v2 parsing + mapping to FHIR R4. Rule-based for the common segments;
unmapped messages can fall back to the MLX AI layer (see core.py)."""
import hl7

# Minimal HL7v2 administrative-sex -> FHIR gender
_GENDER = {"M": "male", "F": "female", "O": "other", "U": "unknown", "A": "other", "N": "other"}


def parse(raw: str) -> hl7.Message:
    # HL7 segments are CR-delimited; tolerate \n and \r\n from various senders.
    return hl7.parse(raw.replace("\r\n", "\r").replace("\n", "\r"))


def _s(msg, seg, field, comp=None, default=""):
    """Safe accessor. Stringify then split on separators — robust to python-hl7's quirk of
    not wrapping single-value fields in the full Field/Repetition/Component hierarchy."""
    try:
        f = str(msg.segment(seg)[field]).strip()
        if comp is None:
            return f
        parts = f.split("~")[0].split("^")   # first repetition, then components
        return parts[comp].strip() if comp < len(parts) else default
    except Exception:
        return default


def message_type(msg) -> str:
    try:
        mt = msg.segment("MSH")[9]
        return "^".join(str(c) for c in mt[0]) if hasattr(mt[0], "__iter__") else str(mt)
    except Exception:
        return ""


def control_id(msg) -> str:
    return _s(msg, "MSH", 10)


def make_ack(msg, code: str = "AA", text: str = "") -> str:
    """Build an HL7 ACK reusing the inbound MSH. code: AA(accept)|AE(error)|AR(reject)."""
    msh = msg.segment("MSH")
    sending_app, sending_fac = _s(msg, "MSH", 3), _s(msg, "MSH", 4)
    recv_app, recv_fac = _s(msg, "MSH", 5), _s(msg, "MSH", 6)
    cid = control_id(msg) or "1"
    # swap sender/receiver in the ACK
    ack = (
        f"MSH|^~\\&|{recv_app}|{recv_fac}|{sending_app}|{sending_fac}|"
        f"||ACK|{cid}|P|2.5\r"
        f"MSA|{code}|{cid}|{text}\r"
    )
    return ack


def to_fhir_bundle(msg) -> dict:
    """Map common HL7v2 messages to a FHIR transaction Bundle.
    Returns {} if nothing mappable (caller may AI-fallback)."""
    entries = []
    mtype = message_type(msg)

    # --- PID -> Patient (present in ADT, ORU, ORM, etc.) ---
    patient_fullurl = None
    try:
        pid = msg.segment("PID")
        mrn = _s(msg, "PID", 3, 0) or _s(msg, "PID", 2, 0)
        family = _s(msg, "PID", 5, 0)
        given = _s(msg, "PID", 5, 1)
        sex = _GENDER.get(_s(msg, "PID", 8).upper(), None)
        dob_raw = _s(msg, "PID", 7)
        dob = None
        if len(dob_raw) >= 8 and dob_raw[:8].isdigit():
            dob = f"{dob_raw[0:4]}-{dob_raw[4:6]}-{dob_raw[6:8]}"
        patient = {"resourceType": "Patient"}
        if mrn:
            patient["identifier"] = [{"system": "urn:mac-fhir:mrn", "value": mrn}]
        if family or given:
            patient["name"] = [{"family": family, "given": [given] if given else []}]
        if sex:
            patient["gender"] = sex
        if dob:
            patient["birthDate"] = dob
        # PID-11 address, PID-13 phone (best-effort)
        street, city, state, zip_ = _s(msg, "PID", 11, 0), _s(msg, "PID", 11, 2), _s(msg, "PID", 11, 3), _s(msg, "PID", 11, 4)
        if any([street, city, state, zip_]):
            patient["address"] = [{k: v for k, v in
                                   {"line": [street] if street else None, "city": city or None,
                                    "state": state or None, "postalCode": zip_ or None}.items() if v}]
        phone = _s(msg, "PID", 13, 0)
        if phone:
            patient["telecom"] = [{"system": "phone", "value": phone}]
        patient_fullurl = f"urn:uuid:patient-{mrn or control_id(msg)}"
        # ADT is authoritative for demographics -> PUT (replace). ORU/ORM must NOT clobber an
        # existing richer Patient -> conditional create (ifNoneExist) so we only fill a gap.
        if mrn and mtype.startswith("ADT"):
            req = {"method": "PUT", "url": f"Patient?identifier=urn:mac-fhir:mrn|{mrn}"}
        elif mrn:
            req = {"method": "POST", "url": "Patient", "ifNoneExist": f"identifier=urn:mac-fhir:mrn|{mrn}"}
        else:
            req = {"method": "POST", "url": "Patient"}
        entries.append({"fullUrl": patient_fullurl, "resource": patient, "request": req})
    except Exception:
        pass

    # --- OBX -> Observation (ORU results) ---
    try:
        for obx in msg.segments("OBX"):
            code_txt = str(obx[3][0][1]) if len(obx[3]) and len(obx[3][0]) > 1 else str(obx[3])
            code_sys = str(obx[3][0][2]) if len(obx[3]) and len(obx[3][0]) > 2 else ""
            code_id = str(obx[3][0][0]) if len(obx[3]) and len(obx[3][0]) > 0 else ""
            value = str(obx[5]) if len(obx) > 5 else ""
            units = str(obx[6][0][0]) if len(obx) > 6 and len(obx[6]) else ""
            obs = {
                "resourceType": "Observation",
                "status": "final",
                "code": {"text": code_txt or code_id,
                         "coding": [{"system": "http://loinc.org" if "LN" in code_sys else code_sys,
                                     "code": code_id, "display": code_txt}] if code_id else []},
            }
            try:
                obs["valueQuantity"] = {"value": float(value), "unit": units}
            except (ValueError, TypeError):
                if value:
                    obs["valueString"] = value
            if patient_fullurl:
                obs["subject"] = {"reference": patient_fullurl}
            entries.append({"resource": obs, "request": {"method": "POST", "url": "Observation"}})
    except Exception:
        pass

    # --- PV1 -> Encounter (ADT visits) ---
    try:
        if msg.segment("PV1"):
            cls = {"I": "IMP", "O": "AMB", "E": "EMER", "P": "AMB", "R": "AMB"}.get(_s(msg, "PV1", 2).upper(), "AMB")
            adt = _s(msg, "PV1", 44)
            enc = {"resourceType": "Encounter", "status": "in-progress",
                   "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": cls}}
            if patient_fullurl:
                enc["subject"] = {"reference": patient_fullurl}
            if len(adt) >= 8 and adt[:8].isdigit():
                enc["period"] = {"start": f"{adt[0:4]}-{adt[4:6]}-{adt[6:8]}"}
            entries.append({"resource": enc, "request": {"method": "POST", "url": "Encounter"}})
    except Exception:
        pass

    # --- OBR -> ServiceRequest (ORM orders) ---
    try:
        if mtype.startswith("ORM"):
            for obr in msg.segments("OBR"):
                svc_code = str(obr[4][0][0]) if len(obr) > 4 and len(obr[4]) and len(obr[4][0]) else ""
                svc_txt = str(obr[4][0][1]) if len(obr) > 4 and len(obr[4]) and len(obr[4][0]) > 1 else ""
                sr = {"resourceType": "ServiceRequest", "status": "active", "intent": "order",
                      "code": {"text": svc_txt or svc_code,
                               "coding": [{"code": svc_code, "display": svc_txt}] if svc_code else []}}
                if patient_fullurl:
                    sr["subject"] = {"reference": patient_fullurl}
                entries.append({"resource": sr, "request": {"method": "POST", "url": "ServiceRequest"}})
    except Exception:
        pass

    if not entries:
        return {}
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries,
            "meta": {"tag": [{"system": "urn:mac-fhir:source", "code": "hl7v2", "display": mtype}]}}
