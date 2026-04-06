import os
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

ORIGIN_FETCH_DELAY_SECONDS = float(os.getenv("ORIGIN_FETCH_DELAY_SECONDS", "2.0"))

content_store = {
    "index": {
        "content": "Welcome to Distributed CDN demo.",
        "version": datetime.now(timezone.utc).isoformat(),
    },
    "video_intro": {
        "content": "Sample video metadata payload.",
        "version": datetime.now(timezone.utc).isoformat(),
    },
    "news": {
        "content": "Breaking: Edge caching simulation is live.",
        "version": datetime.now(timezone.utc).isoformat(),
    },
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
  <title>Origin Server UI</title>
  <style>
    :root { --bg:#f4f7fb; --card:#ffffff; --txt:#102a43; --accent:#0f766e; --muted:#486581; }
    body { margin:0; font-family:Segoe UI,Tahoma,sans-serif; background:var(--bg); color:var(--txt); }
    .wrap { max-width:960px; margin:24px auto; padding:0 16px; }
    .card { background:var(--card); border-radius:12px; padding:16px; margin-bottom:16px; box-shadow:0 8px 24px rgba(16,42,67,.08); }
    h1, h2 { margin:0 0 10px; }
    .muted { color:var(--muted); }
    input, textarea, button { width:100%; margin-top:8px; padding:10px; border-radius:8px; border:1px solid #cbd2d9; box-sizing:border-box; }
    button { background:var(--accent); color:#fff; border:none; cursor:pointer; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
    pre { background:#0b1f33; color:#d9e2ec; padding:12px; border-radius:8px; overflow:auto; }
    a { color:var(--accent); }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Origin Server</h1>
      <div class="muted">Port 5000 | Source of truth for all content.</div>
      <div style="margin-top:8px;">Other UIs: <a href="http://localhost:5001" target="_blank">Edge US</a> | <a href="http://localhost:5002" target="_blank">Edge EU</a> | <a href="http://localhost:5003" target="_blank">Edge Asia</a> | <a href="http://localhost:5004" target="_blank">Traffic Manager</a> | <a href="http://localhost:5005" target="_blank">Purge Service</a></div>
    </div>

    <div class="card row">
      <div>
        <h2>Fetch Content</h2>
        <input id="getKey" value="index" placeholder="content key" />
        <button onclick="getContent()">GET /content/&lt;key&gt;</button>
      </div>
      <div>
        <h2>List Keys</h2>
        <button onclick="listKeys()">GET /content</button>
      </div>
    </div>

    <div class="card">
      <h2>Update Content</h2>
      <input id="putKey" value="index" placeholder="content key" />
      <textarea id="putVal" rows="4">Updated content from Origin UI</textarea>
      <button onclick="updateContent()">PUT /content/&lt;key&gt;</button>
      <div class="muted" style="margin-top:8px;">After update, purge edge caches using Purge Service UI.</div>
    </div>

    <div class="card">
      <h2>Result</h2>
      <pre id="out">Ready</pre>
    </div>
  </div>

<script>
const out = document.getElementById('out');
const pretty = (obj) => JSON.stringify(obj, null, 2);

async function listKeys() {
  const r = await fetch('/content');
  out.textContent = pretty(await r.json());
}

async function getContent() {
  const key = document.getElementById('getKey').value.trim();
  const r = await fetch(`/content/${encodeURIComponent(key)}`);
  out.textContent = pretty(await r.json());
}

async function updateContent() {
  const key = document.getElementById('putKey').value.trim();
  const content = document.getElementById('putVal').value;
  const r = await fetch(`/content/${encodeURIComponent(key)}`, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({content})
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
    return jsonify({"status": "ok", "service": "origin"})


@app.get("/content")
def list_content():
    return jsonify({"keys": sorted(content_store.keys()), "count": len(content_store)})


@app.get("/content/<key>")
def get_content(key: str):
    item = content_store.get(key)
    if not item:
        return jsonify({"error": f"key '{key}' not found"}), 404

    time.sleep(ORIGIN_FETCH_DELAY_SECONDS)

    return jsonify(
        {
            "key": key,
            "content": item["content"],
            "version": item["version"],
            "source": "origin",
            "simulated_delay_seconds": ORIGIN_FETCH_DELAY_SECONDS,
        }
    )


@app.put("/content/<key>")
def update_content(key: str):
    payload = request.get_json(silent=True) or {}
    new_content = payload.get("content")
    if not isinstance(new_content, str) or not new_content.strip():
        return jsonify({"error": "Provide non-empty JSON field: content"}), 400

    version = datetime.now(timezone.utc).isoformat()
    content_store[key] = {"content": new_content, "version": version}

    return jsonify(
        {
            "message": "content updated",
            "key": key,
            "version": version,
            "note": "Call purge service to invalidate edge caches.",
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
