from __future__ import annotations

import argparse
import html
import io
import json
from cgi import FieldStorage
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional, Tuple
from zipfile import ZipFile, ZIP_DEFLATED

from favicon import render_head_links, try_load_asset

from .parser import parse_calendar_markdown, to_json_document


def render_form(
    initial_text: str = "",
    *,
    error: Optional[str] = None,
    year: int = 2025,
    timezone: str = "UTC-4",
    source: str = "markdown_import",
    filename: str = "calendar.json",
    files_info: Optional[List[str]] = None,
) -> bytes:
    head_links = render_head_links("    ")
    error_html = f"<div class='error'>⚠️ {html.escape(error)}</div>" if error else ""
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
      .hint {{
        font-size: 0.85rem;
        color: #555;
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
    </style>
  </head>
  <body>
    <h1>Takvim Dönüştürücü</h1>
    <p>ForexFactory tarzı markdown verisini JSON şemasına çevir.</p>
    {error_html}
    <form method='POST' enctype='multipart/form-data'>
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
        Markdown Dosyaları (opsiyonel)
        <input type='file' name='markdown_file' accept='.md,text/plain' multiple/>
        <span class='hint'>Dosya seçilmezse aşağıdaki metin kutusundaki içerik kullanılır; birden fazla dosya seçersen her biri için ayrı JSON indirilir.</span>
      </label>
      <label>
        Markdown İçeriği
        <textarea name='markdown'>{html.escape(initial_text)}</textarea>
      </label>
      <button type='submit'>JSON'a Dönüştür</button>
    </form>
  </body>
</html>"""
    return html_doc.encode("utf-8")


def parse_form(body: bytes, content_type: str, headers) -> Tuple[Dict[str, str], Dict[str, List[FieldStorage]]]:
    environ = {
        'REQUEST_METHOD': 'POST',
        'CONTENT_TYPE': content_type,
        'CONTENT_LENGTH': str(len(body)),
    }
    form = FieldStorage(
        fp=io.BytesIO(body),
        headers=headers,
        environ=environ,
        keep_blank_values=True,
    )
    field_values: Dict[str, List[str]] = {}
    file_values: Dict[str, List[FieldStorage]] = {}
    if form.list:
        for item in form.list:
            if item.filename:
                file_values.setdefault(item.name, []).append(item)
            else:
                field_values.setdefault(item.name, []).append(item.value)
    flat_fields = {k: v[-1] for k, v in field_values.items()}
    return flat_fields, file_values

def _sanitize_filename(name: str) -> str:
    cleaned = ''.join(ch for ch in name if ch.isalnum() or ch in ('-', '_', '.'))
    cleaned = cleaned or 'calendar.json'
    if '.' not in cleaned:
        cleaned += '.json'
    return cleaned


class CalendarHandler(BaseHTTPRequestHandler):
    form_defaults = {
        "markdown": "",
        "year": "2025",
        "timezone": "UTC-4",
        "source": "markdown_import",
        "filename": "calendar.json",
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
        field_data, file_data = parse_form(payload, content_type, self.headers)
        fields = {**self.form_defaults, **field_data}
        markdown = fields.get("markdown", "")

        file_items = file_data.get("markdown_file") or []
        outputs: List[Tuple[str, bytes]] = []

        try:
            year = int(fields.get("year", "2025") or 2025)
        except Exception:
            year = 2025
        timezone = fields.get("timezone", "UTC-4") or "UTC-4"
        source = fields.get("source", "markdown_import") or "markdown_import"
        filename = fields.get("filename", "calendar.json") or "calendar.json"

        try:
            if file_items:
                for file_item in file_items:
                    try:
                        file_item.file.seek(0)
                    except Exception:  # noqa: BLE001
                        pass
                    content = file_item.file.read().decode("utf-8", errors="replace")
                    days = parse_calendar_markdown(content, year=year)
                    document = to_json_document(
                        days,
                        year=year,
                        timezone=timezone,
                        source=source,
                    )
                    result_bytes = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
                    base = file_item.filename or "calendar.json"
                    safe_name = _sanitize_filename(base)
                    outputs.append((safe_name, result_bytes))

                if len(outputs) == 1:
                    name, result_bytes = outputs[0]
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                    self.send_header("Content-Length", str(len(result_bytes)))
                    self.end_headers()
                    self.wfile.write(result_bytes)
                    return

                buffer = io.BytesIO()
                with ZipFile(buffer, "w", ZIP_DEFLATED) as zf:
                    for name, data in outputs:
                        zf.writestr(name, data)
                zip_bytes = buffer.getvalue()
                zip_name = _sanitize_filename("calendar_bundle.zip")

                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{zip_name}"')
                self.send_header("Content-Length", str(len(zip_bytes)))
                self.end_headers()
                self.wfile.write(zip_bytes)
                return

            # Fallback to textarea content
            if not markdown.strip():
                raise ValueError("Markdown içeriği boş.")

            days = parse_calendar_markdown(markdown, year=year)
            document = to_json_document(
                days,
                year=year,
                timezone=timezone,
                source=source,
            )
            result_bytes = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
            safe_name = _sanitize_filename(filename)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
            self.send_header("Content-Length", str(len(result_bytes)))
            self.end_headers()
            self.wfile.write(result_bytes)
        except Exception as exc:  # noqa: BLE001
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                render_form(
                    markdown,
                    error=str(exc),
                    year=year,
                    timezone=timezone,
                    source=source,
                    filename=filename,
                )
            )

    def log_message(self, format, *args):  # noqa: A003
        pass


def run(host: str, port: int) -> None:
    server = HTTPServer((host, port), CalendarHandler)
    print(f"calendar_md web: http://{host}:{port}/")
    server.serve_forever()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="calendar_md.web",
        description="Markdown takvim -> JSON dönüştürücü web arayüzü",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Sunucu adresi")
    parser.add_argument("--port", type=int, default=2300, help="Port (varsayılan 2300)")
    args = parser.parse_args(argv)

    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
