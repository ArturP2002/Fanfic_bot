# Внешние сервисы FicBrain

Все аккаунты и ключи должны принадлежать владельцу продукта.

| Сервис | Для чего | Переменная окружения |
|--------|----------|----------------------|
| Telegram BotFather | токен бота | `BOT_TOKEN` |
| OpenAI | Whisper + Vision OCR + gpt-4o-mini | `OPENAI_API_KEY`, `OPENAI_VISION_MODEL` |
| yt-dlp | основное скачивание Instagram/TikTok | локально / в Docker |
| Apify | запасное скачивание IG/TikTok | `APIFY_TOKEN`, actors |
| PostgreSQL | основная БД | `DATABASE_URL` |
| Redis | очередь ARQ | `REDIS_URL` |
| SOCKS5-прокси | исходящий трафик с VPS в РФ (Telegram, OpenAI, площадки, yt-dlp) | `SOCKS5_PROXY` |

## Ориентир стоимости одного ролика

- Apify: ~$0.001–0.003
- Whisper: ~$0.006/мин
- OCR OpenAI Vision (gpt-4o-mini): зависит от числа кадров, обычно копейки–центы за ролик
- LLM gpt-4o-mini (извлечение названий): копейки
- Итого порядка $0.03–0.08+ на средний ролик (зависит от длины и числа кадров)

## Смена разработчика

1. Перевыпустить/сменить все токены у владельца.
2. Передать исходный код, `.env.example`, этот список сервисов.
3. Развернуть на VPS через `docker compose` по README.
