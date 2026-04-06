import os

import requests
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "3"))

EDGES = {
    "us": os.getenv("EDGE_US_URL", "http://edge_us:5000"),
    "eu": os.getenv("EDGE_EU_URL", "http://edge_eu:5000"),
    "asia": os.getenv("EDGE_ASIA_URL", "http://edge_asia:5000"),
}


@app.get("/")
def ui():
    return render_template_string(
        """
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Purge Service UI</title>
  <style>
    :root { --bg:#f2f5fb; --card:#ffffff; --txt:#1f2f4d; --accent:#2b6cb0; --muted:#5a6f96; }
    body { margin:0; font-family:Segoe UI,Tahoma,sans-serif; background:var(--bg); color:var(--txt); }
    .wrap { max-width:960px; margin:24px auto; padding:0 16px; }
    .card { background:var(--card); border-radius:12px; padding:16px; margin-bottom:16px; box-shadow:0 8px 24px rgba(31,47,77,.08); }
    h1, h2 { margin:0 0 10px; }
    .muted { color:var(--muted); }
    input, button { width:100%; margin-top:8px; padding:10px; border-radius:8px; border:1px solid #c7d2e5; box-sizing:border-box; }
    button { background:var(--accent); color:#fff; border:none; cursor:pointer; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    pre { background:#152338; color:#dbe8ff; padding:12px; border-radius:8px; overflow:auto; }
    a { color:var(--accent); }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Purge Service</h1>
      <div class="muted">Port 5005 | Broadcast cache invalidation to all edge nodes.</div>
      <div style="margin-top:8px;">Service UIs: <a href="http://localhost:5000" target="_blank">Origin</a> | <a href="http://localhost:5001" target="_blank">Edge US</a> | <a href="http://localhost:5002" target="_blank">Edge EU</a> | <a href="http://localhost:5003" target="_blank">Edge Asia</a> | <a href="http://localhost:5004" target="_blank">Traffic Manager</a></div>
    </div>

    <div class="card row">
      <div>
        <h2>Purge Single Key</h2>
        <input id="key" value="index" placeholder="content key" />
        <button onclick="purgeKey()">POST /purge { key }</button>
      </div>
      <div>
        <h2>Purge Everything</h2>
        <button onclick="purgeAll()">POST /purge {}</button>
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

async function purgeKey() {
  const key = document.getElementById('key').value.trim();
  const r = await fetch('/purge', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key})
  });
  out.textContent = pretty(await r.json());
}

async function purgeAll() {
  const r = await fetch('/purge', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({})
  });
  out.textContent = pretty(await r.json());
}
</script>
</body>
</html>
        """
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "purge_service"})


@app.post("/purge")
def purge():
    payload = request.get_json(silent=True) or {}
    key = payload.get("key")

    results = []
    for region, edge_url in EDGES.items():
        endpoint = f"{edge_url}/purge/{key}" if key else f"{edge_url}/purge"
        try:
            response = requests.delete(endpoint, timeout=REQUEST_TIMEOUT_SECONDS)
            results.append(
                {
                    "region": region,
                    "edge_url": edge_url,
                    "status": response.status_code,
                    "response": response.json(),
                }
            )
        except requests.RequestException as exc:
            results.append(
                {
                    "region": region,
                    "edge_url": edge_url,
                    "status": 502,
                    "response": {"error": str(exc)},
                }
            )

    return jsonify(
        {
            "message": "purge broadcast completed",
            "key": key,
            "results": results,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
