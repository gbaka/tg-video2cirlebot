"""Async FFmpeg conversion with display-aspect normalization and selected fragments."""

import asyncio
import contextlib
import inspect
import json
import math
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, ClassVar

ProgressCallback = Callable[[int], Awaitable[None] | None]


class VideoConverter:
    FPS = 30
    MAXRATE = "4M"
    BUFSIZE = "8M"
    THREADS = 1
    SUPPORTED_EXTENSIONS: ClassVar[set[str]] = {
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".flv",
        ".wmv",
        ".m4v",
        ".3gp",
        ".3g2",
        ".ts",
        ".mts",
        ".m2ts",
        ".vob",
        ".ogv",
        ".mxf",
        ".f4v",
        ".asf",
        ".rm",
        ".rmvb",
        ".divx",
        ".xvid",
        ".mpg",
        ".mpeg",
        ".mpe",
        ".mpv",
        ".m2v",
        ".m1v",
        ".dv",
        ".dif",
        ".dvf",
        ".nsv",
        ".roq",
        ".amv",
        ".qt",
    }

    def __init__(
        self,
        probe_timeout_sec: float = 15,
        encode_timeout_sec: float = 120,
        terminate_timeout_sec: float = 2,
    ):
        self.probe_timeout_sec = probe_timeout_sec
        self.encode_timeout_sec = encode_timeout_sec
        self.terminate_timeout_sec = terminate_timeout_sec

    @classmethod
    def is_supported(cls, filename: str) -> bool:
        return Path(filename).suffix.lower() in cls.SUPPORTED_EXTENSIONS

    @staticmethod
    async def _process(
        args: list[str],
        timeout: float = 120,
        progress: ProgressCallback | None = None,
        duration: float = 0,
        terminate_timeout: float = 2,
    ) -> bytes:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Drainers survive callback errors so reaping never blocks on full pipes.
        callback_error: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()
        output = bytearray()
        errors = bytearray()
        retention = 256 * 1024
        callback_task: asyncio.Future[None] | None = None

        async def drain(reader: asyncio.StreamReader, *, stdout: bool) -> None:
            nonlocal callback_task
            pending = bytearray()
            last = 0
            while chunk := await reader.read(8192):
                target = output if stdout else errors
                if not stdout or not progress:
                    target.extend(chunk[: max(0, retention - len(target))])
                if not stdout or not progress or callback_error.done():
                    continue
                pending.extend(chunk)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    if line.startswith(b"out_time_us=") and duration > 0:
                        try:
                            value = min(
                                99,
                                max(
                                    last,
                                    int(float(line.split(b"=", 1)[1]) / 1_000_000 / duration * 100),
                                ),
                            )
                            if value > last:
                                result = progress(value)
                                if inspect.isawaitable(result):
                                    callback_task = asyncio.ensure_future(result)
                                    await callback_task
                                    callback_task = None
                                last = value
                        except BaseException as exc:
                            callback_error.set_result(exc)
                            break
                if len(pending) > 8192:
                    pending.clear()

        assert process.stdout is not None and process.stderr is not None
        readers = [
            asyncio.create_task(drain(process.stdout, stdout=True)),
            asyncio.create_task(drain(process.stderr, stdout=False)),
        ]

        async def communicate() -> None:
            await asyncio.gather(*readers)
            await process.wait()

        communication = asyncio.create_task(communicate())

        async def stop() -> None:
            if callback_task is not None:
                callback_task.cancel()
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), terminate_timeout)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
            await process.wait()
            await communication

        try:
            async with asyncio.timeout(timeout):
                pending: set[asyncio.Future[Any]] = {communication, callback_error}
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if callback_error in done:
                    raise callback_error.result()
                await communication
        except BaseException:
            cleanup = asyncio.create_task(stop())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            raise
        finally:
            callback_error.cancel()
        stdout, stderr = bytes(output), bytes(errors)
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace")[-2000:])
        return stdout

    @staticmethod
    async def probe(path: str, *, timeout: float = 15) -> dict[str, Any]:
        raw = await VideoConverter._process(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-threads",
                str(VideoConverter.THREADS),
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                path,
            ],
            timeout=timeout,
        )
        data = json.loads(raw)
        stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        if not stream:
            raise ValueError("video stream not found")
        width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
        duration = float(data.get("format", {}).get("duration", stream.get("duration", 0)))
        if (
            not 0 < width <= 8192
            or not 0 < height <= 8192
            or width * height > 33_554_432
            or not math.isfinite(duration)
            or duration <= 0
        ):
            raise ValueError("invalid video dimensions or duration")
        rotation = float(stream.get("tags", {}).get("rotate", 0))
        for side in stream.get("side_data_list", []):
            if "rotation" in side:
                rotation = float(side["rotation"])
        sar = stream.get("sample_aspect_ratio", "1:1")
        # FFprobe reports unspecified SAR as N/A or 0:1; both mean square pixels.
        if sar in {"N/A", "0:1"}:
            sar = "1:1"
        try:
            numerator, denominator = (float(part) for part in sar.split(":"))
            ratio = numerator / denominator
        except (ValueError, ZeroDivisionError, AttributeError) as exc:
            raise ValueError("invalid sample aspect ratio") from exc
        if (
            not math.isfinite(rotation)
            or not math.isfinite(numerator)
            or not math.isfinite(denominator)
            or numerator <= 0
            or denominator <= 0
            or width * ratio * height > 33_554_432
            or not math.isfinite(ratio)
            or not 1 / 16 <= ratio <= 16
            or not 2 <= width * ratio <= 8192
        ):
            raise ValueError("invalid display dimensions or sample aspect ratio")
        display_width, display_height = width * ratio, float(height)
        if round(rotation) % 180:
            display_width, display_height = display_height, display_width
        return {
            "rotation": rotation,
            "display_width": display_width,
            "display_height": display_height,
            "width": width,
            "height": height,
            "duration": duration,
            "sample_aspect_ratio": stream.get("sample_aspect_ratio", "1:1"),
        }

    async def convert_to_circle(
        self,
        input_path: str,
        resolution: int = 360,
        crf: int = 24,
        preset: str = "fast",
        output_path: str | None = None,
        *,
        crop_mode: str = "crop",
        trim_start: float = 0,
        trim_duration: float | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if not self.is_supported(input_path):
            raise ValueError("unsupported input format")
        if (
            resolution < 2
            or resolution % 2
            or not 0 <= crf <= 51
            or crop_mode not in {"crop", "fit"}
            or not math.isfinite(trim_start)
            or trim_start < 0
            or (
                trim_duration is not None
                and (not math.isfinite(trim_duration) or trim_duration <= 0)
            )
        ):
            raise ValueError("invalid encode options")
        info = await self.probe(input_path, timeout=self.probe_timeout_sec)
        if trim_start >= info["duration"]:
            raise ValueError("fragment starts beyond source")
        duration = min(trim_duration or info["duration"], info["duration"] - trim_start)
        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)
        # Normalize anamorphic input before FFmpeg's post-autorotation crop/fit.
        normal = "scale=trunc(iw*sar/2)*2:ih,setsar=1,"
        if crop_mode == "crop":
            vf = normal + rf"crop=min(iw\,ih):min(iw\,ih),scale={resolution}:{resolution},setsar=1"
        else:
            vf = normal + (
                f"scale={resolution}:{resolution}:force_original_aspect_ratio=decrease,"
                f"pad={resolution}:{resolution}:(ow-iw)/2:(oh-ih)/2,setsar=1"
            )

        async def notify(value: int) -> None:
            if progress_callback:
                result = progress_callback(value)
                if inspect.isawaitable(result):
                    await result

        try:
            await notify(0)
            await self._process(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-threads",
                    str(self.THREADS),
                    "-filter_threads",
                    str(self.THREADS),
                    "-filter_complex_threads",
                    str(self.THREADS),
                    "-ss",
                    str(trim_start),
                    "-i",
                    input_path,
                    "-t",
                    str(duration),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0?",
                    "-vf",
                    vf,
                    "-c:v",
                    "libx264",
                    "-threads",
                    str(self.THREADS),
                    "-r",
                    str(self.FPS),
                    "-maxrate",
                    self.MAXRATE,
                    "-bufsize",
                    self.BUFSIZE,
                    "-crf",
                    str(crf),
                    "-preset",
                    preset,
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-map_metadata",
                    "-1",
                    "-movflags",
                    "+faststart",
                    "-progress",
                    "pipe:1",
                    "-nostats",
                    output_path,
                ],
                timeout=self.encode_timeout_sec,
                progress=progress_callback,
                duration=duration,
                terminate_timeout=self.terminate_timeout_sec,
            )
            meta = await self.probe(output_path, timeout=self.probe_timeout_sec)
            meta["size_bytes"] = os.path.getsize(output_path)
            await notify(100)
            return output_path, meta
        except BaseException:
            Path(output_path).unlink(missing_ok=True)
            raise
