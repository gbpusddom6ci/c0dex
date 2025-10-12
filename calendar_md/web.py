from __future__ import annotations

import argparse
import html
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs

from favicon import render_head_links, try_load_asset

from .parser import parse_calendar_markdown, to_json_document


def render_form(
    initial_text: str = "",
    *,
    result: Optional[str] = None,
    error: Optional[str] = None,
    year: int = 2025,
    timezone: str = "UTC-4",
    source: str = "markdown_import",
) -> bytes:
    head_links = render_head_links("    ")
    result_html = ""
    if error:
        result_html = f"<div class='error'>⚠️ {html.escape(error)}</div>"
    elif result is not None:
        escaped_result = html.escape(result)
        result_html = f"""
        <section class='result'>
          <h3>Dönüştürülen JSON</h3>
          <textarea readonly rows='20'>{escaped_result}</textarea>
        </section>
        """

    html_doc = f"""<!doctype html>
<html lang='tr'>
  <head>
    <meta charset='utf-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    {head_links}
    <title>Takvim Dönüştürücü</title>
    <style>
      body {{
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        background: #f5f5f5;
        color: #1f1f1f;
        padding: 24px;
      }}
      h1 {{
        margin: 0 0 16px;
      }}
      form {{
        display: grid;
        gap: 12px;
        max-width: 960px;
      }}
      textarea {{
        width: 100%;
        min-height: 240px;
        font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
        font-size: 14px;
        padding: 12px;
        border-radius: 8px;
        border: 1px solid #d0d0d0;
        resize: vertical;
      }}
      .row {{
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
      }}
      label {{
        display: flex;
        flex-direction: column;
        font-weight: 600;
        font-size: 0.9rem;
      }}
      input[type='text'], input[type='number'] {{
        padding: 8px;
        border-radius: 6px;
        border: 1px solid #d0d0d0;
        font-size: 0.95rem;
      }}
      button {{
        align-self: flex-start;
        padding: 10px 18px;
        background: #0f62fe;
        border: none;
        border-radius: 8px;
        color: white;
        font-weight: 600;
        cursor: pointer;
      }}
      .error {{
        background: #ffe8e8;
        border: 1px solid #ff9b9b;
        color: #a60000;
        padding: 12px;
        border-radius: 8px;
        margin-bottom: 16px;
        max-width: 960px;
      }}
      .result textarea {{
        background: #1f1f1f;
        color: #f5f5f5;
      }}
    </style>
  </head>
  <body>
    <h1>Takvim Dönüştürücü</h1>
    <p>ForexFactory tarzı markdown verisini JSON şemasına çevir.</p>
    {result_html}
    <form method='POST'>
      <div class='row'>
        <label>
          Yıl
          <input type='number' name='year' value='{year}' min='2000' max='2100'/>
        </label>
        <label>
          Timezone
          <input type='text' name='timezone' value='{html.escape(timezone)}'/>
        </label>
        <label>
          Kaynak
          <input type='text' name='source' value='{html.escape(source)}'/>
        </label>
      </div>
      <label>
        Markdown İçeriği
        <textarea name='markdown' required>{html.escape(initial_text)}</textarea>
      </label>
      <button type='submit'>JSON'a Dönüştür</button>
    </form>
  </body>
</html>"""
    return html_doc.encode("utf-8")


def parse_form(body: bytes, content_type: str) -> Dict[str, str]:
    if content_type.startswith("application/x-www-form-urlencoded"):
        parsed = parse_qs(body.decode("utf-8", errors="replace"))
        return {k: v[0] for k, v in parsed.items() if v}
    return {}


class CalendarHandler(BaseHTTPRequestHandler):
    form_defaults = {
        "markdown": "",
        "year": "2025",
        "timezone": "UTC-4",
        "source": "markdown_import",
    }

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
            self.wfile.write(render_form())
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_error(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        content_type = self.headers.get("Content-Type", "")
        payload = self.rfile.read(length) if length > 0 else b""
        fields = {**self.form_defaults, **parse_form(payload, content_type)}
        markdown = fields.get("markdown", "")

        error = None
        result = None
        try:
            year = int(fields.get("year", "2025") or 2025)
            timezone = fields.get("timezone", "UTC-4") or "UTC-4"
            source = fields.get("source", "markdown_import") or "markdown_import"

            days = parse_calendar_markdown(markdown, year=year)
            document = to_json_document(
                days,
                year=year,
                timezone=timezone,
                source=source,
            )
            result = json.dumps(document, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            render_form(
                markdown,
                result=result,
                error=error,
                year=int(fields.get("year", "2025") or 2025),
                timezone=fields.get("timezone", "UTC-4") or "UTC-4",
                source=fields.get("source", "markdown_import") or "markdown_import",
            )
        )

    def log_message(self, format, *args):  # noqa: A003
        pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="calendar_md.web",
        description="Markdown takvim -> JSON dönüştürücü web arayüzü",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Sunucu adresi")
    parser.add_argument("--port", type=int, default=2300, help="Port (varsayılan 2300)")
    args = parser.parse_args(argv)

    server = HTTPServer((args.host, args.port), CalendarHandler)
    print(f"calendar_md web: http://{args.host}:{args.port}/")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
