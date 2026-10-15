"""Serve the FIT parse benchmark page to this machine and the phones on the same Wi-Fi (SP-313).

    .venv/bin/python docs/research/fit-parse-bench/serve.py            # http://<this Mac's IP>:8765/
    .venv/bin/python docs/research/fit-parse-bench/serve.py --https    # self-signed https (OPFS on phones)

Builds fixtures/ first when it is missing (make_fixtures.py). Plain http works for
the parse timings everywhere, but a phone opening http://192.168.x.x is not a
"secure context", so the browser hides OPFS and part ② skips the OPFS numbers.
--https makes a throwaway self-signed certificate (openssl, in a temp folder that is
deleted as soon as the server has loaded it); the phone warns once ("not private") and, after you accept, the
page is a secure context.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".js": "text/javascript", ".json": "application/json", ".fit": "application/octet-stream"}

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):  # quiet: one line per page load is enough
        if self.path.endswith((".html", "/")):
            super().log_message(fmt, *args)


def lan_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))          # TEST-NET address: nothing is sent, it only picks the route
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def self_signed(tmp: Path, ips: list[str]) -> tuple[Path, Path]:
    cert, key = tmp / "cert.pem", tmp / "key.pem"
    san = ",".join(["DNS:localhost", "IP:127.0.0.1"] + [f"IP:{ip}" for ip in ips])
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", "/CN=fit-parse-bench", "-addext", f"subjectAltName={san}",
                    "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
    return cert, key


def make_server(port: int, bind: str = "0.0.0.0") -> http.server.ThreadingHTTPServer:
    handler = functools.partial(Handler, directory=str(BENCH))
    return http.server.ThreadingHTTPServer((bind, port), handler)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--https", action="store_true", help="self-signed https, so phones get OPFS")
    args = ap.parse_args()
    if not (BENCH / "fixtures" / "manifest.json").exists():
        print("fixtures/ missing: running make_fixtures.py …")
        subprocess.run([sys.executable, str(BENCH / "make_fixtures.py")], check=True)
    ips = lan_ips()
    srv = make_server(args.port)
    scheme = "http"
    if args.https:
        tmp = Path(tempfile.mkdtemp(prefix="fit-bench-"))
        cert, key = self_signed(tmp, ips)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        shutil.rmtree(tmp, ignore_errors=True)        # loaded: the throwaway key need not stay on disk
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
    print("Open on this machine:  %s://localhost:%d/" % (scheme, args.port))
    for ip in ips:
        print("Open on the phone:     %s://%s:%d/   (same Wi-Fi)" % (scheme, ip, args.port))
    print("Ctrl-C to stop.", flush=True)

    def _stop(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
