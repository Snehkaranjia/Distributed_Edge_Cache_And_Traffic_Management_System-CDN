import logging
import os
import shutil
import threading
import uuid

import requests
from flask import Flask, Response, jsonify, render_template, request, send_from_directory, stream_with_context

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | traffic_manager | %(message)s",
)
logger = logging.getLogger("traffic_manager")

REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "3"))
MAX_IN_FLIGHT = int(os.getenv("MAX_IN_FLIGHT", "20"))
SERVICE_PORT = int(os.getenv("PORT", "5004"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_PUBLIC_DIR = os.path.join(BASE_DIR, "public")
ORIGIN_PUBLIC_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "origin", "public"))

_edge_env = {
    "us": os.getenv("EDGE_US_URL"),
    "eu": os.getenv("EDGE_EU_URL"),
    "asia": os.getenv("EDGE_ASIA_URL"),
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


def ensure_directory(path: str):
    os.makedirs(path, exist_ok=True)


def copy_origin_public_to_local_public() -> int:
    ensure_directory(LOCAL_PUBLIC_DIR)
    copied_files = 0

    if not os.path.isdir(ORIGIN_PUBLIC_DIR):
        logger.info("origin_public_missing path=%s", ORIGIN_PUBLIC_DIR)
        return copied_files

    for root, _, file_names in os.walk(ORIGIN_PUBLIC_DIR):
        relative_root = os.path.relpath(root, ORIGIN_PUBLIC_DIR)
        target_root = LOCAL_PUBLIC_DIR if relative_root == "." else os.path.join(LOCAL_PUBLIC_DIR, relative_root)
        ensure_directory(target_root)

        for file_name in file_names:
            source_path = os.path.join(root, file_name)
            target_path = os.path.join(target_root, file_name)
            shutil.copy2(source_path, target_path)
            copied_files += 1

    logger.info("traffic_manager_public_sync copied_files=%s", copied_files)
    return copied_files


copy_origin_public_to_local_public()


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


@app.get("/public")
def list_local_public_files():
    files = []
    for root, _, file_names in os.walk(LOCAL_PUBLIC_DIR):
        for file_name in file_names:
            full_path = os.path.join(root, file_name)
            relative_path = os.path.relpath(full_path, LOCAL_PUBLIC_DIR).replace("\\", "/")
            files.append(relative_path)

    files.sort()
    return jsonify({"count": len(files), "files": files})


@app.post("/public/sync")
def sync_local_public_files():
    copied_files = copy_origin_public_to_local_public()
    return jsonify({"copied_files": copied_files})


@app.get("/public/<path:filename>")
def serve_local_public_file(filename: str):
    requested_path = os.path.abspath(os.path.join(LOCAL_PUBLIC_DIR, filename))
    public_root = os.path.abspath(LOCAL_PUBLIC_DIR)
    if not requested_path.startswith(public_root + os.sep):
        return jsonify({"error": "invalid_path"}), 400

    if not os.path.exists(requested_path):
        return jsonify({"error": f"file '{filename}' not found"}), 404

    return send_from_directory(LOCAL_PUBLIC_DIR, filename, conditional=True)


@app.get("/stream/<path:filename>")
def stream_media(filename: str):
    client_region = request.args.get("region", "asia").lower()
    chosen_region, edge_url = pick_edge(client_region)
    if not edge_url:
        return jsonify({"error": "no_healthy_edges"}), 503

    upstream_headers = {}
    incoming_range = request.headers.get("Range")
    if incoming_range:
        upstream_headers["Range"] = incoming_range

    try:
        upstream_response = requests.get(
            f"{edge_url}/public/{filename}",
            headers=upstream_headers,
            stream=True,
            timeout=(REQUEST_TIMEOUT_SECONDS, 60),
        )
    except requests.RequestException as exc:
        logger.exception("stream_media_failed file=%s error=%s", filename, exc)
        return jsonify({"error": "edge_request_failed", "details": str(exc)}), 502

    if upstream_response.status_code not in (200, 206):
        details = {"error": "media_not_found", "status": upstream_response.status_code}
        try:
            details = upstream_response.json()
        except ValueError:
            pass
        finally:
            upstream_response.close()
        return jsonify(details), upstream_response.status_code

    passthrough_headers = {}
    for header_name in [
        "Content-Type",
        "Content-Length",
        "Content-Range",
        "Accept-Ranges",
        "Cache-Control",
        "ETag",
        "Last-Modified",
    ]:
        header_value = upstream_response.headers.get(header_name)
        if header_value:
            passthrough_headers[header_name] = header_value

    def generate_chunks():
        try:
            for chunk in upstream_response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream_response.close()

    response = Response(
        stream_with_context(generate_chunks()),
        status=upstream_response.status_code,
        headers=passthrough_headers,
        direct_passthrough=True,
    )
    response.headers["X-Routed-Edge-Region"] = chosen_region
    response.headers["X-Routed-Edge-Url"] = edge_url
    response.headers["X-Edge-Cache"] = upstream_response.headers.get("X-Edge-Cache", "unknown")
    response.headers["X-Edge-Name"] = upstream_response.headers.get("X-Edge-Name", "unknown")
    return response


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=SERVICE_PORT, debug=False)
