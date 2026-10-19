#!/usr/bin/env python3
"""
AutotranscribeDiscordPy
Connects to Discord Voice Channel (channels/1483217544214085663/1550276943406964778)
Reimplementing Serein's native voice transport and DAVE v1 protocol in Python & C++.
"""

import asyncio
import signal
import sys
from autotranscribe.gateway import DiscordGateway
from autotranscribe.voice_client import DiscordVoiceClient
from autotranscribe.config import GUILD_ID, CHANNEL_ID

async def main():
    print("=" * 65)
    print("  AutotranscribeDiscordPy - Serein & DAVE Voice Reimplementation")
    print(f"  Target Guild ID:   {GUILD_ID}")
    print(f"  Target Channel ID: {CHANNEL_ID}")
    print("=" * 65)

    gateway = DiscordGateway()
    voice_client = None

    def handle_exit():
        print("\n[Main] Shutdown signal received, disconnecting cleanly...")
        if voice_client:
            asyncio.create_task(voice_client.close())
        asyncio.create_task(gateway.close())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_exit)
        except NotImplementedError:
            pass

    try:
        print("[Step 1/2] Connecting to Discord Gateway to request Voice Session...")
        creds = await gateway.connect_and_join_vc()
        print(f"[Step 1/2] Voice Session Acquired! Server: {creds.endpoint}")

        print("[Step 2/2] Connecting to Voice Gateway and negotiating DAVE v1...")
        voice_client = DiscordVoiceClient(creds)
        await voice_client.connect()

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[Error] {e}")
        import traceback
        traceback.print_exc()
    finally:
        if voice_client:
            await voice_client.close()
        await gateway.close()
        print("[Main] Disconnected.")

if __name__ == "__main__":
    asyncio.run(main())
