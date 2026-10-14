"""Перечисления доменной модели."""

import enum


class ReadingStatus(str, enum.Enum):
    """Статус чтения в личной библиотеке."""

    WANT = "want"
    READING = "reading"
    READ = "read"
    DROPPED = "dropped"


class WorkStatus(str, enum.Enum):
    """Статус написания произведения."""

    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    UNKNOWN = "unknown"


class ConfirmationStatus(str, enum.Enum):
    """Признак подтверждённости записи в общей базе."""

    CONFIRMED = "confirmed"
    NEEDS_REVIEW = "needs_review"


class SourceType(str, enum.Enum):
    """Тип источника рекомендации."""

    VIDEO_URL = "video_url"
    VIDEO_FILE = "video_file"
    SCREENSHOT = "screenshot"
    TEXT = "text"
    FIC_LINK = "fic_link"


class JobType(str, enum.Enum):
    """Тип фоновой задачи обработки."""

    VIDEO_URL = "video_url"
    VIDEO_FILE = "video_file"
    SCREENSHOTS = "screenshots"


class JobStatus(str, enum.Enum):
    """Статус фоновой задачи."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
