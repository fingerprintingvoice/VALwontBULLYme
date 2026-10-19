# AutotranscribeDiscordPy

A native reimplementation of **Serein's voice protocol** and **Discord DAVE v1 (Audio/Video End-to-End Encryption)** built with **Python** and **Rust**.

This project connects to Discord Voice Channels without third-party frameworks like `discord-native-voice` or `speechrecognition`. It implements the exact transport and cryptographic specifications defined by Discord's modern voice architecture and Serein.

---

## Architecture & Layout

```
AutotranscribeDiscordPy/
├── crates/                         # Native Rust crates
│   └── dave_voice/
│       ├── Cargo.toml              # Direct cdylib build
│       ├── src/lib.rs              # DAVE MLS + Serein XChaCha20Poly1305 C-ABI
│       └── target/release/         # Built shared library (gitignored)
│           └── libdave_voice.so
├── autotranscribe/                 # Pure Python package (no binary clutter)
│   ├── __init__.py
│   ├── config.py                   # Credentials, Guild ID, Channel ID, and browser fingerprint
│   ├── gateway.py                  # Discord Gateway v10 (User token Auth, Voice State Update)
│   ├── voice_client.py             # Voice WS v8 + UDP transport & DAVE v1 handshake
│   ├── native_bridge.py            # ctypes bridge loading directly from crates/ target/
│   └── audio_decoder.py            # Dynamic libopus.so.0 wrapper, PLC, & MultiSpeakerAudioSink
├── output/                         # Unified output directory (gitignored)
│   ├── captures/                   # Per-speaker and mixed .wav recordings
│   └── logs/                       # Telemetry and session logs
├── main.py                         # Continuous voice listener daemon
├── capture_vc.py                   # 10-second capture & telemetry tool
├── README.md
└── .gitignore
```

---

## Key Protocol Milestones Implemented

1. **Gateway v10 Connection & Browser Fingerprint (`gateway.py`)**:
   - Replicates Serein's official browser fingerprint properties (`Chrome / Linux / 128.0.0.0`, `client_build_number: 634304`) to prevent automated client / bot quarantine.
   - Connects to `wss://gateway.discord.gg/?v=10&encoding=json` and authenticates via Opcode 2.
   - Emits Opcode 4 (Voice State Update) with `guild_id: 1483217544214085663` and `channel_id: 1550276943406964778`.
   - Handles `VOICE_STATE_UPDATE` and `VOICE_SERVER_UPDATE` to acquire the active session token and voice media endpoint.

2. **C++ Native Engine & Ninja Build (`native/`)**:
   - **XChaCha20-Poly1305 AEAD (`aead_xchacha20_poly1305_rtpsize`)**:
     - Custom RFC 8439 HChaCha20 20-round quarter-round permutation in C++ for 32-byte subkey derivation.
     - OpenSSL-accelerated ChaCha20-Poly1305 AEAD decryption with 12-byte RTP Associated Data (AAD).
   - **RTP Unpacking**: Strips extension headers and padding bytes in native code before exposing frames.
   - **UDP Discovery**: Constructs Discord's 74-byte discovery packet and parses the public mapped IP/port response.

3. **DAVE Protocol v1 E2EE Negotiation (`voice_client.py`)**:
   - Connects to Discord Voice Gateway v8 (`wss://{endpoint}/?v=8`).
   - Identifies with `max_dave_protocol_version: 1`.
   - Emits Opcode 1 (Select Protocol) with `mode: "aead_xchacha20_poly1305_rtpsize"`.
   - Generates and transmits the client's MLS KeyPackage on **Opcode 26** (`bytes([26]) + key_package`).
   - Receives and processes **Opcode 25** (`set_external_sender`), **Opcode 30** (`Welcome`), and acknowledges epoch transitions with **Opcode 23**.
   - Maintains UDP keepalive pings (`0x13, 0x37, 0xCA, 0xFE`) and decrypts real-time incoming voice packets from speaking participants.

---

## Compiling & Running

### 1. Build the C++ Shared Library with Ninja:
```bash
ninja
```
This compiles `native/src/dave_voice.cpp`, links the MLS static library and OpenSSL into `build/libdave_voice.so`.

### 2. Run the Voice Client:
```bash
python3 main.py
```
