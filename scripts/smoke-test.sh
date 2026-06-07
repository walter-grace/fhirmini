#!/usr/bin/env bash
set -uo pipefail

# End-to-end smoke test of the whole appliance. Exits non-zero on any failure.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PASS=0; FAIL=0
ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ❌ $1"; FAIL=$((FAIL+1)); }
chk()  { [ "$1" = "$2" ] && ok "$3" || bad "$3 (got '$1' want '$2')"; }

echo "── 1. FHIR core ──────────────────────────────"
chk "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/fhir/metadata)" "200" "FHIR metadata responds"
PID=$(curl -s -X POST http://127.0.0.1:8080/fhir/Patient -H 'Content-Type: application/fhir+json' \
   -d '{"resourceType":"Patient","name":[{"family":"Smoke","given":["Test"]}]}' | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
[ -n "$PID" ] && ok "created Patient/$PID" || bad "create Patient"
chk "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/fhir/Patient/$PID)" "200" "read Patient back"

echo "── 2. AI layer ───────────────────────────────"
chk "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/ai/health)" "200" "AI sidecar responds"
curl -s -X POST http://127.0.0.1:8090/ai/index -H 'Content-Type: application/json' -d '{}' >/dev/null 2>&1 \
  && ok "AI index ran" || bad "AI index"
N=$(curl -s -X POST http://127.0.0.1:8090/ai/search -H 'Content-Type: application/json' -d '{"q":"heart problems","k":3}' \
   | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null)
[ "${N:-0}" -ge 1 ] 2>/dev/null && ok "semantic search returned $N hits" || bad "semantic search"

echo "── 3. Integration engine (MLLP HL7v2 → FHIR) ─"
chk "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8088/engine/health)" "200" "engine responds"
MRN="SMOKE-$RANDOM"
fhir-ai/.venv/bin/python - "$MRN" <<'PY'
import socket,sys
mrn=sys.argv[1]; VT,FS,CR=0x0b,0x1c,0x0d
hl7=f"MSH|^~\\&|SMOKE|T|MAC|M|20260606||ADT^A04|SMK{mrn}|P|2.5\rPID|||{mrn}^^^H^MR||Smoke^Tester||19900101|M\r"
s=socket.create_connection(("127.0.0.1",2575),timeout=10)
s.sendall(bytes([VT])+hl7.encode()+bytes([FS,CR])); ack=s.recv(4096); s.close()
sys.exit(0 if b"|AA|" in ack else 1)
PY
[ $? -eq 0 ] && ok "MLLP ACK accepted" || bad "MLLP ACK"
sleep 1
T=$(curl -s "http://127.0.0.1:8080/fhir/Patient?identifier=urn:mac-fhir:mrn|$MRN" | python3 -c "import sys,json;print(json.load(sys.stdin).get('total',0))" 2>/dev/null)
chk "${T:-0}" "1" "HL7 patient landed in FHIR"

echo "──────────────────────────────────────────────"
echo "RESULT: $PASS passed, $FAIL failed"
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
