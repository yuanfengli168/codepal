"""CodePal entry point."""
from __future__ import annotations

import uvicorn

from codepal.api.app import create_app
from codepal.config import get_config

app = create_app()

if __name__ == "__main__":
    cfg = get_config()
    uvicorn.run(
        "codepal.main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.server.log_level,
        reload=False,
    )
