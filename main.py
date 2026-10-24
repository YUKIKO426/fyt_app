# main.py — entry

import asyncio
import threading
from aiohttp import web

from config import HTTP_HOST, HTTP_PORT
from audio_engine import engine, create_http_app
from vc_client import vc
from gui_app import AppWindow


def run_asyncio_loop(loop, ready_event):
    asyncio.set_event_loop(loop)

    async def _boot():
        http_app = create_http_app()
        runner   = web.AppRunner(http_app)
        await runner.setup()
        site = web.TCPSite(runner, HTTP_HOST, HTTP_PORT)
        await site.start()
        print(f"[http] stream → http://{HTTP_HOST}:{HTTP_PORT}/stream")

        await vc.start()
        ready_event.set()

    loop.run_until_complete(_boot())
    loop.run_forever()


def main():
    loop        = asyncio.new_event_loop()
    ready_event = threading.Event()

    t = threading.Thread(target=run_asyncio_loop,
                          args=(loop, ready_event), daemon=True)
    t.start()

    print("[main] waiting for bot + http server...")
    ready_event.wait(timeout=60)
    print("[main] launching GUI")

    def quit_app():
        fut = asyncio.run_coroutine_threadsafe(vc.stop(), loop)
        try:
            fut.result(timeout=5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        print("[main] goodbye")

    app = AppWindow(loop, vc, quit_app)
    app.mainloop()


if __name__ == "__main__":
    main()
