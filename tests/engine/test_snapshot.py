import pytest

from saidkick.snapshot import snapshot

pytestmark = pytest.mark.browser


async def test_aria_snapshot_names_controls(tab):
    s = await snapshot(tab, mode="aria")
    assert 'button "Send"' in s
    assert 'textbox "Username"' in s


async def test_aria_snapshot_is_much_smaller_than_html(tab):
    assert len(await snapshot(tab, mode="aria")) < len(await snapshot(tab, mode="html"))


async def test_within_css_scopes_snapshot(tab):
    s = await snapshot(tab, mode="aria", within_css="#f")
    assert 'button "Send"' in s
    assert "edit me" not in s


async def test_text_mode(tab):
    assert "edit me" in await snapshot(tab, mode="text")


async def test_bad_mode_raises(tab):
    with pytest.raises(ValueError):
        await snapshot(tab, mode="dom")
