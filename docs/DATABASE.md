# Структура базы данных FicBrain

## users
Пользователи Telegram. Поля: `id`, `telegram_id`, `username`, `first_name`, `created_at`.

## works
Общая база произведений (одна карточка на фанфик для всех пользователей).
Ключевые поля: названия, автор, нормализованные поля, фандом, пейринг, статус написания,
объём, теги, ссылки, `confirmation_status` (`confirmed` / `needs_review`), даты.

## work_aliases
Альтернативные названия и сокращения. Все ведут к одному `works.id`.

## library_items
Личная библиотека: `user_id` + `work_id` + `reading_status`
(`want` / `reading` / `read` / `dropped`). Unique по паре user/work.

## sources
Источник рекомендации (ролик, скрин, текст, ссылка): тип, площадка, url, автор публикации, raw_text.

## source_works
Связь many-to-many: какие произведения были найдены в источнике.

## processing_jobs
Фоновые задачи разбора медиа и их статусы.

## error_reports
Сообщения пользователей «Сообщить об ошибке».
