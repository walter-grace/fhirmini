"""fhirmini MCP server.

One Model Context Protocol server that turns the whole fhirmini stack into agent tools:
  lifecycle  — stack_status / stack_start / stack_stop
  FHIR       — fhir_create_patient / fhir_create_observation / fhir_search / fhir_read / fhir_count
  engine     — hl7_send_adt / hl7_send_raw / engine_messages
  AI         — ai_search / ai_ask / ai_extract

Transports:
  (default) stdio              — for Claude Desktop/Code and local picoclaw
  --http [--host H --port P]   — streamable-HTTP for remote/edge picoclaw over the network

Everything talks to the already-running local services; the agent is the brain, fhirmini
is the body. Loopback by default — see docs/MCP.md for exposing to edge devices safely.
"""
import argparse, base64, json, os, socket, subprocess, time, urllib.request, urllib.error
from mcp.server.fastmcp import FastMCP

FHIR_BASE   = os.environ.get("FHIR_BASE", "http://127.0.0.1:8080/fhir")
AI_BASE     = os.environ.get("AI_BASE", "http://127.0.0.1:8090")
ENGINE_BASE = os.environ.get("ENGINE_BASE", "http://127.0.0.1:8088")
MLLP_HOST   = os.environ.get("MLLP_HOST", "127.0.0.1")
MLLP_PORT   = int(os.environ.get("MLLP_PORT", "2575"))
ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

mcp = FastMCP("fhirmini")


# --------------------------------------------------------------------------- helpers
def _http(method, url, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Accept": "application/fhir+json"}
    if data:
        h["Content-Type"] = "application/fhir+json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:400]}
    except Exception as e:
        return 0, {"error": str(e)}


def _code(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status
    except Exception:
        return 0


def _entries(bundle, limit=20):
    out = []
    for e in bundle.get("entry", [])[:limit]:
        r = e.get("resource", {})
        out.append({"resourceType": r.get("resourceType"), "id": r.get("id"),
                    "summary": _summarize(r)})
    return out


def _summarize(r):
    t = r.get("resourceType")
    if t == "Patient":
        n = (r.get("name") or [{}])[0]
        return f"{' '.join(n.get('given', []))} {n.get('family', '')}".strip() + f" ({r.get('gender','?')}, {r.get('birthDate','?')})"
    if t == "Observation":
        c = r.get("code", {}).get("text") or (r.get("code", {}).get("coding") or [{}])[0].get("display", "")
        vq = r.get("valueQuantity", {})
        return f"{c} = {vq.get('value','')} {vq.get('unit','')}".strip()
    if t in ("Condition", "Procedure"):
        return r.get("code", {}).get("text") or "(coded)"
    return t or "?"


# --------------------------------------------------------------------------- lifecycle
@mcp.tool()
def stack_status() -> dict:
    """Report health of every fhirmini service (FHIR, AI, engine, MLLP) and resource counts.
    Call this first to know what's running before doing other operations."""
    s = {
        "fhir":   _code(f"{FHIR_BASE}/metadata?_summary=true") == 200,
        "ai":     _code(f"{AI_BASE}/ai/health") == 200,
        "engine": _code(f"{ENGINE_BASE}/engine/health") == 200,
    }
    try:
        with socket.create_connection((MLLP_HOST, MLLP_PORT), timeout=3):
            s["mllp"] = True
    except Exception:
        s["mllp"] = False
    counts = {}
    if s["fhir"]:
        for rt in ("Patient", "Observation", "Condition"):
            _, b = _http("GET", f"{FHIR_BASE}/{rt}?_summary=count")
            counts[rt] = b.get("total")
    return {"services": s, "counts": counts, "all_up": all(s.values())}


@mcp.tool()
def stack_start() -> dict:
    """Start any fhirmini services that are down (FHIR, AI sidecar, integration engine).
    Idempotent — already-running services are left alone. Returns what was started."""
    started = []
    checks = {"fhir": (f"{FHIR_BASE}/metadata?_summary=true", "run-hapi.sh", "hapi"),
              "ai": (f"{AI_BASE}/ai/health", "run-ai.sh", "ai"),
              "engine": (f"{ENGINE_BASE}/engine/health", "run-engine.sh", "engine")}
    for name, (url, script, log) in checks.items():
        if _code(url) == 200:
            continue
        os.makedirs(f"{ROOT}/logs", exist_ok=True)
        with open(f"{ROOT}/logs/{log}.out", "ab") as out, open(f"{ROOT}/logs/{log}.err", "ab") as err:
            subprocess.Popen(["/bin/bash", f"{ROOT}/scripts/{script}"],
                             cwd=ROOT, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        started.append(name)
    # wait briefly for readiness
    deadline = time.time() + 60
    while time.time() < deadline and started:
        if all(_code(checks[n][0]) == 200 for n in started):
            break
        time.sleep(2)
    return {"started": started, "status": stack_status()["services"]}


@mcp.tool()
def stack_stop() -> dict:
    """Stop the manually-run fhirmini services (AI sidecar + integration engine).
    Does NOT touch Postgres or a launchd-managed HAPI. Use for a clean shutdown of agent-started services."""
    killed = []
    for pat in ("uvicorn app:app", "uvicorn engine.server:app"):
        rc = subprocess.run(["pkill", "-f", pat]).returncode
        if rc == 0:
            killed.append(pat)
    return {"stopped": killed}


# --------------------------------------------------------------------------- FHIR
@mcp.tool()
def fhir_create_patient(family: str, given: str = "", gender: str = "", birth_date: str = "", mrn: str = "") -> dict:
    """Create a Patient. gender: male|female|other|unknown. birth_date: YYYY-MM-DD.
    If mrn is given, the patient is upserted by that MRN (no duplicates). Returns the new id."""
    p = {"resourceType": "Patient", "name": [{"family": family, "given": [given] if given else []}]}
    if gender:
        p["gender"] = gender
    if birth_date:
        p["birthDate"] = birth_date
    if mrn:
        p["identifier"] = [{"system": "urn:mac-fhir:mrn", "value": mrn}]
        st, r = _http("PUT", f"{FHIR_BASE}/Patient?identifier=urn:mac-fhir:mrn|{mrn}", p)
    else:
        st, r = _http("POST", f"{FHIR_BASE}/Patient", p)
    return {"ok": st in (200, 201), "status": st, "id": r.get("id"), "resource": _summarize(r) if r.get("resourceType") else None}


@mcp.tool()
def fhir_create_observation(code_text: str, value: float, unit: str = "", patient_id: str = "", loinc: str = "") -> dict:
    """Record an Observation (e.g. a vital sign or lab) for a patient. code_text describes
    what was measured (e.g. 'Heart rate'); value/unit the result; patient_id links it."""
    obs = {"resourceType": "Observation", "status": "final",
           "code": {"text": code_text, "coding": [{"system": "http://loinc.org", "code": loinc, "display": code_text}] if loinc else []},
           "valueQuantity": {"value": value, "unit": unit}}
    if patient_id:
        obs["subject"] = {"reference": f"Patient/{patient_id}"}
    st, r = _http("POST", f"{FHIR_BASE}/Observation", obs)
    return {"ok": st in (200, 201), "status": st, "id": r.get("id")}


@mcp.tool()
def fhir_search(resource_type: str, query: str = "", limit: int = 20) -> dict:
    """Search FHIR resources. resource_type e.g. Patient|Observation|Condition. query is a
    raw FHIR search string e.g. 'name=Smith' or 'code=8867-4'. Returns summarized matches."""
    url = f"{FHIR_BASE}/{resource_type}?_count={limit}" + (f"&{query}" if query else "")
    st, b = _http("GET", url)
    return {"status": st, "total": b.get("total"), "results": _entries(b, limit)}


@mcp.tool()
def fhir_read(resource_type: str, resource_id: str) -> dict:
    """Fetch one FHIR resource by type and id (full JSON)."""
    st, r = _http("GET", f"{FHIR_BASE}/{resource_type}/{resource_id}")
    return {"status": st, "resource": r}


@mcp.tool()
def fhir_count(resource_type: str) -> dict:
    """Count resources of a given type (e.g. how many Patients are in the repository)."""
    st, b = _http("GET", f"{FHIR_BASE}/{resource_type}?_summary=count")
    return {"resource_type": resource_type, "total": b.get("total")}


# --------------------------------------------------------------------------- integration engine
def _mllp_send(hl7: str, timeout=15):
    VT, FS, CR = 0x0B, 0x1C, 0x0D
    frame = bytes([VT]) + hl7.encode() + bytes([FS, CR])
    with socket.create_connection((MLLP_HOST, MLLP_PORT), timeout=timeout) as s:
        s.sendall(frame)
        s.settimeout(timeout)
        buf = b""
        while bytes([FS, CR]) not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    ack = buf.strip(bytes([VT, FS, CR])).decode("utf-8", "ignore")
    return ack


@mcp.tool()
def hl7_send_adt(mrn: str, family: str, given: str = "", gender: str = "M", birth_date: str = "") -> dict:
    """Send an HL7 v2 ADT^A04 patient-registration message over MLLP (the hospital-standard
    path). The engine parses it, maps to FHIR, and upserts the Patient. gender: M|F|O.
    birth_date: YYYYMMDD. Returns the ACK code and the resulting FHIR patient."""
    dob = birth_date.replace("-", "")
    hl7 = (f"MSH|^~\\&|AGENT|FHIRMINI|FHIRMINI|MINI|20260101||ADT^A04|AGT{int(time.time())}|P|2.5\r"
           f"PID|||{mrn}^^^AGENT^MR||{family}^{given}||{dob}|{gender}\r")
    try:
        ack = _mllp_send(hl7)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    time.sleep(0.5)
    st, b = _http("GET", f"{FHIR_BASE}/Patient?identifier=urn:mac-fhir:mrn|{mrn}")
    pat = _entries(b, 1)
    return {"ok": "|AA|" in ack, "ack": "AA" if "|AA|" in ack else ack[:80], "fhir_patient": pat[0] if pat else None}


@mcp.tool()
def hl7_send_raw(hl7_message: str) -> dict:
    """Send a raw HL7 v2 message (segments separated by \\r or newlines) over MLLP. Returns the ACK."""
    try:
        ack = _mllp_send(hl7_message.replace("\\r", "\r").replace("\n", "\r"))
        return {"ok": "|AA|" in ack, "ack": ack[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def engine_messages(limit: int = 10) -> dict:
    """List the most recent messages processed by the integration engine (audit/ledger view)."""
    st, msgs = _http("GET", f"{ENGINE_BASE}/engine/messages?limit={limit}")
    return {"status": st, "messages": msgs}


# --------------------------------------------------------------------------- AI layer
@mcp.tool()
def ai_search(query: str, k: int = 8) -> dict:
    """Semantic search over indexed FHIR resources (on-device MLX embeddings, no keywords
    needed — e.g. 'heart problems' finds myocardial infarction). Returns ranked matches."""
    st, r = _http("POST", f"{AI_BASE}/ai/search", {"q": query, "k": k})
    return {"status": st, "results": r.get("results", r)}


@mcp.tool()
def ai_ask(question: str, k: int = 8) -> dict:
    """Ask a natural-language clinical question; answered by RAG over the indexed FHIR data
    using the on-device LLM, with citations. Requires the MLX LLM server (fhirmini llm start)
    or AI_BACKEND=openrouter."""
    st, r = _http("POST", f"{AI_BASE}/ai/ask", {"q": question, "k": k}, timeout=300)
    return {"status": st, "answer": r.get("answer"), "citations": r.get("citations")}


@mcp.tool()
def ai_extract(text: str, resource_type: str = "Condition") -> dict:
    """Extract a structured FHIR resource from free clinical text using the on-device LLM."""
    st, r = _http("POST", f"{AI_BASE}/ai/extract", {"text": text, "resource_type": resource_type}, timeout=300)
    return {"status": st, "resource": r.get("resource")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve over streamable-HTTP (for remote/edge clients)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8200)
    a = ap.parse_args()
    if a.http:
        mcp.settings.host = a.host
        mcp.settings.port = a.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
