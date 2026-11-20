from __future__ import annotations

import re

_UI_STYLESHEET = '<link rel="stylesheet" href="/admin-ui.css?v=3"/>'
_UI_SCRIPT = '<script src="/admin-ui.js?v=3" defer></script>'
_ANIMATE_CDN_RE = re.compile(
    r"\s*<link\s+rel=[\"']stylesheet[\"']\s+href=[\"']"
    r"https://cdnjs\.cloudflare\.com/ajax/libs/animate\.css/[^\"']+/animate\.min\.css"
    r"[\"']\s*/?>",
    flags=re.IGNORECASE,
)


def render_index_html(index_html: str) -> str:
    """Inject the local admin UI layer into the legacy single-file frontend.

    The original admin remains the source of page markup and business behaviour.
    Keeping the visual/accessibility layer in separate assets lets us improve the
    interface without duplicating or forking the 140 KB inline application.
    """
    html = _ANIMATE_CDN_RE.sub("", index_html)

    if "/admin-ui.css" not in html:
        if "</head>" not in html:
            raise ValueError("admin index is missing </head>")
        html = html.replace("</head>", f"{_UI_STYLESHEET}\n</head>", 1)

    if "/admin-ui.js" not in html:
        if "</body>" not in html:
            raise ValueError("admin index is missing </body>")
        html = html.replace("</body>", f"{_UI_SCRIPT}\n</body>", 1)

    return html
