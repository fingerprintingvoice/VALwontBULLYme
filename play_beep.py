#!/usr/bin/env python3
"""
Plays a 1-second sine wave beep (880Hz) into the active Discord Voice Channel
using DAVE v1 E2EE audio transmission.
"""

import asyncio
import math
import struct
import time

from autotranscribe.gateway import DiscordGateway
from autotranscribe.voice_client import DiscordVoiceClient
from autotranscribe.config import GUILD_ID, CHANNEL_ID


def generate_beep_chunks(frequency_hz: float = 880.0, duration_sec: float = 1.0, sample_rate: int = 48000):
    """
    Generates 20ms chunks of 48kHz stereo 16-bit PCM containing a clean sine wave
    with smooth 10ms fade-in and fade-out envelopes to prevent clicks.
    """
    total_frames = int(sample_rate * duration_sec)
    chunk_frames = 960  # 20ms at 48kHz
    chunks = []
    current_frame = 0

    fade_samples = 480  # 10ms

    while current_frame < total_frames:
        samples = []
        n = min(chunk_frames, total_frames - current_frame)
        for i in range(n):
            frame_idx = current_frame + i
            t = frame_idx / sample_rate

            # Smooth fade in / fade out envelope
            fade = 1.0
            if frame_idx < fade_samples:
                fade = frame_idx / float(fade_samples)
            elif (total_frames - frame_idx) < fade_samples:
                fade = (total_frames - frame_idx) / float(fade_samples)

            # Amplitude ~35% (-9 dBFS)
            val = int(math.sin(2.0 * math.pi * frequency_hz * t) * 12000.0 * fade)
            samples.extend([val, val])  # Stereo (L, R)

        if n < chunk_frames:
            samples.extend([0, 0] * (chunk_frames - n))

        pcm = struct.pack(f"<{len(samples)}h", *samples)
        chunks.append(pcm)
        current_frame += chunk_frames

    return chunks


async def main():
    print("=" * 65)
    print("  AutotranscribeDiscordPy - Outbound DAVE v1 Audio Stream")
    print(f"  Target Guild:   {GUILD_ID}")
    print(f"  Target Channel: {CHANNEL_ID}")
    print("  Payload:        1-Second 880Hz Sine Wave Beep")
    print("=" * 65)

    print("[Step 1/3] Synthesizing 1.0-second 880Hz PCM beep audio...")
    beep_chunks = generate_beep_chunks(frequency_hz=880.0, duration_sec=1.0)
    print(f"[Step 1/3] Generated {len(beep_chunks)} x 20ms audio frames ({len(beep_chunks) * 3840:,} bytes).")

    gateway = DiscordGateway()
    voice_client = None

    try:
        print("[Step 2/3] Connecting to Discord Gateway to request Voice Session...")
        creds = await gateway.connect_and_join_vc()
        print(f"[Step 2/3] Voice Session Acquired! Server: {creds.endpoint}")

        voice_client = DiscordVoiceClient(creds)
        voice_task = asyncio.create_task(voice_client.connect())

        print("[Step 3/3] Waiting for DAVE v1 MLS group handshake and key schedule...")
        await voice_client.wait_until_ready(timeout=15.0)
        print("🔒 DAVE MLS Group Session is ACTIVE and ready for E2EE transmission!")

        # Small 200ms pause to let voice receiver settle
        await asyncio.sleep(0.2)

        print("🎙️ Transmitting 1-second beep into the voice channel...")
        await voice_client.play_pcm_stream(beep_chunks)
        print("✅ Transmission complete!")

        # Small 300ms pause before disconnecting cleanly
        await asyncio.sleep(0.3)

    except Exception as e:
        print(f"[Error] {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Closing voice connection cleanly...")
        if voice_client:
            await voice_client.close()
        if 'voice_task' in locals() and not voice_task.done():
            voice_task.cancel()
        await gateway.close()
        print("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())
