#!/usr/bin/env python3
"""Serve the static report and proxy Ai strategy LLM requests.

The browser calls /llmapi/v1/* on the report server origin. This handler forwards
those requests to the internal model server, avoiding browser CORS preflight
failures and keeping the upstream API key out of static page assets.
"""

from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, parse, request


DEFAULT_UPSTREAM_BASE_URL = "http://10.89.189.109:8000/llmapi/v1"
DEFAULT_TIMEOUT_SECONDS = 60
MAX_PROXY_BODY_BYTES = 4 * 1024 * 1024


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class BasicDataHandler(SimpleHTTPRequestHandler):
    server_version = "BasicDataReport/1.1"

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        if self.path.startswith("/llmapi/v1/") or self.path == "/llmapi/v1":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "*"))
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "authorization,content-type,api-key,x-api-key")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()
            return
        super().do_OPTIONS()

    def do_GET(self) -> None:
        if self.path.startswith("/llmapi/v1/") or self.path == "/llmapi/v1":
            self.proxy_to_llm()
            return
        if self.is_blocked_static_path():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path.startswith("/llmapi/v1/") or self.path == "/llmapi/v1":
            self.proxy_to_llm()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def is_blocked_static_path(self) -> bool:
        path = parse.urlparse(self.path).path.replace("\\", "/")
        if path.startswith("/.git/") or path in {"/.git", "/.gitignore", "/.gitattributes"}:
            return True
        if getattr(self.server, "serving_deploy_root", False):
            if path == "/scripts" or path.startswith("/scripts/"):
                return True
            if path == "/config" or path.startswith("/config/"):
                return True
            if path == "/logs" or path.startswith("/logs/"):
                return True
        if path.endswith("/ai_strategy_proxy.env") or path == "/config/ai_strategy_proxy.env":
            return True
        return False

    def proxy_to_llm(self) -> None:
        upstream_base = os.environ.get("AI_STRATEGY_UPSTREAM_BASE_URL", DEFAULT_UPSTREAM_BASE_URL).rstrip("/")
        timeout = float(os.environ.get("AI_STRATEGY_UPSTREAM_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))
        api_key = os.environ.get("AI_STRATEGY_UPSTREAM_API_KEY", "")
        auth_header = os.environ.get("AI_STRATEGY_UPSTREAM_AUTH_HEADER", "Authorization").strip() or "Authorization"
        parsed = parse.urlparse(self.path)
        suffix = parsed.path[len("/llmapi/v1") :]
        upstream_url = f"{upstream_base}{suffix}"
        if parsed.query:
            upstream_url = f"{upstream_url}?{parsed.query}"

        body = None
        if self.command in {"POST", "PUT", "PATCH"}:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length > MAX_PROXY_BODY_BYTES:
                self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": {"message": "request body too large"}})
                return
            body = self.rfile.read(content_length) if content_length else b""

        headers = {
            "Accept": self.headers.get("Accept", "application/json"),
            "Content-Type": self.headers.get("Content-Type", "application/json"),
        }
        if api_key:
            if auth_header.lower() == "authorization":
                headers["Authorization"] = f"Bearer {api_key}"
            else:
                headers[auth_header] = api_key
        elif self.headers.get("Authorization"):
            headers["Authorization"] = self.headers["Authorization"]

        upstream_request = request.Request(
            upstream_url,
            data=body,
            method=self.command,
            headers=headers,
        )
        try:
            with request.urlopen(upstream_request, timeout=timeout) as response:
                data = response.read()
                self.send_response(response.status)
                self.copy_response_headers(response.headers)
                self.end_headers()
                self.wfile.write(data)
        except error.HTTPError as exc:
            data = exc.read()
            self.send_response(exc.code)
            self.copy_response_headers(exc.headers)
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:  # noqa: BLE001
            self.send_json(
                HTTPStatus.BAD_GATEWAY,
                {
                    "error": {
                        "message": f"LLM proxy failed: {exc}",
                        "upstream": upstream_base,
                    }
                },
            )

    def copy_response_headers(self, headers) -> None:  # type: ignore[no-untyped-def]
        skip = {"connection", "transfer-encoding", "content-encoding", "server", "date"}
        for key, value in headers.items():
            if key.lower() in skip:
                continue
            self.send_header(key, value)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "*"))

    def send_json(self, status: HTTPStatus, payload: dict) -> None:
        data = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "*"))
        self.end_headers()
        self.wfile.write(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve report static files with an Ai strategy LLM proxy.")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "7676")))
    parser.add_argument("--directory", type=Path, default=Path.cwd())
    parser.add_argument("--env-file", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    directory = args.directory.resolve()
    env_file = args.env_file or directory / "config" / "ai_strategy_proxy.env"
    load_env_file(env_file)
    serving_deploy_root = (directory / "basic_data" / "index.html").exists()
    handler = lambda *h_args, **h_kwargs: BasicDataHandler(*h_args, directory=str(directory), **h_kwargs)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.serving_deploy_root = serving_deploy_root  # type: ignore[attr-defined]
    print(f"Serving {directory} on http://{args.host}:{args.port}/", flush=True)
    print(f"LLM proxy /llmapi/v1/* -> {os.environ.get('AI_STRATEGY_UPSTREAM_BASE_URL', DEFAULT_UPSTREAM_BASE_URL)}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
