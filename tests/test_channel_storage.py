"""A channel and its genuine invitation are one atomic setting."""
import json

import aiosqlite
import pytest

from db import Database
from repositories import ChannelRepo, SettingsRepo


async def test_setting_and_clear_keep_channel_invite_paired(tmp_path):
    database = Database(str(tmp_path / "channel.db"))
    await database.init()
    channel = ChannelRepo(database)
    settings = SettingsRepo(database)
    await channel.set_with_invite("-1001234567890", "https://t.me/+abcdef")
    assert await channel.get_link() == "-1001234567890"
    assert json.loads(await settings.get("channel_invite_link")) == {
        "channel": "-1001234567890", "url": "https://t.me/+abcdef",
    }
    await channel.set_link("@new_name")
    assert json.loads(await settings.get("channel_invite_link")) == {
        "channel": "@new_name", "url": "",
    }
    await channel.set_link("")
    assert await channel.get_link() == ""
    assert json.loads(await settings.get("channel_invite_link"))["url"] == ""


async def test_failed_invite_write_rolls_back_channel(tmp_path):
    database = Database(str(tmp_path / "rollback.db"))
    await database.init()
    channel = ChannelRepo(database)
    await channel.set_with_invite("@old_name", "")
    async with database.connect() as conn:
        await conn.execute("CREATE TRIGGER fail_invite BEFORE UPDATE ON bot_settings "
                           "WHEN NEW.key='channel_invite_link' "
                           "BEGIN SELECT RAISE(ABORT, 'write failure'); END")
        await conn.commit()
    with pytest.raises(aiosqlite.IntegrityError, match="write failure"):
        await channel.set_with_invite("@new_name", "https://t.me/+abcdef")
    assert await channel.get_link() == "@old_name"
