"""Runtime configuration and startup failure paths without contacting Telegram."""
from pathlib import Path

import pytest

import main
from config_loader import Config


async def test_missing_config_returns_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CONFIG_PATH", tmp_path / "missing.yaml")
    assert await main.main() == 1


async def test_shutdown_drains_before_jobs_with_one_shared_deadline():
    import asyncio
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock
    events = []
    timeouts = []
    def close():
        events.append("requests.close")
    def drain(name):
        async def run(timeout):
            events.append(name)
            timeouts.append(timeout)
            await asyncio.sleep(0.01)
        return run
    ctx = NS(config=NS(processing=NS(shutdown_timeout_sec=0.1)),
             requests=NS(close=close, shutdown=drain("requests")),
             albums=NS(shutdown=drain("albums")),
             conversion_queue=NS(shutdown=drain("queue")), jobs=NS(stop=AsyncMock()))
    function = getattr(main, "shutdown_context", None)
    assert function is not None
    await function(ctx)
    assert events == ["requests.close", "albums", "queue", "requests"]
    assert 0 <= timeouts[2] < timeouts[1] < timeouts[0] <= 0.1
    ctx.jobs.stop.assert_awaited_once()


async def test_startup_failure_closes_session_and_does_not_leave_tasks(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock

    import yaml

    import context
    path = tmp_path / "test.yaml"
    path.write_text(yaml.safe_dump({"bot": {"token": "123:ABC", "admin_ids": [1]}}))
    bot = NS(session=NS(close=AsyncMock()), get_me=AsyncMock(side_effect=RuntimeError("offline")))
    dispatcher = NS(storage=NS(close=AsyncMock()), update=NS(outer_middleware=lambda _: None))
    monkeypatch.setattr(main, "CONFIG_PATH", path)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "build_bot", lambda _: bot)
    monkeypatch.setattr(main, "build_dispatcher", lambda: dispatcher)
    with pytest.raises(RuntimeError, match="offline"):
        await main.main()
    bot.session.close.assert_awaited_once()
    assert context.ctx is None
    assert not (tmp_path / "data" / "health.json").exists()



async def test_real_startup_wires_resources_and_cleans_them(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace as NS
    from unittest.mock import AsyncMock

    import yaml

    import context
    from healthcheck import is_healthy
    config_path = tmp_path / "test.yaml"
    config_path.write_text(yaml.safe_dump({
        "bot": {"token": "123:ABC", "admin_ids": [1]},
        "processing": {"workers": 2, "queue_size": 3, "temp_dir": "private-jobs",
                       "probe_timeout_sec": 4, "encode_timeout_sec": 6},
    }))
    bot = NS(session=NS(close=AsyncMock()), get_me=AsyncMock(return_value=NS(username="test")))
    middlewares = []
    async def poll(supplied_bot, **kwargs):
        ctx = context.get_ctx()
        assert supplied_bot is bot and kwargs["close_bot_session"] is False
        assert ctx.conversion_queue.workers == 2 and ctx.conversion_queue.queue_size == 3
        assert ctx.converter.probe_timeout_sec == 4 and ctx.converter.encode_timeout_sec == 6
        assert ctx.tempfiles is not None and ctx.tempfiles.root == tmp_path / "private-jobs"
        assert ctx.conversion_cache is not None
        assert ctx.requests in middlewares
        assert is_healthy(tmp_path / "data/health.json", tmp_path / "data/bot_data.db")
        assert {job.name for job in ctx.jobs.jobs} == {
            "expire_subscriptions", "cleanup_temp", "daily_stats", "heartbeat",
        }
        await ctx.users.get_or_create(42, "test", "Test")
    dispatcher = NS(storage=NS(close=AsyncMock()),
                    update=NS(outer_middleware=middlewares.append),
                    resolve_used_update_types=lambda: ["message"], start_polling=poll)
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(main, "build_bot", lambda _: bot)
    monkeypatch.setattr(main, "build_dispatcher", lambda: dispatcher)
    assert await main.main() == 0
    bot.session.close.assert_awaited_once()
    dispatcher.storage.close.assert_awaited_once()
    assert context.ctx is None
    assert not (tmp_path / "data/health.json").exists()
    assert not any(task.get_name().startswith("job:") for task in asyncio.all_tasks())


async def test_cloud_session_uses_telegram_api():
    builder = getattr(main, "build_bot", None)
    assert builder is not None
    config = Config.load()
    config.bot.token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    bot = builder(config)
    try:
        assert bot.session.api.is_local is False
        assert bot.session.api.api_url(bot.token, "getMe").startswith("https://api.telegram.org/")
    finally:
        await bot.session.close()


async def test_local_session_uses_configured_server():
    builder = getattr(main, "build_bot", None)
    assert builder is not None
    config = Config.load()
    config.bot.token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
    config.api.local = True
    config.api.base_url = "http://bot-api:8081"
    bot = builder(config)
    try:
        assert bot.session.api.is_local is True
        assert bot.session.api.api_url(bot.token, "getMe").startswith("http://bot-api:8081/")
    finally:
        await bot.session.close()


def test_image_does_not_copy_live_config():
    root = Path(__file__).resolve().parent.parent
    ignores = (root / ".dockerignore").read_text().splitlines()
    dockerfile = (root / "Dockerfile").read_text()
    assert "config.yaml" in ignores
    assert "*.yaml" not in dockerfile
    assert "CMD python healthcheck.py" in dockerfile
