"""Добавление фанфиков: текст, ссылки, скриншоты, ролики."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from arq import create_pool
from arq.connections import RedisSettings

from app import texts
from app.config import get_settings
from app.db.session import async_session_factory
from app.keyboards import (
    batch_result_keyboard,
    choose_candidate_keyboard,
    choose_platform_hit_keyboard,
    confirm_match_keyboard,
    main_menu,
    single_add_keyboard,
    subscribe_keyboard,
)
from app.models import ProcessingJob
from app.models.enums import ConfirmationStatus, JobStatus, JobType, ReadingStatus, SourceType
from app.services import access as access_service
from app.services import library as library_service
from app.services.batch_store import batch_from_resolution, load_batch, save_batch
from app.services.extract import ExtractedWork, extract_works_with_llm, is_short_catalog_query
from app.services.formatting import (
    format_batch_list_from_payload,
    format_batch_summary,
    format_catalog_confirm,
    format_single_work,
)
from app.services.matching import find_matches
from app.services.media.download import detect_video_platform
from app.services.parsers import detect_platform, parse_fic_url
from app.services.resolution import resolve_extracted, resolve_parsed_link
from app.services.users import get_or_create_user
from app.services.works import confirm_work, create_work, get_work
from app.states import AddStates

logger = logging.getLogger(__name__)
router = Router()

URL_RE = re.compile(r"https?://\S+", re.I)


async def _ensure_processing_allowed(message: Message) -> bool:
    """Проверяет подписку/пробные использования. При отказе шлёт paywall."""
    async with async_session_factory() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        result = access_service.check_processing_access(user, message.from_user.id)
        if result.consume_trial and result.allowed:
            # Инициализация free_uses_left могла измениться
            await session.commit()
        elif not result.allowed:
            await session.commit()
            await message.answer(
                result.message or texts.SUBSCRIBE_INTRO.format(
                    price=get_settings().subscription_price_rub,
                    status="",
                    mode_hint="",
                ),
                reply_markup=subscribe_keyboard(),
            )
            return False
        else:
            await session.commit()
        return True


async def _consume_trial_after_enqueue(telegram_id: int) -> None:
    """Списывает пробное использование после успешной постановки job."""
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=telegram_id)
        result = access_service.check_processing_access(user, telegram_id)
        if result.consume_trial:
            left = await access_service.consume_trial_use(session, user)
            logger.info(
                "Trial списан telegram_id=%s осталось=%s",
                telegram_id,
                left,
            )
        else:
            # сохранить возможную инициализацию free_uses_left
            await session.commit()


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    # redis://host:port/db
    return RedisSettings.from_dsn(settings.redis_url)


async def _enqueue_job(job_name: str, **kwargs) -> None:
    """Ставит задачу в очередь ARQ."""
    settings = get_settings()
    logger.info(
        "Ставлю задачу в очередь: name=%s kwargs=%s redis=%s",
        job_name,
        kwargs,
        settings.redis_url,
    )
    redis = await create_pool(_redis_settings())
    try:
        job = await redis.enqueue_job(job_name, **kwargs)
        job_key = getattr(job, "job_id", None) or getattr(job, "job_try", None) or job
        logger.info(
            "Задача поставлена в очередь: name=%s arq_job=%s db_job_id=%s",
            job_name,
            job_key,
            kwargs.get("job_id"),
        )
    except Exception:
        logger.exception("Не удалось поставить задачу в очередь: %s", job_name)
        raise
    finally:
        await redis.close()


@router.message(F.text == "➕ Добавить")
async def add_start(message: Message, state: FSMContext) -> None:
    """Вход в режим добавления."""
    await state.set_state(AddStates.waiting_content)
    await message.answer(texts.ADD_PROMPT, reply_markup=main_menu())


@router.message(F.photo)
async def on_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    """Приём скриншотов (по одному или альбомом)."""
    current = await state.get_state()
    data = await state.get_data()
    photos = list(data.get("photos", []))

    # Берём наибольший размер
    photo = message.photo[-1]
    photos.append(photo.file_id)
    await state.update_data(photos=photos)
    await state.set_state(AddStates.collecting_photos)

    # Альбом или продолжение сбора — ждём команду «готово»
    if message.media_group_id or (current and "collecting_photos" in current):
        await state.update_data(media_group_id=message.media_group_id)
        await message.answer(
            f"Скриншотов: {len(photos)}. Когда закончишь — напиши «готово»."
        )
        return

    # Одиночный скрин — разбираем сразу
    if not await _ensure_processing_allowed(message):
        await state.clear()
        return
    await message.answer(texts.ANALYZING_SCREENSHOTS)
    await _process_photos(message, state, bot, photos)


@router.message(AddStates.collecting_photos, F.text.casefold() == "готово")
async def photos_done(message: Message, state: FSMContext, bot: Bot) -> None:
    """Завершение сбора скриншотов."""
    data = await state.get_data()
    photos = data.get("photos") or []
    if not photos:
        await message.answer("Скриншотов пока нет. Пришли изображения.")
        return
    if not await _ensure_processing_allowed(message):
        await state.clear()
        return
    await message.answer(texts.ANALYZING_SCREENSHOTS)
    await _process_photos(message, state, bot, photos)


async def _process_photos(
    message: Message,
    state: FSMContext,
    bot: Bot,
    file_ids: list[str],
) -> None:
    """Скачивает фото и ставит задачу OCR."""
    settings = get_settings()
    tmp = Path(settings.tmp_dir) / f"shots_{message.from_user.id}"
    tmp.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for idx, file_id in enumerate(file_ids):
        file = await bot.get_file(file_id)
        dest = tmp / f"{idx}.jpg"
        await bot.download_file(file.file_path, destination=dest)
        paths.append(str(dest))

    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        job = ProcessingJob(
            user_id=user.id,
            job_type=JobType.SCREENSHOTS.value,
            status=JobStatus.PENDING.value,
            payload_json=json.dumps({"paths": paths}, ensure_ascii=False),
            telegram_chat_id=message.chat.id,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    await state.clear()
    await _enqueue_job("process_screenshots", job_id=job_id)
    await _consume_trial_after_enqueue(message.from_user.id)


@router.message(F.video)
@router.message(F.document)
async def on_video_file(message: Message, state: FSMContext, bot: Bot) -> None:
    """Загруженный видеофайл."""
    file_id = None
    if message.video:
        file_id = message.video.file_id
    elif message.document and (
        (message.document.mime_type or "").startswith("video/")
        or (message.document.file_name or "").lower().endswith(
            (".mp4", ".mov", ".mkv", ".webm")
        )
    ):
        file_id = message.document.file_id
    else:
        return

    if not await _ensure_processing_allowed(message):
        return
    await message.answer(texts.ANALYZING_VIDEO)
    settings = get_settings()
    tmp = Path(settings.tmp_dir) / f"video_{message.from_user.id}"
    tmp.mkdir(parents=True, exist_ok=True)
    file = await bot.get_file(file_id)
    dest = tmp / "upload.mp4"
    await bot.download_file(file.file_path, destination=dest)

    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        job = ProcessingJob(
            user_id=user.id,
            job_type=JobType.VIDEO_FILE.value,
            status=JobStatus.PENDING.value,
            payload_json=json.dumps({"path": str(dest)}, ensure_ascii=False),
            telegram_chat_id=message.chat.id,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    await state.clear()
    await _enqueue_job("process_video_file", job_id=job_id)
    await _consume_trial_after_enqueue(message.from_user.id)


@router.message(F.text)
async def on_text(message: Message, state: FSMContext) -> None:
    """Текст, списки, ссылки на ролики и фанфики."""
    text = (message.text or "").strip()
    if not text or text in {
        "➕ Добавить",
        "📚 Моя библиотека",
        "ℹ️ Как это работает",
        "💎 Подписка",
    }:
        return

    # Не перехватываем чужие FSM-состояния (иначе жалобы/поиск не доходят до своих хендлеров)
    current = await state.get_state()
    if current and any(
        name in current
        for name in (
            "LibraryStates",
            "ErrorStates",
            "SubscribeStates",
            "AdminBloggerStates",
            "collecting_photos",
        )
    ):
        raise SkipHandler()

    # Чистим хвост пунктуации у URL из сообщения
    urls = [u.rstrip(").,]\"'") for u in URL_RE.findall(text)]
    video_url = next((u for u in urls if detect_video_platform(u)), None)
    fic_url = next((u for u in urls if detect_platform(u)), None)

    # Голая неподдерживаемая ссылка — не выдумываем фанфик из URL
    text_without_urls = URL_RE.sub("", text).strip()
    if urls and not text_without_urls and not video_url and not fic_url:
        await message.answer(
            "По этой ссылке не вижу ни ролика Instagram/TikTok, "
            "ни страницы фанфика (AO3 / Ficbook / FanFiction.net)."
        )
        return

    if video_url:
        if not await _ensure_processing_allowed(message):
            return
        await message.answer(texts.ANALYZING_VIDEO)
        async with async_session_factory() as session:
            user = await get_or_create_user(session, telegram_id=message.from_user.id)
            job = ProcessingJob(
                user_id=user.id,
                job_type=JobType.VIDEO_URL.value,
                status=JobStatus.PENDING.value,
                payload_json=json.dumps({"url": video_url}, ensure_ascii=False),
                telegram_chat_id=message.chat.id,
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id
        await state.clear()
        await _enqueue_job("process_video_url", job_id=job_id)
        await _consume_trial_after_enqueue(message.from_user.id)
        return

    if fic_url:
        await _handle_fic_link(message, fic_url)
        await state.clear()
        return

    await message.answer(texts.ANALYZING_TEXT)
    used_paid = await _handle_text_extract(message, text)
    await state.clear()
    if used_paid:
        await _consume_trial_after_enqueue(message.from_user.id)


async def _handle_fic_link(message: Message, url: str) -> None:
    """Добавление по прямой ссылке на фанфик."""
    try:
        parsed = await parse_fic_url(url)
    except Exception:
        logger.exception("Ошибка парсинга ссылки %s", url)
        await message.answer(
            "Не удалось прочитать страницу по ссылке. Попробуй позже или пришли название текстом."
        )
        return

    if not parsed:
        await message.answer(
            "Пока умею читать ссылки AO3, Ficbook и FanFiction.net."
        )
        return

    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        resolved = await resolve_parsed_link(
            session,
            user.id,
            title=parsed.title,
            author=parsed.author,
            status=parsed.status,
            word_count=parsed.word_count,
            tags=parsed.tags,
            fandom=parsed.fandom,
            pairing=parsed.pairing,
            source_url=parsed.source_url,
            original_url=parsed.original_url,
        )
        if resolved.library_item:
            await message.answer(texts.ALREADY_IN_LIBRARY)
            return

        source = await library_service.create_source(
            session,
            user.id,
            SourceType.FIC_LINK.value,
            platform=parsed.platform,
            url=url,
            work_ids=[resolved.work.id] if resolved.work else [],
        )
        _ = source
        await message.answer(
            format_single_work(resolved),
            reply_markup=single_add_keyboard(resolved.work.id),
        )


async def _handle_text_extract(message: Message, text: str) -> bool:
    """Извлечение из текста и показ результата.

    Короткие запросы (MOAM, Lionheart) сначала сверяем с каталогом — без LLM.
    Возвращает True, если списали платный/пробный слот (был вызов LLM).
    """
    extracted: list[ExtractedWork] | None = None
    used_paid = False
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)

        if is_short_catalog_query(text):
            match = await find_matches(session, text.strip())
            if match.confidence == "high" and match.best:
                logger.info(
                    "Catalog-first: «%s» → work_id=%s score=%.1f",
                    text.strip(),
                    match.best.work.id,
                    match.best.score,
                )
                extracted = [
                    ExtractedWork(title=text.strip(), confidence=0.95)
                ]

        if extracted is None:
            # LLM — платная операция
            access = access_service.check_processing_access(
                user, message.from_user.id
            )
            await session.commit()
            if not access.allowed:
                await message.answer(
                    access.message or "Нужна подписка.",
                    reply_markup=subscribe_keyboard(),
                )
                return False
            extracted = await extract_works_with_llm(text)
            used_paid = True
        if not extracted:
            await message.answer(texts.NOTHING_FOUND)
            return used_paid

        batch = await resolve_extracted(session, user.id, extracted)
        await library_service.create_source(
            session,
            user.id,
            SourceType.TEXT.value,
            raw_text=text,
            work_ids=[i.work.id for i in batch.items if i.work],
        )
        token = await save_batch(batch_from_resolution(user.id, batch))

        # Уточнения по medium match / platform hits
        for idx, item in enumerate(batch.items):
            if item.needs_user_choice and item.candidate_work_ids:
                works = []
                for wid in item.candidate_work_ids:
                    w = await get_work(session, wid)
                    if w:
                        works.append(w)
                await message.answer(
                    f"Нашла несколько вариантов для «{item.extracted.title}». Какой ты имела в виду?",
                    reply_markup=choose_candidate_keyboard(token, idx, works),
                )
            elif item.platform_hits and not item.work:
                await message.answer(
                    f"Нашла похожие работы на площадках для «{item.extracted.title}». "
                    "Какую выбрать?",
                    reply_markup=choose_platform_hit_keyboard(
                        token, idx, item.platform_hits
                    ),
                )
            elif item.needs_catalog_confirm and item.work:
                await message.answer(
                    format_catalog_confirm(item),
                    reply_markup=confirm_match_keyboard(token, idx),
                )

        if (
            len(batch.items) == 1
            and batch.items[0].work
            and not batch.items[0].needs_user_choice
            and not batch.items[0].platform_hits
        ):
            item = batch.items[0]
            if item.library_item:
                status = item.library_item.reading_status
                if status == ReadingStatus.READ.value:
                    await message.answer(f"☑ Ты это уже читала.\n\n{item.work.title}")
                else:
                    await message.answer(texts.ALREADY_IN_LIBRARY)
                return used_paid
            if item.needs_catalog_confirm:
                return used_paid
            await message.answer(
                format_single_work(item),
                reply_markup=single_add_keyboard(item.work.id),
            )
            return used_paid

        summary = format_batch_summary(batch)
        await message.answer(
            summary,
            reply_markup=batch_result_keyboard(token, len(batch.new_items)),
        )
        return used_paid


@router.callback_query(F.data.startswith("batch:view:"))
async def batch_view(callback: CallbackQuery) -> None:
    """Показать все найденные работы."""
    token = callback.data.split(":")[2]
    payload = await load_batch(token)
    if not payload:
        await callback.answer("Результаты устарели, пришли материал снова", show_alert=True)
        return
    await callback.message.answer(format_batch_list_from_payload(payload))
    await callback.answer()


@router.callback_query(F.data.startswith("batch:add:"))
async def batch_add(callback: CallbackQuery) -> None:
    """Добавить все новые работы в библиотеку."""
    token = callback.data.split(":")[2]
    payload = await load_batch(token)
    if not payload:
        await callback.answer("Результаты устарели", show_alert=True)
        return

    added = 0
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        for item in payload["items"]:
            work_id = item.get("work_id")
            if not work_id or item.get("library_status"):
                continue
            if item.get("needs_user_choice"):
                continue
            _, created = await library_service.add_to_library(
                session, user.id, work_id, ReadingStatus.WANT.value
            )
            if created:
                added += 1
    await callback.message.answer(f"Добавила в библиотеку: {added}")
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("add:one:"))
async def add_one(callback: CallbackQuery) -> None:
    """Добавить одну работу."""
    work_id = int(callback.data.split(":")[2])
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        item, created = await library_service.add_to_library(
            session, user.id, work_id, ReadingStatus.WANT.value
        )
        if not created:
            await callback.answer("Уже в библиотеке", show_alert=True)
            return
        title = item.work.title if item.work else str(work_id)
    await callback.message.answer(f"Сохранила: {title} 🔖")
    await callback.answer("Добавлено")


@router.callback_query(F.data.startswith("add:reject:"))
async def add_reject(callback: CallbackQuery) -> None:
    """Пользователь отклонил предложенную работу."""
    await callback.message.answer("Ок, не добавляю. Можешь прислать другое название.")
    await callback.answer()


@router.callback_query(F.data.startswith("match:yes:"))
async def match_yes(callback: CallbackQuery) -> None:
    """Подтверждение нового/сомнительного фанфика."""
    _, _, token, index_s = callback.data.split(":")
    index = int(index_s)
    payload = await load_batch(token)
    if not payload:
        await callback.answer("Сессия устарела", show_alert=True)
        return
    item = payload["items"][index]
    work_id = item.get("work_id")
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        if work_id:
            await confirm_work(session, work_id)
            await library_service.add_to_library(
                session, user.id, work_id, ReadingStatus.WANT.value
            )
        else:
            work = await create_work(
                session,
                title=item["title"],
                author=item.get("author"),
                confirmation_status=ConfirmationStatus.CONFIRMED.value,
            )
            await library_service.add_to_library(
                session, user.id, work.id, ReadingStatus.WANT.value
            )
    await callback.message.answer("Принято и сохранено 🔖")
    await callback.answer()


@router.callback_query(F.data.startswith("match:no:"))
async def match_no(callback: CallbackQuery) -> None:
    """Отказ от сомнительного результата."""
    await callback.message.answer("Ок, не добавляю эту запись в базу.")
    await callback.answer()


@router.callback_query(F.data.startswith("match:pick:"))
async def match_pick(callback: CallbackQuery) -> None:
    """Выбор конкретного кандидата."""
    parts = callback.data.split(":")
    work_id = int(parts[4])
    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        item, created = await library_service.add_to_library(
            session, user.id, work_id, ReadingStatus.WANT.value
        )
        title = item.work.title
    msg = f"Сохранила: {title}" if created else texts.ALREADY_IN_LIBRARY
    await callback.message.answer(msg)
    await callback.answer()


@router.callback_query(F.data.startswith("match:none:"))
async def match_none(callback: CallbackQuery) -> None:
    """Ни один кандидат не подошёл."""
    await callback.message.answer(
        "Хорошо. Можешь прислать точное название или ссылку на фанфик."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("phit:pick:"))
async def platform_hit_pick(callback: CallbackQuery) -> None:
    """Пользователь выбрала кандидата с AO3/Ficbook — парсим страницу и сохраняем."""
    # phit:pick:{token}:{index}:{hit_idx}
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    token, index_s, hit_idx_s = parts[2], parts[3], parts[4]
    index = int(index_s)
    hit_idx = int(hit_idx_s)
    payload = await load_batch(token)
    if not payload:
        await callback.answer("Сессия устарела", show_alert=True)
        return
    try:
        item = payload["items"][index]
        hit = item["platform_hits"][hit_idx]
    except (IndexError, KeyError, TypeError):
        await callback.answer("Вариант не найден", show_alert=True)
        return

    url = hit.get("url")
    if not url:
        await callback.answer("Нет ссылки", show_alert=True)
        return

    await callback.answer("Загружаю страницу…")
    try:
        parsed = await parse_fic_url(url)
    except Exception:
        logger.exception("Не удалось разобрать platform hit %s", url)
        await callback.message.answer(
            "Не удалось открыть страницу. Попробуй прислать ссылку вручную."
        )
        return
    if not parsed:
        await callback.message.answer(
            "Не удалось прочитать страницу. Пришли ссылку на фанфик текстом."
        )
        return

    async with async_session_factory() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        resolved = await resolve_parsed_link(
            session,
            user.id,
            title=parsed.title,
            author=parsed.author,
            status=parsed.status,
            word_count=parsed.word_count,
            tags=parsed.tags,
            fandom=parsed.fandom,
            pairing=parsed.pairing,
            source_url=parsed.source_url,
            original_url=parsed.original_url,
        )
        if resolved.library_item:
            await callback.message.answer(texts.ALREADY_IN_LIBRARY)
            return
        await library_service.create_source(
            session,
            user.id,
            SourceType.FIC_LINK.value,
            platform=parsed.platform,
            url=url,
            work_ids=[resolved.work.id] if resolved.work else [],
        )
        await callback.message.answer(
            format_single_work(resolved),
            reply_markup=single_add_keyboard(resolved.work.id),
        )


@router.callback_query(F.data.startswith("phit:none:"))
async def platform_hit_none(callback: CallbackQuery) -> None:
    """Ни один кандидат с площадки не подошёл."""
    await callback.message.answer(
        "Ок. Можешь прислать точную ссылку на фанфик (AO3 / Ficbook / FanFiction.net)."
    )
    await callback.answer()
