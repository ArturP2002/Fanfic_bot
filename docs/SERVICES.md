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
| ЮKassa | годовая подписка | `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `YOOKASSA_TEST_MODE`, `SUBSCRIPTION_PRICE_RUB` |
| Google Sheets | промокоды блогеров и атрибуции (листы «Промокоды», «Атрибуции») | `GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEETS_SPREADSHEET_ID` |

## Ориентир стоимости одного ролика

Пайплайн: скачивание → ffmpeg (аудио + до `VIDEO_MAX_FRAMES` кадров) → Whisper → Vision OCR → один LLM extract.

| Статья | Оценка |
|--------|--------|
| Apify (если yt-dlp упал) | ~$0.001–0.003 |
| Whisper (`whisper-1`) | ~$0.006 / мин аудио |
| OCR OpenAI Vision (`gpt-4o-mini`, `detail=high`, батчи по 4 кадра) | основная статья; обычно центы за ролик |
| LLM extract (`gpt-4o-mini`, 1 запрос) | копейки |
| Поиск AO3/Ficbook по названию | $0 (HTTP scrape) |

**Итого порядка $0.03–0.08+** на средний ролик (≈ 3–8 ₽ при курсе ~100 ₽/$).  
Длинные ролики, много кадров и retry на 429 поднимают стоимость. При подписке 299 ₽/год бюджет на ИИ комфортный при умеренной активности; мониторить usage в кабинете OpenAI.

Доминирующие рычаги: длительность аудио (Whisper), число кадров и `detail=high` (OCR), длина transcript+OCR в extract-промпте.

## Подписка и пробный доступ

- `FREE_TRIAL_USES` — число бесплатных разборов; `0` = жёсткий paywall.
- Админы из `ADMIN_TELEGRAM_IDS` всегда без ограничений.
- Оплата: кнопка «💎 Подписка» → ссылка `confirmation_url` ЮKassa → «Проверить оплату».
- Промокоды блогеров: `/blogger` (админ), deep link `/start promo_CODE`, ввод при оплате.

## Смена разработчика

1. Перевыпустить/сменить все токены у владельца.
2. Передать исходный код, `.env.example`, этот список сервисов.
3. Развернуть на VPS через `docker compose` по README.
