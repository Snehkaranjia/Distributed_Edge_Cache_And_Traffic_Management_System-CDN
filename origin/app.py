import logging
import os
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | origin | %(message)s",
)
logger = logging.getLogger("origin")

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


@app.before_request
def log_request_start():
    logger.info(
        "request_start method=%s path=%s remote=%s",
        request.method,
        request.path,
        request.remote_addr,
    )


@app.after_request
def log_request_end(response):
    logger.info(
        "request_end method=%s path=%s status=%s",
        request.method,
        request.path,
        response.status_code,
    )
    return response


@app.get("/")
def root():
    return jsonify(
        {
            "service": "origin",
            "message": "Origin API is running. Use /content or /content/<key>.",
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "origin"})


@app.get("/content")
def list_content():
    logger.info("list_content keys=%s", len(content_store))
    return jsonify({"keys": sorted(content_store.keys()), "count": len(content_store)})


@app.get("/content/<key>")
def get_content(key: str):
    item = content_store.get(key)
    if not item:
        logger.warning("content_not_found key=%s", key)
        return jsonify({"error": f"key '{key}' not found"}), 404

    logger.info("origin_fetch_start key=%s delay_seconds=%.2f", key, ORIGIN_FETCH_DELAY_SECONDS)
    time.sleep(ORIGIN_FETCH_DELAY_SECONDS)
    logger.info("origin_fetch_end key=%s version=%s", key, item["version"])

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
        logger.warning("update_rejected key=%s reason=invalid_content", key)
        return jsonify({"error": "Provide non-empty JSON field: content"}), 400

    version = datetime.now(timezone.utc).isoformat()
    content_store[key] = {"content": new_content, "version": version}
    logger.info(
        "content_updated key=%s version=%s content_length=%s",
        key,
        version,
        len(new_content),
    )

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
