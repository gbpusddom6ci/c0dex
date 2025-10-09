from __future__ import annotations

import argparse
import socket
import threading
import time
import re
from dataclasses import dataclass
from http import client
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Iterable, List, Tuple
from urllib.parse import urlsplit

from landing.web import build_html
from app48.web import run as run_app48
from app72.web import run as run_app72
from app80.web import run as run_app80
from app120.web import run as run_app120
from app321.web import run as run_app321


@dataclass(frozen=True)
class Backend:
    name: str
    host: str
    port: int
    prefix: str
    description: str

    def normalize_prefix(self) -> str:
        prefix = self.prefix
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        if prefix != "/":
            prefix = prefix.rstrip("/")
        return prefix

    def match(self, request_path: str) -> Tuple[bool, str]:
        prefix = self.normalize_prefix()
        parsed = urlsplit(request_path)  # only path+query fragments
        path = parsed.path
        if prefix == "/":
            sub_path = path or "/"
        else:
            if path == prefix:
                sub_path = "/"
            elif path.startswith(prefix + "/"):
                sub_path = path[len(prefix):]
            else:
                return False, ""
        if not sub_path.startswith("/"):
            sub_path = "/" + sub_path
        if parsed.query:
            sub_path = f"{sub_path}?{parsed.query}"
        return True, sub_path


def wait_for_port(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"Backend {host}:{port} başlatılamadı")


def rewrite_html_paths(body: bytes, prefix: str) -> bytes:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body

    normalized_prefix = prefix.rstrip("/")
    if not normalized_prefix:
        return body

    def repl(match: re.Match[str]) -> str:
        attr = match.group(1)
        quote = match.group(2)
        path = match.group(3)
        # path always starts with /
        new_path = normalized_prefix + path
        new_path = re.sub(r"//+", "/", new_path)
        if not new_path.startswith("/"):
            new_path = "/" + new_path
        return f"{attr}={quote}{new_path}{quote}"

    pattern = r"(href|action)=(['\"])(/[^'\"]*)\2"
    rewritten = re.sub(pattern, repl, text)
    return rewritten.encode("utf-8")


def strip_hop_headers(headers: Iterable[Tuple[str, str]]) -> List[Tuple[str, str]]:
    hop_by_hop = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}
    return [(k, v) for k, v in headers if k.lower() not in hop_by_hop]


def make_handler(backends: List[Backend], landing_bytes: bytes):
    class UnifiedHandler(BaseHTTPRequestHandler):
        server_version = "CandlesUnified/1.0"

        def _serve_landing(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(landing_bytes)))
            self.end_headers()
            self.wfile.write(landing_bytes)

        def _serve_health(self) -> None:
            payload = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in {"/", "/index", "/index.html"}:
                self._serve_landing()
                return
            if self.path == "/health":
                self._serve_health()
                return
            for backend in backends:
                matched, sub_path = backend.match(self.path)
                if matched:
                    self._proxy(backend, sub_path)
                    return
            self.send_error(404, "Not Found")

        def do_POST(self) -> None:  # noqa: N802
            for backend in backends:
                matched, sub_path = backend.match(self.path)
                if matched:
                    self._proxy(backend, sub_path)
                    return
            self.send_error(404, "Not Found")

        def _proxy(self, backend: Backend, sub_path: str) -> None:
            content_length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(content_length) if content_length > 0 else None

            headers = {k: v for k, v in self.headers.items()}
            headers.pop("Accept-Encoding", None)
            headers["Host"] = f"{backend.host}:{backend.port}"
            headers["Connection"] = "close"
            if body is None:
                headers.pop("Content-Length", None)
            else:
                headers["Content-Length"] = str(len(body))

            conn = client.HTTPConnection(backend.host, backend.port, timeout=15)
            try:
                conn.request(self.command, sub_path, body=body, headers=headers)
                resp = conn.getresponse()
                status = resp.status
                reason = resp.reason
                resp_body = resp.read()
                resp_headers = strip_hop_headers(resp.getheaders())
                content_type = next((v for k, v in resp_headers if k.lower() == "content-type"), "")
                proxied_body = resp_body
                if "text/html" in content_type:
                    proxied_body = rewrite_html_paths(resp_body, backend.normalize_prefix())
                self.send_response(status, reason)
                for header, value in resp_headers:
                    if header.lower() == "content-length":
                        continue
                    self.send_header(header, value)
                self.send_header("Content-Length", str(len(proxied_body)))
                self.end_headers()
                self.wfile.write(proxied_body)
            finally:
                conn.close()

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            pass

    return UnifiedHandler


def start_backend_thread(name: str, target, host: str, port: int) -> threading.Thread:
    thread = threading.Thread(target=target, args=(host, port), name=f"{name}-server", daemon=True)
    thread.start()
    wait_for_port(host, port)
    return thread


def run(host: str, port: int, backend_host: str, app48_port: int, app72_port: int, app80_port: int, app120_port: int, app321_port: int) -> None:
    backends = [
        Backend(name="app48", host=backend_host, port=app48_port, prefix="/app48", description="48 dakikalık mum sayımı ve dönüştürücü"),
        Backend(name="app72", host=backend_host, port=app72_port, prefix="/app72", description="72 dakikalık sayım ve 12→72 dönüştürücü (7x12m)"),
        Backend(name="app80", host=backend_host, port=app80_port, prefix="/app80", description="80 dakikalık sayım ve 20→80 dönüştürücü (4x20m)"),
        Backend(name="app120", host=backend_host, port=app120_port, prefix="/app120", description=""),
        Backend(name="app321", host=backend_host, port=app321_port, prefix="/app321", description="60 dakikalık sayım araçları"),
    ]

    start_backend_thread("app48", run_app48, backend_host, app48_port)
    start_backend_thread("app72", run_app72, backend_host, app72_port)
    start_backend_thread("app80", run_app80, backend_host, app80_port)
    start_backend_thread("app120", run_app120, backend_host, app120_port)
    start_backend_thread("app321", run_app321, backend_host, app321_port)

    app_links = {
        backend.name: {
            "title": backend.name,
            "url": backend.normalize_prefix() + "/",
            "description": backend.description,
        }
        for backend in backends
    }
    landing_bytes = build_html(app_links)

    handler_cls = make_handler(backends, landing_bytes)
    server = HTTPServer((host, port), handler_cls)
    print(f"appsuite web: http://{host}:{port}/")
    server.serve_forever()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="appsuite.web", description="Tüm uygulamalar için birleşik web sunucusu")
    parser.add_argument("--host", default="0.0.0.0", help="Genel sunucu adresi (vars: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=2000, help="Genel port (vars: 2000)")
    parser.add_argument("--backend-host", default="127.0.0.1", help="İç servislerin dinleyeceği adres (vars: 127.0.0.1)")
    parser.add_argument("--app48-port", type=int, default=9200, help="app48 iç portu")
    parser.add_argument("--app72-port", type=int, default=9201, help="app72 iç portu")
    parser.add_argument("--app80-port", type=int, default=9202, help="app80 iç portu")
    parser.add_argument("--app120-port", type=int, default=9203, help="app120 iç portu")
    parser.add_argument("--app321-port", type=int, default=9204, help="app321 iç portu")
    args = parser.parse_args(argv)

    run(args.host, args.port, args.backend_host, args.app48_port, args.app72_port, args.app80_port, args.app120_port, args.app321_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
