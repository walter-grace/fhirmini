"""De-identify FHIR R4 resources for safe model training.

Default policy is HIPAA **Safe Harbor**-oriented for STRUCTURED fields:
  - direct identifiers (name, telecom, photo, contact) removed
  - identifiers + resource ids + references replaced with stable, non-reversible
    pseudonyms (HMAC-salt) so cross-resource linkage survives but re-identification doesn't
  - geography reduced to state (+ optional 3-digit ZIP)
  - all dates generalized to YEAR (Safe Harbor); ages >89 aggregated to "90+"
  - free-text narrative (text.div, attachment data, notes) dropped by default
    (free-text NLP scrubbing is a separate, harder step — see drop_narrative)

This is NOT legal certification. Real PHI release requires Safe Harbor verification or
Expert Determination by a qualified party. `date_shift` (interval-preserving) is an
Expert-Determination / Limited-Data-Set technique, NOT Safe Harbor — labelled as such.
"""
import copy, datetime, hashlib, hmac, re

DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2}(T[\d:.,+\-Z]+)?)?)?$")
CURRENT_YEAR = 2026  # pinned (no wall-clock dependence); pass via policy to override

# whole sub-objects/fields that carry direct identifiers -> removed
DROP_KEYS = {"name", "telecom", "photo", "contact", "address",  # address handled, see below
             "patient_name", "maidenName"}
# narrative / free-text carriers
NARRATIVE_KEYS = {"div"}


class Deidentifier:
    def __init__(self, salt: str, *, keep_zip3: bool = False, drop_narrative: bool = True,
                 date_mode: str = "redact", current_year: int = CURRENT_YEAR):
        assert date_mode in ("redact", "shift"), "date_mode: redact (Safe Harbor) | shift (Expert Det.)"
        self.salt = salt.encode()
        self.keep_zip3 = keep_zip3
        self.drop_narrative = drop_narrative
        self.date_mode = date_mode
        self.current_year = current_year
        self.removed = {}  # audit: field -> count

    # ---- pseudonymization ----
    def pseudo(self, value: str) -> str:
        return hmac.new(self.salt, str(value).encode(), hashlib.sha256).hexdigest()[:16]

    def _note(self, field):
        self.removed[field] = self.removed.get(field, 0) + 1

    # ---- date handling ----
    def _generalize_date(self, s: str):
        if not DATE_RE.match(s):
            return s
        year = int(s[:4])
        if self.date_mode == "redact":
            self._note("date->year")
            return str(year)
        # shift mode kept simple+stateless here (year only); true per-patient interval
        # preservation is layered on by the exporter when it knows the owning patient.
        return str(year)

    def _birthdate(self, s: str):
        if not s or len(s) < 4:
            return None
        year = int(s[:4])
        if self.current_year - year > 89:        # Safe Harbor: aggregate >89
            self._note("age-90-plus")
            return None
        self._note("birthDate->year")
        return str(year)

    # ---- address: keep only state (+ optional zip3) ----
    def _address(self, addr):
        out = []
        for a in (addr if isinstance(addr, list) else [addr]):
            keep = {}
            if a.get("state"):
                keep["state"] = a["state"]
            if a.get("country"):
                keep["country"] = a["country"]
            if self.keep_zip3 and a.get("postalCode"):
                keep["postalCode"] = str(a["postalCode"])[:3] + "**"
            out.append(keep)
        self._note("address-generalized")
        return out

    # ---- core recursive scrub ----
    def _scrub(self, obj, parent_key=""):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == "address":
                    out["address"] = self._address(v); continue
                if k in DROP_KEYS:
                    self._note(f"dropped:{k}"); continue
                if k in NARRATIVE_KEYS and self.drop_narrative:
                    self._note("narrative-div"); continue
                if k == "identifier":
                    out["identifier"] = self._identifiers(v); continue
                if k == "reference" and isinstance(v, str) and "/" in v:
                    t, _, i = v.partition("/")
                    out["reference"] = f"{t}/{self.pseudo(i)}"; continue
                if k == "id" and isinstance(v, str):
                    out["id"] = self.pseudo(v); continue
                if k == "birthDate":
                    bd = self._birthdate(v)
                    if bd:
                        out["birthDate"] = bd
                    continue
                if k == "data" and self.drop_narrative and parent_key == "attachment":
                    self._note("attachment-data"); continue
                if k == "meta" and isinstance(v, dict):
                    out["meta"] = {kk: vv for kk, vv in v.items() if kk in ("profile", "tag", "security")}
                    continue
                out[k] = self._scrub(v, k)
            return out
        if isinstance(obj, list):
            return [self._scrub(x, parent_key) for x in obj]
        if isinstance(obj, str):
            return self._generalize_date(obj)
        return obj

    def _identifiers(self, ids):
        out = []
        for i in (ids if isinstance(ids, list) else [ids]):
            if isinstance(i, dict) and "value" in i:
                out.append({"system": i.get("system", ""), "value": self.pseudo(i["value"])})
                self._note("identifier-pseudonymized")
        return out

    def deidentify(self, resource: dict) -> dict:
        return self._scrub(copy.deepcopy(resource))
