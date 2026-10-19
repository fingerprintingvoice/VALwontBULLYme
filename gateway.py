import asyncio
import json
import websockets
from dataclasses import dataclass
from typing import Optional
from .config import TOKEN, GUILD_ID, CHANNEL_ID, GATEWAY_URL, BROWSER_PROPERTIES

@dataclass
class VoiceCredentials:
    user_id: int
    session_id: str
    voice_token: str
    endpoint: str
    guild_id: int
    channel_id: int


class DiscordGateway:
    """Maintains connection to Discord Gateway v10 and requests Voice State."""

    def __init__(self, token: str = TOKEN, guild_id: int = GUILD_ID, channel_id: int = CHANNEL_ID):
        self.token = token
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._hb_task: Optional[asyncio.Task] = None
        self._running = False
        self.credentials: Optional[VoiceCredentials] = None
        self._creds_event = asyncio.Event()

    async def connect_and_join_vc(self) -> VoiceCredentials:
        """Connects to the Gateway, joins the configured VC, and returns voice credentials."""
        self._running = True
        self.ws = await websockets.connect(
            GATEWAY_URL,
            max_size=16 * 1024 * 1024,
            ping_interval=None,
        )

        # Receive Opcode 10 Hello
        raw_hello = await self.ws.recv()
        hello = json.loads(raw_hello)
        hb_interval = hello["d"]["heartbeat_interval"] / 1000.0

        self._hb_task = asyncio.create_task(self._heartbeat_loop(hb_interval))

        # Send Opcode 2 Identify with Serein browser fingerprint
        identify_payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "capabilities": 30717,
                "properties": BROWSER_PROPERTIES,
                "presence": {
                    "status": "online",
                    "since": 0,
                    "activities": [],
                    "afk": False,
                },
                "compress": False,
            },
        }
        await self.ws.send(json.dumps(identify_payload))
        print("[Gateway] Sent Identify with Serein browser fingerprint.")

        # Listen for READY and dispatch events
        user_id = None
        session_id = None
        voice_token = None
        endpoint = None

        while self._running:
            raw_msg = await self.ws.recv()
            msg = json.loads(raw_msg)
            op = msg.get("op")
            t = msg.get("t")

            if t == "READY":
                session_id = msg["d"]["session_id"]
                user_id = int(msg["d"]["user"]["id"])
                print(f"[Gateway] Logged in as User ID: {user_id}, Session: {session_id}")

                # Send Opcode 4 to join Voice Channel
                vsu = {
                    "op": 4,
                    "d": {
                        "guild_id": str(self.guild_id),
                        "channel_id": str(self.channel_id),
                        "self_mute": False,
                        "self_deaf": False,
                        "self_video": False,
                    },
                }
                await self.ws.send(json.dumps(vsu))
                print(f"[Gateway] Sent Voice State Update: joining VC {self.channel_id}...")

            elif t == "VOICE_STATE_UPDATE":
                d = msg["d"]
                if user_id and int(d.get("user_id", 0)) == user_id:
                    session_id = d.get("session_id", session_id)
                    print(f"[Gateway] Voice State confirmed. Channel: {d.get('channel_id')}")

            elif t == "VOICE_SERVER_UPDATE":
                d = msg["d"]
                if int(d.get("guild_id", 0)) == self.guild_id:
                    voice_token = d["token"]
                    endpoint = d["endpoint"]
                    print(f"[Gateway] Voice Server Update: Endpoint={endpoint}")

            if user_id and session_id and voice_token and endpoint:
                self.credentials = VoiceCredentials(
                    user_id=user_id,
                    session_id=session_id,
                    voice_token=voice_token,
                    endpoint=endpoint,
                    guild_id=self.guild_id,
                    channel_id=self.channel_id,
                )
                self._creds_event.set()
                return self.credentials

        raise RuntimeError("Gateway closed before voice credentials arrived.")

    async def _heartbeat_loop(self, interval: float):
        try:
            while self._running and self.ws and not self.ws.closed:
                await asyncio.sleep(interval)
                await self.ws.send(json.dumps({"op": 1, "d": None}))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def close(self):
        self._running = False
        if self._hb_task:
            self._hb_task.cancel()
        if self.ws and not self.ws.closed:
            await self.ws.close()
