"""FSM-состояния бота."""

from aiogram.fsm.state import State, StatesGroup


class AddStates(StatesGroup):
    """Режим добавления контента."""

    waiting_content = State()
    collecting_photos = State()


class LibraryStates(StatesGroup):
    """Поиск по библиотеке."""

    waiting_query = State()


class ErrorStates(StatesGroup):
    """Сообщение об ошибке."""

    waiting_description = State()
