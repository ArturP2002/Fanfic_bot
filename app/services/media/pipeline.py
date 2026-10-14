"""Пайплайн разбора ролика и скриншотов."""

from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from app.config import get_settings
from app.services.extract import ExtractedWork, extract_works_with_llm
from app.services.media.download import download_video_from_url
from app.services.media.errors import PipelineError, ProcessingError, RateLimitError
from app.services.media.ffmpeg_tools import extract_audio, extract_frames, has_audio_stream
from app.services.media.ocr import ocr_images
from app.services.media.whisper_stt import transcribe_audio

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


def _job_dir() -> Path:
    settings = get_settings()
    root = Path(settings.tmp_dir) / str(uuid4())
    root.mkdir(parents=True, exist_ok=True)
    logger.info("Создана временная папка: %s", root)
    return root


def cleanup_dir(path: Path | None) -> None:
    """Удаляет временную директорию после обработки."""
    if path and path.exists():
        shutil.rmtree(path, ignore_errors=True)
        logger.info("Временные файлы удалены: %s", path)


async def _emit(on_progress: ProgressCallback | None, stage: str) -> None:
    if on_progress is None:
        return
    try:
        await on_progress(stage)
    except Exception:
        logger.exception("Не удалось отправить этап прогресса: %s", stage)


async def analyze_video_file(
    video_path: Path,
    *,
    on_progress: ProgressCallback | None = None,
    caption: str | None = None,
) -> tuple[list[ExtractedWork], str, str]:
    """Полный анализ локального видеофайла: речь + OCR кадров + caption.

    Возвращает (works, transcript, ocr_text).
    """
    settings = get_settings()
    work_dir = video_path.parent
    audio_path = work_dir / "audio.mp3"
    frames_dir = work_dir / "frames"
    started = time.monotonic()

    size = video_path.stat().st_size if video_path.exists() else 0
    logger.info(
        "Старт анализа видеофайла: path=%s size=%s байт max_frames=%s caption=%s",
        video_path,
        size,
        settings.video_max_frames,
        bool(caption),
    )

    transcript = ""
    ocr_text = ""
    try:
        if not has_audio_stream(video_path):
            logger.info("Аудидорожки нет — Whisper пропускаю")
        else:
            await _emit(on_progress, "speech")
            t0 = time.monotonic()
            logger.info("Шаг 1/4: извлечение аудио…")
            extract_audio(video_path, audio_path)
            logger.info(
                "Аудио готово: %s (%.1f с)",
                audio_path,
                time.monotonic() - t0,
            )
            t0 = time.monotonic()
            logger.info("Шаг 2/4: распознавание речи (Whisper)…")
            transcript = await transcribe_audio(audio_path)
            logger.info(
                "Whisper готов: длина_текста=%s (%.1f с). Превью: %s",
                len(transcript),
                time.monotonic() - t0,
                (transcript[:200] + "…") if len(transcript) > 200 else transcript,
            )
    except Exception:
        logger.exception("Не удалось извлечь/распознать речь — продолжаю без транскрипта")

    try:
        await _emit(on_progress, "frames")
        t0 = time.monotonic()
        logger.info("Шаг 3/4: извлечение кадров и OCR…")
        frames = extract_frames(video_path, frames_dir, settings.video_max_frames)
        logger.info("Кадров получено: %s (%.1f с)", len(frames), time.monotonic() - t0)
        t0 = time.monotonic()
        ocr_text = await ocr_images(frames)
        logger.info(
            "OCR готов: длина_текста=%s (%.1f с). Превью: %s",
            len(ocr_text),
            time.monotonic() - t0,
            (ocr_text[:200] + "…") if len(ocr_text) > 200 else ocr_text,
        )
    except RateLimitError:
        raise
    except PipelineError:
        raise
    except Exception:
        logger.exception("Не удалось извлечь кадры/OCR")

    if not transcript and not ocr_text and not (caption or "").strip():
        logger.warning("Пустые transcript, OCR и caption — извлекать нечего")
        return [], transcript, ocr_text

    await _emit(on_progress, "titles")
    t0 = time.monotonic()
    logger.info("Шаг 4/4: извлечение названий через LLM…")
    try:
        works = await extract_works_with_llm(
            text="",
            transcript=transcript or None,
            ocr_text=ocr_text or None,
            caption=(caption or "").strip() or None,
        )
    except RateLimitError:
        raise
    except Exception as exc:
        raise ProcessingError(f"Не удалось извлечь названия: {exc}") from exc

    logger.info(
        "LLM извлёк произведений: %s (%.1f с). Итого анализ занял %.1f с",
        len(works),
        time.monotonic() - t0,
        time.monotonic() - started,
    )
    for idx, work in enumerate(works, start=1):
        logger.info(
            "  #%s title=%r author=%r confidence=%.2f",
            idx,
            work.title,
            work.author,
            work.confidence,
        )
    return works, transcript, ocr_text


async def analyze_video_url(
    url: str,
    *,
    on_progress: ProgressCallback | None = None,
) -> tuple[list[ExtractedWork], dict, Path]:
    """Скачивает ролик по URL и анализирует. Возвращает works, meta, tmp_dir."""
    tmp = _job_dir()
    started = time.monotonic()
    logger.info("Старт пайплайна video_url: %s", url)
    try:
        await _emit(on_progress, "download")
        t0 = time.monotonic()
        logger.info("Шаг 0: скачивание ролика…")
        video_path, meta = await download_video_from_url(url, tmp)
        caption = (meta.get("caption") or "").strip() or None
        logger.info(
            "Скачивание завершено за %.1f с: path=%s platform=%s author=%s caption_len=%s",
            time.monotonic() - t0,
            video_path,
            meta.get("platform"),
            meta.get("author_handle"),
            len(caption or ""),
        )
        works, transcript, ocr_text = await analyze_video_file(
            video_path,
            on_progress=on_progress,
            caption=caption,
        )
        meta["transcript"] = transcript
        meta["ocr_text"] = ocr_text
        logger.info(
            "Пайплайн video_url завершён за %.1f с, найдено произведений: %s",
            time.monotonic() - started,
            len(works),
        )
        return works, meta, tmp
    except PipelineError:
        cleanup_dir(tmp)
        raise
    except Exception:
        logger.exception("Пайплайн video_url упал для url=%s", url)
        cleanup_dir(tmp)
        raise


async def analyze_screenshots(
    image_paths: list[Path],
    *,
    on_progress: ProgressCallback | None = None,
) -> tuple[list[ExtractedWork], str, Path]:
    """OCR + extract для набора скриншотов."""
    tmp = _job_dir()
    started = time.monotonic()
    logger.info("Старт OCR скриншотов: count=%s", len(image_paths))
    local_paths = []
    for idx, src in enumerate(image_paths):
        dest = tmp / f"shot_{idx:03d}{src.suffix or '.jpg'}"
        shutil.copy(src, dest)
        local_paths.append(dest)
        logger.info("Скопирован скрин %s → %s", src, dest)

    await _emit(on_progress, "frames")
    t0 = time.monotonic()
    try:
        ocr_text = await ocr_images(local_paths)
    except RateLimitError:
        cleanup_dir(tmp)
        raise
    logger.info(
        "OCR скриншотов готов за %.1f с, длина=%s",
        time.monotonic() - t0,
        len(ocr_text),
    )
    await _emit(on_progress, "titles")
    works = await extract_works_with_llm(text="", ocr_text=ocr_text or None)
    logger.info(
        "Скриншоты обработаны за %.1f с, произведений=%s",
        time.monotonic() - started,
        len(works),
    )
    return works, ocr_text, tmp
