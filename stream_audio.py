#!/usr/bin/env python3
"""
Streams any audio file (.opus, .wav, .mp3, etc.) into the active Discord Voice Channel
using DAVE v1 E2EE audio transmission.
"""

import asyncio
import os
import sys
import subprocess
import time

from autotranscribe.gateway import DiscordGateway
from autotranscribe.voice_client import DiscordVoiceClient
from autotranscribe.config import GUILD_ID, CHANNEL_ID


def load_audio_chunks(file_path: str):
    """
    Decodes any audio file into 20ms chunks of 48kHz stereo 16-bit PCM (3840 bytes each)
    using ffmpeg.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    cmd = [
        "ffmpeg", "-i", file_path,
        "-f", "s16le",
        "-ar", "48000",
        "-ac", "2",
        "pipe:1"
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    chunks = []
    chunk_size = 960 * 2 * 2  # 3840 bytes (20ms at 48kHz stereo 16-bit)

    while True:
        data = proc.stdout.read(chunk_size)
        if not data:
            break
        if len(data) < chunk_size:
            data = data + b"\x00" * (chunk_size - len(data))
        chunks.append(data)

    proc.wait()
    return chunks


async def main():
    target_file = sys.argv[1] if len(sys.argv) > 1 else "demoa.opus"

    print("=" * 65)
    print("  AutotranscribeDiscordPy - DAVE v1 Audio File Streamer")
    print(f"  Target Guild:   {GUILD_ID}")
    print(f"  Target Channel: {CHANNEL_ID}")
    print(f"  Audio Source:   {target_file}")
    print("=" * 65)

    print(f"[Step 1/3] Loading and decoding '{target_file}' to 48kHz PCM...")
    chunks = load_audio_chunks(target_file)
    duration_s = (len(chunks) * 20) / 1000.0
    print(f"[Step 1/3] Loaded {len(chunks)} x 20ms frames ({duration_s:.2f}s total audio).")

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

        await asyncio.sleep(0.2)

        print(f"🎙️ Streaming '{target_file}' ({duration_s:.2f}s) into the voice channel...")
        await voice_client.play_pcm_stream(chunks)
        print("✅ Transmission complete!")

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
