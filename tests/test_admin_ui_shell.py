from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from admin.ui_shell import render_index_html

ROOT = Path(__file__).resolve().parents[1]


def test_ui_shell_replaces_external_animation_dependency_and_injects_local_assets():
    source = """<!doctype html>
<html>
<head>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/animate.css/4.1.1/animate.min.css"/>
</head>
<body><main>admin</main></body>
</html>
"""

    rendered = render_index_html(source)

    assert "cdnjs.cloudflare.com" not in rendered
    assert rendered.count("/admin-ui.css?v=2") == 1
    assert rendered.count("/admin-ui.js?v=2") == 1
    assert rendered.index("/admin-ui.css?v=2") < rendered.index("</head>")
    assert rendered.index("/admin-ui.js?v=2") < rendered.index("</body>")


def test_ui_shell_is_idempotent():
    source = "<html><head></head><body>admin</body></html>"
    once = render_index_html(source)
    twice = render_index_html(once)

    assert twice.count("/admin-ui.css?v=2") == 1
    assert twice.count("/admin-ui.js?v=2") == 1


def test_admin_ui_assets_cover_responsive_accessible_behaviour():
    css = (ROOT / "admin" / "ui.css").read_text(encoding="utf-8")
    js = (ROOT / "admin" / "ui.js").read_text(encoding="utf-8")

    assert "@media (max-width: 900px)" in css
    assert "table.admin-responsive-table" in css
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert "admin-mobile-nav-open" in css

    assert "aria-current" in js
    assert "aria-modal" in js
    assert "admin-responsive-table" in js
    assert "Ctrl" not in js  # shortcuts are keyboard-event based, not OS-specific labels
    assert "event.metaKey" in js
    assert "event.ctrlKey" in js


def test_runtime_launchers_use_enhanced_admin_app():
    container_launcher = (ROOT / "container-entrypoint.sh").read_text(encoding="utf-8")

    assert "admin.ui_app:app" in container_launcher
    assert "admin.app:app" not in container_launcher
    assert not (ROOT / "admin.bat").exists()
    assert not (ROOT / "daily.bat").exists()
    assert not (ROOT / "hh.bat").exists()


def test_enhanced_admin_app_imports_with_single_root_route():
    code = """
from admin.ui_app import app
root = [r for r in app.router.routes if getattr(r, 'path', None) == '/' and 'GET' in (getattr(r, 'methods', None) or set())]
css = [r for r in app.router.routes if getattr(r, 'path', None) == '/admin-ui.css']
js = [r for r in app.router.routes if getattr(r, 'path', None) == '/admin-ui.js']
assert len(root) == 1, len(root)
assert len(css) == 1, len(css)
assert len(js) == 1, len(js)
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
