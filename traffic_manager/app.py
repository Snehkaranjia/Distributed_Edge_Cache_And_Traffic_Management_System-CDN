import os
import threading

import requests
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "3"))
MAX_IN_FLIGHT = int(os.getenv("MAX_IN_FLIGHT", "20"))

EDGE_MAP = {
    "us": os.getenv("EDGE_US_URL", "http://edge_us:5000"),
    "eu": os.getenv("EDGE_EU_URL", "http://edge_eu:5000"),
    "asia": os.getenv("EDGE_ASIA_URL", "http://edge_asia:5000"),
}

FALLBACK_ORDER = {
    "us": ["us", "eu", "asia"],
    "eu": ["eu", "us", "asia"],
    "asia": ["asia", "eu", "us"],
}

in_flight_lock = threading.Lock()
in_flight_requests = 0


def is_edge_healthy(edge_url: str) -> bool:
    try:
        response = requests.get(f"{edge_url}/health", timeout=REQUEST_TIMEOUT_SECONDS)
        return response.status_code == 200
    except requests.RequestException:
        return False


def pick_edge(client_region: str):
    order = FALLBACK_ORDER.get(client_region, ["us", "eu", "asia"])
    for region in order:
        url = EDGE_MAP[region]
        if is_edge_healthy(url):
            return region, url
    return None, None


@app.get("/")
def index():
    return render_template_string(
        """
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Traffic Manager UI</title>
  <style>
    :root { --bg:#f9f6f1; --card:#ffffff; --txt:#3d2f1f; --accent:#c05621; --muted:#6b4f32; }
    body { margin:0; font-family:Segoe UI,Tahoma,sans-serif; background:var(--bg); color:var(--txt); }
    .wrap { max-width:960px; margin:24px auto; padding:0 16px; }
    .card { background:var(--card); border-radius:12px; padding:16px; margin-bottom:16px; box-shadow:0 8px 24px rgba(61,47,31,.08); }
    h1, h2 { margin:0 0 10px; }
    .muted { color:var(--muted); }
    input, select, button { width:100%; margin-top:8px; padding:10px; border-radius:8px; border:1px solid #ddc7b0; box-sizing:border-box; }
    button { background:var(--accent); color:#fff; border:none; cursor:pointer; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    pre { background:#2a1f14; color:#f5e9dc; padding:12px; border-radius:8px; overflow:auto; }
    a { color:var(--accent); }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Traffic Manager</h1>
      <div class="muted">Port 5004 | Routes client to nearest healthy edge with failover.</div>
      <div style="margin-top:8px;">Service UIs: <a href="http://localhost:5000" target="_blank">Origin</a> | <a href="http://localhost:5001" target="_blank">Edge US</a> | <a href="http://localhost:5002" target="_blank">Edge EU</a> | <a href="http://localhost:5003" target="_blank">Edge Asia</a> | <a href="http://localhost:5005" target="_blank">Purge Service</a></div>
    </div>

    <div class="card row">
      <div>
        <h2>Fetch via Traffic Manager</h2>
        <input id="key" value="index" placeholder="content key" />
        <select id="region">
          <option value="asia">asia</option>
          <option value="eu">eu</option>
          <option value="us">us</option>
        </select>
        <button onclick="fetchThroughTM()">GET /fetch/&lt;key&gt;?region=...</button>
      </div>
      <div>
        <h2>Edge Health</h2>
        <button onclick="checkEdges()">GET /edges</button>
      </div>
    </div>

    <div class="card">
      <h2>Result</h2>
      <pre id="out">Ready</pre>
    </div>
  </div>

<script>
const out = document.getElementById('out');
const pretty = (obj) => JSON.stringify(obj, null, 2);

async function fetchThroughTM() {
  const key = document.getElementById('key').value.trim();
  const region = document.getElementById('region').value;
  const r = await fetch(`/fetch/${encodeURIComponent(key)}?region=${encodeURIComponent(region)}`);
  out.textContent = pretty(await r.json());
}

async function checkEdges() {
  const r = await fetch('/edges');
  out.textContent = pretty(await r.json());
}
</script>
</body>
</html>
        """
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "traffic_manager", "in_flight": in_flight_requests})


@app.get("/edges")
def edges():
    result = {}
    for region, url in EDGE_MAP.items():
        result[region] = {"url": url, "healthy": is_edge_healthy(url)}
    return jsonify(result)


@app.get("/fetch/<key>")
def fetch(key: str):
    global in_flight_requests

    client_region = request.args.get("region", "asia").lower()

    with in_flight_lock:
        if in_flight_requests >= MAX_IN_FLIGHT:
            return jsonify({"error": "load_shed", "message": "Too many concurrent requests"}), 503
        in_flight_requests += 1

    try:
        chosen_region, edge_url = pick_edge(client_region)
        if not edge_url:
            return jsonify({"error": "no_healthy_edges"}), 503

        response = requests.get(f"{edge_url}/content/{key}", timeout=REQUEST_TIMEOUT_SECONDS + 5)
        payload = response.json()

        return (
            jsonify(
                {
                    "traffic_manager": "ok",
                    "client_region": client_region,
                    "routed_region": chosen_region,
                    "edge_url": edge_url,
                    "upstream_status": response.status_code,
                    "data": payload,
                }
            ),
            response.status_code,
        )
    except requests.RequestException as exc:
        return jsonify({"error": "edge_request_failed", "details": str(exc)}), 502
    finally:
        with in_flight_lock:
            in_flight_requests -= 1


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
