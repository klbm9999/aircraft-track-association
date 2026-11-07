"""HTTP service: the association API plus a small web interface.

Endpoints
    GET  /health                         service status, model version and configuration hash
    POST /tracklets/associate            run every approach on posted records (see AssociateRequest)
    GET  /scenarios                      packaged scenarios available to the web interface
    GET  /scenarios/{partition}/{name}   raw records of one packaged scenario, plus its labels
    GET  /                               the web interface

/tracklets/associate never reads labels. /scenarios/... returns labels separately so the
interface can show whether a decision matches them; they are never posted back as inputs.

Every error is JSON: {"error": {"code": str, "message": str}} with a matching status code
(404 unknown scenario, 422 invalid request or input, 500 unexpected failure).
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException

from .io import InputError, read_jsonl
from .pipeline import APPROACHES, MODEL_VERSION, analyze, config, digest, read_snapshot

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
DATA_DIR = Path(
    os.environ.get("ATA_DATA_DIR", Path(__file__).parents[2] / "candidate-packet" / "data")
)
PARTITIONS = ("train", "validation", "test", "staged_demo")
NAME = re.compile(r"^[A-Za-z0-9_]+$")
MAX_RECORDS = 200_000  # far above any packaged scenario; bounds a single request's work

app = FastAPI(title="Aircraft track association", version=MODEL_VERSION)


class AssociateRequest(BaseModel):
    """Raw records exactly as they appear in the JSONL files; validated by io.py."""

    snapshot_id: str | None = None
    receipt_cutoff: str | None = Field(
        None, description="ISO 8601 UTC; records received after it are left out"
    )
    observations: list[Any] = Field(max_length=MAX_RECORDS)
    supporting: list[Any] = Field(default_factory=list, max_length=MAX_RECORDS)


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(InputError)
async def input_error(_: Request, exc: InputError):
    return error(422, "invalid_input", str(exc))


@app.exception_handler(RequestValidationError)
async def request_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", ()))
    return error(422, "invalid_request", f"{where}: {first.get('msg', 'invalid request')}")


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    code = "not_found" if exc.status_code == 404 else "http_error"
    return error(exc.status_code, code, str(exc.detail))


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception):
    log.exception("unhandled error")
    return error(500, "internal_error", "unexpected server error")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "approaches": list(APPROACHES),
        "config_sha256": digest(config()),
        "scenarios_available": DATA_DIR.is_dir(),
    }


@app.post("/tracklets/associate")
def associate(req: AssociateRequest) -> dict:
    return analyze(req.observations, req.supporting, req.snapshot_id, req.receipt_cutoff)


def scenario_dir(partition: str, name: str) -> Path:
    d = DATA_DIR / partition / name
    if partition not in PARTITIONS or not NAME.match(name) or not d.is_dir():
        raise HTTPException(404, f"unknown scenario {partition}/{name}")
    return d


@app.get("/scenarios")
def scenarios() -> list[dict]:
    out = []
    for partition in PARTITIONS:
        for d in sorted((DATA_DIR / partition).glob("*/snapshot.json")):
            meta = json.loads(d.read_text())
            out.append(
                {
                    "partition": partition,
                    "name": d.parent.name,
                    "snapshot_id": meta["snapshot_id"],
                    "receipt_cutoff": meta.get("receipt_cutoff"),
                    "tracklet_count": meta["tracklet_count"],
                }
            )
    return out


@app.get("/scenarios/{partition}/{name}")
def scenario(partition: str, name: str) -> dict:
    d = scenario_dir(partition, name)
    return {**read_snapshot(d), "labels": read_jsonl(d / "labels.jsonl")}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
