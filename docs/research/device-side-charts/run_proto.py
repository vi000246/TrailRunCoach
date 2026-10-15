"""SP-325: time the device prototype (proto/proto.html) in Chromium: desktop, and a phone-sized
viewport with CDP CPU throttling 4x / 6x (an emulation, not a real phone)."""
import json
import sys
import threading
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

D = Path(sys.argv[1])
srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=str(D)))
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
ws = sorted({p.name.split("_")[0][1:] for p in D.glob("w*_meta.json")}, key=int)
rows = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for label, rate, vp in (("desktop", 1, {"width": 1280, "height": 900}),
                            ("phone-4x", 4, {"width": 390, "height": 844}),
                            ("phone-6x", 6, {"width": 390, "height": 844})):
        ctx = b.new_context(viewport=vp, device_scale_factor=2 if rate > 1 else 1)
        for w in ws:
            for step in (5, 1):
                pg = ctx.new_page()
                cdp = ctx.new_cdp_session(pg)
                cdp.send("Emulation.setCPUThrottlingRate", {"rate": rate})
                pg.goto(f"http://127.0.0.1:{port}/proto.html?w={w}&step={step}&reps=5")
                pg.wait_for_function("window.__result", timeout=180000)
                r = pg.evaluate("window.__result")
                r["env"] = label
                rows.append(r)
                print(json.dumps({k: r.get(k) for k in ("env", "w", "step", "n_points", "plot_bytes", "device_cache_read",
                                                         "device_decode", "device_compute", "device_setoption", "server_setoption", "device_draw", "device_total",
                                                         "server_parse", "server_draw", "server_total", "error")}), flush=True)
                if label == "phone-4x" and step == 5 and w == ws[-1]:
                    pg.screenshot(path=str(D.parent / "proto_phone.png"), full_page=True)
                if label == "desktop" and step == 5 and w == ws[-1]:
                    pg.screenshot(path=str(D.parent / "proto_desktop.png"), full_page=True)
                pg.close()
        ctx.close()
    b.close()
(D.parent / "device.json").write_text(json.dumps(rows, indent=1))
srv.shutdown()
