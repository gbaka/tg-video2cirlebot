"""
Модуль конвертации видео в формат Telegram Video Note (кружок).
Использует ffmpeg для максимальной поддержки форматов.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import ffmpeg

logger = logging.getLogger(__name__)


class VideoConverter:
    """Конвертер видео в формат кружка (video note) для Telegram."""

    # Поддерживаемые расширения (ffmpeg поддерживает практически всё)
    SUPPORTED_EXTENSIONS = {
        '.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv',
        '.m4v', '.3gp', '.3g2', '.ts', '.mts', '.m2ts', '.vob',
        '.ogv', '.mxf', '.f4v', '.asf', '.rm', '.rmvb', '.divx',
        '.xvid', '.mpg', '.mpeg', '.mpe', '.mpv', '.m2v', '.m1v',
        '.dv', '.dif', '.dvf', '.nsv', '.roq', '.amv', '.qt'
    }

    def __init__(
        self,
        target_resolution: int = 360,
        crf: int = 24,
        preset: str = "fast",
        max_size_mb: int = 50
    ):
        self.target_resolution = target_resolution
        self.crf = crf
        self.preset = preset
        self.max_size_bytes = max_size_mb * 1024 * 1024 if max_size_mb > 0 else 0

    @classmethod
    def is_supported(cls, filename: str) -> bool:
        """Проверяет, поддерживается ли формат файла."""
        ext = Path(filename).suffix.lower()
        return ext in cls.SUPPORTED_EXTENSIONS

    async def convert_to_circle(
        self,
        input_path: str,
        output_path: Optional[str] = None
    ) -> Tuple[str, dict]:
        """
        Конвертирует видео в формат кружка.

        Args:
            input_path: Путь к исходному видео
            output_path: Путь для результата (если None — создастся временный файл)

        Returns:
            Tuple[путь_к_файлу, метаданные_видео]

        Raises:
            ValueError: если формат не поддерживается или файл слишком большой
            RuntimeError: если ffmpeg вернул ошибку
        """
        if not self.is_supported(input_path):
            raise ValueError(f"Неподдерживаемый формат: {Path(input_path).suffix}")

        # Проверяем размер входного файла
        input_size = os.path.getsize(input_path)
        if self.max_size_bytes and input_size > self.max_size_bytes * 3:  # запас на конвертацию
            raise ValueError(f"Файл слишком большой: {input_size / 1024 / 1024:.1f} MB")

        if output_path is None:
            fd, output_path = tempfile.mkstemp(suffix='.mp4')
            os.close(fd)

        try:
            # Получаем информацию о видео
            probe = await self._probe_video(input_path)
            duration = float(probe.get('format', {}).get('duration', 0))

            # Находим видео-поток (не аудио)
            video_stream = None
            for stream in probe.get('streams', []):
                if stream.get('codec_type') == 'video':
                    video_stream = stream
                    break

            if not video_stream:
                raise ValueError("Видео-поток не найден в файле")

            width = int(video_stream.get('width', 0))
            height = int(video_stream.get('height', 0))

            if width <= 0 or height <= 0:
                raise ValueError(f"Некорректные размеры видео: {width}x{height}")

            # Вычисляем crop для квадрата
            crop_size = min(width, height)
            x_offset = (width - crop_size) // 2
            y_offset = (height - crop_size) // 2

            # Строим фильтр: crop to square -> scale to target resolution
            filter_complex = (
                f"crop={crop_size}:{crop_size}:{x_offset}:{y_offset},"
                f"scale={self.target_resolution}:{self.target_resolution}:force_original_aspect_ratio=decrease,"
                f"pad={self.target_resolution}:{self.target_resolution}:(ow-iw)/2:(oh-ih)/2"
            )

            # Запускаем ffmpeg асинхронно
            await self._run_ffmpeg(
                input_path,
                output_path,
                filter_complex,
                duration
            )

            # Проверяем результат
            output_size = os.path.getsize(output_path)
            if self.max_size_bytes and output_size > self.max_size_bytes:
                raise ValueError(
                    f"Результат превышает лимит {self.max_size_mb} MB: {output_size / 1024 / 1024:.1f} MB"
                )

            # Получаем метаданные результата
            result_probe = await self._probe_video(output_path)
            result_stream = result_probe.get('streams', [{}])[0]

            metadata = {
                'duration': float(result_probe.get('format', {}).get('duration', 0)),
                'width': int(result_stream.get('width', 0)),
                'height': int(result_stream.get('height', 0)),
                'size_bytes': output_size,
                'codec': result_stream.get('codec_name', 'unknown')
            }

            logger.info(f"Конвертация завершена: {output_path} ({output_size / 1024:.1f} KB)")
            return output_path, metadata

        except Exception as e:
            # Удаляем временный файл при ошибке
            if os.path.exists(output_path):
                os.unlink(output_path)
            raise

    async def _probe_video(self, path: str) -> dict:
        """Получает информацию о видео через ffprobe."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: ffmpeg.probe(path))

    async def _run_ffmpeg(
        self,
        input_path: str,
        output_path: str,
        filter_complex: str,
        duration: float
    ) -> None:
        """Запускает ffmpeg для конвертации."""

        def _run():
            (
                ffmpeg
                .input(input_path)
                .output(
                    output_path,
                    vf=filter_complex,
                    vcodec='libx264',
                    acodec='aac',
                    audio_bitrate='128k',
                    crf=self.crf,
                    preset=self.preset,
                    pix_fmt='yuv420p',  # Для совместимости с Telegram
                    movflags='+faststart',  # Для стриминга
                    t=duration if duration > 0 else None  # Обрезаем по длительности исходника
                )
                .overwrite_output()
                .run(capture_stdout=True, capture_stderr=True, quiet=True)
            )

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _run)
        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
            logger.error(f"FFmpeg error: {stderr}")
            raise RuntimeError(f"Ошибка конвертации: {stderr}")

    def get_max_duration_for_size(self, bitrate_kbps: int = 2000) -> float:
        """Оценка максимальной длительности видео для заданного размера."""
        if not self.max_size_bytes:
            return float('inf')
        # Приблизительный расчет: size = bitrate * duration / 8
        return (self.max_size_bytes * 8) / (bitrate_kbps * 1000)