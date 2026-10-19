import ctypes
import wave
import struct
from typing import Optional, Dict, Tuple

class OpusDecoder:
    """Wrapper for libopus.so.0 to decode 48kHz stereo Opus frames into 16-bit PCM."""

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

        self._lib.opus_decode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._lib.opus_decode.restype = ctypes.c_int

        self._lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        self._lib.opus_decoder_destroy.restype = None

        err = ctypes.c_int(0)
        self._decoder = self._lib.opus_decoder_create(
            self.sample_rate, self.channels, ctypes.byref(err)
        )
        if err.value != 0 or not self._decoder:
            raise RuntimeError(f"Failed to initialize opus decoder (code {err.value})")

        # 120ms max buffer at 48kHz (5760 samples per channel)
        self._max_samples = 5760
        self._pcm_buf = (ctypes.c_int16 * (self._max_samples * self.channels))()

    def decode(self, opus_bytes: bytes) -> Optional[bytes]:
        """Decodes an Opus packet into 16-bit PCM bytes."""
        if not opus_bytes:
            return None

        # Standard Discord Opus silence marker: 3 bytes \xf8\xff\xfe
        if opus_bytes == b"\xf8\xff\xfe":
            # Output 20ms of pure silence (960 stereo samples = 3840 bytes)
            return b"\x00" * (960 * self.channels * 2)

        in_buf = (ctypes.c_uint8 * len(opus_bytes)).from_buffer_copy(opus_bytes)
        samples = self._lib.opus_decode(
            self._decoder,
            in_buf,
            len(opus_bytes),
            self._pcm_buf,
            self._max_samples,
            0,
        )
        if samples <= 0:
            return None

        return ctypes.string_at(self._pcm_buf, samples * self.channels * 2)

    def decode_plc(self, frame_size: int = 960) -> Optional[bytes]:
        """Packet Loss Concealment: reconstructs lost audio frame using internal LPC state."""
        samples = self._lib.opus_decode(
            self._decoder,
            None,
            0,
            self._pcm_buf,
            frame_size,
            0,
        )
        if samples <= 0:
            return None

        return ctypes.string_at(self._pcm_buf, samples * self.channels * 2)

    def close(self):
        if self._decoder:
            self._lib.opus_decoder_destroy(self._decoder)
            self._decoder = None

    def __del__(self):
        self.close()


class OpusEncoder:
    """Wrapper for libopus.so.0 to encode 48kHz stereo 16-bit PCM into Opus frames."""

    APPLICATION_AUDIO = 2049
    APPLICATION_VOIP = 2048

    def __init__(self, sample_rate: int = 48000, channels: int = 2, application: int = 2048):
        self.sample_rate = sample_rate
        self.channels = channels
        self._lib = ctypes.CDLL("libopus.so.0")

        self._lib.opus_encoder_create.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
        ]
        self._lib.opus_encoder_create.restype = ctypes.c_void_p

        self._lib.opus_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_int32,
        ]
        self._lib.opus_encode.restype = ctypes.c_int32

        self._lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        self._lib.opus_encoder_destroy.restype = None

        err = ctypes.c_int(0)
        self._encoder = self._lib.opus_encoder_create(
            self.sample_rate, self.channels, application, ctypes.byref(err)
        )
        if err.value != 0 or not self._encoder:
            raise RuntimeError(f"Failed to initialize opus encoder (code {err.value})")

        self._out_buf = (ctypes.c_uint8 * 4000)()

    def encode(self, pcm_bytes: bytes, frame_size: int = 960) -> bytes:
        """
        Encodes 16-bit interleaved PCM bytes (frame_size * channels * 2 bytes) into an Opus frame.
        For 20ms @ 48kHz stereo, frame_size is 960 (3840 bytes).
        """
        pcm_arr = (ctypes.c_int16 * (len(pcm_bytes) // 2)).from_buffer_copy(pcm_bytes)
        res = self._lib.opus_encode(
            self._encoder,
            pcm_arr,
            frame_size,
            self._out_buf,
            4000,
        )
        if res < 0:
            raise RuntimeError(f"Opus encoding failed (code {res})")
        return bytes(self._out_buf[:res])

    def close(self):
        if hasattr(self, "_encoder") and self._encoder:
            self._lib.opus_encoder_destroy(self._encoder)
            self._encoder = None

    def __del__(self):
        self.close()


class MultiSpeakerAudioSink:
    """
    Demuxes incoming RTP streams per speaker, decodes Opus with independent decoder states,
    handles packet loss concealment (PLC), and writes clean WAV files.
    """

    def __init__(self, output_dir: str = "output/captures", output_prefix: str = "capture"):
        self.output_dir = output_dir
        self.output_prefix = output_prefix
        import os
        os.makedirs(self.output_dir, exist_ok=True)
        self.decoders: Dict[int, OpusDecoder] = {}
        self.wav_writers: Dict[int, wave.Wave_write] = {}
        self.speaker_packet_counts: Dict[int, int] = {}
        self.speaker_sample_counts: Dict[int, int] = {}
        self.last_seq: Dict[int, int] = {}
        
        # Mixed output writer
        self.mixed_writer: Optional[wave.Wave_write] = None
        self._init_mixed()

    def _init_mixed(self):
        import os
        filename = os.path.join(self.output_dir, f"{self.output_prefix}_mixed.wav")
        self.mixed_writer = wave.open(filename, "wb")
        self.mixed_writer.setnchannels(2)
        self.mixed_writer.setsampwidth(2)
        self.mixed_writer.setframerate(48000)

    def _get_or_create_speaker(self, key: int, name: str) -> Tuple[OpusDecoder, wave.Wave_write]:
        if key not in self.decoders:
            dec = OpusDecoder(sample_rate=48000, channels=2)
            import os
            filename = os.path.join(self.output_dir, f"{self.output_prefix}_{name}.wav")
            wf = wave.open(filename, "wb")
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(48000)
            self.decoders[key] = dec
            self.wav_writers[key] = wf
            self.speaker_packet_counts[key] = 0
            self.speaker_sample_counts[key] = 0
            self.last_seq[key] = -1
        return self.decoders[key], self.wav_writers[key]

    def write_frame(
        self, speaker_id: int, speaker_name: str, opus_frame: bytes, seq: Optional[int] = None
    ) -> Optional[int]:
        decoder, wf = self._get_or_create_speaker(speaker_id, speaker_name)

        # Handle packet loss concealment if sequence jump detected (up to 3 lost packets)
        if seq is not None and self.last_seq[speaker_id] != -1:
            expected_seq = (self.last_seq[speaker_id] + 1) & 0xFFFF
            gap = (seq - expected_seq) & 0xFFFF
            if 0 < gap <= 3:
                for _ in range(gap):
                    plc_pcm = decoder.decode_plc(960)
                    if plc_pcm:
                        wf.writeframes(plc_pcm)
                        if self.mixed_writer:
                            self.mixed_writer.writeframes(plc_pcm)
                        self.speaker_sample_counts[speaker_id] += 960

        if seq is not None:
            self.last_seq[speaker_id] = seq

        pcm = decoder.decode(opus_frame)
        if pcm:
            wf.writeframes(pcm)
            if self.mixed_writer:
                self.mixed_writer.writeframes(pcm)
            self.speaker_packet_counts[speaker_id] += 1
            num_samples = len(pcm) // 4  # 16-bit stereo = 4 bytes per sample frame
            self.speaker_sample_counts[speaker_id] += num_samples
            return num_samples
        return None

    def close(self):
        # Apply smooth 10ms fade-out silence to prevent abrupt cutoff clicks
        fade_silence = b"\x00" * (480 * 4)  # 10ms stereo silence
        for wf in self.wav_writers.values():
            try:
                wf.writeframes(fade_silence)
                wf.close()
            except Exception:
                pass

        if self.mixed_writer:
            try:
                self.mixed_writer.writeframes(fade_silence)
                self.mixed_writer.close()
            except Exception:
                pass

        for dec in self.decoders.values():
            try:
                dec.close()
            except Exception:
                pass

        self.wav_writers.clear()
        self.decoders.clear()
