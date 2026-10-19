"""
Audio Level, True Peak, and Abuse Telemetry Analyzer.
Uses libopus.so.0 floating-point decoding to detect inter-sample peak overshoots (> 0 dBFS),
digital clipping, and sustained ear-blasting/square-wave patterns in real-time.
"""

import ctypes
import math
from dataclasses import dataclass
from typing import Optional, List, Dict


@dataclass
class FrameTelemetry:
    user_id: int
    ssrc: int
    seq: int
    sample_count: int
    peak_float: float
    peak_dbfs: float
    rms_float: float
    rms_dbfs: float
    crest_factor_db: float
    overshoot_dbfs: float
    overshoot_samples: int
    is_clipping: bool
    is_ear_blaster: bool
    is_overshoot: bool


class AudioAbuseAnalyzer:
    """
    Per-speaker floating-point telemetry analyzer.
    Tracks peak, RMS, inter-sample overshoot (> 0 dBFS), and abusive patterns.
    """

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self._lib = ctypes.CDLL("libopus.so.0")

        self._lib.opus_decoder_create.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib.opus_decoder_create.restype = ctypes.c_void_p

        self._lib.opus_decode_float.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._lib.opus_decode_float.restype = ctypes.c_int

        self._lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        self._lib.opus_decoder_destroy.restype = None

        # Max 120ms frame buffer at 48kHz (5760 samples per channel)
        self._float_buf = (ctypes.c_float * (5760 * self.channels))()

        # Decoders per SSRC
        self._decoders: Dict[int, ctypes.c_void_p] = {}
        # Violation histories per user
        self.violation_counts: Dict[int, Dict[str, int]] = {}

    def _get_or_create_decoder(self, ssrc: int) -> ctypes.c_void_p:
        if ssrc not in self._decoders:
            err = ctypes.c_int(0)
            dec = self._lib.opus_decoder_create(self.sample_rate, self.channels, ctypes.byref(err))
            if err.value != 0 or not dec:
                raise RuntimeError(f"Failed to create float opus decoder (code {err.value})")
            self._decoders[ssrc] = dec
        return self._decoders[ssrc]

    def analyze_frame(self, ssrc: int, user_id: int, seq: int, opus_frame: bytes) -> Optional[FrameTelemetry]:
        """
        Decodes an Opus packet directly into 32-bit floating point PCM and measures
        True Peak, RMS, crest factor, and inter-sample overshoot.
        """
        if not opus_frame or opus_frame == b"\xf8\xff\xfe":
            return None

        decoder = self._get_or_create_decoder(ssrc)
        in_buf = (ctypes.c_uint8 * len(opus_frame)).from_buffer_copy(opus_frame)

        samples_decoded = self._lib.opus_decode_float(
            decoder,
            in_buf,
            len(opus_frame),
            self._float_buf,
            5760,
            0,
        )

        if samples_decoded <= 0:
            return None

        total_values = samples_decoded * self.channels
        max_abs = 0.0
        sum_sq = 0.0
        overshoot_count = 0
        clamped_count = 0

        for i in range(total_values):
            val = abs(self._float_buf[i])
            if val > max_abs:
                max_abs = val
            sum_sq += val * val
            if val > 1.0:
                overshoot_count += 1
            if val >= 0.999:
                clamped_count += 1

        rms = math.sqrt(sum_sq / total_values) if total_values > 0 else 0.0

        peak_dbfs = 20.0 * math.log10(max_abs) if max_abs > 1e-6 else -120.0
        rms_dbfs = 20.0 * math.log10(rms) if rms > 1e-6 else -120.0
        crest_factor_db = peak_dbfs - rms_dbfs

        overshoot_dbfs = max(0.0, peak_dbfs) if max_abs > 1.0 else 0.0
        is_overshoot = max_abs > 1.005  # > +0.04 dBFS True Peak
        is_clipping = (clamped_count / float(total_values)) > 0.08  # > 8% samples pegged at ceiling
        is_ear_blaster = (rms_dbfs > -6.0) and (crest_factor_db < 4.0)  # Low dynamic range square blasting

        # Update stats
        if user_id not in self.violation_counts:
            self.violation_counts[user_id] = {
                "overshoots": 0,
                "clipping_bursts": 0,
                "ear_blasts": 0,
                "max_true_peak_dbfs": 0.0,
            }

        stats = self.violation_counts[user_id]
        if is_overshoot:
            stats["overshoots"] += 1
            if peak_dbfs > stats["max_true_peak_dbfs"]:
                stats["max_true_peak_dbfs"] = peak_dbfs
        if is_clipping:
            stats["clipping_bursts"] += 1
        if is_ear_blaster:
            stats["ear_blasts"] += 1

        return FrameTelemetry(
            user_id=user_id,
            ssrc=ssrc,
            seq=seq,
            sample_count=samples_decoded,
            peak_float=max_abs,
            peak_dbfs=peak_dbfs,
            rms_float=rms,
            rms_dbfs=rms_dbfs,
            crest_factor_db=crest_factor_db,
            overshoot_dbfs=overshoot_dbfs,
            overshoot_samples=overshoot_count,
            is_clipping=is_clipping,
            is_ear_blaster=is_ear_blaster,
            is_overshoot=is_overshoot,
        )

    def close(self):
        for dec in self._decoders.values():
            if dec:
                self._lib.opus_decoder_destroy(dec)
        self._decoders.clear()

    def __del__(self):
        self.close()
