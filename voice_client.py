import asyncio
import json
import ssl
import socket
import time
from typing import Optional, Dict
import websockets

from .gateway import VoiceCredentials
from .native_bridge import DaveVoiceSession, TransportCrypto, UdpHelper
from .audio_decoder import OpusEncoder


class DiscordVoiceClient:
    """
    Native Discord Voice (v8) + DAVE v1 client implemented in Python and Rust.
    Reimplements Serein's voice transport and DAVE cryptographic handshake.
    """

    def __init__(self, creds: VoiceCredentials):
        self.creds = creds
        self.dave: Optional[DaveVoiceSession] = None
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.udp_sock: Optional[socket.socket] = None
        self.secret_key: Optional[bytes] = None
        self.ssrc: Optional[int] = None
        self.server_ip: Optional[str] = None
        self.server_port: Optional[int] = None
        self.my_ip: Optional[str] = None
        self.my_port: Optional[int] = None

        self._running = False
        self._v_hb_task: Optional[asyncio.Task] = None
        self._udp_task: Optional[asyncio.Task] = None
        self._keepalive_seq = 0

        # Outbound audio stream state
        self._out_seq = 0
        self._out_ts = 0
        self._out_nonce = 1
        self._encoder: Optional[OpusEncoder] = None
        self._is_ready_event = asyncio.Event()

        # Mapping of SSRC -> user_id
        self.ssrc_to_user: Dict[int, int] = {}
        self.user_to_ssrc: Dict[int, int] = {}
        self._audio_callback = None

    async def connect(self):
        """Connects to Discord Voice WebSocket, negotiates DAVE, and establishes UDP transport."""
        self._running = True

        # Initialize native DAVE MLS session
        self.dave = DaveVoiceSession(self.creds.user_id, self.creds.channel_id)
        print(f"[DAVE] Initialized native DAVE MLS session for User {self.creds.user_id} in Channel {self.creds.channel_id}")

        voice_uri = f"wss://{self.creds.endpoint}/?v=8"
        print(f"[Voice WS] Connecting to {voice_uri} ...")
        ssl_ctx = ssl.create_default_context()
        self.ws = await websockets.connect(
            voice_uri,
            ssl=ssl_ctx,
            max_size=16 * 1024 * 1024,
            ping_interval=None,
        )
        print("[Voice WS] Connected successfully.")

        # 1. Hello (Opcode 8)
        raw_hello = await self.ws.recv()
        hello = json.loads(raw_hello)
        v_hb_interval = hello["d"]["heartbeat_interval"] / 1000.0
        self._v_hb_task = asyncio.create_task(self._voice_heartbeat_loop(v_hb_interval))

        # 2. Identify (Opcode 0)
        v_identify = {
            "op": 0,
            "d": {
                "server_id": str(self.creds.guild_id),
                "user_id": str(self.creds.user_id),
                "session_id": self.creds.session_id,
                "token": self.creds.voice_token,
                "video": False,
                "streams": [],
                "max_dave_protocol_version": 1,
            },
        }
        await self.ws.send(json.dumps(v_identify))
        print("[Voice WS] Sent Voice Opcode 0 Identify with DAVE protocol v1 support.")

        # 3. Main event loop: Ready, UDP discovery, Session Description, DAVE KeyPackage
        key_package = self.dave.get_key_package()
        print(f"[DAVE] Pre-generated MLS KeyPackage ({len(key_package)} bytes).")

        while self._running:
            raw_msg = await self.ws.recv()

            if isinstance(raw_msg, str):
                msg = json.loads(raw_msg)
                op = msg.get("op")
                d = msg.get("d", {})

                if op == 2:  # Ready
                    self.ssrc = d["ssrc"]
                    self.server_ip = d["ip"]
                    self.server_port = d["port"]
                    print(f"[Voice] Ready received! SSRC={self.ssrc}, Server={self.server_ip}:{self.server_port}")

                    # Run UDP Discovery
                    await self._perform_udp_discovery()

                    # Select Protocol (Opcode 1)
                    select_proto = {
                        "op": 1,
                        "d": {
                            "protocol": "udp",
                            "data": {
                                "address": self.my_ip,
                                "port": self.my_port,
                                "mode": "aead_xchacha20_poly1305_rtpsize",
                            },
                            "codecs": [
                                {
                                    "name": "opus",
                                    "type": "audio",
                                    "priority": 1000,
                                    "payload_type": 120,
                                }
                            ],
                        },
                    }
                    await self.ws.send(json.dumps(select_proto))
                    print(f"[Voice] Sent Opcode 1 Select Protocol with aead_xchacha20_poly1305_rtpsize.")

                elif op == 4:  # Session Description
                    self.secret_key = bytes(d["secret_key"])
                    dave_ver = d.get("dave_protocol_version")
                    print(f"[Voice] Opcode 4 Session Description: mode={d.get('mode')}, dave_version={dave_ver}")

                    # Send Opcode 5 Speaking (0 = not transmitting)
                    await self.ws.send(
                        json.dumps({"op": 5, "d": {"delay": 0, "speaking": 0, "ssrc": self.ssrc}})
                    )
                    print("[Voice] Sent Opcode 5 Speaking.")

                    # Send Opcode 26 DAVE KeyPackage
                    dave_kp_packet = bytes([26]) + key_package
                    await self.ws.send(dave_kp_packet)
                    print(f"[DAVE] Sent Opcode 26 DAVE MLS KeyPackage ({len(dave_kp_packet)} bytes) to voice gateway.")

                    # Start background UDP media loop now that transport key is available
                    if self._udp_task is None:
                        self._udp_task = asyncio.create_task(self._udp_media_loop())

                elif op == 5:  # Speaking announcement
                    uid = int(d.get("user_id", 0))
                    user_ssrc = d.get("ssrc")
                    speaking = d.get("speaking", 0)
                    if user_ssrc:
                        self.ssrc_to_user[user_ssrc] = uid
                        self.user_to_ssrc[uid] = user_ssrc
                    if speaking:
                        print(f"[Speaking] User {uid} (SSRC {user_ssrc}) is SPEAKING 🎙️")
                    else:
                        print(f"[Speaking] User {uid} stopped speaking.")

                elif op == 11:  # Participant list
                    users = d.get("user_ids", [])
                    print(f"[Voice] Active participants in VC: {len(users)} users ({', '.join(users[:5])}...)")

                elif op == 21:  # Prepare transition
                    t_id = d.get("transition_id", 0)
                    print(f"[DAVE] Opcode 21 Prepare Transition (id={t_id})")
                    # Acknowledge transition readiness (Opcode 23)
                    await self.ws.send(json.dumps({"op": 23, "d": {"transition_id": t_id}}))

                elif op == 22:  # Execute transition
                    t_id = d.get("transition_id", 0)
                    print(f"[DAVE] Opcode 22 Execute Transition (id={t_id})! Group epoch updated.")
                    if self.dave and self.dave.is_ready():
                        self._is_ready_event.set()

            elif isinstance(raw_msg, bytes):
                # Binary frame from Discord Voice Gateway (DAVE signaling)
                if len(raw_msg) < 3:
                    continue
                opcode = raw_msg[2]
                payload = raw_msg[3:]

                if opcode == 25:  # External Sender
                    print(f"[DAVE] Received Opcode 25 Set External Sender ({len(payload)} bytes).")
                    self.dave.set_external_sender(payload)
                    print("[DAVE] Set external sender credential in native MLS session.")

                elif opcode == 27:  # Proposals
                    op_type = payload[0] if len(payload) > 0 else 0
                    proposals_data = payload[1:]
                    print(f"[DAVE] Received Opcode 27 Proposals (op_type={op_type}, {len(proposals_data)} bytes).")
                    resp = self.dave.process_proposals(op_type, proposals_data)
                    if resp:
                        # Send commit/welcome (Opcode 28)
                        commit_packet = bytes([28]) + resp
                        await self.ws.send(commit_packet)
                        print(f"[DAVE] Sent Opcode 28 Commit/Welcome ({len(commit_packet)} bytes).")

                elif opcode in (29, 30):  # Commit (29) or Welcome (30)
                    t_id = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else 0
                    print(f"[DAVE] Received {'Commit' if opcode == 29 else 'Welcome'} (op={opcode}, transition_id={t_id}, len={len(payload)}).")
                    try:
                        if opcode == 29:
                            self.dave.process_commit(payload[2:])
                        else:
                            self.dave.process_welcome(payload[2:])
                        print(f"[DAVE] Processed {'Commit' if opcode == 29 else 'Welcome'} successfully. MLS ready: {self.dave.is_ready()}")
                        if self.dave.is_ready():
                            self._is_ready_event.set()

                        # Acknowledge transition with Opcode 23
                        await self.ws.send(json.dumps({"op": 23, "d": {"transition_id": t_id}}))
                    except Exception as e:
                        print(f"[DAVE] Error processing group transition: {e}")

    async def _perform_udp_discovery(self):
        """Constructs and sends Discord's 74-byte UDP IP discovery packet via native C++ helper."""
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_sock.setblocking(False)
        self.udp_sock.connect((self.server_ip, self.server_port))

        discovery_pkt = UdpHelper.build_discovery_packet(self.ssrc)
        self.udp_sock.send(discovery_pkt)

        # Wait for reply
        loop = asyncio.get_running_loop()
        reply = await loop.sock_recv(self.udp_sock, 74)
        self.my_ip, self.my_port = UdpHelper.parse_discovery_response(reply, self.ssrc)
        print(f"[UDP Discovery] Discovered External Address: {self.my_ip}:{self.my_port}")

    async def _udp_media_loop(self):
        """Handles UDP keepalives and decrypts incoming voice RTP packets using C++ XChaCha20-Poly1305."""
        loop = asyncio.get_running_loop()
        last_ping = 0.0

        print("[UDP Media] Listening for incoming voice audio datagrams...")
        packet_count = 0

        while self._running and self.udp_sock:
            now = time.time()
            if now - last_ping >= 5.0:
                self._keepalive_seq = (self._keepalive_seq + 1) & 0xFFFFFFFF
                ping = UdpHelper.build_keepalive_packet(self._keepalive_seq)
                try:
                    self.udp_sock.send(ping)
                    last_ping = now
                except Exception as e:
                    print(f"[UDP Ping] Failed: {e}")

            try:
                data = await asyncio.wait_for(loop.sock_recv(self.udp_sock, 4096), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"[UDP Media Loop Exception] {type(e).__name__}: {e}")
                break

            if len(data) < 12:
                # 8-byte UDP ping response (0x1337F00D)
                if len(data) == 8 and data.startswith(b"\x13\x37\xf0\x0d"):
                    continue
                continue

            # Ignore RTCP control packets (e.g. 200 Sender Report, 201 Receiver Report)
            if 192 <= data[1] <= 223:
                continue

            # Decrypt RTP packet using XChaCha20-Poly1305 engine
            if self.secret_key:
                result = TransportCrypto.decrypt_rtp(self.secret_key, data)
                if result is not None:
                    payload, ssrc, seq, ts, pt = result
                    packet_count += 1
                    sender = self.ssrc_to_user.get(ssrc, f"SSRC:{ssrc}")
                    if packet_count % 50 == 1:
                        print(f"[Audio In] Decrypted RTP audio frame from {sender} (len={len(payload)} bytes, seq={seq}, ts={ts})")
                    if self._audio_callback:
                        try:
                            self._audio_callback(ssrc, seq, ts, pt, payload)
                        except Exception as e:
                            print(f"[Audio Callback Error] {e}")
                else:
                    print(f"[UDP Decrypt Error] TransportCrypto.decrypt_rtp returned None for {len(data)}-byte packet")

    async def _voice_heartbeat_loop(self, interval: float):
        try:
            while self._running and self.ws and not self.ws.closed:
                await asyncio.sleep(min(interval, 5.0))
                now_ms = int(time.time() * 1000)
                await self.ws.send(json.dumps({"op": 3, "d": {"t": now_ms}}))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def wait_until_ready(self, timeout: float = 15.0):
        await asyncio.wait_for(self._is_ready_event.wait(), timeout=timeout)

    async def set_speaking(self, speaking: bool):
        if self.ws and not self.ws.closed and self.ssrc:
            spk_flag = 1 if speaking else 0
            msg = {"op": 5, "d": {"delay": 0, "speaking": spk_flag, "ssrc": self.ssrc}}
            await self.ws.send(json.dumps(msg))

    async def play_pcm_stream(self, pcm_chunks):
        """
        Transmits 48kHz stereo 16-bit PCM chunks (960 stereo samples / 3840 bytes per 20ms frame)
        over UDP into the voice channel using DAVE v1 E2EE.
        """
        if not self._encoder:
            self._encoder = OpusEncoder(sample_rate=48000, channels=2)

        print("[Audio Out] Starting playback stream...")
        await self.set_speaking(True)
        start_time = time.monotonic()
        frame_idx = 0

        for pcm_chunk in pcm_chunks:
            # 1. Encode PCM to Opus
            opus_frame = self._encoder.encode(pcm_chunk, frame_size=960)

            # 2. DAVE v1 encrypt Opus frame
            if self.dave and self.dave.is_ready():
                try:
                    encrypted_frame = self.dave.encrypt_opus(opus_frame)
                except Exception as e:
                    print(f"[DAVE Outbound Encrypt Error] {e}")
                    encrypted_frame = opus_frame
            else:
                encrypted_frame = opus_frame

            # 3. Wrap in RTP and XChaCha20Poly1305 transport encrypt
            seq = self._out_seq & 0xFFFF
            ts = self._out_ts & 0xFFFFFFFF
            self._out_seq += 1
            self._out_ts += 960
            counter = self._out_nonce
            self._out_nonce += 1

            rtp_packet = TransportCrypto.encrypt_rtp(
                self.secret_key,
                counter,
                self.ssrc,
                seq,
                ts,
                120,
                encrypted_frame,
            )

            # 4. Dispatch over UDP socket
            if self.udp_sock:
                self.udp_sock.send(rtp_packet)

            frame_idx += 1
            expected_time = start_time + (frame_idx * 0.02)
            delay = expected_time - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)

        # 5. Trailing silence packets (5 frames of \xf8\xff\xfe)
        for _ in range(5):
            seq = self._out_seq & 0xFFFF
            ts = self._out_ts & 0xFFFFFFFF
            self._out_seq += 1
            self._out_ts += 960
            counter = self._out_nonce
            self._out_nonce += 1

            silence_frame = b"\xf8\xff\xfe"
            if self.dave and self.dave.is_ready():
                try:
                    encrypted_silence = self.dave.encrypt_opus(silence_frame)
                except Exception:
                    encrypted_silence = silence_frame
            else:
                encrypted_silence = silence_frame

            silence_pkt = TransportCrypto.encrypt_rtp(
                self.secret_key,
                counter,
                self.ssrc,
                seq,
                ts,
                120,
                encrypted_silence,
            )
            if self.udp_sock:
                self.udp_sock.send(silence_pkt)
            await asyncio.sleep(0.02)

        await self.set_speaking(False)
        print(f"[Audio Out] Playback finished ({frame_idx} frames sent).")

    async def close(self):
        self._running = False
        if self._v_hb_task:
            self._v_hb_task.cancel()
        if self._udp_task:
            self._udp_task.cancel()
        if self.ws and not self.ws.closed:
            await self.ws.close()
        if self.udp_sock:
            self.udp_sock.close()
