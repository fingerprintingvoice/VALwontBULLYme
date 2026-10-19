"""
Configuration and constants for AutotranscribeDiscordPy.
Mirrors Serein's official browser fingerprint and protocol configurations.
"""

TOKEN = "MTU2MTg3Nzg0OTk2NTczMTk1Mw.G576k-._HgwoBCYX-A1wHJLkXTK-SGXMHFrZ_HVPYxTFw"

GUILD_ID = 1483217544214085663
CHANNEL_ID = 1549861666248855684

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
VOICE_GATEWAY_VERSION = 8

# Serein-compatible browser fingerprint properties to prevent bot quarantine
BROWSER_PROPERTIES = {
    "os": "Linux",
    "browser": "Chrome",
    "device": "",
    "system_locale": "en-US",
    "browser_user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "browser_version": "128.0.0.0",
    "os_version": "",
    "referrer": "",
    "referring_domain": "",
    "referrer_current": "",
    "referring_domain_current": "",
    "release_channel": "stable",
    "client_build_number": 634304,
    "client_event_source": None
}
