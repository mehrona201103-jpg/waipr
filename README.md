# WAIP SHOP

Telegram-бот + **Mini App** для продажи привилегий SA:MP / CRMP.

## Возможности
- Категории: уровни, админка, скины, валюта (своя сумма), сброс пароля
- Цены в **USD**; Stars: `1 USD = STARS_PER_USD` (по умолчанию 50)
- Валюта: `1 USD = MONEY_PER_USD` (100 000), минимум `MONEY_MIN_USD`
- Оплата:
  - **Telegram Stars (XTR)** — счёт на баланс бота, выдача сразу
  - **CryptoBot** — авто, выдача сразу
  - Fallback: подарок на `@STARS_GIFT_USERNAME` без скринов
- Админ меняет только USD — Stars пересчитываются сами
- Web App: SVG-иконки, анимации, без эмодзи

## Web App
1. HTTPS (nginx + certbot / Cloudflare Tunnel / ngrok)
2. В `config.py` укажи `WEBAPP_URL` без слэша в конце
3. Прокси HTTPS → `http://127.0.0.1:8080`

### Быстрый тест
```bash
cloudflared tunnel --url http://127.0.0.1:8080
```

## APK с GitHub Releases

1. Загрузи `.apk` как **Release asset** в GitHub.
2. Прямая ссылка:
   ```
   https://github.com/USER/REPO/releases/download/v1.0.0/game.apk
   ```
   или latest:
   ```
   https://github.com/USER/REPO/releases/latest/download/game.apk
   ```
   Имя файла в URL = имя asset.
3. В `config.py`:
   ```python
   APK_DOWNLOAD_URL = "https://github.com/USER/REPO/releases/latest/download/game.apk"
   ```
4. Перезапусти бота. Кнопка APK в WebApp откроет ссылку и начнёт скачивание.

## Сброс пароля
`PASSWORD_HASH` в config:

| Длина password | Метод |
|----------------|-------|
| plain | `plain` |
| 32 hex | `md5` |
| 40 hex | `sha1` |
| 64 hex | `sha256` |
| 128 hex | `whirlpool` |

```sql
SELECT name, CHAR_LENGTH(password) FROM accounts LIMIT 5;
```

Бот пишет **хеш** в БД, игроку отдаёт **plain**.

## Установка
```bash
pip install -r requirements.txt
# config.py
python3 main.py
```

## Render
- Start command: `python main.py`; порт берётся из переменной `PORT` автоматически.
- Секреты задавай в Environment: `BOT_TOKEN`, `OWNER_ID`, `GAME_DB_PASSWORD` и т.д. (см. config.py).
- Запускай ТОЛЬКО один экземпляр бота (иначе TelegramConflictError).

## Новое
- **Рефералы**: ссылка в профиле (бот и Web App). +5% рефереру за каждое оплаченное пополнение друга, 100% = любой товар бесплатно.
- **Бонус дня**: 1 000 000 валюты раз в 24 ч (`DAILY_BONUS_*` в config.py).
- **Валюта**: любое количество от `MONEY_MIN_UNITS` (100 000), без верхнего лимита; цена по курсу считается сама.
- **Секретные команды (только владелец)**: `/giveadm Ник Уровень`, `/give cash Ник Сумма`, `/give donate Ник Сумма` (поле `GAME_DONATE_COLUMN`).

## Хостинг — важно
- **Render Free засыпает** через ~15 мин без запросов: бот перестанет отвечать. Бот сам пингует `RENDER_EXTERNAL_URL/health` каждые 10 мин; дополнительно можно настроить UptimeRobot на `/health`.
- **Диск Render Free стирается при каждом деплое/рестарте** → пропадут `orders.sqlite3` (заказы, модераторы, промокоды, рефералы, бонусы). Решение: платный Persistent Disk и переменная `DB_PATH=/var/data/orders.sqlite3`, либо VPS (Hetzner / Timeweb / Aeza от ~3–5 $/мес) + systemd.
- На VPS: `python3 -m venv venv && venv/bin/pip install -r requirements.txt`, сервис systemd с `Restart=always`, HTTPS для Web App — nginx + certbot или Cloudflare Tunnel.
- MySQL игрового сервера должен разрешать удалённый доступ с IP хостинга (у Render IP меняются — лучше VPS со статичным IP и белым списком).
