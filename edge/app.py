import os
import threading
import time

import requests
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

EDGE_NAME = os.getenv("EDGE_NAME", "edge")
EDGE_REGION = os.getenv("EDGE_REGION", "unknown")
ORIGIN_URL = os.getenv("ORIGIN_URL", "http://origin:5000")
CACHE_HIT_DELAY_SECONDS = float(os.getenv("CACHE_HIT_DELAY_SECONDS", "0.1"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "5"))

cache = {}
cache_lock = threading.Lock()


@app.get("/")
def ui():
    return render_template_string(
        """
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Edge UI</title>
  <style>
    :root { --bg:#f3f9f4; --card:#ffffff; --txt:#1e3a2f; --accent:#2f855a; --muted:#466a57; }
    body { margin:0; font-family:Segoe UI,Tahoma,sans-serif; background:var(--bg); color:var(--txt); }
    .wrap { max-width:960px; margin:24px auto; padding:0 16px; }
    .card { background:var(--card); border-radius:12px; padding:16px; margin-bottom:16px; box-shadow:0 8px 24px rgba(30,58,47,.08); }
    h1, h2 { margin:0 0 10px; }
    .muted { color:var(--muted); }
    input, button { width:100%; margin-top:8px; padding:10px; border-radius:8px; border:1px solid #c8d8cc; box-sizing:border-box; }
    button { background:var(--accent); color:#fff; border:none; cursor:pointer; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    pre { background:#14281d; color:#d9f3e4; padding:12px; border-radius:8px; overflow:auto; }
    a { color:var(--accent); }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>{{ edge_name|e }} ({{ edge_region|e }})</h1>
      <div class="muted">Edge cache node UI.</div>
      <div style="margin-top:8px;">Open other services: <a href="http://localhost:5000" target="_blank">Origin</a> | <a href="http://localhost:5004" target="_blank">Traffic Manager</a> | <a href="http://localhost:5005" target="_blank">Purge Service</a></div>
    </div>

    <div class="card row">
      <div>
        <h2>Fetch Through This Edge</h2>
        <input id="key" value="index" placeholder="content key" />
        <button onclick="fetchKey()">GET /content/&lt;key&gt;</button>
      </div>
      <div>
        <h2>Cache Metadata</h2>
        <button onclick="showCache()">GET /cache</button>
      </div>
    </div>

    <div class="card row">
      <div>
        <h2>Purge One Key</h2>
        <input id="purgeKey" value="index" placeholder="key to purge" />
        <button onclick="purgeOne()">DELETE /purge/&lt;key&gt;</button>
      </div>
      <div>
        <h2>Purge All</h2>
        <button onclick="purgeAll()">DELETE /purge</button>
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

async function fetchKey() {
  const key = document.getElementById('key').value.trim();
  const r = await fetch(`/content/${encodeURIComponent(key)}`);
  out.textContent = pretty(await r.json());
}

async function showCache() {
  const r = await fetch('/cache');
  out.textContent = pretty(await r.json());
}

async function purgeOne() {
  const key = document.getElementById('purgeKey').value.trim();
  const r = await fetch(`/purge/${encodeURIComponent(key)}`, {method: 'DELETE'});
  out.textContent = pretty(await r.json());
}

async function purgeAll() {
  const r = await fetch('/purge', {method: 'DELETE'});
  out.textContent = pretty(await r.json());
}
</script>
</body>
</html>
        """,
        edge_name=EDGE_NAME,
        edge_region=EDGE_REGION,
    )


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": EDGE_NAME,
            "region": EDGE_REGION,
            "cached_keys": len(cache),
        }
    )


@app.get("/cache")
def cache_info():
    with cache_lock:
        keys = sorted(cache.keys())
    return jsonify({"edge": EDGE_NAME, "region": EDGE_REGION, "keys": keys, "count": len(keys)})


@app.get("/content/<key>")
def get_content(key: str):
    with cache_lock:
        cached = cache.get(key)

    if cached:
        time.sleep(CACHE_HIT_DELAY_SECONDS)
        return jsonify(
            {
                "key": key,
                "content": cached["content"],
                "version": cached["version"],
                "source": "edge_cache",
                "edge": EDGE_NAME,
                "region": EDGE_REGION,
                "cache_hit": True,
                "simulated_delay_seconds": CACHE_HIT_DELAY_SECONDS,
            }
        )

    try:
        response = requests.get(
            f"{ORIGIN_URL}/content/{key}",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return jsonify({"error": "origin_unreachable", "details": str(exc), "edge": EDGE_NAME}), 502

    if response.status_code != 200:
        return jsonify(response.json()), response.status_code

    payload = response.json()
    with cache_lock:
        cache[key] = {
            "content": payload["content"],
            "version": payload["version"],
            "cached_at": time.time(),
        }

    return jsonify(
        {
            "key": key,
            "content": payload["content"],
            "version": payload["version"],
            "source": "origin_via_edge",
            "edge": EDGE_NAME,
            "region": EDGE_REGION,
            "cache_hit": False,
            "simulated_delay_seconds": payload.get("simulated_delay_seconds", None),
        }
    )


@app.delete("/purge")
def purge_all():
    with cache_lock:
        purged_count = len(cache)
        cache.clear()
    return jsonify({"edge": EDGE_NAME, "region": EDGE_REGION, "purged_all": purged_count})


@app.delete("/purge/<key>")
def purge_key(key: str):
    with cache_lock:
        existed = key in cache
        if existed:
            del cache[key]
    return jsonify({"edge": EDGE_NAME, "region": EDGE_REGION, "key": key, "purged": existed})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
