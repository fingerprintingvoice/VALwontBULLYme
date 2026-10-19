#!/usr/bin/env python3
import asyncio
import os
import sys
import time
import datetime
import logging
from typing import Optional, Dict

from autotranscribe.config import (
    TOKEN,
    GUILD_ID,
    CHANNEL_ID,
)
from autotranscribe.gateway import DiscordGateway
from autotranscribe.voice_client import DiscordVoiceClient
from autotranscribe.audio_decoder import MultiSpeakerAudioSink, OpusDecoder

LOG_DIR = os.path.join("output", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "vc_capture.log")

def setup_logging():
    log_formatter = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logger = logging.getLogger("VCCapture")
    logger.setLevel(logging.DEBUG)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(log_formatter)
    logger.addHandler(ch)

    # File handler
    fh = logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(log_formatter)
    logger.addHandler(fh)

    return logger

class VCCapturer:
    def __init__(self, duration_sec: float = 10.0, output_dir: str = os.path.join("output", "captures")):
        self.duration_sec = duration_sec
        self.output_dir = output_dir
        self.logger = setup_logging()
        self.gateway: Optional[DiscordGateway] = None
        self.voice_client: Optional[DiscordVoiceClient] = None
        self.timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.output_prefix = f"capture_{self.timestamp}"
        self.sink = MultiSpeakerAudioSink(output_dir=self.output_dir, output_prefix=self.output_prefix)
        self.capture_started = False
        self.start_time = 0.0
        self.total_packets = 0
        self.speaker_stats: Dict[int, Dict[str, any]] = {}
        self._stop_event = asyncio.Event()

    def _on_audio_packet(self, ssrc: int, seq: int, ts: int, pt: int, payload: bytes):
        now = time.time()
        if not self.capture_started:
            self.capture_started = True
            self.start_time = now
            self.logger.info(f"🎙️ First audio packet received! Starting {self.duration_sec}s capture window...")
            asyncio.create_task(self._capture_timer())

        self.total_packets += 1
        user_id = self.voice_client.ssrc_to_user.get(ssrc, 0)
        speaker_name = f"user_{user_id}" if user_id else f"ssrc_{ssrc}"

        if ssrc not in self.speaker_stats:
            self.speaker_stats[ssrc] = {
                "user_id": user_id,
                "first_ts": ts,
                "last_seq": seq,
                "packets": 0,
                "bytes": 0,
                "samples": 0,
            }

        stats = self.speaker_stats[ssrc]
        stats["packets"] += 1
        stats["bytes"] += len(payload)
        stats["last_seq"] = seq

        # Check for standard Discord silence marker (\xf8\xff\xfe)
        is_silence = (payload == b"\xf8\xff\xfe")
        dave_decrypted = False
        opus_payload = None

        if is_silence:
            opus_payload = payload
            dave_decrypted = True
        else:
            # If DAVE v1 is active, the payload is DAVE-encrypted.
            # We MUST successfully decrypt it before passing to OpusDecoder.
            if not user_id or not self.voice_client.dave or not self.voice_client.dave.is_ready():
                self.logger.debug(
                    f"RTP Frame #{self.total_packets:04d} dropped: DAVE MLS not ready yet (user {user_id})"
                )
                return

            try:
                decrypted = self.voice_client.dave.decrypt_opus(user_id, payload)
                if decrypted:
                    opus_payload = decrypted
                    dave_decrypted = True
                else:
                    return
            except Exception as e:
                # NEVER pass ciphertext to OpusDecoder. Dropping corrupted/undecryptable frame.
                self.logger.debug(
                    f"RTP Frame #{self.total_packets:04d} dropped: DAVE decrypt failed ({e})"
                )
                return

        # Decode and write to per-speaker and mixed WAV cleanly
        samples = self.sink.write_frame(ssrc, speaker_name, opus_payload, seq)
        if samples:
            stats["samples"] += samples

        # Detailed per-packet logging in log file
        self.logger.debug(
            f"RTP Frame #{self.total_packets:04d} | SSRC: {ssrc} | User: {user_id or 'Unknown'} | "
            f"Seq: {seq} | TS: {ts} | Size: {len(payload)}B | DAVE: {dave_decrypted} | Samples: {samples or 0}"
        )

        if self.total_packets % 50 == 0:
            elapsed = now - self.start_time
            self.logger.info(
                f"Capture Progress: {elapsed:.1f}s / {self.duration_sec}s | "
                f"Total Packets: {self.total_packets} | Active Speaker: {speaker_name}"
            )

    async def _capture_timer(self):
        await asyncio.sleep(self.duration_sec)
        self.logger.info(f"⏱️ Reached {self.duration_sec}s capture limit. Stopping capture.")
        self._stop_event.set()

    async def run(self):
        self.logger.info("=" * 70)
        self.logger.info("AutotranscribeDiscordPy - Voice Channel 10-Second Capture")
        self.logger.info(f"Guild ID:   {GUILD_ID}")
        self.logger.info(f"Channel ID: {CHANNEL_ID}")
        self.logger.info(f"Duration:   {self.duration_sec} seconds")
        self.logger.info(f"Log File:   {LOG_FILE}")
        self.logger.info("=" * 70)

        self.gateway = DiscordGateway()
        creds = await self.gateway.connect_and_join_vc()
        self.logger.info(f"Gateway Voice credentials received. Endpoint: {creds.endpoint}")

        self.voice_client = DiscordVoiceClient(creds)

        # Hook the audio packet handler
        self.voice_client._audio_callback = self._on_audio_packet

        # Launch voice client connect in background task
        voice_task = asyncio.create_task(self.voice_client.connect())

        # Wait until capture is completed or max timeout waiting for audio activity
        max_idle_wait = 45.0
        start_wait = time.time()
        try:
            while not self._stop_event.is_set():
                if not self.capture_started and (time.time() - start_wait > max_idle_wait):
                    self.logger.warning("Capture timed out waiting for audio activity.")
                    break
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.logger.info("Closing voice connection and flushing audio files...")
            await self.voice_client.close()
            voice_task.cancel()
            await self.gateway.close()
            self.sink.close()

        # Output final statistics summary
        self.logger.info("=" * 70)
        self.logger.info("CAPTURE SUMMARY")
        self.logger.info(f"Total RTP Packets Captured: {self.total_packets}")
        for ssrc, stat in self.speaker_stats.items():
            duration_s = stat["samples"] / 48000.0
            self.logger.info(
                f"  Speaker SSRC {ssrc} (User: {stat['user_id']}): "
                f"{stat['packets']} packets, {stat['bytes']} bytes, {duration_s:.2f}s audio"
            )
        self.logger.info(f"Log file written to: {os.path.abspath(LOG_FILE)}")
        self.logger.info(f"Audio WAV files saved to '{self.output_dir}':")
        if os.path.exists(self.output_dir):
            for f in sorted(os.listdir(self.output_dir)):
                if f.startswith(self.output_prefix) and f.endswith(".wav"):
                    p = os.path.join(self.output_dir, f)
                    size = os.path.getsize(p)
                    self.logger.info(f"  - {p} ({size:,} bytes)")
        self.logger.info("=" * 70)

if __name__ == "__main__":
    capturer = VCCapturer(duration_sec=10.0)
    try:
        asyncio.run(capturer.run())
    except KeyboardInterrupt:
        pass
