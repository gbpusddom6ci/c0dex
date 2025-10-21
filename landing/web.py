from __future__ import annotations

import argparse
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, Optional, Tuple

from favicon import render_head_links, try_load_asset

_BASE_DIR = Path(__file__).resolve().parent
_PHOTO_DIR = _BASE_DIR.parent / "photos"

_LOCAL_ASSET_META = {
    "/assets/bg_stars.gif": (_BASE_DIR / "bg_stars.gif", "image/gif"),
    "/assets/lobotomy.jpg": (_PHOTO_DIR / "lobotomy.jpg", "image/jpeg"),
    "/assets/kan.jpeg": (_PHOTO_DIR / "kan.jpeg", "image/jpeg"),
    "/assets/kits.jpg": (_PHOTO_DIR / "kits.jpg", "image/jpeg"),
    "/assets/penguins.jpg": (_PHOTO_DIR / "penguins.jpg", "image/jpeg"),
    "/assets/romantizma.png": (_PHOTO_DIR / "romantizma.png", "image/png"),
    "/assets/silkroad.jpg": (_PHOTO_DIR / "silkroad.jpg", "image/jpeg"),
    "/assets/suicide.png": (_PHOTO_DIR / "suicide.png", "image/png"),
}

_LOCAL_ASSETS: Dict[str, Tuple[bytes, str]] = {}
for route, (fs_path, content_type) in _LOCAL_ASSET_META.items():
    if not fs_path.is_file():  # pragma: no cover - defensive guard
        raise FileNotFoundError(f"Yerel asset bulunamadı: {fs_path}")
    _LOCAL_ASSETS[route] = (fs_path.read_bytes(), content_type)

_IMAGE_SOURCES = {
    "logo": "/assets/lobotomy.jpg",
    "app48": "/assets/kan.jpeg",
    "app72": "/assets/kits.jpg",
    "app80": "/assets/penguins.jpg",
    "app120": "/assets/romantizma.png",
    "app321": "/assets/silkroad.jpg",
    "calendar_md": "/assets/suicide.png",
}

_PLANET_LAYOUT = [
    ("app48", "planet planet--app48"),
    ("app72", "planet planet--app72"),
    ("app80", "planet planet--app80"),
    ("app120", "planet planet--app120"),
    ("app321", "planet planet--app321"),
    ("calendar_md", "planet planet--calendar"),
]


def _normalize_path(path: str) -> str:
    root = path.split("?", 1)[0]
    if not root.startswith("/"):
        root = "/" + root
    return root


def try_load_local_asset(path: str) -> Optional[Tuple[bytes, str]]:
    return _LOCAL_ASSETS.get(_normalize_path(path))


def build_html(app_links: Dict[str, Dict[str, str]]) -> bytes:
    planets = []
    for key, classes in _PLANET_LAYOUT:
        meta = app_links.get(key)
        if not meta:
            continue
        src = _IMAGE_SOURCES.get(key)
        if not src:
            continue
        url = meta.get("url", "#")
        title = meta.get("title", key)
        planets.append(
            f"<a class='{classes}' href='{html.escape(url)}' target='_blank' rel='noopener noreferrer'>"
            f"<img src='{src}' alt='{html.escape(title)}'>"
            "</a>"
        )

    head_links = render_head_links("    ")
    page = f"""<!doctype html>
<html lang='tr'>
  <head>
    <meta charset='utf-8'>
    {head_links}
    <title>malw.ooo</title>
    <style>
      html, body {{
        margin: 0;
        padding: 0;
        min-height: 100%;
        font-family: "Comic Sans MS", "Arial", sans-serif;
        color: #ff0000;
      }}
      body {{
        display: flex;
        justify-content: center;
        align-items: center;
      }}
      .portal {{
        position: relative;
        width: 540px;
        height: 540px;
        margin: 60px auto;
      }}
      .portal img {{
        border: 0;
      }}
      .portal .logo {{
        position: absolute;
        top: 50%;
        left: 50%;
        width: 165px;
        max-width: 40%;
        transform: translate(-50%, -50%);
        box-shadow: 0 0 25px rgba(255, 255, 255, 0.25);
      }}
      .planet {{
        position: absolute;
        width: 130px;
        transform: translate(-50%, -50%);
      }}
      .planet img {{
        display: block;
        width: 100%;
        height: auto;
      }}
      .planet--app48 {{ top: 6%; left: 50%; }}
      .planet--app72 {{ top: 24%; left: 90%; }}
      .planet--app80 {{ top: 70%; left: 92%; }}
      .planet--app120 {{ top: 92%; left: 52%; }}
      .planet--app321 {{ top: 70%; left: 10%; }}
      .planet--calendar {{ top: 24%; left: 12%; }}
      @media (max-width: 640px) {{
        body {{ padding: 40px 0; }}
        .portal {{
          width: 320px;
          height: 480px;
        }}
        .portal .logo {{
          width: 150px;
        }}
        .planet {{
          width: 90px;
        }}
        .planet--app48 {{ top: 8%; left: 50%; }}
        .planet--app72 {{ top: 28%; left: 90%; }}
        .planet--app80 {{ top: 72%; left: 92%; }}
        .planet--app120 {{ top: 94%; left: 52%; }}
        .planet--app321 {{ top: 72%; left: 10%; }}
        .planet--calendar {{ top: 28%; left: 12%; }}
      }}
    </style>
  </head>
  <body bgcolor='#000000' background='/assets/bg_stars.gif' text='#ff0000' link='#ff4c4c' vlink='#ff4c4c' alink='#ff4c4c'>
    <center>
      <div class='portal'>
        <img class='logo' src='{_IMAGE_SOURCES["logo"]}' alt='malw.ooo'>
        {''.join(planets)}
      </div>
      <div class='footer'>
        <br><br><br>
        <font size='-1' color='#ff0000' face='Times New Roman, serif'>marketmalware</font>
      </div>
    </center>
  </body>
</html>"""
    return page.encode("utf-8")


def make_handler(html_bytes: bytes):
    class LandingHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            normalized = _normalize_path(self.path)
            local_asset = try_load_local_asset(normalized)
            if local_asset:
                payload, content_type = local_asset
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            asset = try_load_asset(self.path)
            if asset:
                payload, content_type = asset
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            if normalized in {"/", "/index", "/index.html"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)
            elif normalized == "/health":
                payload = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_error(404, "Not Found")

        def log_message(self, format, *args):  # noqa: A003
            pass

    return LandingHandler


def run(host: str, port: int, app_links: Dict[str, Dict[str, str]]) -> None:
    html_bytes = build_html(app_links)
    handler_cls = make_handler(html_bytes)
    server = HTTPServer((host, port), handler_cls)
    print(f"landing page: http://{host}:{port}/")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="landing.web", description="Basit landing page")
    parser.add_argument("--host", default="127.0.0.1", help="Sunucu adresi (vars: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2000, help="Port (vars: 2000)")
    parser.add_argument(
        "--app48-url",
        default="http://127.0.0.1:2020/",
        help="app48 web arayüzü için URL",
    )
    parser.add_argument(
        "--app321-url",
        default="http://127.0.0.1:2019/",
        help="app321 web arayüzü için URL",
    )
    parser.add_argument(
        "--app72-url",
        default="http://127.0.0.1:2172/",
        help="app72 web arayüzü için URL",
    )
    parser.add_argument(
        "--app80-url",
        default="http://127.0.0.1:2180/",
        help="app80 web arayüzü için URL",
    )
    parser.add_argument(
        "--app120-url",
        default="http://127.0.0.1:2120/",
        help="app120 web arayüzü için URL",
    )
    parser.add_argument(
        "--calendar-url",
        default="http://127.0.0.1:2300/",
        help="Takvim dönüştürücü arayüzü için URL",
    )
    args = parser.parse_args(argv)

    app_links = {
        "app48": {"title": "app48", "url": args.app48_url},
        "app72": {"title": "app72", "url": args.app72_url},
        "app80": {"title": "app80", "url": args.app80_url},
        "app120": {"title": "app120", "url": args.app120_url},
        "app321": {"title": "app321", "url": args.app321_url},
        "calendar_md": {"title": "Takvim Dönüştürücü", "url": args.calendar_url},
    }

    run(args.host, args.port, app_links)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
