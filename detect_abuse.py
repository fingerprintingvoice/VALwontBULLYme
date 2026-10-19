#!/usr/bin/env python3
"""
Discord Real-Time Audio Abuse & True Peak Overshoot Detection Bot.
Connects to Discord Voice Channels with DAVE v1 E2EE MLS decryption,
analyzes incoming audio streams in real-time, and flags:
- True Peak Overshoots (> 0 dBFS, inter-sample filter ringing up to +10 dBFS)
- Hard Digital Clipping (pinned to +/- 32767 rails)
- Ear-Blasting (sustained low dynamic range square wave blasting)
"""

import asyncio
import os
import sys
import time
import argparse
import logging
from typing import Optional, Dict

from autotranscribe.config import (
    TOKEN,
    GUILD_ID,
    CHANNEL_ID,
)
from autotranscribe.gateway import DiscordGateway
from autotranscribe.voice_client import DiscordVoiceClient
from autotranscribe.audio_decoder import OpusDecoder, OpusEncoder
from autotranscribe.audio_analyzer import AudioAbuseAnalyzer, FrameTelemetry

LOG_DIR = os.path.join("output", "logs")
os.makedirs(LOG_DIR, exist_ok=True)
ALERT_LOG_FILE = os.path.join(LOG_DIR, "abuse_alerts.log")


def setup_abuse_logger():
    logger = logging.getLogger("AbuseDetector")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    fh = logging.FileHandler(ALERT_LOG_FILE, mode="a", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


class AudioAbuseMonitor:
    def __init__(self, guild_id: int = GUILD_ID, channel_id: int = CHANNEL_ID, duration_sec: Optional[float] = None):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.duration_sec = duration_sec
        self.analyzer = AudioAbuseAnalyzer(sample_rate=48000, channels=2)
        self.logger = setup_abuse_logger()
        self.gateway: Optional[DiscordGateway] = None
        self.voice_client: Optional[DiscordVoiceClient] = None
        self._stop_event = asyncio.Event()
        self.start_time = 0.0
        self.total_frames = 0
        self.alert_count = 0
        self.user_stats: Dict[int, Dict[str, any]] = {}

    def _on_audio_packet(self, ssrc: int, seq: int, ts: int, pt: int, payload: bytes):
        if not payload or payload == b"\xf8\xff\xfe":
            return

        user_id = self.voice_client.ssrc_to_user.get(ssrc, 0)
        speaker_label = f"User {user_id}" if user_id else f"SSRC {ssrc}"

        # Initialize user stats tracking
        if user_id not in self.user_stats:
            self.user_stats[user_id] = {
                "ssrc": ssrc,
                "label": speaker_label,
                "frames": 0,
                "max_true_peak": -120.0,
                "overshoot_frames": 0,
                "clipping_frames": 0,
                "ear_blast_frames": 0,
            }

        # In DAVE v1, decrypt Opus payload before analyzing
        opus_payload = None
        if not self.voice_client.dave or not self.voice_client.dave.is_ready():
            return

        try:
            opus_payload = self.voice_client.dave.decrypt_opus(user_id, payload)
        except Exception:
            return

        if not opus_payload:
            return

        # Perform floating-point telemetry analysis
        tel = self.analyzer.analyze_frame(ssrc=ssrc, user_id=user_id, seq=seq, opus_frame=opus_payload)
        if not tel:
            return

        self.total_frames += 1
        stats = self.user_stats[user_id]
        stats["frames"] += 1
        if tel.peak_dbfs > stats["max_true_peak"]:
            stats["max_true_peak"] = tel.peak_dbfs

        is_abusive = False
        flags = []

        if tel.is_overshoot:
            stats["overshoot_frames"] += 1
            flags.append(f"OVERSHOOT (+{tel.overshoot_dbfs:.2f}dB)")
            is_abusive = True

        if tel.is_clipping:
            stats["clipping_frames"] += 1
            flags.append("CLIPPING")
            is_abusive = True

        if tel.is_ear_blaster:
            stats["ear_blast_frames"] += 1
            flags.append("EAR_BLASTER")
            is_abusive = True

        if is_abusive:
            self.alert_count += 1
            alert_msg = (
                f"🚨 [ABUSE ALERT #{self.alert_count:04d}] {speaker_label} (SSRC {ssrc}) | "
                f"Peak: {tel.peak_float:.3f} ({tel.peak_dbfs:+.2f} dBFS) | RMS: {tel.rms_dbfs:+.2f} dBFS | "
                f"Flags: [{' | '.join(flags)}]"
            )
            print(alert_msg)
            self.logger.info(alert_msg)
        elif self.total_frames % 50 == 0:
            # Periodic clean telemetry heartbeat
            print(
                f"🎙️ [Normal Audio] {speaker_label} | "
                f"Peak: {tel.peak_dbfs:+.2f} dBFS | RMS: {tel.rms_dbfs:+.2f} dBFS | Crest: {tel.crest_factor_db:.1f} dB"
            )

    async def run(self):
        print("=" * 70)
        print("  DISCORD REAL-TIME AUDIO ABUSE & TRUE PEAK DETECTOR (DAVE v1)")
        print(f"  Target Guild:   {self.guild_id}")
        print(f"  Target Channel: {self.channel_id}")
        print(f"  Alerts File:    {ALERT_LOG_FILE}")
        if self.duration_sec:
            print(f"  Run Duration:   {self.duration_sec} seconds")
        print("=" * 70)

        self.gateway = DiscordGateway(guild_id=self.guild_id, channel_id=self.channel_id)
        print("[1/3] Connecting to Discord Gateway...")
        creds = await self.gateway.connect_and_join_vc()
        print(f"[1/3] Voice Session assigned. Endpoint: {creds.endpoint}")

        self.voice_client = DiscordVoiceClient(creds)
        self.voice_client._audio_callback = self._on_audio_packet

        print("[2/3] Connecting to Voice Server and establishing DAVE MLS session...")
        voice_task = asyncio.create_task(self.voice_client.connect())
        await self.voice_client.wait_until_ready(timeout=15.0)
        print("🔒 DAVE MLS session established! Listening for audio frames...")
        print("[3/3] Monitoring active speakers for audio abuse & overshoots...\n")

        self.start_time = time.time()
        try:
            if self.duration_sec:
                await asyncio.sleep(self.duration_sec)
            else:
                while True:
                    await asyncio.sleep(1.0)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            print("\nShutting down detector and summarizing session...")
            await self.voice_client.close()
            voice_task.cancel()
            await self.gateway.close()
            self.analyzer.close()
            self.print_summary()

    def print_summary(self):
        print("\n" + "=" * 70)
        print("AUDIO ABUSE DETECTION SUMMARY")
        print(f"  Total Audio Frames Processed: {self.total_frames}")
        print(f"  Total Abuse Alerts Triggered: {self.alert_count}")
        print("-" * 70)
        for uid, s in self.user_stats.items():
            print(f"  Speaker: {s['label']} (SSRC {s['ssrc']})")
            print(f"    Total Frames:     {s['frames']}")
            print(f"    Max True Peak:    {s['max_true_peak']:+.2f} dBFS")
            print(f"    Overshoot Frames: {s['overshoot_frames']}")
            print(f"    Clipping Frames:  {s['clipping_frames']}")
            print(f"    Ear Blast Frames: {s['ear_blast_frames']}")
        print("=" * 70)


def analyze_local_file(file_path: str):
    """
    Offline analyzer mode to test any local audio or Opus file directly.
    """
    if not os.path.exists(file_path):
        print(f"Error: file not found: {file_path}")
        sys.exit(1)

    print("=" * 70)
    print(f"  OFFLINE AUDIO ABUSE TEST: {file_path}")
    print("=" * 70)

    import subprocess
    cmd = [
        "ffmpeg", "-i", file_path,
        "-f", "s16le", "-ar", "48000", "-ac", "2",
        "pipe:1"
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunk_size = 960 * 2 * 2  # 3840 bytes (20ms)

    enc = OpusEncoder(sample_rate=48000, channels=2)
    analyzer = AudioAbuseAnalyzer(sample_rate=48000, channels=2)

    frame_idx = 0
    max_peak = -120.0
    overshoot_count = 0
    clipping_count = 0
    blast_count = 0

    while True:
        data = proc.stdout.read(chunk_size)
        if not data:
            break
        if len(data) < chunk_size:
            data += b"\x00" * (chunk_size - len(data))

        opus_pkt = enc.encode(data)
        tel = analyzer.analyze_frame(ssrc=1, user_id=1, seq=frame_idx, opus_frame=opus_pkt)
        if tel:
            if tel.peak_dbfs > max_peak:
                max_peak = tel.peak_dbfs
            if tel.is_overshoot:
                overshoot_count += 1
            if tel.is_clipping:
                clipping_count += 1
            if tel.is_ear_blaster:
                blast_count += 1

            t_sec = (frame_idx * 20) / 1000.0
            if tel.is_overshoot or tel.is_clipping or tel.is_ear_blaster:
                flags = []
                if tel.is_overshoot:
                    flags.append(f"OVERSHOOT (+{tel.overshoot_dbfs:.2f}dB)")
                if tel.is_clipping:
                    flags.append("CLIPPING")
                if tel.is_ear_blaster:
                    flags.append("EAR_BLASTER")
                print(f"  Frame {frame_idx:03d} [{t_sec:.2f}s]: Peak {tel.peak_float:.3f} ({tel.peak_dbfs:+.2f} dBFS) | RMS {tel.rms_dbfs:+.2f} dBFS | [{' | '.join(flags)}]")

        frame_idx += 1

    proc.wait()
    analyzer.close()

    print("-" * 70)
    print(f"Results for '{file_path}':")
    print(f"  Total Frames:     {frame_idx} ({(frame_idx * 20)/1000.0:.2f}s)")
    print(f"  Max True Peak:    {max_peak:+.2f} dBFS")
    print(f"  Overshoot Frames: {overshoot_count}")
    print(f"  Clipping Frames:  {clipping_count}")
    print(f"  Ear Blast Frames: {blast_count}")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Discord Audio Abuse & True Peak Overshoot Detector")
    parser.add_argument("--file", "-f", help="Analyze local audio file offline", default=None)
    parser.add_argument("--duration", "-d", help="Run detector in Discord VC for N seconds", type=float, default=None)
    parser.add_argument("--guild", "-g", help="Discord Guild ID", type=int, default=None)
    parser.add_argument("--channel", "-c", help="Discord Voice Channel ID", type=int, default=None)
    args = parser.parse_args()

    if args.file:
        analyze_local_file(args.file)
    else:
        guild_id = args.guild or GUILD_ID
        channel_id = args.channel or CHANNEL_ID
        monitor = AudioAbuseMonitor(guild_id=guild_id, channel_id=channel_id, duration_sec=args.duration)
        try:
            asyncio.run(monitor.run())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

