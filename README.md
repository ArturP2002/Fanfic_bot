# FicBrain

Telegram-бот — «память для фанфиков». Принимает ролики, скриншоты, текст и прямые ссылки,
находит произведения, сверяет с общей базой и сохраняет в личную библиотеку.

## Стек

- Python 3.12, aiogram 3
- PostgreSQL 16, SQLAlchemy 2, Alembic
- Redis + ARQ (очередь тяжёлых задач)
- OpenAI: Whisper + Vision OCR + gpt-4o-mini
- Apify (Instagram / TikTok)
- ffmpeg
- Парсеры: AO3, Ficbook, FanFiction.net

## Быстрый старт (локально)

1. Скопируйте окружение:

```bash
cp .env.example .env
# заполните BOT_TOKEN и при необходимости ключи OpenAI / Apify
```

2. Установите зависимости:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Для локального `DATABASE_URL` используйте `localhost`:

```env
DATABASE_URL=postgresql+asyncpg://ficbrain:ficbrain_secret@localhost:5433/ficbrain
REDIS_URL=redis://localhost:6379/0
```

> Порт PostgreSQL на хосте — **5433** (внутри Docker по-прежнему 5432), чтобы не пересекаться с локальным Postgres.

3. Создайте таблицы и импортируйте стартовую базу (один раз):

```bash
python -m app.cli init-db
python -m app.cli import-works data/start_works.csv
```

4. Запустите всё сразу (Postgres + Redis + worker + бот):

```bash
python main.py
```

Отдельные процессы, если нужно:

```bash
docker compose up -d postgres redis
python -m app.main
python -m app.worker
# или: arq app.worker.WorkerSettings
```

Либо всё через Docker:

```bash
docker compose up --build
```

## Импорт таблицы

Шаблон: `data/templates/works_template.csv` (те же колонки для Excel `.xlsx`).

```bash
python -m app.cli import-works path/to/works.xlsx
```

Повторный импорт не создаёт дубли (по `source_url` или нормализованной паре название+автор).

Упрощённый вариант — таблица только со ссылками на AO3 / Ficbook / FanFiction.net
(шаблон `data/templates/works_links_template.csv`). Название, автор, фандом, пейринг,
статус, объём и метки подтянутся со страниц; колонки «Сокращения» и «Русское название»
можно заполнить вручную. Между загрузками страниц пауза ~1.5 с, поэтому 100 ссылок
импортируются за несколько минут.

Для переводов с Ficbook импорт берёт ссылку на оригинал со страницы перевода и загружает
оригинал: в «Ссылку» попадает оригинал (AO3 / FanFiction.net), в «Ссылку на перевод» —
Ficbook, оригинальное название становится альтернативным. Двуязычные названия вида
«Мера Человека/Measure Of A Man» делятся на русское и оригинальное. Закрытые для гостей
работы AO3 и страницы FanFiction.net (защита Cloudflare) без авторизации не читаются —
такие переводы сохраняются с данными только со страницы Ficbook.

Стартовый список заказчика: `data/start_works.csv`.

## Деплой на VPS

1. Установите Docker + Docker Compose.
2. Скопируйте проект, создайте `.env` с боевыми секретами (`BOT_TOKEN`, `OPENAI_API_KEY`, `APIFY_TOKEN`).
3. Запустите:

```bash
docker compose up -d --build
docker compose exec bot python -m app.cli import-works data/templates/works_template.csv
```

Ориентир по железу: 2–4 GB RAM.

## Внешние сервисы

| Сервис | Назначение |
|--------|------------|
| Telegram Bot API | интерфейс |
| OpenAI | Whisper + Vision OCR + извлечение названий |
| Apify | скачивание IG/TikTok |
| PostgreSQL / Redis | данные и очередь |

Все ключи задаются через переменные окружения, не в коде.

## Документация

- [Ограничения](docs/LIMITATIONS.md)
- [Схема БД](docs/DATABASE.md)

## Смена настроек

Основные параметры — в `.env` (см. `.env.example`): токен бота, БД, Redis, ключи API,
лимит кадров, язык Whisper, id админов, `SOCKS5_PROXY` для VPS в РФ.

## Язык в коде

Комментарии, docstring и сообщения логов — на русском. UI бота — на русском.
