"""Local dashboard and bounded offline demo API. No live research routes."""

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app() -> "FastAPI":
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from .demo import NOTICE, run_demo, scenario_list

    app = FastAPI(title="DeepDive AI", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    assets = Path(__file__).parent / "static"
    slots = asyncio.Semaphore(2)

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        if request.client is not None and request.client.host not in {"127.0.0.1", "::1"}:
            response = JSONResponse({"error": "Local access only."}, status_code=403)
        else:
            response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "phase": "foundation"}

    @app.get("/")
    def dashboard():
        return FileResponse(assets / "index.html")

    @app.get("/api/demo/scenarios")
    def scenarios():
        return {"notice": NOTICE, "scenarios": scenario_list(), "live_research_enabled": False}

    @app.post("/api/demo/runs")
    async def demo_run(request: Request):
        # Cross-site forms cannot send this header. No CORS exceptions are enabled.
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if request.headers.get("x-deepdive-demo") != "offline" or (origin is not None and origin != expected_origin):
            return JSONResponse({"error": "Same-origin offline demo request required."}, status_code=403)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            return JSONResponse({"error": "Expected JSON scenario selection."}, status_code=415)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 1024:
                return JSONResponse({"error": "Scenario selection is too large."}, status_code=413)
            body.extend(chunk)
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            selection = json.loads(body.decode("utf-8"), object_pairs_hook=unique)
            if (not isinstance(selection, dict) or set(selection) != {"scenario_id"}
                    or selection["scenario_id"] not in {row["id"] for row in scenario_list()}):
                raise ValueError
        except (ValueError, TypeError, RecursionError):
            return JSONResponse({"error": "Choose a known offline scenario; arbitrary questions are disabled."}, status_code=400)
        if slots.locked():
            return JSONResponse({"error": "Demo capacity reached. Wait for a running demo to finish."}, status_code=429)
        await slots.acquire()

        async def stream():
            queue: asyncio.Queue = asyncio.Queue(maxsize=128)

            async def execute():
                try:
                    result = await run_demo(selection["scenario_id"], queue.put_nowait)
                    queue.put_nowait({"type": "result", "data": result})
                except Exception:
                    queue.put_nowait({"type": "error", "message": "Offline run failed safely. No live action was taken."})

            task = asyncio.create_task(execute())
            try:
                while True:
                    event = await asyncio.wait_for(queue.get(), 12)
                    yield json.dumps(event, default=lambda value: value.isoformat()) + "\n"
                    if event["type"] in {"result", "error"}:
                        break
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                slots.release()

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    app.mount("/static", StaticFiles(directory=assets), name="static")
    return app
