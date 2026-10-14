"""Локальный запуск FicBrain одной командой: Postgres + Redis, worker, bot.

Использование из корня проекта (с активированным .venv):

    python main.py

Отдельные процессы по-прежнему доступны:
    python -m app.main
    python -m app.worker
    docker compose up -d postgres redis
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.logging_config import setup_logging
from app.proxy import apply_proxy_env

logger = logging.getLogger(__name__)

_children: list[subprocess.Popen[bytes]] = []


def _ensure_infra() -> None:
    """Поднимает Postgres и Redis через docker compose и ждёт healthy."""
    logger.info("Поднимаю Postgres и Redis (docker compose)…")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "postgres", "redis"],
            cwd=ROOT,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Не найден docker. Установите Docker Desktop или поднимите "
            "Postgres/Redis вручную."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Не удалось выполнить `docker compose up -d postgres redis`. "
            "Проверьте, что Docker запущен."
        ) from exc

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        probe = subprocess.run(
            [
                "docker",
                "compose",
                "ps",
                "--status",
                "running",
                "--format",
                "{{.Service}} {{.Health}}",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        health: dict[str, str] = {}
        for line in (probe.stdout or "").splitlines():
            parts = line.strip().split(None, 1)
            if parts:
                health[parts[0]] = parts[1] if len(parts) > 1 else ""
        if (
            health.get("postgres") == "healthy"
            and health.get("redis") == "healthy"
        ):
            logger.info("Postgres и Redis готовы")
            return
        time.sleep(1)

    raise RuntimeError(
        "Postgres/Redis не стали healthy за 60 с. "
        "Проверьте `docker compose ps`."
    )


def _start_worker() -> subprocess.Popen[bytes]:
    """Запускает ARQ-воркер отдельным процессом."""
    logger.info("Запускаю ARQ worker…")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=ROOT,
        env=os.environ.copy(),
    )
    _children.append(proc)
    return proc


def _stop_children() -> None:
    """Корректно останавливает дочерние процессы."""
    for proc in list(_children):
        if proc.poll() is not None:
            continue
        logger.info("Останавливаю процесс pid=%s…", proc.pid)
        proc.terminate()
    for proc in list(_children):
        if proc.poll() is not None:
            continue
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            logger.warning("Принудительно завершаю pid=%s", proc.pid)
            proc.kill()
            proc.wait(timeout=5)


def main() -> None:
    """Postgres + Redis → worker → Telegram-бот (в текущем процессе)."""
    setup_logging()
    apply_proxy_env()

    _ensure_infra()
    _start_worker()

    from app.main import main as run_bot

    logger.info("Запускаю Telegram-бота (Ctrl+C — остановить всё)…")
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C…")
    finally:
        _stop_children()
        logger.info("Все локальные процессы остановлены")


if __name__ == "__main__":
    main()
