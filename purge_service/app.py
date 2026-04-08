import logging
import os

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | purge_service | %(message)s",
)
logger = logging.getLogger("purge_service")

REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "3"))

_edge_env = {
    "us": os.getenv("EDGE_US_URL"),
    "eu": os.getenv("EDGE_EU_URL"),
    "asia": os.getenv("EDGE_ASIA_URL"),
}

# If any EDGE_*_URL is explicitly provided, only use the provided non-empty values.
# Otherwise, fall back to compose-friendly service names.
if any(value is not None for value in _edge_env.values()):
    EDGES = {region: value.strip() for region, value in _edge_env.items() if value and value.strip()}
else:
    EDGES = {
        "us": "http://edge_us:5000",
        "eu": "http://edge_eu:5000",
        "asia": "http://edge_asia:5000",
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
    return jsonify({"service": "purge_service", "message": "Purge API is running. Use POST /purge."})


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "purge_service"})


@app.post("/purge")
def purge():
    if not EDGES:
        return jsonify({"error": "no_edges_configured"}), 500

    payload = request.get_json(silent=True) or {}
    key = payload.get("key")
    logger.info("purge_broadcast_start key=%s", key)

    results = []
    for region, edge_url in EDGES.items():
        endpoint = f"{edge_url}/purge/{key}" if key else f"{edge_url}/purge"
        try:
            response = requests.delete(endpoint, timeout=REQUEST_TIMEOUT_SECONDS)
            logger.info(
                "purge_edge_result region=%s endpoint=%s status=%s",
                region,
                endpoint,
                response.status_code,
            )
            results.append(
                {
                    "region": region,
                    "edge_url": edge_url,
                    "status": response.status_code,
                    "response": response.json(),
                }
            )
        except requests.RequestException as exc:
            logger.exception("purge_edge_failed region=%s endpoint=%s error=%s", region, endpoint, exc)
            results.append(
                {
                    "region": region,
                    "edge_url": edge_url,
                    "status": 502,
                    "response": {"error": str(exc)},
                }
            )

    logger.info("purge_broadcast_end key=%s total_edges=%s", key, len(EDGES))
    return jsonify(
        {
            "message": "purge broadcast completed",
            "key": key,
            "results": results,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
