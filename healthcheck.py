"""Docker readiness: app event-loop heartbeat, live PID and readable SQLite."""

import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path


def write_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "pid": os.getpid(), "updated_at": time.time(), "ready": True,
    }), encoding="utf-8")
    temporary.replace(path)


def is_healthy(heartbeat: str | Path, database: str | Path, max_age: float = 60) -> bool:
    try:
        payload = json.loads(Path(heartbeat).read_text(encoding="utf-8"))
        updated = payload["updated_at"]
        pid = payload["pid"]
        if payload.get("ready") is not True or type(pid) is not int or not 0 < pid < 2**31:
            return False
        if (type(updated) not in (int, float) or not math.isfinite(updated)
                or not -5 <= time.time() - updated <= max_age):
            return False
        os.kill(pid, 0)
        uri = Path(database).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as conn:
            conn.execute("PRAGMA schema_version").fetchone()
        return True
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return False


if __name__ == "__main__":
    heartbeat_path = sys.argv[1] if len(sys.argv) > 1 else "data/health.json"
    database_path = sys.argv[2] if len(sys.argv) > 2 else "data/bot_data.db"
    sys.exit(0 if is_healthy(heartbeat_path, database_path) else 1)
