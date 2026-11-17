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
