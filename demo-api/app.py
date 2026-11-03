"""A small web service that exists to be monitored.

It exposes /metrics for Prometheus (request count and latency by route and status) and a few routes
that behave badly on purpose, so every alert in this repository can be triggered and seen working.
"""
import asyncio
import random
import time

from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

app = FastAPI(title="demo-api")

REQUESTS = Counter("http_requests_total", "Requests by method, route and status", ["method", "path", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request duration in seconds by route",
    ["path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)


@app.middleware("http")
async def measure(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # Use the route template, not the raw address, so the number of label values stays small.
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        if path != "/metrics":
            REQUESTS.labels(request.method, path, str(status)).inc()
            LATENCY.labels(path).observe(time.perf_counter() - start)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return {"service": "demo-api", "routes": ["/work", "/slow", "/error", "/flaky", "/health", "/metrics"]}


@app.get("/work")
async def work():
    await asyncio.sleep(random.uniform(0.01, 0.12))
    return {"result": "done"}


@app.get("/slow")
async def slow():
    """Always slow: makes the latency alert fire."""
    await asyncio.sleep(0.8)
    return {"result": "slow but fine"}


@app.get("/error")
async def error():
    """Always fails: makes the error-rate alert fire."""
    raise HTTPException(status_code=500, detail="this route fails on purpose")


@app.get("/flaky")
async def flaky():
    """Fails about one request in five."""
    if random.random() < 0.2:
        raise HTTPException(status_code=500, detail="unlucky")
    return {"result": "lucky"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
