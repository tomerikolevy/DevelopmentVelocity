"""Exports the velocity page as one self-contained, read-only HTML file."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import WEB_DIR, Config
from .server import dashboard_data
from .store import Store


def export_static(store: Store, cfg: Config, output: str) -> Path:
    html = (WEB_DIR / "index.html").read_text()
    css = (WEB_DIR / "common.css").read_text()
    data = dashboard_data(store, cfg)
    data["exported_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    # "</" inside inline scripts would end the <script> tag early.
    data_js = "window.STATIC_DATA = " + json.dumps(data).replace("</", "<\\/") + ";"
    scripts = "\n".join(
        f"<script>\n{(WEB_DIR / name).read_text().replace('</script', '<\\/script')}\n</script>"
        for name in ("charts.js", "dashboard.js")
    )
    html = html.replace('<link rel="stylesheet" href="/static/common.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="/static/charts.js"></script>\n<script src="/static/dashboard.js"></script>',
                        f"<script>{data_js}</script>\n{scripts}")
    html = html.replace('<a href="/" class="active">Velocity</a>', '<a class="active">Velocity</a>')
    out = Path(output)
    out.write_text(html)
    return out
