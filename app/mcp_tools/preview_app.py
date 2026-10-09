"""The ui://rich-studio/preview page: render.js and its styles in one self-contained HTML for MCP hosts.

Hosts show the page in a sandbox without network access, so everything is inlined.
KaTeX fonts and upload:<name> media do not load there; formulas fall back to system fonts.
"""

import functools
from pathlib import Path

URI = "ui://rich-studio/preview"
STATIC = Path(__file__).resolve().parent.parent / "static"

STYLES = ("vendor/katex/katex.min.css", "tg.css")
SCRIPTS = ("vendor/katex/katex.min.js", "vendor/markdown-it.min.js", "render.js", "preview-app.js")

PAGE_CSS = """html,body{margin:0}body{background:transparent;font:14px system-ui,sans-serif}
#root{padding:8px}.note{margin:4px 8px;padding:6px 10px;border-radius:8px;white-space:pre-wrap}
.note.error{background:#fde8e8;color:#9b1c1c}.note.warning{background:#fdf6e3;color:#7a5b00}"""


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _script(code: str) -> str:
    # an inline script ends at the first "</script" whatever the JS around it means
    return "<script>" + code.replace("</script", "<\\/script") + "</script>"


@functools.cache
def html() -> str:
    styles = "".join(f"<style>{_read(n)}</style>" for n in STYLES)
    scripts = "".join(_script(_read(n)) for n in SCRIPTS)
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Rich Studio</title>{styles}<style>{PAGE_CSS}</style></head>"
        f'<body><div id="root" class="tg-preview"></div><div id="notes"></div>{scripts}</body></html>'
    )
