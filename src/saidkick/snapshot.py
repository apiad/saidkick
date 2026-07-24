"""Page snapshots for agents.

``aria`` is the default and the one that matters. It is compact enough to fit
in a context window, and every node carries a role and accessible name that map
directly onto a ``by_role`` / ``by_label`` / ``by_text`` locator — so an agent
that has read the snapshot already knows how to address everything it can see.

Implementation note: ``page.accessibility`` was removed from Playwright and
``page._snapshot_for_ai()`` does not exist. ``locator.aria_snapshot()`` is the
API, and it returns a YAML-shaped string:

    - heading "Fixture" [level=1]
    - textbox "Username":
      - /placeholder: your name
    - button "Send"
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ManagedTab

MODES = ("aria", "text", "html")


async def snapshot(
    tab: "ManagedTab", mode: str = "aria", within_css: str | None = None
) -> str:
    tab.context.touch()
    root = tab.page.locator(within_css) if within_css else tab.page.locator("body")
    if mode == "aria":
        return await root.aria_snapshot()
    if mode == "text":
        return await root.inner_text()
    if mode == "html":
        return await root.inner_html()
    raise ValueError(f"unknown snapshot mode: {mode!r} (expected one of {MODES})")
