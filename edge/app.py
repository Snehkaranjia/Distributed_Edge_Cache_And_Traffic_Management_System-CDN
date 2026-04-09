import logging
import os
import shutil
import socket
import threading
import time

import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | edge | %(message)s",
)
logger = logging.getLogger("edge")

EDGE_NAME = os.getenv("EDGE_NAME", "edge_friend")
EDGE_REGION = os.getenv("EDGE_REGION", "asia")
ORIGIN_URL = os.getenv("ORIGIN_URL", "http://127.0.0.1:5000")
ORIGIN_URLS = [u.strip() for u in os.getenv("ORIGIN_URLS", "").split(",") if u.strip()]
if not ORIGIN_URLS:
    ORIGIN_URLS = [ORIGIN_URL]
EDGE_HOSTNAME = socket.gethostname()
CACHE_HIT_DELAY_SECONDS = float(os.getenv("CACHE_HIT_DELAY_SECONDS", "0.1"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "5"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "600"))
SERVICE_PORT = int(os.getenv("PORT", "5000"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_PUBLIC_DIR = os.path.join(BASE_DIR, "public")
ORIGIN_PUBLIC_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "origin", "public"))

logger.info(
    "edge_config edge=%s region=%s host=%s origins=%s port=%s",
    EDGE_NAME,
    EDGE_REGION,
    EDGE_HOSTNAME,
    ORIGIN_URLS,
    SERVICE_PORT,
)

cache = {}
cache_lock = threading.Lock()


def ensure_directory(path: str):
    os.makedirs(path, exist_ok=True)


def copy_origin_public_to_edge_public() -> int:
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

    logger.info("public_sync_complete edge=%s copied_files=%s", EDGE_NAME, copied_files)
    return copied_files


def fetch_public_file_from_origin(filename: str) -> bool:
    for origin_url in ORIGIN_URLS:
        file_url = f"{origin_url}/public/{filename}"
        logger.info("origin_file_attempt file=%s edge=%s origin=%s", filename, EDGE_NAME, origin_url)
        try:
            response = requests.get(file_url, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            logger.warning("origin_file_attempt_failed file=%s edge=%s origin=%s error=%s", filename, EDGE_NAME, origin_url, exc)
            continue

        if response.status_code != 200:
            logger.warning("origin_file_unavailable file=%s edge=%s origin=%s status=%s", filename, EDGE_NAME, origin_url, response.status_code)
            continue

        target_file_path = os.path.abspath(os.path.join(LOCAL_PUBLIC_DIR, filename))
        public_root = os.path.abspath(LOCAL_PUBLIC_DIR)
        if not target_file_path.startswith(public_root + os.sep):
            logger.warning("invalid_file_path file=%s edge=%s", filename, EDGE_NAME)
            return False

        ensure_directory(os.path.dirname(target_file_path))
        with open(target_file_path, "wb") as file_pointer:
            file_pointer.write(response.content)

        logger.info("origin_file_cached file=%s edge=%s origin=%s", filename, EDGE_NAME, origin_url)
        return True

    return False


copy_origin_public_to_edge_public()


def fetch_from_origin(key: str):
    for origin_url in ORIGIN_URLS:
        logger.info("origin_attempt key=%s edge=%s origin=%s", key, EDGE_NAME, origin_url)
        try:
            response = requests.get(
                f"{origin_url}/content/{key}",
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            return origin_url, response
        except requests.RequestException as exc:
            logger.warning("origin_attempt_failed key=%s edge=%s origin=%s error=%s", key, EDGE_NAME, origin_url, exc)

    return None, None


@app.before_request
def log_request_start():
    logger.info(
        "request_start method=%s path=%s edge=%s region=%s remote=%s",
        request.method,
        request.path,
        EDGE_NAME,
        EDGE_REGION,
        request.remote_addr,
    )


@app.after_request
def log_request_end(response):
    logger.info(
        "request_end method=%s path=%s edge=%s status=%s",
        request.method,
        request.path,
        EDGE_NAME,
        response.status_code,
    )
    return response


@app.get("/")
def root():
    return jsonify(
        {
            "service": EDGE_NAME,
            "region": EDGE_REGION,
            "message": "Edge API is running. Use /content/<key>.",
        }
    )


@app.get("/health")
def health():
    with cache_lock:
        cached_keys = len(cache)
    return jsonify(
        {
            "status": "ok",
            "service": EDGE_NAME,
            "region": EDGE_REGION,
            "edge_hostname": EDGE_HOSTNAME,
            "origin_urls": ORIGIN_URLS,
            "cached_keys": cached_keys,
            "cache_ttl_seconds": CACHE_TTL_SECONDS,
        }
    )


@app.get("/cache")
def cache_info():
    with cache_lock:
        keys = sorted(cache.keys())
    logger.info("cache_info edge=%s key_count=%s", EDGE_NAME, len(keys))
    return jsonify({"edge": EDGE_NAME, "region": EDGE_REGION, "keys": keys, "count": len(keys)})


@app.get("/public")
def list_public_files():
    files = []
    for root, _, file_names in os.walk(LOCAL_PUBLIC_DIR):
        for file_name in file_names:
            full_path = os.path.join(root, file_name)
            relative_path = os.path.relpath(full_path, LOCAL_PUBLIC_DIR).replace("\\", "/")
            files.append(relative_path)

    files.sort()
    logger.info("public_files_list edge=%s count=%s", EDGE_NAME, len(files))
    return jsonify({"edge": EDGE_NAME, "region": EDGE_REGION, "count": len(files), "files": files})


@app.post("/public/sync")
def sync_public_files():
    copied_files = copy_origin_public_to_edge_public()
    return jsonify({"edge": EDGE_NAME, "region": EDGE_REGION, "copied_files": copied_files})


@app.get("/public/<path:filename>")
def serve_public_file(filename: str):
    requested_path = os.path.abspath(os.path.join(LOCAL_PUBLIC_DIR, filename))
    public_root = os.path.abspath(LOCAL_PUBLIC_DIR)

    if not requested_path.startswith(public_root + os.sep):
        return jsonify({"error": "invalid_path"}), 400

    if os.path.exists(requested_path):
        response = send_from_directory(LOCAL_PUBLIC_DIR, filename, conditional=True)
        response.headers["X-Edge-Cache"] = "HIT"
        response.headers["X-Edge-Name"] = EDGE_NAME
        return response

    if fetch_public_file_from_origin(filename):
        response = send_from_directory(LOCAL_PUBLIC_DIR, filename, conditional=True)
        response.headers["X-Edge-Cache"] = "MISS"
        response.headers["X-Edge-Name"] = EDGE_NAME
        return response

    return jsonify({"error": f"file '{filename}' not found"}), 404


@app.get("/content/<key>")
def get_content(key: str):
    now = time.time()
    with cache_lock:
        cached = cache.get(key)

    if cached:
        age_seconds = now - cached["cached_at"]
        if age_seconds < CACHE_TTL_SECONDS:
            logger.info(
                "served_request key=%s source=edge_cache edge=%s host=%s client=%s",
                key,
                EDGE_NAME,
                EDGE_HOSTNAME,
                request.remote_addr,
            )
            logger.info(
                "cache_hit key=%s edge=%s age_seconds=%.2f ttl_seconds=%s",
                key,
                EDGE_NAME,
                age_seconds,
                CACHE_TTL_SECONDS,
            )
            time.sleep(CACHE_HIT_DELAY_SECONDS)
            return jsonify(
                {
                    "key": key,
                    "content": cached["content"],
                    "version": cached["version"],
                    "source": "edge_cache",
                    "edge": EDGE_NAME,
                    "region": EDGE_REGION,
                    "edge_hostname": EDGE_HOSTNAME,
                    "cache_hit": True,
                    "cache_age_seconds": round(age_seconds, 3),
                    "cache_ttl_seconds": CACHE_TTL_SECONDS,
                    "simulated_delay_seconds": CACHE_HIT_DELAY_SECONDS,
                }
            )

        logger.info(
            "cache_expired key=%s edge=%s age_seconds=%.2f ttl_seconds=%s",
            key,
            EDGE_NAME,
            age_seconds,
            CACHE_TTL_SECONDS,
        )
        with cache_lock:
            cache.pop(key, None)

    logger.info("cache_miss key=%s edge=%s origins=%s", key, EDGE_NAME, ORIGIN_URLS)

    used_origin, response = fetch_from_origin(key)
    if response is None:
        logger.error("all_origins_unreachable key=%s edge=%s", key, EDGE_NAME)
        return jsonify({"error": "origin_unreachable", "details": "all configured origins failed", "edge": EDGE_NAME}), 502

    if response.status_code != 200:
        logger.warning("origin_error key=%s edge=%s origin=%s status=%s", key, EDGE_NAME, used_origin, response.status_code)
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": "origin_bad_response", "status": response.status_code}
        return jsonify(payload), response.status_code

    payload = response.json()
    cached_at = time.time()
    with cache_lock:
        cache[key] = {
            "content": payload["content"],
            "version": payload["version"],
            "cached_at": cached_at,
        }

    logger.info("cache_store key=%s edge=%s version=%s", key, EDGE_NAME, payload["version"])
    logger.info(
        "served_request key=%s source=origin_via_edge edge=%s host=%s client=%s origin=%s",
        key,
        EDGE_NAME,
        EDGE_HOSTNAME,
        request.remote_addr,
        used_origin,
    )

    return jsonify(
        {
            "key": key,
            "content": payload["content"],
            "version": payload["version"],
            "source": "origin_via_edge",
            "edge": EDGE_NAME,
            "region": EDGE_REGION,
            "edge_hostname": EDGE_HOSTNAME,
            "cache_hit": False,
            "cache_ttl_seconds": CACHE_TTL_SECONDS,
            "origin_used": used_origin,
            "simulated_delay_seconds": payload.get("simulated_delay_seconds"),
        }
    )


@app.delete("/purge")
def purge_all():
    with cache_lock:
        purged_count = len(cache)
        cache.clear()
    logger.info("purge_all edge=%s purged_count=%s", EDGE_NAME, purged_count)
    return jsonify({"edge": EDGE_NAME, "region": EDGE_REGION, "purged_all": purged_count})


@app.delete("/purge/<key>")
def purge_key(key: str):
    with cache_lock:
        existed = key in cache
        if existed:
            del cache[key]
    logger.info("purge_key edge=%s key=%s purged=%s", EDGE_NAME, key, existed)
    return jsonify({"edge": EDGE_NAME, "region": EDGE_REGION, "key": key, "purged": existed})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=SERVICE_PORT, debug=False)
