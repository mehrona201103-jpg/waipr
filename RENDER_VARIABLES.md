# WAIP SHOP — Render Environment Variables

В Render открой Service → Environment → Add Environment Variable. Добавь значения из своих аккаунтов/хостинга; секреты не отправляй в чат.

## Обязательные
- `BOT_TOKEN` — токен бота от @BotFather.
- `OWNER_ID` — твой числовой Telegram ID.
- `WEBAPP_URL` — HTTPS адрес WebApp, например `https://ИМЯ-СЕРВИСА.onrender.com/webapp/`.
- `CHANNEL_ID` — числовой ID канала, обычно начинается с `-100`; для приватного канала используй именно ID.
- `CHANNEL_USERNAME` — публичный username канала без `@`; можно оставить пустым, если канал приватный.
- `CHANNEL_URL` — ссылка для кнопки подписки: публичная `https://t.me/username` или приватная invite-ссылка `https://t.me/+...`.
- `GAME_DB_HOST` — MySQL host.
- `GAME_DB_PORT` — обычно `3306`.
- `GAME_DB_USER` — MySQL username.
- `GAME_DB_PASSWORD` — новый пароль MySQL.
- `GAME_DB_NAME` — имя базы данных.

## Настройки структуры игровой таблицы (если отличаются от стандартных)
- `GAME_ACCOUNT_TABLE` — таблица аккаунтов, по умолчанию `accounts`.
- `GAME_NICK_COLUMN` — колонка ника, по умолчанию `name`.
- `GAME_LEVEL_COLUMN` — уровень, по умолчанию `level`.
- `GAME_ADMIN_COLUMN` — админ-уровень, по умолчанию `admin`.
- `GAME_MONEY_COLUMN` — игровая валюта, по умолчанию `money`.
- `GAME_SKIN_COLUMN` — скин, по умолчанию `skin`.
- `GAME_PASSWORD_COLUMN` — пароль, по умолчанию `password`.
- `GAME_DONATE_COLUMN` — донат, по умолчанию `donate`.
- `PASSWORD_HASH` — `md5`, `sha1`, `sha256`, `double_md5`, `whirlpool` или `plain` — выбери алгоритм своего игрового мода.

## Рекомендуемые
- `DB_PATH` — `/var/data/orders.sqlite3` только если подключён Render Persistent Disk, иначе не задавай.
- `PYTHON_VERSION` — `3.11.9` (если Render просит версию Python).

## Важно
1. Добавь Telegram-бота администратором канала с правом просмотра участников.
2. `CHANNEL_ID` и `CHANNEL_USERNAME` должны относиться к одному и тому же каналу. Для приватного канала `CHANNEL_ID` обязателен.
3. Если ты владелец магазина, `OWNER_ID` должен совпадать с твоим Telegram ID — он будет пропущен через обязательную подписку. Остальные пользователи проходят реальную проверку Telegram.
4. Не вставляй `BOT_TOKEN` или пароль базы в исходники GitHub.
5. После изменения переменных нажми Save Changes и дождись нового Deploy.
6. На бесплатном Render локальная SQLite может сбрасываться при перезапуске/переразмещении; для сохранения заказов нужен Persistent Disk или внешняя БД.

## Что не нужно добавлять
`PORT` Render задаёт автоматически. `WEBAPP_HOST`, `WEBAPP_PORT`, `CHANNEL_ID` уже обрабатываются кодом; не задавай `PORT` вручную.
