"""In-page attention overlay: the page asks for help on its own behalf.

This is channel three of the announcement (terminal dashboard, the agent's own
words, the page itself). When the browser is headful, the tab that needs help
raises itself, shows a banner, pulses its border, and marks its title and
favicon so it is findable in a buried window.

**The overlay must be invisible to the agent.** If it is not, the next
accessibility snapshot contains saidkick's own banner and the agent tries to
interact with it instead of the page. Three things make that true, and all
three are asserted by tests:

1. ``aria-hidden="true"`` plus ``role="presentation"`` on the host, which makes
   Playwright's ``aria_snapshot()`` skip the subtree entirely;
2. a **closed** shadow root, so page scripts and ``querySelector`` cannot reach
   the contents;
3. ``pointer-events: none``, so it never intercepts a click.
"""

import logging
from typing import TYPE_CHECKING

from playwright.async_api import Error as PWError

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ManagedTab

log = logging.getLogger("saidkick.overlay")

HOST_ID = "saidkick-attention"
TITLE_PREFIX = "⚠ saidkick needs you — "

_SHOW_JS = """
(reason) => {
  const HOST_ID = 'saidkick-attention';
  if (document.getElementById(HOST_ID)) return;   // idempotent

  window.__saidkickPrev = {
    title: document.title,
    favicon: (document.querySelector("link[rel*='icon']") || {}).href || null,
  };

  const host = document.createElement('div');
  host.id = HOST_ID;
  host.setAttribute('aria-hidden', 'true');
  host.setAttribute('role', 'presentation');
  host.style.cssText =
    'position:fixed;inset:0;z-index:2147483647;pointer-events:none';

  const root = host.attachShadow({mode: 'closed'});
  root.innerHTML = `
    <style>
      @keyframes sk-pulse {
        0%,100% { box-shadow: inset 0 0 0 4px rgba(239,68,68,.95); }
        50%     { box-shadow: inset 0 0 0 4px rgba(239,68,68,.35); }
      }
      .ring { position:fixed; inset:0; animation: sk-pulse 1.4s ease-in-out infinite; }
      .banner {
        position:fixed; top:0; left:0; right:0;
        background:#ef4444; color:#fff; padding:10px 16px;
        font:600 14px/1.4 system-ui,sans-serif; text-align:center;
      }
    </style>
    <div class="ring"></div>
    <div class="banner"></div>`;
  root.querySelector('.banner').textContent = 'saidkick needs you: ' + reason;
  document.documentElement.appendChild(host);

  document.title = '\\u26a0 saidkick needs you \\u2014 ' + window.__saidkickPrev.title;
  let link = document.querySelector("link[rel*='icon']");
  if (!link) {
    link = document.createElement('link');
    link.rel = 'icon';
    document.head.appendChild(link);
  }
  link.href =
    'data:image/svg+xml,' +
    encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">' +
      '<circle cx="8" cy="8" r="7" fill="%23ef4444"/></svg>');
}
"""

_HIDE_JS = """
() => {
  const host = document.getElementById('saidkick-attention');
  if (host) host.remove();
  const prev = window.__saidkickPrev;
  if (prev) {
    document.title = prev.title;
    const link = document.querySelector("link[rel*='icon']");
    if (link) {
      if (prev.favicon) link.href = prev.favicon;
      else link.remove();
    }
    delete window.__saidkickPrev;
  }
}
"""


async def show(tab: "ManagedTab", reason: str) -> None:
    """Raise the tab and mark it as needing attention. Idempotent."""
    await tab.page.evaluate(_SHOW_JS, reason)
    try:
        await tab.page.bring_to_front()
    except PWError as exc:  # pragma: no cover - no-op in headless
        log.debug("bring_to_front unavailable: %s", exc)


async def hide(tab: "ManagedTab") -> None:
    """Remove the overlay and restore title and favicon. Safe without a prior show."""
    await tab.page.evaluate(_HIDE_JS)
