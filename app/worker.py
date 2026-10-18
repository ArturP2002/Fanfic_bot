"""ARQ-воркер фоновой обработки роликов и скриншотов."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Нужно до импортов app.*, если файл запускают как python app/worker.py
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from arq.connections import RedisSettings

from app import texts
from app.config import get_settings
from app.db.session import async_session_factory
from app.keyboards import (
    batch_result_keyboard,
    choose_candidate_keyboard,
    choose_platform_hit_keyboard,
    confirm_match_keyboard,
    single_add_keyboard,
)
from app.logging_config import setup_logging
from app.proxy import apply_proxy_env, create_bot
from app.models import ProcessingJob
from app.models.enums import JobStatus, SourceType
from app.services import library as library_service
from app.services.batch_store import batch_from_resolution, save_batch
from app.services.formatting import (
    format_batch_summary,
    format_catalog_confirm,
    format_single_work,
)
from app.services.media.errors import (
    DownloadError,
    PipelineError,
    ProcessingError,
    RateLimitError,
    RecognitionError,
)
from app.services.media.pipeline import (
    analyze_screenshots,
    analyze_video_file,
    analyze_video_url,
    cleanup_dir,
)
from app.services.resolution import resolve_extracted
from app.services.works import get_work

logger = logging.getLogger(__name__)

_PROGRESS_TEXTS = {
    "download": texts.PROGRESS_DOWNLOAD,
    "speech": texts.PROGRESS_SPEECH,
    "frames": texts.PROGRESS_FRAMES,
    "titles": texts.PROGRESS_TITLES,
    "match": texts.PROGRESS_MATCH,
}


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def _set_job_status(
    job_id: int,
    status: str,
    *,
    result: dict | None = None,
    error: str | None = None,
) -> ProcessingJob | None:
    logger.info("Обновляю статус job_id=%s → %s", job_id, status)
    async with async_session_factory() as session:
        job = await session.get(ProcessingJob, job_id)
        if not job:
            logger.error("Задача job_id=%s не найдена в БД", job_id)
            return None
        job.status = status
        if result is not None:
            job.result_json = json.dumps(result, ensure_ascii=False)
        if error is not None:
            job.error_message = error
        await session.commit()
        await session.refresh(job)
        return job


async def _notify(chat_id: int, text: str, reply_markup=None) -> None:
    logger.info("Отправляю сообщение в chat_id=%s: %s", chat_id, text[:120].replace("\n", " "))
    bot = create_bot()
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        logger.exception("Не удалось отправить сообщение в chat_id=%s", chat_id)
        raise
    finally:
        await bot.session.close()


def _user_error_text(exc: BaseException, *, from_url: bool) -> str:
    """Подбирает понятное сообщение пользователю по типу ошибки."""
    if isinstance(exc, DownloadError):
        return texts.VIDEO_DOWNLOAD_FAILED
    if isinstance(exc, RateLimitError):
        return texts.VIDEO_RATE_LIMIT
    if isinstance(exc, RecognitionError):
        return texts.NOTHING_FOUND
    if isinstance(exc, (ProcessingError, PipelineError)):
        return texts.VIDEO_PROCESSING_FAILED if from_url else texts.VIDEO_FILE_PROCESSING_FAILED
    # На всякий случай: старые RuntimeError про скачивание
    msg = str(exc).lower()
    if from_url and ("скачать" in msg or "download" in msg or "yt-dlp" in msg or "apify" in msg):
        return texts.VIDEO_DOWNLOAD_FAILED
    if "rate limit" in msg or "429" in msg:
        return texts.VIDEO_RATE_LIMIT
    return texts.VIDEO_PROCESSING_FAILED if from_url else texts.VIDEO_FILE_PROCESSING_FAILED


def _make_progress(chat_id: int):
    """Колбэк коротких этапных сообщений без технических деталей."""

    async def _on_progress(stage: str) -> None:
        text = _PROGRESS_TEXTS.get(stage)
        if text:
            await _notify(chat_id, text)

    return _on_progress


async def _finish_with_batch(
    job: ProcessingJob,
    extracted,
    *,
    source_type: str,
    platform: str | None = None,
    url: str | None = None,
    author_handle: str | None = None,
    raw_text: str | None = None,
) -> None:
    """Резолюция и отправка результата пользователю."""
    async with async_session_factory() as session:
        batch = await resolve_extracted(session, job.user_id, extracted)
        await library_service.create_source(
            session,
            job.user_id,
            source_type,
            platform=platform,
            url=url,
            author_handle=author_handle,
            raw_text=raw_text,
            work_ids=[i.work.id for i in batch.items if i.work],
        )
        token = await save_batch(batch_from_resolution(job.user_id, batch))

        for idx, item in enumerate(batch.items):
            if item.needs_user_choice and item.candidate_work_ids:
                works = []
                for wid in item.candidate_work_ids:
                    w = await get_work(session, wid)
                    if w:
                        works.append(w)
                await _notify(
                    job.telegram_chat_id,
                    f"Нашла несколько вариантов для «{item.extracted.title}». "
                    "Какой ты имела в виду?",
                    choose_candidate_keyboard(token, idx, works),
                )
            elif item.platform_hits and not item.work:
                await _notify(
                    job.telegram_chat_id,
                    f"Нашла похожие работы на площадках для «{item.extracted.title}». "
                    "Какую выбрать?",
                    choose_platform_hit_keyboard(token, idx, item.platform_hits),
                )
            elif item.needs_catalog_confirm and item.work:
                await _notify(
                    job.telegram_chat_id,
                    format_catalog_confirm(item),
                    confirm_match_keyboard(token, idx),
                )

        if not batch.items:
            await _notify(job.telegram_chat_id, texts.NOTHING_FOUND)
        elif (
            len(batch.items) == 1
            and batch.items[0].work
            and not batch.items[0].needs_user_choice
            and not batch.items[0].needs_catalog_confirm
            and not batch.items[0].platform_hits
        ):
            item = batch.items[0]
            if item.library_item:
                await _notify(job.telegram_chat_id, texts.ALREADY_IN_LIBRARY)
            else:
                await _notify(
                    job.telegram_chat_id,
                    format_single_work(item),
                    single_add_keyboard(item.work.id),
                )
        else:
            await _notify(
                job.telegram_chat_id,
                format_batch_summary(batch),
                batch_result_keyboard(token, len(batch.new_items)),
            )

        await _set_job_status(
            job.id,
            JobStatus.DONE.value,
            result={"count": len(batch.items), "token": token},
        )


async def process_video_url(ctx, job_id: int) -> None:
    """Обработка ссылки на Instagram/TikTok."""
    logger.info("===== WORKER взял video_url job_id=%s =====", job_id)
    await _set_job_status(job_id, JobStatus.RUNNING.value)
    async with async_session_factory() as session:
        job = await session.get(ProcessingJob, job_id)
        if not job:
            logger.error("video_url: job_id=%s отсутствует в БД", job_id)
            return
        payload = json.loads(job.payload_json or "{}")
        url = payload["url"]
        chat_id = job.telegram_chat_id
        user_id = job.user_id
        logger.info(
            "video_url job_id=%s user_id=%s chat_id=%s url=%s",
            job_id,
            user_id,
            chat_id,
            url,
        )

    tmp_dir = None
    on_progress = _make_progress(chat_id)
    try:
        works, meta, tmp_dir = await analyze_video_url(url, on_progress=on_progress)
        logger.info(
            "video_url job_id=%s анализ готов, произведений=%s",
            job_id,
            len(works),
        )
        if not works:
            await _set_job_status(job_id, JobStatus.DONE.value, result={"count": 0})
            await _notify(chat_id, texts.NOTHING_FOUND)
            return

        await on_progress("match")
        async with async_session_factory() as session:
            job = await session.get(ProcessingJob, job_id)
            assert job
            await _finish_with_batch(
                job,
                works,
                source_type=SourceType.VIDEO_URL.value,
                platform=meta.get("platform"),
                url=url,
                author_handle=meta.get("author_handle"),
                raw_text="\n".join(
                    part
                    for part in (
                        meta.get("caption") or "",
                        meta.get("transcript") or "",
                        meta.get("ocr_text") or "",
                    )
                    if part
                ),
            )
        logger.info("===== WORKER завершил video_url job_id=%s успешно =====", job_id)
    except Exception as exc:
        logger.exception("Ошибка обработки video_url job_id=%s", job_id)
        await _set_job_status(job_id, JobStatus.FAILED.value, error=str(exc))
        await _notify(chat_id, _user_error_text(exc, from_url=True))
    finally:
        cleanup_dir(tmp_dir)


async def process_video_file(ctx, job_id: int) -> None:
    """Обработка загруженного видеофайла."""
    logger.info("Старт обработки video_file job_id=%s", job_id)
    await _set_job_status(job_id, JobStatus.RUNNING.value)
    async with async_session_factory() as session:
        job = await session.get(ProcessingJob, job_id)
        if not job:
            return
        payload = json.loads(job.payload_json or "{}")
        path = Path(payload["path"])
        chat_id = job.telegram_chat_id

    on_progress = _make_progress(chat_id)
    try:
        works, transcript, ocr_text = await analyze_video_file(
            path,
            on_progress=on_progress,
        )
        if not works:
            await _set_job_status(job_id, JobStatus.DONE.value, result={"count": 0})
            await _notify(chat_id, texts.NOTHING_FOUND)
            return

        await on_progress("match")
        async with async_session_factory() as session:
            job = await session.get(ProcessingJob, job_id)
            assert job
            await _finish_with_batch(
                job,
                works,
                source_type=SourceType.VIDEO_FILE.value,
                raw_text=f"{transcript}\n{ocr_text}",
            )
    except Exception as exc:
        logger.exception("Ошибка обработки video_file job_id=%s", job_id)
        await _set_job_status(job_id, JobStatus.FAILED.value, error=str(exc))
        await _notify(chat_id, _user_error_text(exc, from_url=False))
    finally:
        cleanup_dir(path.parent)


async def process_screenshots(ctx, job_id: int) -> None:
    """Обработка скриншотов."""
    logger.info("Старт обработки screenshots job_id=%s", job_id)
    await _set_job_status(job_id, JobStatus.RUNNING.value)
    async with async_session_factory() as session:
        job = await session.get(ProcessingJob, job_id)
        if not job:
            return
        payload = json.loads(job.payload_json or "{}")
        paths = [Path(p) for p in payload.get("paths", [])]
        chat_id = job.telegram_chat_id

    tmp_dir = None
    on_progress = _make_progress(chat_id)
    try:
        works, ocr_text, tmp_dir = await analyze_screenshots(
            paths,
            on_progress=on_progress,
        )
        if not works:
            await _set_job_status(job_id, JobStatus.DONE.value, result={"count": 0})
            await _notify(chat_id, texts.NOTHING_FOUND)
            return

        await on_progress("match")
        async with async_session_factory() as session:
            job = await session.get(ProcessingJob, job_id)
            assert job
            await _finish_with_batch(
                job,
                works,
                source_type=SourceType.SCREENSHOT.value,
                raw_text=ocr_text,
            )
    except Exception as exc:
        logger.exception("Ошибка обработки screenshots job_id=%s", job_id)
        await _set_job_status(job_id, JobStatus.FAILED.value, error=str(exc))
        if isinstance(exc, RateLimitError):
            await _notify(chat_id, texts.VIDEO_RATE_LIMIT)
        else:
            await _notify(chat_id, texts.NOTHING_FOUND)
    finally:
        cleanup_dir(tmp_dir)
        for p in paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


class WorkerSettings:
    """Настройки ARQ worker."""

    functions = [process_video_url, process_video_file, process_screenshots]
    # Скачивание IG + Whisper/OCR + enrich легко > 5 мин
    job_timeout = 900
    redis_settings = property(lambda self: _redis_settings())  # type: ignore

    @staticmethod
    async def on_startup(ctx):
        setup_logging()
        apply_proxy_env()
        settings = get_settings()
        logger.info(
            "ARQ worker запущен. redis=%s functions=%s job_timeout=%s",
            settings.redis_url,
            [fn.__name__ for fn in WorkerSettings.functions],
            WorkerSettings.job_timeout,
        )

    @staticmethod
    async def on_shutdown(ctx):
        logger.info("ARQ worker остановлен")


# ARQ ожидает атрибуты класса, не property — задаём явно
WorkerSettings.redis_settings = _redis_settings()  # type: ignore[misc]


def main() -> None:
    """Точка входа воркера (альтернатива команде `arq app.worker.WorkerSettings`)."""
    setup_logging()
    apply_proxy_env()
    from arq.worker import Worker

    settings = get_settings()
    logger.info(
        "Запуск ARQ worker FicBrain, redis=%s",
        settings.redis_url,
    )
    Worker(WorkerSettings).run()


if __name__ == "__main__":
    main()
