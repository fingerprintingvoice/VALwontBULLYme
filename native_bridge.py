import os
import ctypes
from typing import Optional, Tuple

def _find_library_path() -> str:
    pkg_dir = os.path.dirname(__file__)
    root_dir = os.path.abspath(os.path.join(pkg_dir, ".."))
    candidates = [
        os.path.join(root_dir, "crates", "dave_voice", "target", "release", "libdave_voice.so"),
        os.path.join(root_dir, "target", "release", "libdave_voice.so"),
        os.path.join(pkg_dir, "lib", "libdave_voice.so"),
    ]
    for p in candidates:
        abs_p = os.path.abspath(p)
        if os.path.exists(abs_p):
            return abs_p
    return os.path.abspath(candidates[0])

_SO_PATH = _find_library_path()

class _NativeLibrary:
    _instance: Optional["_NativeLibrary"] = None
    _lib: Optional[ctypes.CDLL] = None

    @classmethod
    def get(cls) -> ctypes.CDLL:
        if cls._lib is None:
            so_path = _find_library_path()
            if not os.path.exists(so_path):
                raise FileNotFoundError(
                    f"Native library {so_path} not found. Please compile with:\n"
                    f"  cargo build --release --manifest-path crates/dave_voice/Cargo.toml"
                )
            lib = ctypes.CDLL(so_path)
            cls._bind_signatures(lib)
            cls._lib = lib
        return cls._lib

    @staticmethod
    def _bind_signatures(lib: ctypes.CDLL):
        # DAVE session
        lib.dave_voice_new.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
        lib.dave_voice_new.restype = ctypes.c_void_p

        lib.dave_voice_free.argtypes = [ctypes.c_void_p]
        lib.dave_voice_free.restype = None

        lib.dave_voice_get_key_package.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.dave_voice_get_key_package.restype = ctypes.c_int

        lib.dave_voice_set_external_sender.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
        ]
        lib.dave_voice_set_external_sender.restype = ctypes.c_int

        lib.dave_voice_process_proposals.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint8,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.dave_voice_process_proposals.restype = ctypes.c_int

        lib.dave_voice_process_commit.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
        ]
        lib.dave_voice_process_commit.restype = ctypes.c_int

        lib.dave_voice_process_welcome.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
        ]
        lib.dave_voice_process_welcome.restype = ctypes.c_int

        lib.dave_voice_is_ready.argtypes = [ctypes.c_void_p]
        lib.dave_voice_is_ready.restype = ctypes.c_int

        lib.dave_voice_decrypt_opus.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.dave_voice_decrypt_opus.restype = ctypes.c_int

        lib.dave_voice_encrypt_opus.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.dave_voice_encrypt_opus.restype = ctypes.c_int

        # Transport crypto
        lib.xchacha20_rtp_decrypt.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint8),
        ]
        lib.xchacha20_rtp_decrypt.restype = ctypes.c_int

        lib.xchacha20_rtp_encrypt.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint16,
            ctypes.c_uint32,
            ctypes.c_uint8,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        lib.xchacha20_rtp_encrypt.restype = ctypes.c_int

        # UDP Helpers
        lib.build_udp_discovery_packet.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
        ]
        lib.build_udp_discovery_packet.restype = None

        lib.parse_udp_discovery_response.argtypes = [
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_uint16),
        ]
        lib.parse_udp_discovery_response.restype = ctypes.c_int

        lib.build_udp_keepalive_packet.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
        ]
        lib.build_udp_keepalive_packet.restype = None


class DaveVoiceSession:
    """Manages DAVE v1 E2EE MLS group key lifecycle via C++ / Native."""

    def __init__(self, user_id: int, channel_id: int):
        self._lib = _NativeLibrary.get()
        self._handle = self._lib.dave_voice_new(user_id, channel_id)
        if not self._handle:
            raise RuntimeError("Failed to create native DAVE voice session.")

    def __del__(self):
        if hasattr(self, "_handle") and self._handle:
            self._lib.dave_voice_free(self._handle)
            self._handle = None

    def get_key_package(self) -> bytes:
        buf = (ctypes.c_uint8 * 4096)()
        out_len = ctypes.c_size_t(0)
        res = self._lib.dave_voice_get_key_package(
            self._handle, buf, 4096, ctypes.byref(out_len)
        )
        if res != 0:
            raise RuntimeError(f"Failed to generate DAVE KeyPackage (code {res})")
        return bytes(buf[: out_len.value])

    def set_external_sender(self, data: bytes):
        arr = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        res = self._lib.dave_voice_set_external_sender(self._handle, arr, len(data))
        if res != 0:
            raise RuntimeError(f"Failed to set external sender (code {res})")

    def process_proposals(self, op_type: int, data: bytes) -> Optional[bytes]:
        arr = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        out_buf = (ctypes.c_uint8 * 8192)()
        out_len = ctypes.c_size_t(0)
        res = self._lib.dave_voice_process_proposals(
            self._handle, op_type, arr, len(data), out_buf, 8192, ctypes.byref(out_len)
        )
        if res == 1:
            return bytes(out_buf[: out_len.value])
        elif res == 0:
            return None
        else:
            raise RuntimeError(f"Failed to process proposals (code {res})")

    def process_commit(self, data: bytes):
        arr = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        res = self._lib.dave_voice_process_commit(self._handle, arr, len(data))
        if res != 0:
            raise RuntimeError(f"Failed to process commit (code {res})")

    def process_welcome(self, data: bytes):
        arr = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        res = self._lib.dave_voice_process_welcome(self._handle, arr, len(data))
        if res != 0:
            raise RuntimeError(f"Failed to process welcome (code {res})")

    def is_ready(self) -> bool:
        return bool(self._lib.dave_voice_is_ready(self._handle))

    def decrypt_opus(self, user_id: int, frame: bytes) -> bytes:
        in_arr = (ctypes.c_uint8 * len(frame)).from_buffer_copy(frame)
        out_buf = (ctypes.c_uint8 * 4096)()
        out_len = ctypes.c_size_t(0)
        res = self._lib.dave_voice_decrypt_opus(
            self._handle, user_id, in_arr, len(frame), out_buf, 4096, ctypes.byref(out_len)
        )
        if res != 0:
            raise RuntimeError(f"DAVE Opus decryption failed (code {res})")
        return bytes(out_buf[: out_len.value])

    def encrypt_opus(self, frame: bytes) -> bytes:
        in_arr = (ctypes.c_uint8 * len(frame)).from_buffer_copy(frame)
        out_buf = (ctypes.c_uint8 * 4096)()
        out_len = ctypes.c_size_t(0)
        res = self._lib.dave_voice_encrypt_opus(
            self._handle, in_arr, len(frame), out_buf, 4096, ctypes.byref(out_len)
        )
        if res != 0:
            raise RuntimeError(f"DAVE Opus encryption failed (code {res})")
        return bytes(out_buf[: out_len.value])


class TransportCrypto:
    """XChaCha20-Poly1305 transport encryption and RTP handling."""

    @staticmethod
    def decrypt_rtp(key: bytes, packet: bytes) -> Optional[Tuple[bytes, int, int, int, int]]:
        """
        Decrypts an RTP packet encrypted with aead_xchacha20_poly1305_rtpsize.
        Returns: (frame_payload, ssrc, sequence, timestamp, payload_type) or None on failure.
        """
        lib = _NativeLibrary.get()
        key_arr = (ctypes.c_uint8 * 32).from_buffer_copy(key)
        pkt_arr = (ctypes.c_uint8 * len(packet)).from_buffer_copy(packet)
        out_buf = (ctypes.c_uint8 * 4096)()
        out_len = ctypes.c_size_t(0)
        ssrc = ctypes.c_uint32(0)
        seq = ctypes.c_uint16(0)
        ts = ctypes.c_uint32(0)
        pt = ctypes.c_uint8(0)

        res = lib.xchacha20_rtp_decrypt(
            key_arr,
            pkt_arr,
            len(packet),
            out_buf,
            4096,
            ctypes.byref(out_len),
            ctypes.byref(ssrc),
            ctypes.byref(seq),
            ctypes.byref(ts),
            ctypes.byref(pt),
        )
        if res != 0:
            return None
        return (bytes(out_buf[: out_len.value]), ssrc.value, seq.value, ts.value, pt.value)

    @staticmethod
    def encrypt_rtp(
        key: bytes,
        counter: int,
        ssrc: int,
        sequence: int,
        timestamp: int,
        payload_type: int,
        payload: bytes,
    ) -> bytes:
        """
        Encrypts an RTP packet payload using aead_xchacha20_poly1305_rtpsize.
        Returns full encrypted RTP packet ready for wire transmission.
        """
        lib = _NativeLibrary.get()
        key_arr = (ctypes.c_uint8 * 32).from_buffer_copy(key)
        pl_arr = (ctypes.c_uint8 * len(payload)).from_buffer_copy(payload)
        out_buf = (ctypes.c_uint8 * 4096)()
        out_len = ctypes.c_size_t(0)

        res = lib.xchacha20_rtp_encrypt(
            key_arr,
            counter,
            ssrc,
            sequence,
            timestamp,
            payload_type,
            pl_arr,
            len(payload),
            out_buf,
            4096,
            ctypes.byref(out_len),
        )
        if res != 0:
            raise RuntimeError(f"xchacha20_rtp_encrypt failed (code {res})")
        return bytes(out_buf[: out_len.value])


class UdpHelper:
    """Discord Voice UDP packet builders and parsers."""

    @staticmethod
    def build_discovery_packet(ssrc: int) -> bytes:
        lib = _NativeLibrary.get()
        buf = (ctypes.c_uint8 * 74)()
        lib.build_udp_discovery_packet(ssrc, buf)
        return bytes(buf)

    @staticmethod
    def parse_discovery_response(packet: bytes, ssrc: int) -> Tuple[str, int]:
        lib = _NativeLibrary.get()
        pkt_arr = (ctypes.c_uint8 * 74).from_buffer_copy(packet)
        ip_buf = ctypes.create_string_buffer(64)
        port = ctypes.c_uint16(0)
        res = lib.parse_udp_discovery_response(pkt_arr, ssrc, ip_buf, ctypes.byref(port))
        if res != 0:
            raise ValueError(f"Invalid discovery packet response (code {res})")
        return ip_buf.value.decode("utf-8"), port.value

    @staticmethod
    def build_keepalive_packet(sequence: int) -> bytes:
        lib = _NativeLibrary.get()
        buf = (ctypes.c_uint8 * 8)()
        lib.build_udp_keepalive_packet(sequence, buf)
        return bytes(buf)
