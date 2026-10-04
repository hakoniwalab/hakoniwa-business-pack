"""The viewers' web server (tools/recipe/viewer_http_server.py): a viewer page loads
dozens of ES modules at once (more with several viewers open); none may be
reset (python -m http.server, with its backlog of 5, reset most of them and
the viewer stopped with "error")."""

from __future__ import annotations

import concurrent.futures
from functools import partial
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

from tools.recipe.viewer_http_server import ViewerHTTPServer

MODULES = 150  # about what two viewers ask for at once


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return


class ViewerHttpServerTest(unittest.TestCase):
    def test_mime_types_are_initialized_before_serving(self) -> None:
        with mock.patch("tools.recipe.viewer_http_server.mimetypes.init") as initialize:
            server = ViewerHTTPServer(("127.0.0.1", 0), _Quiet)
            self.addCleanup(server.server_close)
        initialize.assert_called_once_with()

    def test_a_burst_of_module_requests_is_all_served(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for number in range(MODULES):
                Path(directory, f"module{number}.js").write_text("export {};\n" * 100, encoding="utf-8")
            server = ViewerHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=directory))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            port = server.server_address[1]

            def get(number: int) -> object:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/module{number}.js", timeout=10) as response:
                        return response.status
                except OSError as exc:
                    return type(exc).__name__

            with concurrent.futures.ThreadPoolExecutor(MODULES) as pool:
                statuses = list(pool.map(get, range(MODULES)))
        self.assertEqual(statuses, [200] * MODULES)


if __name__ == "__main__":
    unittest.main()
