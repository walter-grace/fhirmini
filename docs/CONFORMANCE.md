# fhirmini — FHIR conformance

What this server conforms to, verified against a live instance. Two tiers: the **base FHIR
RESTful spec** (what makes it a FHIR server) and the **real-world / US-regulatory layer**
([ONC §170.315(g)(10)](https://onc-healthit.github.io/api-resource-guide/g10-criterion/)).

## Tier A — base FHIR RESTful API ([hl7.org/fhir/http.html](https://hl7.org/fhir/http.html)) ✅ complete
FHIR **R4 (4.0.1)**, HAPI FHIR 8.10.

| Requirement | Status |
|---|---|
| `capabilities` (`GET /metadata`) — SHALL | ✅ |
| Formats: JSON + XML (+ Turtle), UTF-8, content negotiation | ✅ |
| read · vread · update · patch · delete · create | ✅ |
| history — instance / type / system | ✅ |
| search + `_include`/`_revinclude` · `_sort` · `_count` · modifiers · chaining | ✅ |
| paging (RFC-5005 Bundle links) | ✅ |
| transaction & batch | ✅ |
| versioning · `ETag` · `If-Match` (versioned-update) | ✅ |
| conditional create / update / delete (`If-None-Exist`) | ✅ |
| `OperationOutcome` errors + correct status codes | ✅ |
| `$validate` · `$expand` · `$everything` · `_summary`/`_elements`/`_format` | ✅ |
| 146 R4 resource types | ✅ |

## Tier B — real-world / ONC g(10) ([test method](https://www.healthit.gov/test-method/standardized-api-patient-and-population-services))
g(10) requires **FHIR R4 + US Core + SMART App Launch + Bulk Data** together.

| Requirement | Status | Notes |
|---|---|---|
| FHIR R4 (4.0.1) | ✅ | |
| **US Core profiles** + validation | ✅ | US Core 6.1.0 loaded (59 StructureDefinitions); `$validate?profile=…us-core-patient` enforces them |
| **Bulk Data `$export`** (system/group) | ✅ | async 202 + `$export-poll-status`; `bulk_export_enabled: true` |
| Terminology (`$expand`/`$lookup`) | ◐ | works on built-in/loaded ValueSets; large external code systems (SNOMED/LOINC full) not loaded — use an external terminology service for those |
| Subscriptions | ◐ | resource supported; delivery channel not configured |
| AuditEvent / Provenance | ◐ | resource types supported; not auto-generated (audit today = HAPI access log + engine ledger) |
| **SMART on FHIR** (OAuth2 scopes) | ❌ | **see design below** — biggest remaining item |

## SMART on FHIR — design (not yet implemented)
Today auth is **Cloudflare Access / Zero-Trust at the edge** — transport+identity gating, good
for a private/gated API, but **not** FHIR-native SMART App Launch (OAuth2 with resource scopes
like `patient/Observation.read`). g(10) and SMART-app ecosystems need the latter.

HAPI does **not** ship a full SMART authorization *server*; it enforces scopes once a token
exists. So the design is **token issuer + HAPI enforcement**:

```
  SMART app ─▶ authorize/token (IdP)  ──issues JWT w/ scopes──▶
            ─▶ fhirmini /fhir (Bearer)  ──HAPI AuthorizationInterceptor + SearchNarrowing──▶ data
```
Options, recommended order:
1. **OAuth/IdP in front (recommended):** run a SMART-capable authorization server (e.g.
   Keycloak, or a SMART reference auth) for `/authorize` + `/token`, standalone + EHR launch,
   refresh tokens, well-known `smart-configuration`. HAPI validates the JWT and enforces scopes
   via `AuthorizationInterceptor` + `SearchNarrowingInterceptor`. Front the whole thing with the
   Cloudflare tunnel.
2. **All-in-one HAPI interceptor** issuing/validating dev tokens — fine for testing, not g(10).
3. **Commercial** (Aidbox/Firely) if certification is the near-term goal.

Effort: **large** (the major remaining work). Validate with the
[ONC (g)(10) Inferno test kit](https://github.com/onc-healthit/onc-certification-g10-test-kit).

## Bottom line
- **Standards-compliant FHIR R4 server:** ✅ yes (Tier A complete; US Core + Bulk Data added).
- **ONC-certifiable / EHR-interop ready:** remaining gap is **SMART on FHIR auth** (designed
  above) + full terminology + active subscriptions/audit, as needed.

> Synthetic-data / dev-sandbox posture holds: real PHI needs the SMART/auth layer, a
> Cloudflare Enterprise BAA, and the safeguards in the README before any of this carries PHI.
