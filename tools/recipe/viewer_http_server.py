#!/usr/bin/env python3
"""Serve a browser viewer's files: python -m http.server with a long listen backlog.

The Three.js and map viewers load dozens of ES modules at once (more with
several viewers open). The standard http.server listens with a backlog of 5,
and the system resets the connections beyond it (ERR_CONNECTION_RESET): the
viewer stops with "error". This server is the same otherwise -- the current
directory (or --directory), every address unless --bind says otherwise --
with a backlog of 128.

  python tools/recipe/viewer_http_server.py 8000
  python tools/recipe/viewer_http_server.py --port 8000 --directory <dir> --bind 127.0.0.1
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class ViewerHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer whose connections wait their turn instead of being reset."""

    request_queue_size = 128
    daemon_threads = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("port_positional", nargs="?", type=int, metavar="port", help="as python -m http.server PORT")
    parser.add_argument("--port", type=int, help="the port (default 8000)")
    parser.add_argument("--bind", default="", help="the address (default: every address, as http.server)")
    parser.add_argument("--directory", type=Path, default=Path.cwd(), help="what to serve (default: the current directory)")
    args = parser.parse_args(argv)
    port = args.port or args.port_positional or 8000
    handler = partial(SimpleHTTPRequestHandler, directory=str(args.directory.resolve()))
    server = ViewerHTTPServer((args.bind, port), handler)
    print(f"Serving {args.directory.resolve()} on {args.bind or '0.0.0.0'}:{server.server_address[1]} (backlog 128)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
