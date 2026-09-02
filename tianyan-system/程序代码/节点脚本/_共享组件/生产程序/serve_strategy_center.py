from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "AGENTS.md").is_file())
DEFAULT_SITE_DIR = PROJECT_ROOT / "site" / "strategy_center"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the strategy center static site.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    site_dir = args.site_dir.resolve()
    handler = lambda *handler_args, **handler_kwargs: SimpleHTTPRequestHandler(  # noqa: E731
        *handler_args,
        directory=str(site_dir),
        **handler_kwargs,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving {site_dir} at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
