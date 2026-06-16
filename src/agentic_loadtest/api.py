"""FastAPI application: REST control plane, live WebSocket metrics, and the UI.

Endpoints
    GET  /api/health          liveness/readiness probe
    GET  /api/scenarios       list available scenarios
    GET  /api/config          current/default run config
    GET  /api/status          run state + metrics snapshot
    GET  /api/timeline        full per-second timeline (for late-joining charts)
    POST /api/start           start a run with a RunConfig body
    POST /api/stop            stop the current run
    POST /api/reset           clear metrics from the last run (when not running)
    WS   /ws                  pushes {snapshot, point} ~1/s while running
    GET  /                    the dashboard
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import RunConfig, ServerSettings, dump_run_config, load_run_config
from .orchestrator import Orchestrator

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: ServerSettings) -> FastAPI:
    app = FastAPI(title="Agentic Load Test", version="0.1.0")
    orch = Orchestrator(settings.scenarios_dir, settings.fixtures_dir, settings.prompts_dir)
    default_config = load_run_config(settings.config)

    app.state.orchestrator = orch
    app.state.settings = settings

    # ----- REST -----------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "state": orch.state.value}

    @app.get("/api/scenarios")
    async def scenarios() -> dict:
        return {
            "scenarios": [
                {
                    "name": s.name,
                    "description": s.description,
                    "weight": s.weight,
                    "tools": s.tools,
                    "max_turns": s.max_turns,
                    "follow_ups": len(s.follow_ups),
                }
                for s in orch.available_scenarios.values()
            ]
        }

    @app.post("/api/scenarios/reload")
    async def reload_scenarios() -> dict:
        orch.reload_scenarios()
        return {"count": len(orch.available_scenarios)}

    @app.get("/api/prompts")
    async def prompts() -> dict:
        """List available system-prompt presets with a rough token estimate."""
        items = []
        d = settings.prompts_dir
        if d.exists():
            for fp in sorted(d.glob("*.md")) + sorted(d.glob("*.txt")):
                text = fp.read_text()
                items.append(
                    {"name": fp.name, "chars": len(text), "tokens_est": max(1, len(text) // 4)}
                )
        return {"prompts": items}

    @app.get("/api/prompts/{name}")
    async def prompt(name: str) -> JSONResponse:
        """Return the contents of one preset (used by the UI 'Load preset' button)."""
        # Guard against path traversal: only a bare filename in the prompts dir.
        fp = (settings.prompts_dir / Path(name).name)
        if not fp.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        text = fp.read_text()
        return JSONResponse({"name": fp.name, "content": text, "tokens_est": max(1, len(text) // 4)})

    @app.get("/api/config")
    async def get_config() -> dict:
        cfg = orch.config or default_config
        return cfg.model_dump(mode="json")

    @app.get("/api/status")
    async def status() -> dict:
        return {
            "state": orch.state.value,
            "running": orch.is_running,
            "config": orch.config.model_dump(mode="json") if orch.config else None,
            "metrics": orch.metrics.snapshot() if orch.metrics else None,
            "prompt_pool": orch.pool_info,
        }

    @app.get("/api/timeline")
    async def timeline() -> dict:
        return {"timeline": orch.metrics.timeline if orch.metrics else []}

    @app.post("/api/start")
    async def start(cfg: RunConfig) -> JSONResponse:
        try:
            await orch.start(cfg)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        # Persist as the new default so the form repopulates next time.
        with contextlib.suppress(Exception):
            dump_run_config(cfg, settings.config)
        return JSONResponse({"state": orch.state.value})

    @app.post("/api/stop")
    async def stop() -> dict:
        await orch.stop()
        return {"state": orch.state.value}

    @app.post("/api/reset")
    async def reset() -> JSONResponse:
        try:
            orch.reset()
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse({"state": orch.state.value})

    # ----- WebSocket ------------------------------------------------------

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                payload = {
                    "state": orch.state.value,
                    "running": orch.is_running,
                    "metrics": orch.metrics.snapshot() if orch.metrics else None,
                    "prompt_pool": orch.pool_info,
                    "point": orch.metrics.timeline[-1]
                    if orch.metrics and orch.metrics.timeline
                    else None,
                }
                await socket.send_json(payload)
                await asyncio.sleep(1.0)
        except (WebSocketDisconnect, asyncio.CancelledError):
            return
        except RuntimeError:
            return

    # ----- UI -------------------------------------------------------------

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
