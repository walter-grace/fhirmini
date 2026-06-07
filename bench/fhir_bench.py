#!/usr/bin/env python3
"""fhirmini benchmark harness — compare FHIR API performance across targets
(e.g. Mac mini native vs cloud/RunPod docker, direct vs Cloudflare Tunnel).

Design notes (learned the hard way):
- Each worker holds ONE persistent keep-alive connection (http.client). A naive
  new-connection-per-request generator exhausts ephemeral ports and benchmarks
  the OS, not the server.
- Write scenarios are OPT-IN (--writes) and every created resource carries the
  meta.tag `urn:fhirmini:bench|1`, so `--cleanup` can delete them precisely.
- MLLP uses one persistent socket per worker (MLLP allows many messages per
  connection); opt-in via --mllp host:port.

Usage:
  fhir_bench.py --base http://127.0.0.1:8080/fhir --label mac-native \
                --concurrency 1,8,32 --seconds 10 --out bench/results-mac.json
  fhir_bench.py --base ... --cleanup           # delete tagged bench resources
Fairness: same dataset, same scenarios, same generator on every target.
"""
import argparse, http.client, json, random, socket, ssl, string, threading, time
import urllib.parse

BENCH_TAG = {"system": "urn:fhirmini:bench", "code": "1"}
TAG_PARAM = "urn:fhirmini:bench|1"


class Client:
    """One persistent keep-alive connection. Reconnects once on failure."""
    def __init__(self, base):
        u = urllib.parse.urlparse(base)
        self.host, self.scheme = u.hostname, u.scheme
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.prefix = u.path.rstrip("/")
        self.conn = None

    def _connect(self):
        if self.scheme == "https":
            self.conn = http.client.HTTPSConnection(self.host, self.port, timeout=30,
                                                    context=ssl.create_default_context())
        else:
            self.conn = http.client.HTTPConnection(self.host, self.port, timeout=30)

    def request(self, method, path, body=None):
        payload = json.dumps(body) if body is not None else None
        headers = {"Accept": "application/fhir+json", "User-Agent": "fhirmini-bench"}
        if payload:
            headers["Content-Type"] = "application/fhir+json"
        t0 = time.perf_counter()
        for attempt in (1, 2):
            try:
                if self.conn is None:
                    self._connect()
                self.conn.request(method, self.prefix + path, body=payload, headers=headers)
                r = self.conn.getresponse()
                r.read()                      # drain so the connection is reusable
                return (time.perf_counter() - t0) * 1000, r.status
            except Exception:
                try:
                    self.conn.close()
                except Exception:
                    pass
                self.conn = None
                if attempt == 2:
                    return (time.perf_counter() - t0) * 1000, 0


class MllpClient:
    """One persistent MLLP socket; many messages per connection."""
    VT, FS, CR = 0x0B, 0x1C, 0x0D

    def __init__(self, host, port):
        self.host, self.port, self.sock = host, int(port), None

    def send(self):
        n = random.randint(0, 10**9)
        hl7 = (f"MSH|^~\\&|BENCH|B|FHIRMINI|M|20260101||ADT^A04|B{n}|P|2.5\r"
               f"PID|||BENCH-{n}^^^B^MR||Bench^Load||19900101|M\r")
        frame = bytes([self.VT]) + hl7.encode() + bytes([self.FS, self.CR])
        t0 = time.perf_counter()
        for attempt in (1, 2):
            try:
                if self.sock is None:
                    self.sock = socket.create_connection((self.host, self.port), timeout=30)
                    self.sock.settimeout(30)
                self.sock.sendall(frame)
                buf = b""
                while bytes([self.FS, self.CR]) not in buf:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        raise ConnectionError("closed")
                    buf += chunk
                return (time.perf_counter() - t0) * 1000, (200 if b"|AA|" in buf else 500)
            except Exception:
                try:
                    self.sock and self.sock.close()
                except Exception:
                    pass
                self.sock = None
                if attempt == 2:
                    return (time.perf_counter() - t0) * 1000, 0


def pct(sorted_ms, p):
    return sorted_ms[min(len(sorted_ms) - 1, int(len(sorted_ms) * p))] if sorted_ms else None


def make_scenarios(base, ctx, mllp, writes):
    pid = lambda: random.choice(ctx["patient_ids"]) if ctx["patient_ids"] else "1"
    letter = lambda: random.choice(string.ascii_lowercase)

    def obs():
        return {"resourceType": "Observation", "status": "final",
                "meta": {"tag": [BENCH_TAG]},
                "code": {"coding": [{"system": "http://loinc.org", "code": "8867-4",
                                     "display": "Heart rate"}]},
                "subject": {"reference": f"Patient/{pid()}"},
                "valueQuantity": {"value": random.randint(55, 110), "unit": "beats/minute"}}

    def txn():
        return {"resourceType": "Bundle", "type": "transaction", "entry": [
            {"resource": {"resourceType": "Patient", "meta": {"tag": [BENCH_TAG]},
                          "name": [{"family": f"Bench{random.randint(0, 10**9)}", "given": ["Load"]}]},
             "request": {"method": "POST", "url": "Patient"}},
            {"resource": obs(), "request": {"method": "POST", "url": "Observation"}}]}

    s = {
        "metadata":     lambda c: c.request("GET", "/metadata?_summary=true"),
        "patient_read": lambda c: c.request("GET", f"/Patient/{pid()}"),
        "name_search":  lambda c: c.request("GET", f"/Patient?name={letter()}&_count=10"),
        "obs_search":   lambda c: c.request("GET", "/Observation?code=8867-4&_count=20"),
    }
    if writes:
        s["obs_write"] = lambda c: c.request("POST", "/Observation", obs())
        s["txn_bundle"] = lambda c: c.request("POST", "", txn())
    if mllp:
        s["hl7_mllp_ingest"] = "MLLP"
    return s


def run_scenario(name, scen, base, mllp, concurrency, seconds):
    results_lock = threading.Lock()
    lat, errors = [], [0]
    stop_at = [0.0]

    def worker():
        client = MllpClient(*mllp.split(":")) if scen == "MLLP" else Client(base)
        do = client.send if scen == "MLLP" else (lambda: scen(client))
        # warmup outside the timed window
        do()
        my_lat, my_err = [], 0
        while time.perf_counter() < stop_at[0]:
            ms, status = do()
            my_lat.append(ms)
            if status == 0 or status >= 400:
                my_err += 1
        with results_lock:
            lat.extend(my_lat)
            errors[0] += my_err

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    stop_at[0] = time.perf_counter() + seconds + 1   # +1 covers warmup skew
    t0 = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    wall = time.perf_counter() - t0
    lat.sort()
    return {"scenario": name, "concurrency": concurrency, "requests": len(lat),
            "rps": round(len(lat) / wall, 1), "errors": errors[0],
            "p50_ms": round(pct(lat, 0.50), 2) if lat else None,
            "p95_ms": round(pct(lat, 0.95), 2) if lat else None,
            "p99_ms": round(pct(lat, 0.99), 2) if lat else None}


def cleanup(base):
    c = Client(base)
    print("Deleting tagged bench resources...")
    for rt in ("Observation", "Patient"):
        ms, st = c.request("DELETE", f"/{rt}?_tag={urllib.parse.quote(TAG_PARAM)}")
        print(f"  DELETE {rt}?_tag=bench -> HTTP {st} ({ms:.0f}ms)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--label", default="target")
    ap.add_argument("--concurrency", default="1,8,32")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--writes", action="store_true", help="include write scenarios (tagged for cleanup)")
    ap.add_argument("--mllp", default="", help="host:port for HL7v2 ingest scenario")
    ap.add_argument("--cost-per-hour", type=float, default=None)
    ap.add_argument("--cleanup", action="store_true", help="delete tagged bench resources and exit")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    if a.cleanup:
        cleanup(a.base)
        return

    boot = Client(a.base)
    _, st = boot.request("GET", "/metadata?_summary=true")
    assert st == 200, f"target not reachable: HTTP {st}"
    ms, st = boot.request("GET", "/Patient?_count=50&_elements=id")
    ctx = {"patient_ids": []}
    try:
        boot2 = Client(a.base)
        import urllib.request
        with urllib.request.urlopen(urllib.request.Request(
                f"{a.base}/Patient?_count=50&_elements=id",
                headers={"Accept": "application/fhir+json"}), timeout=30) as r:
            ctx["patient_ids"] = [e["resource"]["id"] for e in json.loads(r.read()).get("entry", [])]
    except Exception:
        pass

    scenarios = make_scenarios(a.base, ctx, a.mllp, a.writes)
    results = {"label": a.label, "base": a.base, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
               "cost_per_hour": a.cost_per_hour, "runs": []}
    print(f"target={a.label}  base={a.base}  patients={len(ctx['patient_ids'])}\n")
    print(f"{'scenario':<18}{'conc':>5}{'reqs':>8}{'rps':>9}{'p50ms':>9}{'p95ms':>9}{'p99ms':>9}{'err':>7}")
    for name, scen in scenarios.items():
        for c in [int(x) for x in a.concurrency.split(",")]:
            r = run_scenario(name, scen, a.base, a.mllp, c, a.seconds)
            results["runs"].append(r)
            print(f"{name:<18}{c:>5}{r['requests']:>8}{r['rps']:>9}{r['p50_ms']:>9}{r['p95_ms']:>9}{r['p99_ms']:>9}{r['errors']:>7}")
    if a.cost_per_hour:
        best = max((r for r in results["runs"] if r["scenario"] == "patient_read"),
                   key=lambda r: r["rps"], default=None)
        if best:
            results["reads_per_dollar"] = round(best["rps"] * 3600 / a.cost_per_hour)
            print(f"\nreads per $1 (peak patient_read): {results['reads_per_dollar']:,}")
    if a.out:
        with open(a.out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
