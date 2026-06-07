"""
Mac FHIR Server — on-device AI layer (Phase C).

A lightweight FastAPI sidecar that adds semantic search, RAG Q&A, and NLP
extraction *on top of* the native HAPI FHIR server. Embeddings run on-device via
MLX (Apple GPU); generation is pluggable: local MLX (PHI-safe, default) or
OpenRouter (cloud — DEV/synthetic ONLY, hard-guarded off at PHASE=phi-readiness).

Endpoints:
  GET  /ai/health            backend + index status
  POST /ai/index             pull FHIR resources -> embed -> store
  POST /ai/search            semantic search -> ranked resource refs
  POST /ai/ask               RAG Q&A over indexed resources (LLM)
  POST /ai/extract           free clinical text -> structured FHIR (LLM)

No PHI leaves the box when AI_BACKEND=local. See docs/DECISIONS.md.
"""
import os, json, base64, sqlite3, urllib.request, urllib.error, urllib.parse
from contextlib import asynccontextmanager
from typing import Optional
import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ----------------------------------------------------------------------------- config
FHIR_BASE      = os.environ.get("FHIR_BASE", "http://127.0.0.1:8080/fhir")
AI_BACKEND     = os.environ.get("AI_BACKEND", "local").lower()
PHASE          = os.environ.get("PHASE", "dev-sandbox")
EMBED_MODEL    = os.environ.get("AI_EMBED_MODEL", "mlx-community/bge-small-en-v1.5-bf16")
LOCAL_LLM_URL  = os.environ.get("AI_LOCAL_LLM_URL", "http://127.0.0.1:8081/v1")
LOCAL_LLM      = os.environ.get("AI_LOCAL_LLM", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit")
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
DB_PATH        = os.environ.get("AI_DB", os.path.join(os.path.dirname(__file__), "index.db"))

DEFAULT_TYPES = ["Patient", "Observation", "Condition", "DocumentReference",
                 "MedicationRequest", "Procedure", "AllergyIntolerance",
                 "Encounter", "DiagnosticReport", "Immunization"]

# ----------------------------------------------------------------------------- embeddings (MLX)
class Embedder:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self._model = None
        self._tok = None
    def _ensure(self):
        if self._model is None:
            from mlx_embeddings.utils import load
            self._model, self._tok = load(self.model_id)
    def encode(self, texts: list[str]) -> np.ndarray:
        """Return L2-normalized float32 embeddings (N, dim)."""
        self._ensure()
        from mlx_embeddings import generate
        import mlx.core as mx
        out = generate(self._model, self._tok, texts=texts)
        arr = np.array(out.text_embeds.tolist(), dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / np.clip(norms, 1e-8, None)

# ----------------------------------------------------------------------------- vector store (SQLite + in-memory matrix)
class Store:
    def __init__(self, path: str):
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS chunks(
            key TEXT PRIMARY KEY, resource_type TEXT, resource_id TEXT,
            version TEXT, text TEXT, dim INTEGER, vec BLOB)""")
        self.db.commit()
        self.keys: list[str] = []
        self.meta: list[dict] = []
        self.matrix: Optional[np.ndarray] = None
        self._load()
    def _load(self):
        rows = self.db.execute("SELECT key,resource_type,resource_id,text,dim,vec FROM chunks").fetchall()
        self.keys, self.meta, vecs = [], [], []
        for key, rtype, rid, text, dim, vec in rows:
            self.keys.append(key)
            self.meta.append({"resource_type": rtype, "resource_id": rid, "text": text})
            vecs.append(np.frombuffer(vec, dtype=np.float32))
        self.matrix = np.vstack(vecs) if vecs else None
    def upsert(self, items: list[dict]):
        for it in items:
            v = it["vec"].astype(np.float32)
            self.db.execute(
                "INSERT OR REPLACE INTO chunks(key,resource_type,resource_id,version,text,dim,vec) VALUES(?,?,?,?,?,?,?)",
                (it["key"], it["resource_type"], it["resource_id"], it.get("version",""),
                 it["text"], v.shape[0], v.tobytes()))
        self.db.commit()
        self._load()
    def search(self, qvec: np.ndarray, k: int, types: Optional[list[str]]):
        if self.matrix is None: return []
        sims = self.matrix @ qvec  # cosine (all vectors are L2-normalized)
        order = np.argsort(-sims)
        out = []
        for i in order:
            m = self.meta[i]
            if types and m["resource_type"] not in types: continue
            out.append({**m, "score": float(sims[i])})
            if len(out) >= k: break
        return out
    def count(self): return len(self.keys)

# ----------------------------------------------------------------------------- FHIR text extraction
def _cc_text(cc) -> str:
    if not isinstance(cc, dict): return ""
    if cc.get("text"): return cc["text"]
    return " / ".join(c.get("display","") for c in cc.get("coding",[]) if c.get("display"))

def fhir_resource_to_text(r: dict) -> str:
    t = r.get("resourceType", "")
    parts = [t]
    if t == "Patient":
        for n in r.get("name", []):
            parts.append(" ".join(n.get("given", [])) + " " + n.get("family", ""))
        parts += [f"gender {r.get('gender','')}", f"born {r.get('birthDate','')}"]
    elif t == "Observation":
        parts += [_cc_text(r.get("code", {}))]
        vq = r.get("valueQuantity")
        if vq: parts.append(f"value {vq.get('value','')} {vq.get('unit','')}")
        if r.get("valueString"): parts.append(r["valueString"])
        parts.append(_cc_text(r.get("interpretation", [{}])[0]) if r.get("interpretation") else "")
    elif t in ("Condition", "Procedure", "AllergyIntolerance"):
        parts += [_cc_text(r.get("code", {})), _cc_text(r.get("clinicalStatus", {}))]
    elif t == "Immunization":
        parts.append(_cc_text(r.get("vaccineCode", {})))
        if r.get("occurrenceDateTime"):
            parts.append(f"given {r['occurrenceDateTime'][:10]}")
    elif t == "MedicationRequest":
        parts.append(_cc_text(r.get("medicationCodeableConcept", {})))
        for d in r.get("dosageInstruction", []):
            if d.get("text"): parts.append(d["text"])
    elif t == "DocumentReference":
        for c in r.get("content", []):
            att = c.get("attachment", {})
            if att.get("contentType","").startswith("text") and att.get("data"):
                try: parts.append(base64.b64decode(att["data"]).decode("utf-8", "ignore"))
                except Exception: pass
            if att.get("title"): parts.append(att["title"])
    # generic: pull any free-text notes
    for note in r.get("note", []):
        if note.get("text"): parts.append(note["text"])
    if r.get("text", {}).get("div"):
        import re
        parts.append(re.sub("<[^>]+>", " ", r["text"]["div"]))
    return " | ".join(p for p in parts if p and p.strip())

# ----------------------------------------------------------------------------- HTTP helpers
def _http(method: str, url: str, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}
    except Exception as e:
        return 0, {"error": str(e)}

def fhir_get(path: str):
    return _http("GET", f"{FHIR_BASE}/{path}", headers={"Accept": "application/fhir+json"})

def llm_chat(messages: list[dict], max_tokens=800, temperature=0.2) -> str:
    if AI_BACKEND == "openrouter":
        if PHASE == "phi-readiness":
            raise HTTPException(403, "openrouter backend is hard-disabled at PHASE=phi-readiness (PHI must not leave the box)")
        if not OPENROUTER_KEY:
            raise HTTPException(400, "OPENROUTER_API_KEY not set in .env")
        st, r = _http("POST", OPENROUTER_URL,
                      {"model": OPENROUTER_MODEL, "messages": messages,
                       "max_tokens": max_tokens, "temperature": temperature},
                      {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"})
    else:  # local MLX (mlx_vlm.server / mlx_lm.server, OpenAI-compatible)
        st, r = _http("POST", f"{LOCAL_LLM_URL}/chat/completions",
                      {"model": LOCAL_LLM, "messages": messages,
                       "max_tokens": max_tokens, "temperature": temperature},
                      {"Content-Type": "application/json"})
    if st != 200:
        raise HTTPException(502, f"LLM backend '{AI_BACKEND}' error: {r.get('error', r)}")
    return r["choices"][0]["message"]["content"]

# ----------------------------------------------------------------------------- app
embedder = Embedder(EMBED_MODEL)
store: Optional[Store] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    store = Store(DB_PATH)
    yield

app = FastAPI(title="Mac FHIR AI layer", lifespan=lifespan)

class IndexReq(BaseModel):
    resource_types: Optional[list[str]] = None
    max_per_type: int = 1000

class SearchReq(BaseModel):
    q: str
    k: int = 8
    types: Optional[list[str]] = None

class AskReq(BaseModel):
    q: str
    k: int = 8

class ExtractReq(BaseModel):
    text: str
    resource_type: str = "Condition"

@app.get("/ai/health")
def health():
    st, _ = fhir_get("metadata?_summary=true")
    return {"backend": AI_BACKEND, "phase": PHASE, "embed_model": EMBED_MODEL,
            "local_llm": LOCAL_LLM, "fhir_reachable": st == 200,
            "indexed_chunks": store.count() if store else 0}

@app.post("/ai/index")
def index(req: IndexReq):
    types = req.resource_types or DEFAULT_TYPES
    indexed, by_type = 0, {}
    for rtype in types:
        st, bundle = fhir_get(f"{rtype}?_count={min(req.max_per_type,200)}")
        if st != 200: continue
        batch_text, batch_meta = [], []
        for e in bundle.get("entry", []):
            res = e.get("resource", {})
            txt = fhir_resource_to_text(res)
            if not txt.strip(): continue
            rid = res.get("id", "")
            batch_text.append(txt)
            batch_meta.append({"key": f"{rtype}/{rid}", "resource_type": rtype,
                               "resource_id": rid, "version": res.get("meta",{}).get("versionId",""),
                               "text": txt})
        if not batch_text: continue
        vecs = embedder.encode(batch_text)
        store.upsert([{**m, "vec": vecs[i]} for i, m in enumerate(batch_meta)])
        by_type[rtype] = len(batch_text); indexed += len(batch_text)
    return {"indexed": indexed, "by_type": by_type, "total_chunks": store.count()}

@app.post("/ai/search")
def search(req: SearchReq):
    qvec = embedder.encode([req.q])[0]
    return {"query": req.q, "results": store.search(qvec, req.k, req.types)}

@app.post("/ai/ask")
def ask(req: AskReq):
    qvec = embedder.encode([req.q])[0]
    hits = store.search(qvec, req.k, None)
    if not hits:
        return {"answer": "No indexed resources yet — POST /ai/index first.", "citations": []}
    context = "\n".join(f"[{i+1}] {h['resource_type']}/{h['resource_id']}: {h['text'][:400]}"
                        for i, h in enumerate(hits))
    messages = [
        {"role": "system", "content": "You are a clinical data assistant answering ONLY from the "
         "provided FHIR resources. Cite sources as [n]. If the context is insufficient, say so. "
         "This is synthetic test data."},
        {"role": "user", "content": f"FHIR context:\n{context}\n\nQuestion: {req.q}"}]
    answer = llm_chat(messages)
    return {"answer": answer,
            "citations": [{"n": i+1, "ref": f"{h['resource_type']}/{h['resource_id']}", "score": h["score"]}
                          for i, h in enumerate(hits)]}

@app.post("/ai/extract")
def extract(req: ExtractReq):
    messages = [
        {"role": "system", "content": f"Extract a single FHIR R4 {req.resource_type} resource from the "
         f"clinical text. Return ONLY valid JSON for the resource (resourceType={req.resource_type}), "
         "no prose, no markdown fences."},
        {"role": "user", "content": req.text}]
    raw = llm_chat(messages, max_tokens=600)
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return {"resource": json.loads(raw)}
    except json.JSONDecodeError:
        return {"resource": None, "raw": raw, "note": "model did not return valid JSON"}
