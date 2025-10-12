from __future__ import annotations

from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse
import html
from typing import Dict

from favicon import render_head_links, try_load_asset


def build_html(app_links: Dict[str, Dict[str, str]]) -> bytes:
    cards = []
    for key, meta in app_links.items():
        title = meta.get("title", key)
        url = meta.get("url", "#")
        description = meta.get("description", "")
        cards.append(
            f"""
            <article class='card'>
              <h2>{html.escape(title)}</h2>
              <p>{html.escape(description)}</p>
              <a class='button' href='{html.escape(url)}' target='_blank' rel='noopener'>Uygulamayı Aç</a>
            </article>
            """
        )

    head_links = render_head_links("    ")
    page = f"""<!doctype html>
<html lang='tr'>
  <head>
    <meta charset='utf-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    {head_links}
    <title>Trading Araçları | Landing Page</title>
    <style>
      :root {{
        color-scheme: light dark;
        --bg: #f5f5f5;
        --fg: #1f1f1f;
        --card-bg: #ffffff;
        --border: #d9d9d9;
        --accent: #0f62fe;
      }}
      @media (prefers-color-scheme: dark) {{
        :root {{
          --bg: #121212;
          --fg: #f3f3f3;
          --card-bg: #1f1f1f;
          --border: #333333;
        }}
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        background: var(--bg);
        color: var(--fg);
        min-height: 100vh;
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 32px 16px;
      }}
      header {{
        max-width: 1200px;
        width: 100%;
        text-align: center;
        margin-bottom: 24px;
      }}
      header h1 {{ margin: 0 0 12px; font-size: 1.9rem; }}
      header p {{ margin: 0; line-height: 1.6; }}
      main {{
        display: grid;
        gap: 16px;
        width: 100%;
        max-width: 1200px;
      }}
      @media (min-width: 640px) {{
        main {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      }}
      @media (min-width: 1024px) {{
        main {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      }}
      @media (min-width: 1280px) {{
        main {{ grid-template-columns: repeat(5, minmax(0, 1fr)); }}
      }}
      .card {{
        background: var(--card-bg);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 20px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        gap: 12px;
        box-shadow: 0 4px 14px rgba(15, 17, 26, 0.08);
      }}
      .card h2 {{ margin: 0; font-size: 1.3rem; }}
      .card p {{ margin: 0; flex-grow: 1; line-height: 1.5; font-size: 0.95rem; }}
      .button {{
        align-self: flex-start;
        background: var(--accent);
        color: white;
        padding: 10px 16px;
        border-radius: 8px;
        text-decoration: none;
        font-weight: 600;
        transition: filter 150ms ease-in-out, transform 150ms ease-in-out;
      }}
      .button:hover {{
        filter: brightness(1.05);
        transform: translateY(-1px);
      }}
      footer {{
        margin-top: 32px;
        font-size: 0.85rem;
        opacity: 0.7;
      }}
    </style>
  </head>
  <body>
    <header>
      <h1>Trading Araçları</h1>
      <p>app48, app72, app80, app120 ve app321 arayüzlerine tek yerden erişin. Her kart ilgili modülü yeni sekmede açar.</p>
    </header>
    <main>
      {''.join(cards)}
    </main>
    <footer>
      Uygulamaları açmadan önce ilgili web servislerini çalıştırmayı unutmayın.
    </footer>
  </body>
</html>"""
    return page.encode("utf-8")


def make_handler(html_bytes: bytes):
    class LandingHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            asset = try_load_asset(self.path)
            if asset:
                payload, content_type = asset
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if self.path in {"/", "/index", "/index.html"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html_bytes)
            elif self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"ok")
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
        "app48": {
            "title": "app48",
            "url": args.app48_url,
            "description": "48 dakikalık mumlarla sayım, DC listesi ve 12→48 dönüştürücü.",
        },
        "app72": {
            "title": "app72",
            "url": args.app72_url,
            "description": "72 dakikalık sayım, DC analizi ve 12→72 dönüştürücü (7x12m).",
        },
        "app80": {
            "title": "app80",
            "url": args.app80_url,
            "description": "80 dakikalık sayım, DC analizi ve 20→80 dönüştürücü (4x20m).",
        },
        "app120": {
            "title": "app120",
            "url": args.app120_url,
            "description": "120 dakikalık sayım, DC analizi ve 60→120 dönüştürücü.",
        },
        "app321": {
            "title": "app321",
            "url": args.app321_url,
            "description": "60 dakikalık sayım araçları, DC listesi ve offset matrisi.",
        },
        "calendar_md": {
            "title": "Takvim Dönüştürücü",
            "url": args.calendar_url,
            "description": "ForexFactory markdown verisini JSON dosyasına çevir.",
        },
    }

    run(args.host, args.port, app_links)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
