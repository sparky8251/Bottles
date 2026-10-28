"""A scripted HTTP server on 127.0.0.1 for exercising the real client code.

Tests describe outcomes per request path and the server plays them back:

    200                      -> empty 200
    (200, b"body")           -> status and body
    404                      -> status, no body
    STALL                    -> accept the request and answer only after
                                `stall_seconds`, i.e. later than the client's
                                timeout in the test
    ("redirect", "/other")   -> 302 to that location
    ("truncate", 100, b"ab") -> declare 100 bytes, send 2, close
    ("stream", [b"a", b"b"], 0.05) -> the chunks, with a pause between them

Requests the server received are recorded as (method, path, headers).
Nothing here is specific to any HTTP library; the client talks to it over a
real socket, so a change in how requests are made cannot break these tests
as long as the outcome holds.
"""

import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STALL = "stall"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        self._serve(send_body=True)

    def do_HEAD(self):
        self._serve(send_body=False)

    def _serve(self, send_body):
        server = self.server
        server.requests.append(
            (self.command, self.path, {k.lower(): v for k, v in self.headers.items()})
        )
        outcome = server.script.get(self.path, 404)
        if callable(outcome):
            outcome = outcome()

        if outcome == STALL:
            time.sleep(server.stall_seconds)
            try:
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except OSError:
                pass  # the client gave up already, which is the point
            return

        if isinstance(outcome, int):
            self.send_response(outcome)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        kind = outcome[0]
        if kind == "redirect":
            self.send_response(302)
            self.send_header("Location", outcome[1])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if kind == "truncate":
            _, declared, data = outcome
            self.send_response(200)
            self.send_header("Content-Length", str(declared))
            self.end_headers()
            if send_body:
                self.wfile.write(data)
                self.wfile.flush()
            return  # HTTP/1.0: returning closes the connection short
        if kind == "stream":
            _, chunks, delay = outcome
            self.send_response(200)
            self.send_header("Content-Length", str(sum(len(c) for c in chunks)))
            self.end_headers()
            if send_body:
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    time.sleep(delay)
            return

        status, data = outcome
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if send_body:
            self.wfile.write(data)


@contextmanager
def serve(script, stall_seconds=1.0):
    """Yield (server, base_url); `server.requests` lists what it received."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.script = script
    server.requests = []
    server.stall_seconds = stall_seconds
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
