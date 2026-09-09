"""Running the server.

The desktop shell starts this as a child process and talks to it over
loopback. It has no opinion about windows -- that is the shell's job -- so all
that is left here is picking a port, serving, and telling the caller when it is
ready.
"""

from __future__ import annotations

import http.client
import socket
import sys
import time


def free_port() -> int:
    """Ask the OS for an unused port.

    Not a fixed one: two windows, or a stale process from a crash, would
    otherwise collide and fail in a way that reads as "the app is broken".
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until_ready(port: int, timeout: float = 40.0) -> bool:
    """Poll until the server answers, so nothing is shown before it can serve."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conn = None
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1.0)
            conn.request("GET", "/api/status")
            if conn.getresponse().status < 500:
                return True
        except (OSError, http.client.HTTPException):
            time.sleep(0.15)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except (OSError, http.client.HTTPException):
                    pass
    return False


def serve(port: int | None = None) -> int:
    """Serve on `port`, or on any free one, until interrupted."""
    import uvicorn

    from .server.app import app

    port = port or free_port()
    # flush=True matters: stdout is a pipe to the desktop shell, and Python
    # buffers it, so without this the shell's log stays empty while it waits.
    print(f"classhelper is serving on http://127.0.0.1:{port}/", flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0
