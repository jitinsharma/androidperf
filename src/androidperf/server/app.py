"""FastAPI application: REST + WebSocket endpoints for the androidperf UI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..device import DeviceError, list_devices, list_packages, pick_device
from .stream import RecordingSession

app = FastAPI(title="androidperf", docs_url=None, redoc_url=None)

_STATIC_DIR = Path(__file__).parent / "static"
_DEFAULT_RUNS_DIR = Path("./runs")

# Single global session — one recording at a time.
_session = RecordingSession()


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# REST: devices
# ---------------------------------------------------------------------------

@app.get("/api/devices")
async def api_devices() -> list[dict]:
    try:
        pairs = list_devices()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [
        {
            "serial": info.serial,
            "label": info.label,
            "model": info.model,
            "manufacturer": info.manufacturer,
            "sdk": info.sdk,
        }
        for _, info in pairs
    ]


# ---------------------------------------------------------------------------
# REST: packages
# ---------------------------------------------------------------------------

@app.get("/api/packages")
async def api_packages(serial: str | None = None, filter: str | None = None) -> list[str]:
    try:
        device, _ = pick_device(serial)
        return list_packages(device, filter)
    except DeviceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# REST: past runs
# ---------------------------------------------------------------------------

@app.get("/api/runs")
async def api_runs(runs_dir: str = "./runs") -> list[dict]:
    root = Path(runs_dir)
    if not root.exists():
        return []
    runs = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        samples = d / "samples.json"
        report = d / "report.html"
        if not samples.exists():
            continue
        try:
            meta = json.loads(samples.read_text())["meta"]
        except Exception:
            meta = {}
        runs.append({
            "id": d.name,
            "package": meta.get("package", ""),
            "started_at": meta.get("started_at", ""),
            "sample_count": meta.get("sample_count", 0),
            "has_report": report.exists(),
        })
    return runs


@app.get("/api/runs/{run_id}/report")
async def api_run_report(run_id: str, runs_dir: str = "./runs") -> FileResponse:
    path = Path(runs_dir) / run_id / "report.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(path, media_type="text/html")


@app.get("/api/runs/{run_id}/samples")
async def api_run_samples(run_id: str, runs_dir: str = "./runs") -> dict:
    path = Path(runs_dir) / run_id / "samples.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="samples.json not found")
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# WebSocket: live recording
# ---------------------------------------------------------------------------

@app.websocket("/ws/record")
async def ws_record(websocket: WebSocket) -> None:
    await websocket.accept()

    if _session.active:
        await websocket.send_text(json.dumps({"type": "error", "message": "A recording is already in progress."}))
        await websocket.close()
        return

    try:
        raw = await websocket.receive_text()
        params = json.loads(raw)
    except Exception:
        await websocket.send_text(json.dumps({"type": "error", "message": "Invalid start parameters."}))
        await websocket.close()
        return

    loop = asyncio.get_running_loop()
    _session.start(
        serial=params.get("serial"),
        package=params["package"],
        interval=float(params.get("interval", 1.0)),
        duration=float(params["duration"]) if params.get("duration") else None,
        output_dir=Path(params.get("output_dir", "./runs")),
        launch=not params.get("no_launch", False),
        loop=loop,
    )

    async def _stream_out() -> None:
        while True:
            msg = await _session.next_message(timeout=5.0)
            if msg is None:
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue
            await websocket.send_text(msg)
            if json.loads(msg)["type"] in ("done", "error"):
                return

    async def _receive_commands() -> None:
        try:
            while True:
                raw = await websocket.receive_text()
                if json.loads(raw).get("type") == "stop":
                    _session.stop()
                    return
        except WebSocketDisconnect:
            _session.stop()

    stream_task  = asyncio.create_task(_stream_out())
    command_task = asyncio.create_task(_receive_commands())

    # stream_task is the authoritative terminator: it ends only when the session
    # sends "done" or "error". command_task may stop the session early but we must
    # let stream_task drain the final message before tearing down.
    try:
        await stream_task
    except (asyncio.CancelledError, WebSocketDisconnect):
        pass
    finally:
        command_task.cancel()
        try:
            await command_task
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
