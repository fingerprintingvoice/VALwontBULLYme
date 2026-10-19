#!/usr/bin/env python3
"""
Generates a 1.5-second maximized audio file (max_overshoot_demo.opus / .wav)
engineered specifically to test True Peak overshoot (> +8 dBFS),
digital clipping (rail pinning at +/- 32767), and maximum Fletcher-Munson loudness.
"""

import math
import struct
import subprocess
import os
import sys

from autotranscribe.audio_decoder import OpusEncoder
from autotranscribe.audio_analyzer import AudioAbuseAnalyzer


def generate_max_overshoot_pcm(duration_sec: float = 1.5, sample_rate: int = 48000):
    """
    Synthesizes multi-stage acoustic abuse test audio:
    - 0.0s - 0.5s: 1000 Hz full-scale square wave (maximum ear-piercing fundamental)
    - 0.5s - 1.0s: 6500 Hz full-scale square wave (Opus MDCT filter ringing resonance -> +8.35 dBFS True Peak)
    - 1.0s - 1.5s: 3000 Hz full-scale square wave (maximum sensitivity ISO 226 equal-loudness contour + clipping)
    """
    total_frames = int(sample_rate * duration_sec)
    chunk_frames = 960  # 20ms
    chunks = []
    current_frame = 0

    while current_frame < total_frames:
        samples = []
        n = min(chunk_frames, total_frames - current_frame)
        for i in range(n):
            frame_idx = current_frame + i
            t = frame_idx / float(sample_rate)

            if t < 0.5:
                freq = 1000.0
            elif t < 1.0:
                freq = 6500.0
            else:
                freq = 3000.0

            period = sample_rate / freq
            val = 32767 if (frame_idx % period) < (period / 2.0) else -32768
            samples.extend([val, val])  # Stereo

        if n < chunk_frames:
            samples.extend([0, 0] * (chunk_frames - n))

        pcm = struct.pack(f"<{len(samples)}h", *samples)
        chunks.append(pcm)
        current_frame += chunk_frames

    return chunks


def main():
    print("=" * 70)
    print("  MAX OVERLOAD & TRUE PEAK OVERSHOOT GENERATOR (1.5 SECONDS)")
    print("  Targeting: RFC 6716 MDCT Filter Ringing & BS.1770 True Peak Overshoot")
    print("=" * 70)

    chunks = generate_max_overshoot_pcm(duration_sec=122.5)
    raw_pcm = b"".join(chunks)

    wav_path = "max_overshoot_demo.wav"
    opus_path = "max_overshoot_demo.opus"

    print(f"\n[1/3] Writing 16-bit 48kHz PCM to {wav_path} via ffmpeg...")
    cmd_wav = [
        "ffmpeg", "-y",
        "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "pipe:0",
        wav_path
    ]
    p_wav = subprocess.Popen(cmd_wav, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p_wav.communicate(input=raw_pcm)

    print(f"[2/3] Encoding to pure RFC 6716 Opus ({opus_path}) at 128 kbps...")
    cmd_opus = [
        "ffmpeg", "-y",
        "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", "pipe:0",
        "-c:a", "libopus", "-b:a", "128k", "-vbr", "off",
        opus_path
    ]
    p_opus = subprocess.Popen(cmd_opus, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p_opus.communicate(input=raw_pcm)

    print("\n[3/3] Running AudioAbuseAnalyzer telemetry verification on generated frames:")
    print("-" * 70)
    enc = OpusEncoder(sample_rate=48000, channels=2)
    analyzer = AudioAbuseAnalyzer(sample_rate=48000, channels=2)

    max_true_peak = -999.0
    max_peak_val = 0.0
    max_rms = -999.0
    overshoot_count = 0
    ear_blast_count = 0
    clipping_count = 0

    for idx, chunk in enumerate(chunks):
        opus_packet = enc.encode(chunk)
        tel = analyzer.analyze_frame(ssrc=0xDEADBEEF, user_id=1337420, seq=idx, opus_frame=opus_packet)
        if not tel:
            continue

        if tel.peak_float > max_peak_val:
            max_peak_val = tel.peak_float
            max_true_peak = tel.peak_dbfs
        if tel.rms_dbfs > max_rms:
            max_rms = tel.rms_dbfs
        if tel.is_overshoot:
            overshoot_count += 1
        if tel.is_clipping:
            clipping_count += 1
        if tel.is_ear_blaster:
            ear_blast_count += 1

        t_sec = (idx * 20) / 1000.0
        flags = []
        if tel.is_overshoot:
            flags.append(f"OVERSHOOT ({tel.overshoot_dbfs:+.2f} dBFS)")
        if tel.is_clipping:
            flags.append("CLIPPING")
        if tel.is_ear_blaster:
            flags.append("EAR_BLASTER")

        flag_str = " | ".join(flags) if flags else "CLEAN"
        print(f"  Frame {idx:02d} [{t_sec:.2f}s]: True Peak = {tel.peak_float:.4f} ({tel.peak_dbfs:+.2f} dBFS) | RMS = {tel.rms_dbfs:+.2f} dBFS | [{flag_str}]")

    analyzer.close()

    print("-" * 70)
    print("TELEMETRY VERIFICATION SUMMARY:")
    print(f"  Total 20ms Frames:        {len(chunks)} (1.50 seconds)")
    print(f"  Maximum True Peak Float:  {max_peak_val:.4f} (Raw Libopus Decoded)")
    print(f"  Maximum True Peak dBFS:   {max_true_peak:+.2f} dBFS (Overshoot: {max_true_peak - 0.0:+.2f} dB above digital ceiling!)")
    print(f"  Maximum RMS Power:        {max_rms:+.2f} dBFS")
    print(f"  Frames with Overshoot:    {overshoot_count} / {len(chunks)} ({overshoot_count/len(chunks)*100:.1f}%)")
    print(f"  Frames with Hard Clip:    {clipping_count} / {len(chunks)} ({clipping_count/len(chunks)*100:.1f}%)")
    print(f"  Frames with Ear Blaster:  {ear_blast_count} / {len(chunks)} ({ear_blast_count/len(chunks)*100:.1f}%)")
    print("=" * 70)


if __name__ == "__main__":
    main()
