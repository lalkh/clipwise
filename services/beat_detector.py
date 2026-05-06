#!/usr/bin/env python3
"""
Audio beat detection for video editing.
Extracts BPM, beat times, onset peaks, and downbeats from audio/video files.

Usage: python3 beat_detector.py <audio_or_video_path>
Output: JSON to stdout
"""
import json
import os
import subprocess
import sys
import tempfile

import librosa
import numpy as np


def extract_audio(input_path: str) -> str:
    """Extract audio from video to a temp WAV file. Returns empty string if no audio."""
    ext = os.path.splitext(input_path)[1].lower()
    if ext in (".wav", ".mp3", ".flac", ".ogg", ".aac", ".m4a"):
        return input_path

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", input_path, "-vn", "-ar", "22050", "-ac", "1", tmp],
        capture_output=True,
    )
    if result.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 100:
        # No audio track or extraction failed
        if os.path.exists(tmp):
            os.unlink(tmp)
        return ""
    return tmp


def detect_beats(audio_path: str) -> dict:
    """Detect beats, onsets, and energy peaks from audio."""
    y, sr = librosa.load(audio_path, sr=22050)
    duration = len(y) / sr

    if duration < 0.5:
        return {
            "bpm": 0, "duration": duration,
            "beats": [], "onsets": [], "strong_peaks": [],
            "beat_intervals": [], "downbeats": [],
        }

    # Beat tracking
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    bpm = float(np.asarray(tempo).flat[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()

    # Onset detection
    onset_frames = librosa.onset.onset_detect(y=y, sr=sr)
    onset_times = librosa.frames_to_time(onset_frames, sr=sr).tolist()

    # Energy peaks (strong onsets - best cut points)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    peak_frames = librosa.util.peak_pick(
        onset_env, pre_max=3, post_max=3, pre_avg=3, post_avg=5, delta=0.5, wait=10
    )
    peak_times = librosa.frames_to_time(peak_frames, sr=sr).tolist()

    # Beat intervals
    beat_intervals = []
    for i in range(1, len(beat_times)):
        beat_intervals.append(round(beat_times[i] - beat_times[i - 1], 4))

    # Downbeats (every 4th beat starting from the first)
    downbeats = [beat_times[i] for i in range(0, len(beat_times), 4)]

    # Round all values
    beat_times = [round(t, 4) for t in beat_times]
    onset_times = [round(t, 4) for t in onset_times]
    peak_times = [round(t, 4) for t in peak_times]
    downbeats = [round(t, 4) for t in downbeats]

    return {
        "bpm": round(bpm, 1),
        "duration": round(duration, 3),
        "beats": beat_times,
        "onsets": onset_times,
        "strong_peaks": peak_times,
        "beat_intervals": beat_intervals,
        "downbeats": downbeats,
    }


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python3 beat_detector.py <audio_or_video_path>"}))
        sys.exit(1)

    input_path = sys.argv[1]
    if not os.path.exists(input_path):
        print(json.dumps({"error": f"File not found: {input_path}"}))
        sys.exit(1)

    audio_path = extract_audio(input_path)
    if not audio_path:
        print(json.dumps({
            "bpm": 0, "duration": 0,
            "beats": [], "onsets": [], "strong_peaks": [],
            "beat_intervals": [], "downbeats": [],
            "error": "No audio track found"
        }))
        return

    try:
        result = detect_beats(audio_path)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        if audio_path != input_path and os.path.exists(audio_path):
            os.unlink(audio_path)


if __name__ == "__main__":
    main()
