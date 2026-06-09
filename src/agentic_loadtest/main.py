"""Process entrypoint. Reads ``ALT_*`` env vars and serves the app."""

from __future__ import annotations

import uvicorn

from .api import create_app
from .config import ServerSettings


def build() -> tuple[ServerSettings, "FastAPI"]:  # noqa: F821
    settings = ServerSettings()
    return settings, create_app(settings)


def main() -> None:
    settings, app = build()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
