"""Обёртки над ffmpeg для извлечения звука и кадров."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def has_audio_stream(video_path: Path) -> bool:
    """True, если в файле есть хотя бы одна аудидорожка."""
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool((probe.stdout or "").strip())


def extract_audio(video_path: Path, audio_path: Path) -> Path:
    """Извлекает моно-аудио в mp3 для Whisper."""
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    logger.info("Извлечение аудио: %s", video_path.name)
    subprocess.run(cmd, check=True, capture_output=True)
    return audio_path


def extract_frames(video_path: Path, frames_dir: Path, max_frames: int = 8) -> list[Path]:
    """Извлекает равномерно распределённые кадры из видео."""
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Длительность
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        duration = 30.0

    if duration <= 0:
        duration = 30.0

    # fps так, чтобы получить ~max_frames кадров
    fps = max(max_frames / duration, 0.1)
    pattern = str(frames_dir / "frame_%03d.jpg")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps={fps:.4f}",
        "-frames:v",
        str(max_frames),
        pattern,
    ]
    logger.info(
        "Извлечение до %s кадров из %s (длительность=%.1fс)",
        max_frames,
        video_path.name,
        duration,
    )
    subprocess.run(cmd, check=True, capture_output=True)
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    logger.info("Получено кадров: %s", len(frames))
    return frames
