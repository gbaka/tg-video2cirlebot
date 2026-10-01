"""
Пользователи: создание, настройки, поиск, выгрузка
"""

from typing import Any

from repositories.base import Base
from timeutil import now


class _Unset:
    pass


_UNSET = _Unset()


class UserRepo(Base):
    async def get_or_create(
        self,
        user_id: int,
        username: str | None,
        first_name: str | None,
        default_language: str = "ru",
    ) -> dict:
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO users (user_id, username, first_name, language) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, "
                "first_name = excluded.first_name, last_seen = ?",
                (user_id, username, first_name, default_language, now()),
            )
            cur = await conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = await cur.fetchone()
            await conn.commit()
            if row is None:
                raise RuntimeError("Пользователь не найден сразу после вставки")
            return dict(row)
        finally:
            await conn.close()

    async def get(self, user_id: int) -> dict | None:
        conn = await self._conn()
        try:
            cur = await conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = await cur.fetchone()
            return dict(row) if row else None
        finally:
            await conn.close()

    async def set_language(self, user_id: int, lang: str) -> None:
        await self._set(user_id, "language", lang)

    async def set_quality(self, user_id: int, quality: int | None) -> None:
        await self._set(user_id, "quality", quality)

    async def set_video_options(
        self,
        user_id: int,
        *,
        crop_mode: str | None = None,
        trim_start: float | None = None,
        trim_duration: float | _Unset | None = _UNSET,
    ) -> None:
        """Update supplied options; reset_video_fragment clears a selected fragment."""
        fields: list[str] = []
        values: list[Any] = []
        for field, value in (
            ("crop_mode", crop_mode),
            ("trim_start", trim_start),
            ("trim_duration", trim_duration),
        ):
            if value is not _UNSET and (value is not None or field == "trim_duration"):
                fields.append(f"{field} = ?")
                values.append(value)
        if not fields:
            return
        conn = await self._conn()
        try:
            await conn.execute(
                f"UPDATE users SET {', '.join(fields)} WHERE user_id = ?", (*values, user_id)
            )
            await conn.commit()
        finally:
            await conn.close()

    async def reset_video_fragment(self, user_id: int) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "UPDATE users SET trim_start = 0, trim_duration = NULL WHERE user_id = ?",
                (user_id,),
            )
            await conn.commit()
        finally:
            await conn.close()

    async def _set(self, user_id: int, field: str, value: Any) -> None:
        conn = await self._conn()
        try:
            await conn.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
            await conn.commit()
        finally:
            await conn.close()

    async def count_all(self) -> int:
        return await self._scalar("SELECT COUNT(*) FROM users")

    async def count_since(self, since: str) -> int:
        return await self._scalar("SELECT COUNT(*) FROM users WHERE created_at >= ?", (since,))

    async def count_active_since(self, since: str) -> int:
        return await self._scalar("SELECT COUNT(*) FROM users WHERE last_seen >= ?", (since,))

    async def list_recent(self, limit: int = 20) -> list[dict]:
        conn = await self._conn()
        try:
            cur = await conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    @staticmethod
    def _build_filter(query: str) -> tuple[str, list]:
        """Фильтр для поиска: точный ID или LIKE по username/имени."""
        q = (query or "").strip().lstrip("@")
        if not q:
            return "", []
        if q.isascii() and q.isdigit():
            if len(q) > 19 or int(q) > 2**63 - 1:
                return "WHERE 1 = 0", []
            return "WHERE u.user_id = ?", [int(q)]
        like = f"%{q}%"
        return "WHERE u.username LIKE ? OR u.first_name LIKE ?", [like, like]

    async def search_page(self, offset: int, limit: int, query: str = "") -> tuple[list[dict], int]:
        """
        Страница пользователей с активным тарифом, без N+1.

        Returns:
            (строки с полем active_plan, общее количество по фильтру)
        """
        where, params = self._build_filter(query)

        conn = await self._conn()
        try:
            cur = await conn.execute(f"SELECT COUNT(*) FROM users u {where}", tuple(params))
            row = await cur.fetchone()
            total = int(row[0]) if row else 0

            # Подзапрос отдаёт активный тариф одним запросом (вместо запроса на каждого)
            sql = f"""
                SELECT u.*, (
                    SELECT s.plan FROM subscriptions s
                    WHERE s.user_id = u.user_id AND s.active = 1 AND s.expires_at > ?
                    ORDER BY s.expires_at DESC LIMIT 1
                ) AS active_plan
                FROM users u {where}
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?
            """
            cur = await conn.execute(sql, (now(), *params, limit, offset))
            return [dict(r) for r in await cur.fetchall()], total
        finally:
            await conn.close()

    async def export_page(self, offset: int, limit: int) -> list[dict]:
        """Страница пользователей с тарифом и счётчиками — для выгрузки в CSV."""
        conn = await self._conn()
        try:
            cur = await conn.execute(
                """
                SELECT u.user_id, u.username, u.first_name, u.language, u.quality,
                       u.created_at, u.last_seen,
                       (SELECT s.plan FROM subscriptions s
                         WHERE s.user_id = u.user_id AND s.active = 1 AND s.expires_at > ?
                         ORDER BY s.expires_at DESC LIMIT 1) AS active_plan,
                       (SELECT s.expires_at FROM subscriptions s
                         WHERE s.user_id = u.user_id AND s.active = 1 AND s.expires_at > ?
                         ORDER BY s.expires_at DESC LIMIT 1) AS expires_at,
                       (SELECT COUNT(*) FROM usage x
                         WHERE x.user_id = u.user_id AND x.status = 'ok') AS conv_ok,
                       (SELECT COUNT(*) FROM usage x
                         WHERE x.user_id = u.user_id AND x.status = 'error') AS conv_err
                FROM users u
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?
                """,
                (now(), now(), limit, offset),
            )
            return [dict(r) for r in await cur.fetchall()]
        finally:
            await conn.close()
