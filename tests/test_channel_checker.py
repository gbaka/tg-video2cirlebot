"""Channel IDs, membership and safe invitation links."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from channel_checker import ChannelChecker


@pytest.mark.parametrize("link,expected", [
    ("https://t.me/c/1234567890", -1001234567890),
    ("1234567890", -1001234567890),
    ("https://t.me/c/997852516352", -1997852516352),
    ("-1001234567890", -1001234567890),
    ("-1234", -1234),
    ("@valid_name", "@valid_name"),
    ("https://t.me/valid_name", "@valid_name"),
])
def test_channel_ids(link, expected):
    assert ChannelChecker(None, link).chat_id == expected


@pytest.mark.parametrize("link", [
    "https://evil.test/t.me/channel", "https://t.me.evil.test/channel",
    "https://t.me/", "@", "@bad name", "@bad<name>",
    "https://t.me/c/0", "https://t.me/c/-123", "https://t.me/c/997852516353",
    "https://t.me/c/123/4", "https://t.me/valid_name?x=y", "https://t.me/+abcdef",
    "https://user@t.me/valid_name", "https://t.me:443/valid_name", "https://t.me/valid_name#x",
    "0", "997852516353", "-1000000000000", "-1997852516353", "+123", "1_234",
])
def test_reject_malformed_identifiers(link):
    assert ChannelChecker(None, link).chat_id is None


async def test_restricted_actual_member_is_accepted():
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(
        status="restricted", is_member=True,
    )))
    assert await ChannelChecker(bot, "@valid_name").is_member(7)


def test_private_chat_never_fabricates_invitation():
    assert ChannelChecker(None, "-1001234567890").get_join_url() == ""


async def test_private_invitation_lookup_does_not_create_links():
    bot = SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(invite_link=None)),
        create_chat_invite_link=AsyncMock(),
    )
    checker = ChannelChecker(bot, "-1001234567890")
    assert await checker.get_chat_invite_link() is None
    bot.create_chat_invite_link.assert_not_awaited()

