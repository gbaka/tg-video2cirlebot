"""Persistent Telegram file IDs, keyed by source identity and the entire encode recipe."""

import hashlib
import json
from typing import Any

from repositories.base import Base
from video_converter import VideoConverter


def make_cache_key(file_unique_id: str, **options: Any) -> str:
    recipe = {
        "version": 2,
        "fps": VideoConverter.FPS,
        "maxrate": VideoConverter.MAXRATE,
        "bufsize": VideoConverter.BUFSIZE,
        "threads": VideoConverter.THREADS,
        "filter_threads": VideoConverter.THREADS,
        "filter_complex_threads": VideoConverter.THREADS,
        "source": file_unique_id,
        "codec": "libx264",
        "audio": "aac128k",
        "pixel_format": "yuv420p",
        "autorotate": True,
        "faststart": True,
        **options,
    }
    for name in ("trim_start", "trim_duration"):
        if recipe.get(name) is not None:
            recipe[name] = float(recipe[name])
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, allow_nan=False).encode()).hexdigest()


class ConversionCacheRepo(Base):
    async def get_record(self, cache_key: str) -> dict[str, Any] | None:
        """Return validation metadata; legacy rows deliberately contain NULLs."""
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT file_id,source_size,source_duration,output_duration "
                "FROM conversion_cache WHERE cache_key=?",
                (cache_key,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            return dict(
                zip(
                    ("file_id", "source_size", "source_duration", "output_duration"),
                    row,
                    strict=True,
                )
            )
        finally:
            await conn.close()

    async def get(self, cache_key: str) -> str | None:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT file_id FROM conversion_cache WHERE cache_key=?", (cache_key,)
            )
            row = await cur.fetchone()
            return str(row[0]) if row else None
        finally:
            await conn.close()

    async def put(
        self,
        cache_key: str,
        file_id: str,
        *,
        source_size: int | None = None,
        source_duration: float | None = None,
        output_duration: float | None = None,
    ) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO conversion_cache(cache_key,file_id,source_size,source_duration,"
                "output_duration,created_at) VALUES(?,?,?,?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(cache_key) DO UPDATE SET file_id=excluded.file_id,"
                "source_size=excluded.source_size,source_duration=excluded.source_duration,"
                "output_duration=excluded.output_duration,created_at=excluded.created_at",
                (cache_key, file_id, source_size, source_duration, output_duration),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def delete(self, cache_key: str) -> None:
        conn = await self._conn()
        try:
            await conn.execute("DELETE FROM conversion_cache WHERE cache_key=?", (cache_key,))
            await conn.commit()
        finally:
            await conn.close()
