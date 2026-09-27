"""Run the local Zelo voice service."""

import uvicorn
from voice_service.config import VoiceSettings


if __name__ == "__main__":
    settings = VoiceSettings()
    uvicorn.run(
        "voice_service.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
