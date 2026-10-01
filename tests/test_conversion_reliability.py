import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.types import FSInputFile

from plans import Plan
from services import conversion
from tests.fakes import FakeContext, FakeMessage


async def test_cache_reissue_never_downloads_or_probes(tmp_path, monkeypatch, db):
    from repositories.conversion_cache import ConversionCacheRepo

    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.conversion_cache = ConversionCacheRepo(db)
    msg = message(ctx)
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("cached delivery did expensive work")

    ctx.bot.download = forbidden
    monkeypatch.setattr(conversion.VideoConverter, "probe", forbidden)
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert ctx.payloads[-1] == "cached-circle"
    assert len(ctx.payloads) == 2
    assert [row["status"] for row in ctx.usage.records] == ["ok", "ok"]


async def test_cache_record_metadata_migrates_legacy_rows(db):
    from repositories.conversion_cache import ConversionCacheRepo

    cache = ConversionCacheRepo(db)
    await cache.put("legacy", "old-id")
    row = await cache.get_record("legacy")
    assert row["source_duration"] is None
    await cache.put("modern", "new-id", source_size=6, source_duration=3, output_duration=2)
    row = await cache.get_record("modern")
    assert row["source_size"] == 6
    assert row["source_duration"] == 3
    assert row["output_duration"] == 2
    assert await cache.get("modern") == "new-id"


@pytest.mark.parametrize("trim_start", [-1, float("nan"), float("inf"), 3])
async def test_invalid_trim_cannot_deliver_cached_media(tmp_path, monkeypatch, db, trim_start):
    from repositories.conversion_cache import ConversionCacheRepo, make_cache_key

    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.conversion_cache = ConversionCacheRepo(db)
    msg = message(ctx)
    user = dict(USER, trim_start=trim_start, trim_duration=1)
    if trim_start == trim_start and trim_start != float("inf"):
        key = make_cache_key(
            "1",
            resolution=96,
            crf=24,
            preset="fast",
            crop_mode="crop",
            trim_start=trim_start,
            trim_duration=1,
        )
        await ctx.conversion_cache.put(
            key, "cache-id", source_size=6, source_duration=3, output_duration=1
        )
    await conversion.process_batch(ctx, user, "en", PLAN, [msg])
    assert not ctx.payloads and not ctx.encodes
    assert ctx.usage.records[-1]["error"] == "invalid_trim"


async def test_cached_source_size_revalidated_after_api_change(tmp_path, monkeypatch, db):
    from repositories.conversion_cache import ConversionCacheRepo

    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.conversion_cache = ConversionCacheRepo(db)
    msg = message(ctx)
    msg.video.file_size = 0
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    ctx.config.api = SimpleNamespace(input_limit_bytes=lambda _limit: 5)
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert len(ctx.payloads) == 1
    assert ctx.usage.records[-1]["error"] == "size_limit"


async def test_cache_write_failure_after_send_preserves_success_and_quota(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, monkeypatch)

    async def get_record(_key):
        return None

    async def put(*_args, **_kwargs):
        assert ctx.usage.records[-1]["status"] == "ok"
        raise OSError("cache unavailable")

    ctx.conversion_cache = SimpleNamespace(get_record=get_record, put=put)
    msg = message(ctx)
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert len(ctx.payloads) == 1
    assert [row["status"] for row in ctx.usage.records] == ["ok"]
    assert len(msg.answers) == 1


async def test_unsupported_extension_is_html_escaped(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, monkeypatch)
    msg = message(ctx)
    msg.video.file_name = "clip.<script>&"
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert "<script>" not in msg.answers[0]
    assert "&lt;script&gt;&amp;" in msg.answers[0]


@pytest.mark.parametrize(
    "kind,expected", [("runtime", "encode_failed"), ("telegram", "telegram_bad_request")]
)
async def test_errors_use_safe_codes_not_untrusted_exception_text(
    tmp_path, monkeypatch, kind, expected
):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendVideoNote

    ctx = make_ctx(tmp_path, monkeypatch)
    msg = message(ctx)

    async def broken(*_args, **_kwargs):
        if kind == "telegram":
            raise TelegramBadRequest(
                method=SendVideoNote(chat_id=1, video_note="id"), message="secret credential <bad>"
            )
        raise RuntimeError("secret credential <bad>")

    if kind == "telegram":
        msg.answer_video_note = broken
    else:
        ctx.converter.convert_to_circle = broken
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert ctx.usage.records[-1]["error"] == expected
    assert all("secret" not in answer for answer in msg.answers)
    assert msg.answers[-1] == conversion.t("en", "conv.error")


async def test_progress_does_not_repeat_same_percentage(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, monkeypatch)
    original = ctx.converter.convert_to_circle

    async def encode(path, **options):
        for percent in (0, 0, 10, 10, 100, 100):
            await options["progress_callback"](percent)
        return await original(path, **options)

    ctx.converter.convert_to_circle = encode
    msg = message(ctx)
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    edits = [edit for status in msg.statuses for edit in status.edits if "%" in edit]
    assert len(edits) == len(set(edits))


def test_cache_recipe_includes_new_resource_bounded_encoding(monkeypatch):
    import json

    from repositories import conversion_cache

    recipes = []
    original = json.dumps

    def dumps(recipe, **kwargs):
        recipes.append(recipe)
        return original(recipe, **kwargs)

    monkeypatch.setattr(conversion_cache.json, "dumps", dumps)
    conversion_cache.make_cache_key("source")
    assert recipes[0]["version"] == 2
    assert recipes[0]["fps"] == 30
    assert recipes[0]["maxrate"] == "4M"
    assert recipes[0]["bufsize"] == "8M"
    assert recipes[0]["threads"] == 1


@pytest.mark.parametrize("source_duration,output_duration", [(float("nan"), 3), (3, -1), (3, 61)])
async def test_unusable_cache_metadata_falls_back_to_encode(
    tmp_path, monkeypatch, db, source_duration, output_duration
):
    from repositories.conversion_cache import ConversionCacheRepo, make_cache_key

    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.conversion_cache = ConversionCacheRepo(db)
    msg = message(ctx)
    key = make_cache_key(
        "1",
        resolution=96,
        crf=24,
        preset="fast",
        crop_mode="crop",
        trim_start=0,
        trim_duration=None,
    )
    await ctx.conversion_cache.put(
        key,
        "cache-id",
        source_size=6,
        source_duration=source_duration,
        output_duration=output_duration,
    )
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert len(ctx.encodes) == 1
    assert isinstance(ctx.payloads[-1], FSInputFile)


async def test_cancel_during_best_effort_cache_write_does_not_record_failed_conversion(
    tmp_path, monkeypatch
):
    ctx = make_ctx(tmp_path, monkeypatch)
    entered = asyncio.Event()

    async def get_record(_key):
        return None

    async def put(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    ctx.conversion_cache = SimpleNamespace(get_record=get_record, put=put)
    msg = message(ctx)
    task = asyncio.create_task(conversion.process_batch(ctx, USER, "en", PLAN, [msg]))
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert [row["status"] for row in ctx.usage.records] == ["ok"]
    assert len(msg.answers) == 1


class Usage:
    def __init__(self):
        self.records = []

    async def count_user_today(self, _user):
        return sum(record["status"] == "ok" for record in self.records)

    async def add(self, **record):
        self.records.append(record)


def make_ctx(tmp_path, monkeypatch, duration=3):
    ctx = FakeContext()
    ctx.usage = Usage()
    ctx.config.processing = SimpleNamespace(
        temp_dir=str(tmp_path), probe_timeout_sec=1, encode_timeout_sec=1
    )
    ctx.config.api = SimpleNamespace(input_limit_bytes=lambda limit: limit or 20 * 1024 * 1024)
    ctx.payloads = []
    ctx.encodes = []

    async def download(_file, destination):
        Path(destination).write_bytes(b"source")

    async def probe(_path, **_kwargs):
        return {"duration": duration, "width": 160, "height": 90}

    async def encode(input_path, **options):
        ctx.encodes.append(options)
        output = options.get("output_path", str(tmp_path / "out.mp4"))
        Path(output).write_bytes(b"converted")
        return output, {"duration": options.get("trim_duration") or duration, "width": 96}

    ctx.bot = SimpleNamespace(download=download)
    ctx.converter = SimpleNamespace(convert_to_circle=encode)
    monkeypatch.setattr(conversion.VideoConverter, "probe", probe)
    return ctx


def message(ctx):
    msg = FakeMessage()

    async def send(payload, **_kwargs):
        ctx.payloads.append(payload)
        return SimpleNamespace(video_note=SimpleNamespace(file_id="cached-circle"))

    msg.answer_video_note = send
    return msg


PLAN = Plan("free", 50, 60, [96], 96, 24, "fast", 5, 3)
USER = {"user_id": 7, "language": "en"}


async def test_long_source_requires_explicit_duration_even_when_only_fraction_over_limit(
    tmp_path,
    monkeypatch,
):
    ctx = make_ctx(tmp_path, monkeypatch, duration=60.5)
    msg = message(ctx)
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert not ctx.payloads
    assert ctx.usage.records[-1]["error"] == "duration_limit"


async def test_duration_rejection_is_persistent_after_progress_cleanup(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, monkeypatch, duration=90)
    msg = message(ctx)
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert ctx.usage.records[0]["error"] == "duration_limit"
    notices = [status for status in msg.statuses if not status.deleted]
    assert notices, "duration error disappeared with disposable progress"
    assert any("90" in answer for answer in msg.answers)


async def test_explicit_fragment_fit_uses_streaming_upload_and_private_job(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, monkeypatch, duration=90)
    msg = message(ctx)
    await conversion.process_batch(
        ctx, dict(USER, crop_mode="fit", trim_start=2, trim_duration=5), "en", PLAN, [msg]
    )
    assert len(ctx.encodes) == 1
    assert ctx.encodes[0]["crop_mode"] == "fit"
    assert ctx.encodes[0]["trim_start"] == 2
    assert ctx.encodes[0]["trim_duration"] == 5
    assert isinstance(ctx.payloads[0], FSInputFile)
    assert Path(ctx.encodes[0]["output_path"]).parent.name.startswith("job-")
    assert not Path(ctx.encodes[0]["output_path"]).exists()
    assert ctx.usage.records[0]["status"] == "ok"


async def test_cache_keys_all_options_and_reuses_telegram_id(tmp_path, monkeypatch, db):
    from repositories.conversion_cache import ConversionCacheRepo, make_cache_key

    options = {
        "resolution": 96,
        "crf": 24,
        "preset": "fast",
        "crop_mode": "crop",
        "trim_start": 0,
        "trim_duration": None,
    }
    key = make_cache_key("unique", **options)
    for name, value in {
        "resolution": 128,
        "crf": 20,
        "preset": "slow",
        "crop_mode": "fit",
        "trim_start": 2,
        "trim_duration": 3,
    }.items():
        assert key != make_cache_key("unique", **dict(options, **{name: value}))
    assert key != make_cache_key("different", **options)
    cache = ConversionCacheRepo(db)
    await cache.put(key, "circle-id")
    assert await cache.get(key) == "circle-id"
    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.conversion_cache = cache
    msg = message(ctx)
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert len(ctx.encodes) == 1
    assert ctx.payloads[-1] == "cached-circle"
    assert len(ctx.usage.records) == 2


async def test_fresh_access_and_preferences_are_checked_after_queue_and_lock(tmp_path, monkeypatch):
    from conversion_queue import ConversionQueue

    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.conversion_queue = ConversionQueue(queue_size=1)
    gate = asyncio.Event()
    started = asyncio.Event()

    async def blocker():
        started.set()
        await gate.wait()

    busy = ctx.conversion_queue.submit(blocker)
    await started.wait()
    checks = []

    async def fresh(messages):
        assert ctx.locks.busy() == 1
        checks.append("fresh")
        return None

    msg = message(ctx)
    pending = asyncio.create_task(
        conversion.process_batch(ctx, USER, "en", PLAN, [msg], recheck=fresh)
    )
    await asyncio.sleep(0.01)
    assert not checks and not ctx.encodes
    gate.set()
    await busy.wait()
    await pending
    assert checks == ["fresh"]
    assert not ctx.encodes
    assert any("1" in text for text in msg.answers)
    assert all(" #1" not in text for text in msg.answers)
    await ctx.conversion_queue.shutdown()


async def test_cloud_size_cap_rejects_before_cache_or_download(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.config.api = SimpleNamespace(input_limit_bytes=lambda limit: 20 * 1024 * 1024)
    msg = message(ctx)
    msg.video.file_size = 21 * 1024 * 1024
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert not ctx.encodes and not ctx.payloads
    assert msg.answers


@pytest.mark.parametrize("local_validation", [False, True])
async def test_stale_cache_id_falls_back_to_encode(tmp_path, monkeypatch, db, local_validation):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendVideoNote

    from repositories.conversion_cache import ConversionCacheRepo

    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.conversion_cache = ConversionCacheRepo(db)
    msg = message(ctx)
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    original = msg.answer_video_note

    async def stale(payload, **kwargs):
        if isinstance(payload, str):
            if local_validation:
                raise ValueError("invalid cached Telegram identifier")
            raise TelegramBadRequest(
                method=SendVideoNote(chat_id=1, video_note=payload), message="wrong file identifier"
            )
        return await original(payload, **kwargs)

    msg.answer_video_note = stale
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert len(ctx.encodes) == 2
    assert all(row["status"] == "ok" for row in ctx.usage.records)


async def test_album_shutdown_awaits_inflight_handlers():
    from albums import AlbumBuffer

    buffer = AlbumBuffer(delay=0)
    started = asyncio.Event()
    finished = asyncio.Event()

    async def handler(_messages):
        started.set()
        try:
            await asyncio.sleep(0.05)
        finally:
            finished.set()

    buffer.add("g", FakeMessage(), handler)
    await started.wait()
    await buffer.shutdown(timeout=1)
    assert finished.is_set()


async def test_cached_untrimmed_long_source_cannot_bypass_changed_plan(tmp_path, monkeypatch, db):
    from repositories.conversion_cache import ConversionCacheRepo

    ctx = make_ctx(tmp_path, monkeypatch, duration=90)
    ctx.conversion_cache = ConversionCacheRepo(db)
    msg = message(ctx)
    pro = Plan("pro", 50, 90, [96], 96, 24, "fast", 0, 0)
    await conversion.process_batch(ctx, USER, "en", pro, [msg])

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("cached metadata must reject changed plan without download")

    ctx.bot.download = forbidden
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert len(ctx.payloads) == 1
    assert ctx.usage.records[-1]["error"] == "duration_limit"


async def test_encode_coroutine_timeout_persists_error_and_removes_job(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.config.processing.encode_timeout_sec = 0.02
    stopped = asyncio.Event()

    async def slow(_input, **_kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    ctx.converter.convert_to_circle = slow
    msg = message(ctx)
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert stopped.is_set()
    assert ctx.usage.records[-1]["error"] == "timeout"
    assert any(not status.deleted for status in msg.statuses)
    assert not list(tmp_path.glob("job-*"))


async def test_batch_cancel_reaps_coroutine_and_persists_cancelled(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, monkeypatch)
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def slow(_input, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    ctx.converter.convert_to_circle = slow
    msg = message(ctx)
    task = asyncio.create_task(conversion.process_batch(ctx, USER, "en", PLAN, [msg]))
    await started.wait()
    task.cancel()
    result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert stopped.is_set()
    assert ctx.usage.records[-1]["error"] == "cancelled"
    assert any(not status.deleted for status in msg.statuses)
    assert ctx.locks.busy() == 0
    assert not list(tmp_path.glob("job-*"))


async def test_actual_download_size_and_coroutine_timeout_cleanup(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.config.api = SimpleNamespace(input_limit_bytes=lambda limit: 5)
    msg = message(ctx)
    msg.video.file_size = 0
    await conversion.process_batch(ctx, USER, "en", PLAN, [msg])
    assert not ctx.payloads
    assert ctx.usage.records[-1]["error"] == "size_limit"
    assert not list(tmp_path.glob("job-*"))


async def test_duration_limit_is_revalidated_on_cached_fragment(tmp_path, monkeypatch, db):
    from repositories.conversion_cache import ConversionCacheRepo

    ctx = make_ctx(tmp_path, monkeypatch, duration=90)
    ctx.conversion_cache = ConversionCacheRepo(db)
    msg = message(ctx)
    user = dict(USER, trim_start=0, trim_duration=70)
    pro = Plan("pro", 50, 90, [96], 96, 24, "fast", 0, 0)
    await conversion.process_batch(ctx, user, "en", pro, [msg])
    await conversion.process_batch(ctx, user, "en", PLAN, [msg])
    assert len(ctx.payloads) == 1
    assert ctx.usage.records[-1]["error"] == "duration_limit"


async def test_cancellation_while_announcing_queue_position_cancels_the_job(
    tmp_path, monkeypatch
):
    """An accepted job must not outlive the caller that cancelled during the notice."""
    from conversion_queue import ConversionQueue

    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.conversion_queue = ConversionQueue(workers=1, queue_size=1)
    busy_gate = asyncio.Event()
    worker_entered = asyncio.Event()

    async def blocker():
        worker_entered.set()
        await busy_gate.wait()

    occupied = ctx.conversion_queue.submit(blocker)
    await worker_entered.wait()

    msg = message(ctx)
    notice_started = asyncio.Event()
    plain_answer = msg.answer

    async def answer(text, **kwargs):
        if text == conversion.t("en", "conv.queued", position=1):
            notice_started.set()
            await asyncio.Event().wait()
        return await plain_answer(text, **kwargs)

    msg.answer = answer
    task = asyncio.create_task(conversion.process_batch(ctx, USER, "en", PLAN, [msg]))
    await notice_started.wait()
    assert ctx.conversion_queue.pending() == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert ctx.conversion_queue.pending() == 0, "accepted job stayed in the queue"
    busy_gate.set()
    await occupied.wait()
    await asyncio.sleep(0)
    assert not ctx.encodes and not ctx.payloads, "abandoned batch still ran"
    await ctx.conversion_queue.shutdown()


async def test_shutdown_budget_is_not_extended_by_a_stalled_cancellation(tmp_path, monkeypatch):
    """Shutdown must finish within its budget even if the network never answers."""
    import time

    from conversion_queue import ConversionQueue

    ctx = make_ctx(tmp_path, monkeypatch)
    ctx.config.processing.encode_timeout_sec = 30
    ctx.conversion_queue = ConversionQueue(workers=1, queue_size=0)
    downloading = asyncio.Event()

    async def stalled_download(_file_id, **_kwargs):
        downloading.set()
        await asyncio.Event().wait()

    ctx.bot = SimpleNamespace(download=stalled_download)
    msg = message(ctx)
    error_text = conversion.t("en", "conv.error")
    plain_answer = msg.answer
    reached_network = []

    async def stalling_answer(text, **kwargs):
        if text == error_text:
            reached_network.append(text)
            await asyncio.Event().wait()  # a Telegram request that never returns
        return await plain_answer(text, **kwargs)

    msg.answer = stalling_answer
    task = asyncio.create_task(conversion.process_batch(ctx, USER, "en", PLAN, [msg]))
    await downloading.wait()

    began = time.monotonic()
    await asyncio.wait_for(ctx.conversion_queue.shutdown(timeout=0.1), 10)
    elapsed = time.monotonic() - began
    assert elapsed < 1, f"shutdown took {elapsed:.2f}s with a 30s encode budget"
    assert not reached_network, "cancellation cleanup hit the network"
    assert ctx.usage.records[-1]["error"] == "cancelled"
    await asyncio.gather(task, return_exceptions=True)
