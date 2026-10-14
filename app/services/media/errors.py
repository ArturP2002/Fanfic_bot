"""Типизированные ошибки пайплайна разбора медиа."""


class PipelineError(Exception):
    """Базовая ошибка пайплайна."""


class DownloadError(PipelineError):
    """Не удалось скачать ролик по ссылке."""


class RateLimitError(PipelineError):
    """Превышен лимит запросов к внешнему API распознавания."""


class RecognitionError(PipelineError):
    """Не удалось уверенно извлечь названия из медиа."""


class ProcessingError(PipelineError):
    """Прочая ошибка разбора уже скачанного медиа."""
