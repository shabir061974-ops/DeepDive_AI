"""Optional FastAPI shell. No research endpoint or external actions yet."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app() -> "FastAPI":
    from fastapi import FastAPI

    app = FastAPI(title="DeepDive AI", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "phase": "foundation"}

    return app
