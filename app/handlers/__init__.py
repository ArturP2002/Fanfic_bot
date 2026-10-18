"""Роутеры обработчиков Telegram."""

from aiogram import Router

from app.handlers import add, admin, common, errors, library, subscribe


def setup_routers() -> Router:
    """Собирает корневой роутер."""
    root = Router()
    root.include_router(common.router)
    root.include_router(subscribe.router)
    root.include_router(admin.router)
    root.include_router(library.router)
    root.include_router(add.router)
    root.include_router(errors.router)
    return root
