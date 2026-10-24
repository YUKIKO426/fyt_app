# vc_client.py — pyrogram + pytgcalls

from pyrogram import Client
from pyrogram.errors import PeerIdInvalid
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream

from config import API_ID, API_HASH, SESSION_NAME, HTTP_HOST, HTTP_PORT

STREAM_URL = f"http://{HTTP_HOST}:{HTTP_PORT}/stream"


class VCClient:
    def __init__(self):
        self.app          = None
        self.call         = None
        self.joined_chat  = None
        self.connected    = False

    async def start(self):
        self.app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)
        await self.app.start()
        me = await self.app.get_me()
        print(f"[vc] logged in as {me.username or me.first_name}")

        count = 0
        try:
            async for dialog in self.app.get_dialogs():
                _ = dialog.chat.id
                count += 1
            print(f"[vc] peer cache warmed — {count} dialogs")
        except Exception as e:
            print(f"[vc] warm-up failed: {e}")

        self.call = PyTgCalls(self.app)
        await self.call.start()
        print("[vc] pytgcalls started")

    async def join(self, chat_id: int):
        if self.joined_chat == chat_id:
            return
        if self.joined_chat is not None:
            await self.leave()

        try:
            await self.call.play(chat_id, MediaStream(STREAM_URL))
            self.joined_chat = chat_id
            self.connected   = True
            print(f"[vc] joined {chat_id}")
        except PeerIdInvalid:
            print(f"[vc] peer invalid: {chat_id}")
            raise
        except Exception as e:
            print(f"[vc] join error: {e}")
            raise

    async def leave(self):
        if self.joined_chat is None:
            return
        try:
            await self.call.leave_call(self.joined_chat)
        except Exception as e:
            print(f"[vc] leave error: {e}")
        self.joined_chat = None
        self.connected   = False
        print("[vc] left")

    async def stop(self):
        await self.leave()
        try:
            await self.call.stop()
        except Exception:
            pass
        try:
            await self.app.stop()
        except Exception:
            pass


vc = VCClient()
