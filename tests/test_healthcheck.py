"""Health is tied to a live app heartbeat, not a fresh Python process."""

import importlib
import json
import os
import sqlite3
import time


def setup_files(tmp_path, **overrides):
    heartbeat = tmp_path / "health.json"
    db = tmp_path / "bot.db"
    sqlite3.connect(db).close()
    payload = {"pid": os.getpid(), "updated_at": time.time(), "ready": True, **overrides}
    heartbeat.write_text(json.dumps(payload))
    return heartbeat, db

def test_live_heartbeat_is_healthy(tmp_path):
    heartbeat, db = setup_files(tmp_path)
    assert importlib.import_module("healthcheck").is_healthy(heartbeat, db)

def test_invalid_large_pid_is_unhealthy(tmp_path):
    from healthcheck import is_healthy
    heartbeat = tmp_path / "health.json"
    heartbeat.write_text(json.dumps({"pid": 10**30, "updated_at": time.time(), "ready": True}))
    assert is_healthy(heartbeat, tmp_path / "database.sqlite") is False


def test_stale_heartbeat_is_unhealthy(tmp_path):
    heartbeat, db = setup_files(tmp_path, updated_at=time.time() - 120)
    assert not importlib.import_module("healthcheck").is_healthy(heartbeat, db)

def test_not_ready_is_unhealthy(tmp_path):
    heartbeat, db = setup_files(tmp_path, ready=False)
    assert not importlib.import_module("healthcheck").is_healthy(heartbeat, db)

def test_missing_database_does_not_create_file(tmp_path):
    heartbeat, _ = setup_files(tmp_path)
    missing = tmp_path / "missing.db"
    assert not importlib.import_module("healthcheck").is_healthy(heartbeat, missing)
    assert not missing.exists()

def test_invalid_heartbeat_is_unhealthy(tmp_path):
    heartbeat, db = setup_files(tmp_path)
    heartbeat.write_text("not json")
    assert not importlib.import_module("healthcheck").is_healthy(heartbeat, db)
