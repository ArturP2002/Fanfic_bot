"""CLI-утилиты: импорт стартовой базы и служебные команды."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Нужно до импортов app.*, если файл запускают как python app/cli.py
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import delete, select

from app.db.session import async_session_factory, init_db
from app.logging_config import setup_logging
from app.models import LibraryItem, SourceWork, Work, WorkAlias
from app.models.enums import ConfirmationStatus
from app.services.extract import is_junk_title
from app.services.import_works import import_works_from_file

logger = logging.getLogger(__name__)


async def cmd_import_works(path: Path) -> int:
    """Импортирует CSV/XLSX в общую базу."""
    if not path.exists():
        logger.error("Файл не найден: %s", path)
        return 1
    await init_db()
    async with async_session_factory() as session:
        stats = await import_works_from_file(session, path)
    print(
        f"Импорт завершён. Всего строк: {stats['total']}, "
        f"создано: {stats['created']}, дополнено: {stats['updated']}, "
        f"пропущено: {stats['skipped']}, "
        f"не удалось загрузить: {stats['failed']}"
    )
    return 0


async def cmd_init_db() -> int:
    """Создаёт таблицы."""
    await init_db()
    print("Таблицы созданы.")
    return 0


async def cmd_cleanup_junk_works(*, dry_run: bool = False) -> int:
    """Удаляет мусорные works (needs_review + длинный/речевой title).

    Каскадно убирает aliases / source_works / library_items.
    Стартовые confirmed-работы (Lionheart и т.п.) не трогаются.
    """
    await init_db()
    async with async_session_factory() as session:
        result = await session.execute(
            select(Work).where(
                Work.confirmation_status == ConfirmationStatus.NEEDS_REVIEW.value
            )
        )
        candidates = list(result.scalars().all())
        # Мусор: речевые маркеры / длина, либо needs_review без автора и ссылки
        # (типичный след эвристики по транскрипту). Confirmed-стартовые не трогаем.
        junk = [
            w
            for w in candidates
            if is_junk_title(w.title)
            or (not w.author and not w.source_url)
        ]

        if not junk:
            print("Мусорных works не найдено.")
            return 0

        print(f"Найдено мусорных works: {len(junk)}")
        for work in junk[:30]:
            print(f"  id={work.id} title={work.title[:100]!r}")
        if len(junk) > 30:
            print(f"  … и ещё {len(junk) - 30}")

        if dry_run:
            print("dry-run: удаление не выполнялось.")
            return 0

        ids = [w.id for w in junk]
        await session.execute(delete(SourceWork).where(SourceWork.work_id.in_(ids)))
        await session.execute(delete(LibraryItem).where(LibraryItem.work_id.in_(ids)))
        await session.execute(delete(WorkAlias).where(WorkAlias.work_id.in_(ids)))
        await session.execute(delete(Work).where(Work.id.in_(ids)))
        await session.commit()
        print(f"Удалено works: {len(ids)}")
        logger.info("cleanup-junk-works удалил %s записей: %s", len(ids), ids)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI FicBrain")
    sub = parser.add_subparsers(dest="command", required=True)

    import_p = sub.add_parser("import-works", help="Импорт стартовой таблицы фанфиков")
    import_p.add_argument("path", type=Path, help="Путь к CSV или XLSX")

    sub.add_parser("init-db", help="Создать таблицы БД")

    cleanup_p = sub.add_parser(
        "cleanup-junk-works",
        help="Удалить мусорные needs_review works (обрывки речи)",
    )
    cleanup_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет удалено",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "import-works":
        code = asyncio.run(cmd_import_works(args.path))
    elif args.command == "init-db":
        code = asyncio.run(cmd_init_db())
    elif args.command == "cleanup-junk-works":
        code = asyncio.run(cmd_cleanup_junk_works(dry_run=args.dry_run))
    else:
        parser.print_help()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
