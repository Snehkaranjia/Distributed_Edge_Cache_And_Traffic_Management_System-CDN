import logging
import os
import threading
import uuid

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | traffic_manager | %(message)s",
)
logger = logging.getLogger("traffic_manager")

REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "3"))
MAX_IN_FLIGHT = int(os.getenv("MAX_IN_FLIGHT", "20"))
SERVICE_PORT = int(os.getenv("PORT", "5004"))

_edge_env = {
    "us": os.getenv("EDGE_US_URL"),
    "eu": os.getenv("EDGE_EU_URL"),
    "asia": os.getenv("EDGE_ASIA_URL", "http://10.159.173.200:5000"),
}

# If any EDGE_*_URL is explicitly provided, only use the provided non-empty values.
# Otherwise, fall back to compose-friendly service names.
if any(value is not None for value in _edge_env.values()):
    EDGE_MAP = {region: value.strip() for region, value in _edge_env.items() if value and value.strip()}
else:
    EDGE_MAP = {
        "us": "http://edge_us:5000",
        "eu": "http://edge_eu:5000",
        "asia": "http://edge_asia:5000",
    }

logger.info("traffic_manager_config edge_map=%s port=%s", EDGE_MAP, SERVICE_PORT)

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
    preferred_order = FALLBACK_ORDER.get(client_region, ["us", "eu", "asia"])
    order = [region for region in preferred_order if region in EDGE_MAP]

    for region in EDGE_MAP:
        if region not in order:
            order.append(region)

    for region in order:
        url = EDGE_MAP[region]
        if is_edge_healthy(url):
            return region, url
    return None, None


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
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "traffic_manager", "in_flight": in_flight_requests})


@app.get("/edges")
def edges():
    if not EDGE_MAP:
        return jsonify({"error": "no_edges_configured"}), 500

    result = {}
    for region, url in EDGE_MAP.items():
        healthy = is_edge_healthy(url)
        result[region] = {"url": url, "healthy": healthy}
        logger.info("edge_health_check region=%s url=%s healthy=%s", region, url, healthy)
    return jsonify(result)


@app.get("/fetch/<key>")
def fetch(key: str):
    global in_flight_requests

    client_region = request.args.get("region", "asia").lower()

    with in_flight_lock:
        if in_flight_requests >= MAX_IN_FLIGHT:
            logger.warning(
                "load_shed key=%s client_region=%s in_flight=%s",
                key,
                client_region,
                in_flight_requests,
            )
            return jsonify({"error": "load_shed", "message": "Too many concurrent requests"}), 503
        in_flight_requests += 1
        logger.info("in_flight_increment count=%s", in_flight_requests)

    try:
        chosen_region, edge_url = pick_edge(client_region)
        if not edge_url:
            logger.error("no_healthy_edges key=%s client_region=%s", key, client_region)
            return jsonify({"error": "no_healthy_edges"}), 503

        logger.info(
            "route_selected key=%s client_region=%s routed_region=%s edge_url=%s",
            key,
            client_region,
            chosen_region,
            edge_url,
        )

        request_id = str(uuid.uuid4())
        response = requests.get(f"{edge_url}/content/{key}", timeout=REQUEST_TIMEOUT_SECONDS + 5)
        payload = response.json()
        expected_friend_edge = EDGE_MAP.get("asia")
        matched_expected_edge = (edge_url == expected_friend_edge) if expected_friend_edge else None

        logger.info(
            "fetch_proof request_id=%s selected_edge=%s expected_friend_edge=%s matched=%s edge_name=%s edge_host=%s",
            request_id,
            edge_url,
            expected_friend_edge,
            matched_expected_edge,
            payload.get("edge"),
            payload.get("edge_hostname"),
        )
        logger.info(
            "edge_response key=%s routed_region=%s status=%s cache_hit=%s",
            key,
            chosen_region,
            response.status_code,
            payload.get("cache_hit"),
        )

        return (
            jsonify(
                {
                    "traffic_manager": "ok",
                    "request_id": request_id,
                    "client_region": client_region,
                    "routed_region": chosen_region,
                    "edge_url": edge_url,
                    "upstream_status": response.status_code,
                    "verification": {
                        "expected_friend_edge_url": expected_friend_edge,
                        "selected_edge_url": edge_url,
                        "selected_edge_matches_friend_edge": matched_expected_edge,
                        "edge_reported_name": payload.get("edge"),
                        "edge_reported_hostname": payload.get("edge_hostname"),
                    },
                    "data": payload,
                }
            ),
            response.status_code,
        )
    except requests.RequestException as exc:
        logger.exception("edge_request_failed key=%s error=%s", key, exc)
        return jsonify({"error": "edge_request_failed", "details": str(exc)}), 502
    finally:
        with in_flight_lock:
            in_flight_requests -= 1
            logger.info("in_flight_decrement count=%s", in_flight_requests)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=SERVICE_PORT, debug=False)
