"""Semantic locators, ported from saidkick 1.x onto Playwright.

The vocabulary is unchanged from the extension era — it is the best thing 1.x
had. What changed is the resolver underneath: instead of a hand-rolled DOM scan
in a content script, each field maps onto Playwright's own engine, which brings
auto-waiting and leaf-most text matching for free.
"""

import re
from typing import Any

from pydantic import BaseModel
from playwright.async_api import Locator as PWLocator
from playwright.async_api import Page

from . import errors as E

#: Fields that select an element. Exactly one may be set, except for the
#: ``by_role`` + ``by_text`` pair, where ``by_text`` supplies the accessible name.
PRIMARY_FIELDS = ("css", "xpath", "by_text", "by_label", "by_placeholder", "by_role")


class Locator(BaseModel):
    """Shared locator fields for every selector-using operation."""

    css: str | None = None
    xpath: str | None = None
    by_text: str | None = None
    by_label: str | None = None
    by_placeholder: str | None = None
    by_role: str | None = None

    within_css: str | None = None
    nth: int | None = None
    exact: bool = False
    regex: bool = False
    #: Accepted for 1.x compatibility and ignored: Playwright pierces open
    #: shadow roots by default, so there is nothing to opt into.
    pierce_shadow: bool = False
    wait_ms: int = 0

    def primaries(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in PRIMARY_FIELDS if getattr(self, f) is not None}

    def describe(self) -> str:
        """A short, stable rendering used in error details."""
        parts = [f"{k}={v!r}" for k, v in self.primaries().items()]
        if self.within_css:
            parts.append(f"within_css={self.within_css!r}")
        if self.nth is not None:
            parts.append(f"nth={self.nth}")
        return " ".join(parts) or "<no locator>"


def _is_role_with_name(loc: Locator) -> bool:
    """by_role + by_text is one locator, not two: by_text is the accessible name."""
    return loc.by_role is not None and set(loc.primaries()) == {"by_role", "by_text"}


def validate_locator(loc: Locator, required: bool) -> None:
    if loc.exact and loc.regex:
        raise E.LocatorAmbiguous("exact and regex are mutually exclusive")

    count = len(loc.primaries())
    if count > 1 and not _is_role_with_name(loc):
        raise E.LocatorAmbiguous(
            f"specify exactly one locator, got {sorted(loc.primaries())}"
        )
    if required and count == 0:
        raise E.LocatorNotFound(
            "no locator: specify one of " + "/".join(PRIMARY_FIELDS)
        )


def _pattern(value: str, loc: Locator) -> Any:
    return re.compile(value) if loc.regex else value


def resolve(page: Page, loc: Locator) -> PWLocator:
    """Turn a Locator into a Playwright locator. Does not touch the page."""
    validate_locator(loc, required=True)

    root: Any = page.locator(loc.within_css) if loc.within_css else page

    if loc.by_role is not None:
        if loc.by_text is not None:
            found = root.get_by_role(loc.by_role, name=_pattern(loc.by_text, loc), exact=loc.exact)
        else:
            found = root.get_by_role(loc.by_role)
    elif loc.css is not None:
        found = root.locator(loc.css)
    elif loc.xpath is not None:
        found = root.locator(f"xpath={loc.xpath}")
    elif loc.by_text is not None:
        found = root.get_by_text(_pattern(loc.by_text, loc), exact=loc.exact)
    elif loc.by_label is not None:
        found = root.get_by_label(_pattern(loc.by_label, loc), exact=loc.exact)
    else:
        found = root.get_by_placeholder(_pattern(loc.by_placeholder, loc), exact=loc.exact)

    return found.nth(loc.nth) if loc.nth is not None else found
