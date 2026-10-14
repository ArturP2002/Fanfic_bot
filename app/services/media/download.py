"""Скачивание Instagram Reels и TikTok.

Основной путь — yt-dlp (получает сам файл).
Apify используем как запасной вариант / для метаданных.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.proxy import httpx_client, ytdlp_proxy_args
from app.services.media.errors import DownloadError

logger = logging.getLogger(__name__)

# Поддерживаем и /reel/ID, и /username/reel/ID, и /reels/ID
INSTAGRAM_RE = re.compile(
    r"https?://(www\.)?(instagram\.com|instagr\.am)/"
    r"(?:[\w.]+/)?"
    r"(reel|reels|p|tv)/[\w\-]+/?",
    re.I,
)
TIKTOK_RE = re.compile(
    r"https?://(www\.|vm\.|vt\.)?tiktok\.com/.+",
    re.I,
)


def detect_video_platform(url: str) -> str | None:
    """Определяет площадку ролика."""
    if INSTAGRAM_RE.search(url):
        return "instagram"
    if TIKTOK_RE.search(url):
        return "tiktok"
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    """Преобразует ответ Apify (dict / pydantic-модель) в обычный словарь."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {}


def _pick(data: dict[str, Any], *keys: str) -> Any:
    """Возвращает первое непустое значение по списку ключей."""
    for key in keys:
        if key in data and data[key]:
            return data[key]
    return None


def _extract_video_url(item: dict[str, Any]) -> str | None:
    """Достаёт прямую ссылку на mp4 из item разных actors."""
    direct = _pick(
        item,
        "videoUrl",
        "mediaUrl",
        "downloadAddr",
        "video_url",
        "media_url",
    )
    if isinstance(direct, str) and direct.startswith("http"):
        return direct

    video = item.get("video")
    if isinstance(video, dict):
        nested = _pick(video, "url", "downloadAddr", "playAddr", "download_addr")
        if isinstance(nested, str) and nested.startswith("http"):
            return nested

    video_meta = item.get("videoMeta") or item.get("video_meta") or {}
    if isinstance(video_meta, dict):
        nested = _pick(
            video_meta,
            "downloadAddr",
            "originalDownloadAddr",
            "url",
            "playAddr",
            "download_addr",
        )
        if isinstance(nested, str) and nested.startswith("http"):
            return nested
        logger.info(
            "videoMeta без downloadAddr, ключи=%s",
            sorted(video_meta.keys()),
        )

    media_urls = item.get("mediaUrls") or item.get("media_urls") or []
    if isinstance(media_urls, list):
        logger.info("mediaUrls=%s", media_urls)
        for candidate in media_urls:
            if isinstance(candidate, str) and candidate.startswith("http"):
                return candidate
    return None


def _read_ytdlp_info(target_dir: Path, platform: str) -> dict[str, Any]:
    """Читает .info.json, который yt-dlp пишет рядом с видео."""
    infos = sorted(target_dir.glob(f"{platform}_video*.info.json"))
    if not infos:
        return {}
    try:
        import json

        return json.loads(infos[0].read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Не удалось прочитать info.json yt-dlp")
        return {}


def _caption_from_info(info: dict[str, Any]) -> str | None:
    """Достаёт описание поста из метаданных yt-dlp / Apify."""
    for key in ("description", "caption", "text", "title", "fulltitle"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def _download_with_ytdlp(
    url: str, target_dir: Path, platform: str
) -> tuple[Path, dict[str, Any]]:
    """Скачивает ролик через yt-dlp в mp4 и возвращает (path, info)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(target_dir / f"{platform}_video.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--newline",
        "--write-info-json",
        *ytdlp_proxy_args(),
        "-f",
        "mp4/best",
        "-o",
        out_tmpl,
        url,
    ]
    # В лог не светим credentials прокси
    log_cmd: list[str] = []
    skip_next = False
    for part in cmd:
        if skip_next:
            log_cmd.append("<proxy>")
            skip_next = False
            continue
        if part == "--proxy":
            log_cmd.append(part)
            skip_next = True
            continue
        log_cmd.append(part)
    logger.info("Запуск yt-dlp: %s", " ".join(log_cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = (stderr or stdout or b"").decode("utf-8", errors="replace")[-1000:]
        logger.error("yt-dlp завершился с кодом %s: %s", proc.returncode, err)
        raise RuntimeError(f"yt-dlp не смог скачать ролик: {err[:300]}")

    candidates = [
        path
        for path in sorted(target_dir.glob(f"{platform}_video.*"))
        if path.suffix.lower() != ".json" and not path.name.endswith(".info.json")
    ]
    if not candidates:
        raise RuntimeError("yt-dlp отработал, но файл не найден")
    dest = candidates[0]
    info = _read_ytdlp_info(target_dir, platform)
    logger.info(
        "yt-dlp сохранил файл: %s (%s байт), caption=%s",
        dest,
        dest.stat().st_size,
        bool(_caption_from_info(info)),
    )
    return dest, info


def _clean_instagram_url(url: str) -> str:
    """Убирает трекинг-параметры (?psln=…) — Apify ждёт чистый reel/profile URL."""
    return url.split("?", 1)[0].rstrip("/") + "/"


def _apify_run_input(url: str, platform: str) -> dict[str, Any]:
    """Собирает input для Apify actor по площадке."""
    if platform == "instagram":
        # apify/instagram-reel-scraper: username[] принимает usernames И direct reel URLs.
        # Пустой username: [] падает валидацией (minItems: 1); directUrls у этого actor нет.
        return {
            "username": [_clean_instagram_url(url)],
            "resultsLimit": 1,
        }
    # clockworks/tiktok-scraper: shouldDownloadVideos — платный add-on,
    # но без него mediaUrls часто пустой
    return {
        "postURLs": [url],
        "resultsPerPage": 1,
        "shouldDownloadVideos": True,
    }


async def _download_with_apify(url: str, target_dir: Path, platform: str) -> tuple[Path, dict]:
    """Запасной путь через Apify (с shouldDownloadVideos для TikTok)."""
    settings = get_settings()
    if not settings.apify_token:
        raise RuntimeError("APIFY_TOKEN не настроен")

    from apify_client import ApifyClient

    client = ApifyClient(settings.apify_token)
    target_dir.mkdir(parents=True, exist_ok=True)

    if platform == "instagram":
        actor_id = settings.apify_instagram_actor
    else:
        actor_id = settings.apify_tiktok_actor
    run_input = _apify_run_input(url, platform)

    logger.info(
        "Запуск Apify: actor=%s platform=%s shouldDownloadVideos=%s",
        actor_id,
        platform,
        run_input.get("shouldDownloadVideos"),
    )
    run = client.actor(actor_id).call(run_input=run_input)
    run_data = _as_dict(run)
    dataset_id = _pick(run_data, "defaultDatasetId", "default_dataset_id")
    logger.info(
        "Apify run завершён: id=%s status=%s dataset=%s",
        _pick(run_data, "id"),
        _pick(run_data, "status"),
        dataset_id,
    )
    if not dataset_id:
        raise RuntimeError("Apify не вернул dataset")

    items = list(client.dataset(dataset_id).iterate_items())
    logger.info("Apify dataset items=%s", len(items))
    if not items:
        raise RuntimeError("Apify вернул пустой результат")

    item = _as_dict(items[0])
    logger.info("Ключи первого item Apify: %s", sorted(item.keys()))
    video_url = _extract_video_url(item)
    if not video_url:
        raise RuntimeError("В ответе Apify нет прямой ссылки на видео")

    author_meta = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
    author = _pick(item, "ownerUsername", "author") or _pick(
        author_meta, "name", "nickName", "uniqueId"
    )
    caption = _caption_from_info(item)
    meta = {
        "platform": platform,
        "url": url,
        "author_handle": author,
        "caption": caption,
        "raw": {k: item.get(k) for k in ("id", "caption", "text", "title") if k in item},
        "source": "apify",
    }

    dest = target_dir / f"{platform}_video.mp4"
    logger.info("Скачиваю mp4 по прямой ссылке Apify…")
    async with httpx_client(timeout=120.0) as http:
        resp = await http.get(video_url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    logger.info("Видео сохранено через Apify: %s (%s байт)", dest, dest.stat().st_size)
    return dest, meta


async def download_video_from_url(url: str, target_dir: Path) -> tuple[Path, dict]:
    """Скачивает ролик: сначала yt-dlp, затем Apify."""
    platform = detect_video_platform(url)
    if not platform:
        raise DownloadError("Ссылка не похожа на Instagram Reel или TikTok")

    target_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    # 1) Основной путь — yt-dlp
    try:
        path, info = await _download_with_ytdlp(url, target_dir, platform)
        caption = _caption_from_info(info)
        meta = {
            "platform": platform,
            "url": url,
            "author_handle": _pick(info, "uploader", "channel", "creator"),
            "caption": caption,
            "raw": {
                k: info.get(k)
                for k in ("id", "title", "description", "uploader")
                if k in info
            },
            "source": "yt-dlp",
        }
        if caption:
            logger.info("Caption из yt-dlp: %s символов", len(caption))
        return path, meta
    except FileNotFoundError:
        logger.error("yt-dlp не установлен в системе/контейнере")
        errors.append("yt-dlp не установлен")
    except Exception as exc:
        logger.warning("yt-dlp не скачал ролик: %s", exc)
        errors.append(str(exc))

    # 2) Запасной путь — Apify
    try:
        return await _download_with_apify(url, target_dir, platform)
    except Exception as exc:
        logger.exception("Apify тоже не скачал ролик url=%s", url)
        errors.append(str(exc))

    raise DownloadError(
        "Не удалось скачать ролик. "
        + " | ".join(errors[:2])
    )
