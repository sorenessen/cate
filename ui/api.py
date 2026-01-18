# ui/api.py
from __future__ import annotations

import os
import json
import time
import uuid
import asyncio
import pathlib
import subprocess
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
# Where runs + artifacts live (server-side). You can point this at your existing logs/ folder.
DEFAULT_WORKSPACE = os.environ.get("CATE_UI_WORKSPACE", str(pathlib.Path.cwd() / "ui_runs"))

# If "cate" isn't on PATH, set CATE_BIN=/full/path/to/cate
CATE_BIN = os.environ.get("CATE_BIN", "cate")

app = FastAPI(title="CATE UI Bridge", version="0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later if you want
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# In-memory run registry (v1)
# -----------------------------------------------------------------------------
class RunInfo(BaseModel):
    run_id: str
    created_ts: float
    flow: str
    mode: str
    output_dir: str
    status: str  # starting/running/exited
    returncode: Optional[int] = None

RUNS: Dict[str, RunInfo] = {}
QUEUES: Dict[str, asyncio.Queue[str]] = {}
PROCS: Dict[str, subprocess.Popen] = {}

# -----------------------------------------------------------------------------
# Request model
# -----------------------------------------------------------------------------
class RunRequest(BaseModel):
    flows_file: str = "flows.toml"
    flow: str
    mode: str = "auth-pressure"
    concurrency: int = 5
    max_rps: float = 3.0
    stop_on_error_rate: float = 0.5
    vars: Dict[str, str] = {}   # {"username":"x","password":"y"}
    output_prefix: Optional[str] = None  # if provided, used as output prefix

def _ensure_workspace() -> pathlib.Path:
    p = pathlib.Path(DEFAULT_WORKSPACE).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def _safe_join(base: pathlib.Path, rel: str) -> pathlib.Path:
    # Prevent path traversal
    target = (base / rel).resolve()
    if base not in target.parents and base != target:
        raise HTTPException(status_code=400, detail="Invalid path.")
    return target

async def _pump_output(run_id: str, proc: subprocess.Popen) -> None:
    """
    Read process stdout line-by-line and push to SSE queue.
    """
    q = QUEUES[run_id]
    assert proc.stdout is not None

    # Mark running
    RUNS[run_id].status = "running"

    while True:
        line = proc.stdout.readline()
        if not line:
            break
        text = line.rstrip("\n")
        await q.put(text)

    rc = proc.wait()
    RUNS[run_id].status = "exited"
    RUNS[run_id].returncode = rc
    await q.put(f"[CATE_UI] process exited (code={rc})")

# -----------------------------------------------------------------------------
# API
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "workspace": str(_ensure_workspace()), "cate_bin": CATE_BIN}

@app.post("/run")
async def start_run(req: RunRequest):
    workspace = _ensure_workspace()

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    out_dir = workspace / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Choose output prefix
    # For http-flow, CATE typically writes <prefix>.(jsonl|summary etc) depending on your implementation.
    # We'll use an output prefix inside the run folder.
    output_prefix = req.output_prefix or str(out_dir / "run")

    # Build CLI args (adjust to your actual CLI flags if needed)
    cmd = [
        CATE_BIN,
        "http-flow",
        "--flows-file", req.flows_file,
        "--flow", req.flow,
        "--mode", req.mode,
        "--max-rps", str(req.max_rps),
        "--output", output_prefix,
    ]


    for k, v in (req.vars or {}).items():
        cmd += ["--var", f"{k}={v}"]

    # Spawn process
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(pathlib.Path.cwd()),
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"Could not run '{CATE_BIN}'. Set CATE_BIN env var.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start CATE: {e!r}")

    QUEUES[run_id] = asyncio.Queue()
    PROCS[run_id] = proc

    info = RunInfo(
        run_id=run_id,
        created_ts=time.time(),
        flow=req.flow,
        mode=req.mode,
        output_dir=str(out_dir),
        status="starting",
        returncode=None,
    )
    RUNS[run_id] = info

    # Seed a header line into stream
    await QUEUES[run_id].put(f"[CATE_UI] started run_id={run_id}")
    await QUEUES[run_id].put(f"[CATE_UI] cmd: {' '.join(cmd)}")
    await QUEUES[run_id].put(f"[CATE_UI] output_dir: {out_dir}")

    # Start pump task
    asyncio.create_task(_pump_output(run_id, proc))

    return JSONResponse(info.model_dump())

@app.get("/stream")
async def stream(run_id: str = Query(...)):
    if run_id not in RUNS or run_id not in QUEUES:
        raise HTTPException(status_code=404, detail="Unknown run_id")

    async def event_gen():
        q = QUEUES[run_id]
        # Basic SSE stream
        while True:
            msg = await q.get()
            # SSE format
            yield f"data: {msg}\n\n"
            # Exit once process exited and queue drained
            info = RUNS.get(run_id)
            if info and info.status == "exited" and q.empty():
                break

    return StreamingResponse(event_gen(), media_type="text/event-stream")

@app.get("/runs")
def list_runs():
    # newest first
    runs = sorted(RUNS.values(), key=lambda r: r.created_ts, reverse=True)
    return [r.model_dump() for r in runs]

@app.get("/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="Unknown run_id")
    return RUNS[run_id].model_dump()

@app.get("/artifacts")
def list_artifacts(run_id: str = Query(...)):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="Unknown run_id")
    base = pathlib.Path(RUNS[run_id].output_dir)

    files = []
    for p in base.rglob("*"):
        if p.is_file():
            files.append(str(p.relative_to(base)))
    files.sort()
    return {"run_id": run_id, "files": files}

@app.get("/artifact")
def get_artifact(run_id: str = Query(...), path: str = Query(...)):
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="Unknown run_id")
    base = pathlib.Path(RUNS[run_id].output_dir)
    fp = _safe_join(base, path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    # Use FileResponse to let browser render/download
    return FileResponse(str(fp))
