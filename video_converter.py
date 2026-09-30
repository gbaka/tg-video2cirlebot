"""
Конвертация видео в формат Telegram Video Note (кружок).
Параметры кодирования (разрешение/качество) задаются на уровне тарифа.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import ClassVar

import ffmpeg

logger = logging.getLogger(__name__)


class VideoConverter:
    """Конвертер видео в формат кружка (video note) для Telegram."""

    # Поддерживаемые расширения (ffmpeg умеет почти всё)
    SUPPORTED_EXTENSIONS: ClassVar[set[str]] = {
        '.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv',
        '.m4v', '.3gp', '.3g2', '.ts', '.mts', '.m2ts', '.vob',
        '.ogv', '.mxf', '.f4v', '.asf', '.rm', '.rmvb', '.divx',
        '.xvid', '.mpg', '.mpeg', '.mpe', '.mpv', '.m2v', '.m1v',
        '.dv', '.dif', '.dvf', '.nsv', '.roq', '.amv', '.qt',
    }

    @classmethod
    def is_supported(cls, filename: str) -> bool:
        return Path(filename).suffix.lower() in cls.SUPPORTED_EXTENSIONS

    @staticmethod
    async def probe(path: str) -> dict:
        """Возвращает метаданные видео: duration, width, height."""
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ffmpeg.probe(path))
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"), None
        )
        duration = float(data.get("format", {}).get("duration", 0) or 0)
        if not video_stream:
            raise ValueError("Видео-поток не найден")
        return {
            "duration": duration,
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
        }

    async def convert_to_circle(
        self,
        input_path: str,
        resolution: int = 360,
        crf: int = 24,
        preset: str = "fast",
        output_path: str | None = None,
    ) -> tuple[str, dict]:
        """
        Конвертирует видео в квадратный кружок.

        Returns:
            (путь_к_файлу, метаданные {duration, width, height, size_bytes})
        """
        if not self.is_supported(input_path):
            raise ValueError(f"Неподдерживаемый формат: {Path(input_path).suffix}")

        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix=".mp4")
            os.close(fd)

        try:
            info = await self.probe(input_path)
            width, height = info["width"], info["height"]
            duration = info["duration"]
            if width <= 0 or height <= 0:
                raise ValueError(f"Некорректные размеры видео: {width}x{height}")

            # Квадратный кроп по центру
            crop = min(width, height)
            x_off = (width - crop) // 2
            y_off = (height - crop) // 2
            vf = (
                f"crop={crop}:{crop}:{x_off}:{y_off},"
                f"scale={resolution}:{resolution}:force_original_aspect_ratio=decrease,"
                f"pad={resolution}:{resolution}:(ow-iw)/2:(oh-ih)/2"
            )

            await self._run_ffmpeg(input_path, output_path, vf, crf, preset, duration)

            out_size = os.path.getsize(output_path)
            result = await self.probe(output_path)
            metadata = {
                "duration": result["duration"] or duration,
                "width": result["width"],
                "height": result["height"],
                "size_bytes": out_size,
            }
            logger.info("Конвертация готова: %s (%d KB)", output_path, out_size // 1024)
            return output_path, metadata

        except Exception:
            if os.path.exists(output_path):
                os.unlink(output_path)
            raise

    async def _run_ffmpeg(
        self, input_path: str, output_path: str, vf: str,
        crf: int, preset: str, duration: float,
    ) -> None:
        def _run():
            (
                ffmpeg.input(input_path)
                .output(
                    output_path,
                    vf=vf,
                    vcodec="libx264",
                    acodec="aac",
                    audio_bitrate="128k",
                    crf=crf,
                    preset=preset,
                    pix_fmt="yuv420p",
                    movflags="+faststart",
                    t=duration if duration > 0 else None,
                )
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True, quiet=True)
            )

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _run)
        except ffmpeg.Error as e:
            stderr = e.stderr.decode("utf-8", errors="ignore") if e.stderr else str(e)
            logger.error("FFmpeg error: %s", stderr)
            raise RuntimeError("Ошибка конвертации видео") from e
