"""Run the optional local Zelo speech adapter on loopback only."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.getenv("ZELO_VOICE_PORT", "8765"))
    uvicorn.run(
        "voice_service.app:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
