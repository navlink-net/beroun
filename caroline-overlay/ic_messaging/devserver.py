# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The Communicator backend on its own, with scripted accounts: for designing the app in a browser and for the tests.

    python -m ic_messaging.devserver [--port 48790] [--db FILE] [--count 8] [--auto-reply] [--unlinked]
Run it with Caroline's runtime Python (it has FastAPI and uvicorn); put the overlay folder on PYTHONPATH.
"""
import argparse, os, tempfile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=48790)
    ap.add_argument("--db")
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--auto-reply", action="store_true")
    ap.add_argument("--unlinked", action="store_true")
    ap.add_argument("--pace", type=float, default=0.0, help="seconds a fake send/scan takes")
    a = ap.parse_args()
    import uvicorn
    from fastapi import FastAPI
    import ic_messaging
    from .api import build_router
    kw = dict(count=a.count, auto_reply=a.auto_reply, pace=a.pace)
    if a.unlinked:
        kw["linked"] = False
    reg = ic_messaging.build(workspace=tempfile.mkdtemp(prefix="ic-msg-"), fake=True, db=a.db, **kw)
    app = FastAPI()
    app.include_router(build_router(reg, fake=True))
    app.on_event("startup")(reg.start)
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
