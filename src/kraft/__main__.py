from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "kraft.api:app",
        host="127.0.0.1",
        port=int(os.environ.get("KRAFT_PORT", "8765")),
        log_level="warning",
    )
