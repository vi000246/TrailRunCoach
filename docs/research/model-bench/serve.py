"""Serve the model benchmark page to this machine and the phones on the same Wi-Fi (SP-760).

    .venv/bin/python docs/research/model-bench/serve.py            # http://<this Mac's IP>:8766/
    .venv/bin/python docs/research/model-bench/serve.py --https    # self-signed https (OPFS on phones)

make_bundle.py first (fixtures/). The same server as docs/research/fit-parse-bench/serve.py
(no-cache headers, the throwaway self-signed certificate), pointed at this folder. Plain http
is enough for the timings; the "opfs" folder state (a second visit on the device's own
caches) needs a secure context, so --https.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import importlib.util
import shutil
import signal
import ssl
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("fpb_serve", BENCH.parent / "fit-parse-bench" / "serve.py")
fpb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fpb)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--https", action="store_true")
    a = ap.parse_args()
    if not (BENCH / "fixtures" / "manifest.json").exists():
        print("fixtures/ missing: run make_bundle.py --demo <demo base> first")
        return 2
    ips = fpb.lan_ips()
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", a.port), functools.partial(fpb.Handler, directory=str(BENCH)))
    scheme = "http"
    if a.https:
        tmp = Path(tempfile.mkdtemp(prefix="model-bench-"))
        cert, key = fpb.self_signed(tmp, ips)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        shutil.rmtree(tmp, ignore_errors=True)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
    print("Open on this machine:  %s://localhost:%d/" % (scheme, a.port))
    for ip in ips:
        print("Open on the phone:     %s://%s:%d/   (same Wi-Fi)" % (scheme, ip, a.port))
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
