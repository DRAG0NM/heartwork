#!/usr/bin/env python3
"""Desktop app: the local web canvas in a native window (pywebview), same 127.0.0.1 server."""

from __future__ import annotations

import argparse
import threading
from http.server import ThreadingHTTPServer

import store
import web


def main(argv=None):
    parser = argparse.ArgumentParser(description="Heartwork 健康画布桌面版")
    parser.add_argument("--home")
    args = parser.parse_args(argv)
    try:
        import webview
    except ImportError:
        print("桌面窗口需要 pywebview：python -m pip install pywebview\n先改用浏览器打开。", flush=True)
        return web.main(["--home", args.home] if args.home else [])
    store.init(args.home)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(args.home))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    webview.create_window("Heartwork · 健康画布", url, width=1480, height=940, min_size=(900, 640),
                          background_color="#f4f1ea", text_select=True)
    try:
        webview.start()
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
