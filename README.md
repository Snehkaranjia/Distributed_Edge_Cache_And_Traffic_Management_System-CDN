# Distributed Edge Cache & Traffic Management (CDN Simulation)

This project simulates a mini CDN with 6 services:

1. `origin` - source of truth for content
2. `edge_us` - US edge cache
3. `edge_eu` - Europe edge cache
4. `edge_asia` - Asia edge cache
5. `traffic_manager` - routes clients to nearest healthy edge
6. `purge_service` - invalidates stale edge caches

## Architecture

Client -> Traffic Manager -> Edge Node -> Origin (on cache miss)

- Cache miss path simulates slower origin fetch (`2s`)
- Cache hit path simulates fast edge response (`0.1s`)
- If one edge is down, traffic manager fails over to next healthy edge

## Run on Single Device (Docker Compose)

Prerequisites:
- Docker Desktop installed
- `docker compose version` works

### 1) Start all services

```bash
docker compose up --build -d
```

### 2) Open browser dashboards

- Origin UI: `http://localhost:5000`
- Edge US UI: `http://localhost:5001`
- Edge EU UI: `http://localhost:5002`
- Edge Asia UI: `http://localhost:5003`
- Traffic Manager UI: `http://localhost:5004`
- Purge Service UI: `http://localhost:5005`

All UIs are interactive and include forms/buttons for service operations.

### 3) Demo flow from UI

1. Open Traffic Manager UI (`5004`) and fetch key `index` with region `asia`.
2. Fetch same key again from Traffic Manager UI and compare response (`cache_hit` becomes true in edge data).
3. Open Origin UI (`5000`) and update `index` content.
4. Open Purge Service UI (`5005`) and purge key `index`.
5. Fetch `index` again from Traffic Manager UI to verify fresh content is pulled.

### 4) Optional CLI checks

```bash
docker compose ps
curl http://localhost:5004/health
curl http://localhost:5004/edges
```

### 5) Failover demo

Stop one edge:

```bash
docker stop edge_asia
```

Then fetch `region=asia` from Traffic Manager UI (`5004`). It should route to another healthy edge.

Restart:

```bash
docker start edge_asia
```

### 6) Stop everything

```bash
docker compose down
```

## Run on Multiple Devices (5 Laptops)

Use one laptop as the **control node** (runs `origin`, `traffic_manager`, `purge_service`) and three laptops as edge nodes (`edge_us`, `edge_eu`, `edge_asia`). Fifth laptop can be client/load generator.

### Network setup

1. Connect all laptops to same LAN.
2. Find control node IP, example: `192.168.1.10`.
3. Open firewall for ports `5000-5005` on relevant machines.

### Deploy services

Control node:
- Run `origin` on port `5000`
- Run `traffic_manager` on port `5004`
- Run `purge_service` on port `5005`

Edge laptops:
- Run edge app with env vars:
  - `EDGE_NAME=edge_us` / `edge_eu` / `edge_asia`
  - `EDGE_REGION=us` / `eu` / `asia`
  - `ORIGIN_URL=http://<CONTROL_IP>:5000`

Control node traffic manager must point to remote edges:
- `EDGE_US_URL=http://<EDGE_US_IP>:5000`
- `EDGE_EU_URL=http://<EDGE_EU_IP>:5000`
- `EDGE_ASIA_URL=http://<EDGE_ASIA_IP>:5000`

Purge service uses same edge URLs.

### Example (without compose, per device)

```bash
pip install -r requirements.txt
python origin/app.py
python edge/app.py
python traffic_manager/app.py
python purge_service/app.py
```

Use environment variables before each process according to role.

## API Quick Reference

- Origin
  - `GET /content`
  - `GET /content/<key>`
  - `PUT /content/<key>`
- Edge
  - `GET /content/<key>`
  - `GET /cache`
  - `DELETE /purge`
  - `DELETE /purge/<key>`
- Traffic Manager
  - `GET /fetch/<key>?region=asia|eu|us`
  - `GET /edges`
- Purge Service
  - `POST /purge` body `{}` or `{"key":"index"}`

## Suggested Milestones (for your team)

1. Baseline deployment and first successful fetch.
2. Latency comparison chart (miss vs hit).
3. Failover demo with one edge killed.
4. Purge consistency demo after origin update.
5. Load test report and observations.
