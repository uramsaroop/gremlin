"""
Gremlin — chaos controller.

Step 1: the smallest possible FastAPI app, just to prove the server runs.
"""

import asyncio
import random
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

app = FastAPI(title="Gremlin")


# ---------------------------------------------------------------------------
# In-memory chaos state. Lives in this one process's memory only —
# resets on restart, and would NOT be shared across replicas if we ever
# scaled this beyond 1 pod. That's intentional: Gremlin stays a singleton.
# ---------------------------------------------------------------------------
class ChaosState:
    def __init__(self):
        self.latency_enabled = False
        self.latency_min_ms = 0
        self.latency_max_ms = 0

        self.error_enabled = False
        self.error_rate = 0.0


state = ChaosState()

CHAOS_LATENCY_ENABLED = Gauge("gremlin_chaos_latency_enabled", "1 if latency injection is on")
CHAOS_ERROR_ENABLED = Gauge("gremlin_chaos_error_enabled", "1 if error injection is on")
CHAOS_ERROR_RATE = Gauge("gremlin_chaos_error_rate", "Configured error injection rate")


class LatencyConfig(BaseModel):
    enabled: bool
    min_ms: int = 0
    max_ms: int = 0


class ErrorConfig(BaseModel):
    enabled: bool
    rate: float = 0.0  # 0.0 - 1.0


@app.get("/health")
def health():
    """Always honest — never affected by chaos. This is what a liveness
    probe should check: is the process alive, not is business logic healthy."""
    return {"status": "ok"}


@app.get("/affected")
async def affected():
    """The endpoint Beacon actually checks, like any other monitored target.
    Its behavior reflects whatever chaos is currently configured."""
    if state.latency_enabled:
        delay_ms = random.uniform(
            state.latency_min_ms, max(state.latency_min_ms, state.latency_max_ms)
        )
        await asyncio.sleep(delay_ms / 1000)

    if state.error_enabled and random.random() < state.error_rate:
        raise HTTPException(status_code=500, detail="Gremlin injected an error")

    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/chaos/latency")
def set_latency(cfg: LatencyConfig):
    state.latency_enabled = cfg.enabled
    state.latency_min_ms = cfg.min_ms
    state.latency_max_ms = cfg.max_ms
    CHAOS_LATENCY_ENABLED.set(1 if cfg.enabled else 0)
    return {"latency": cfg.dict()}


@app.post("/chaos/errors")
def set_errors(cfg: ErrorConfig):
    if not (0.0 <= cfg.rate <= 1.0):
        raise HTTPException(status_code=400, detail="rate must be between 0.0 and 1.0")
    state.error_enabled = cfg.enabled
    state.error_rate = cfg.rate
    CHAOS_ERROR_ENABLED.set(1 if cfg.enabled else 0)
    CHAOS_ERROR_RATE.set(cfg.rate)
    return {"errors": cfg.dict()}


@app.post("/chaos/reset")
def reset_all():
    state.latency_enabled = False
    state.error_enabled = False
    state.error_rate = 0.0
    CHAOS_LATENCY_ENABLED.set(0)
    CHAOS_ERROR_ENABLED.set(0)
    CHAOS_ERROR_RATE.set(0)
    return {"status": "all chaos reset"}


@app.get("/chaos/status")
def chaos_status():
    return {
        "latency": {
            "enabled": state.latency_enabled,
            "min_ms": state.latency_min_ms,
            "max_ms": state.latency_max_ms,
        },
        "errors": {
            "enabled": state.error_enabled,
            "rate": state.error_rate,
        },
    }
