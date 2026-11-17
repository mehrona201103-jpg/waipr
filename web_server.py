"""HTTP API + static WebApp for WAIP SHOP (Telegram Mini App)."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"


def validate_init_data(init_data: str, bot_token: str) -> dict:
    if not init_data or not bot_token:
        raise ValueError("Нет initData — открой через кнопку бота")
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("Нет hash")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        raise ValueError("Неверная подпись initData")
    user_raw = parsed.get("user")
    if not user_raw:
        raise ValueError("Нет user в initData")
    user = json.loads(user_raw)
    if "id" not in user:
        raise ValueError("Нет user.id")
    return user


def create_app(ctx: dict) -> web.Application:
    app = web.Application(client_max_size=12 * 1024 * 1024)
    store = ctx["store"]
    bot_token = ctx["bot_token"]

    def auth_user(request: web.Request) -> dict:
        init_data = request.headers.get("X-Telegram-Init-Data") or request.query.get("initData") or ""
        return validate_init_data(init_data, bot_token)

    def live_catalog():
        catalog = {}
        for cat, sec in ctx["catalog"].items():
            products = []
            for p in sec.get("products") or []:
                products.append({
                    "id": p["id"],
                    "title": p["title"],
                    "kind": p["kind"],
                    "units": p["units"],
                    "price": float(store.get_price(p["id"])),
                })
            catalog[cat] = {
                "title": sec["title"],
                "icon": sec.get("icon") or cat,
                "custom": bool(sec.get("custom")),
                "products": products,
            }
        return catalog

    async def player_block(nick: str):
        if not nick:
            return None
        try:
            cfg = ctx["get_game_config"]()
            row = await ctx["asyncio"].to_thread(ctx["fetch_player"], nick, cfg)
            if not row:
                return None

            def g(col):
                return row.get(col) or row.get(col.lower())

            return {
                "level": g(cfg.level_col),
                "admin": g(cfg.admin_col),
                "skin": g(cfg.skin_col),
                "money": g(cfg.money_col),
            }
        except Exception:
            logging.exception("player_block")
            return None

    async def check_subscribed(uid: int) -> bool:
        fn = ctx.get("is_subscribed")
        if not fn:
            return True
        bot = ctx.get("bot")
        if not bot:
            return True
        try:
            return await fn(bot, uid)
        except Exception:
            logging.exception("check_subscribed")
            return False

    def channel_url():
        un = ctx.get("channel_username") or ""
        if un:
            return f"https://t.me/{un.lstrip('@')}"
        return None

    async def bootstrap(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        uid = int(user["id"])
        nick = store.get_nickname(uid)
        registered = store.is_registered(uid)
        subscribed = await check_subscribed(uid)
        player = await player_block(nick) if nick else None
        return web.json_response({
            "me": {
                "telegram_id": uid,
                "username": user.get("username"),
                "nickname": nick,
                "registered": registered,
                "player": player,
            },
            "subscribed": subscribed,
            "channel_url": channel_url(),
            "catalog": live_catalog(),
            "crypto_enabled": bool(ctx.get("cryptobot_token")),
            "stars_user": ctx.get("stars_user") or "mukhammad_im",
            "apk_url": ctx.get("apk_url") or None,
            "stars_per_usd": int(ctx.get("stars_per_usd") or 50),
            "money_per_usd": int(ctx.get("money_per_usd") or 100000),
            "money_min_usd": float(ctx.get("money_min_usd") or 1),
        })

    async def check_sub(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        subscribed = await check_subscribed(int(user["id"]))
        return web.json_response({"subscribed": subscribed})

    async def register(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        uid = int(user["id"])
        body = await request.json()
        is_switch = bool(body.get("switch"))
        if not is_switch and not await check_subscribed(uid):
            return web.json_response({"error": "Сначала подпишись на канал"}, status=403)
        nick = (body.get("nickname") or "").strip()
        import re
        if not re.fullmatch(r"[A-Za-z0-9_\[\]-]{3,24}", nick):
            return web.json_response({"error": "Неверный формат ника"}, status=400)
        exists = await ctx["asyncio"].to_thread(ctx["account_exists"], nick, ctx["get_game_config"]())
        if not exists:
            return web.json_response(
                {"error": f"Аккаунт «{nick}» не найден в базе сервера"},
                status=404,
            )
        store.set_nickname(uid, nick, registered=True)
        player = await player_block(nick)
        return web.json_response({
            "ok": True,
            "me": {
                "telegram_id": uid,
                "username": user.get("username"),
                "nickname": nick,
                "registered": True,
                "player": player,
            },
        })

    async def logout(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        uid = int(user["id"])
        if store.active(uid):
            return web.json_response(
                {"error": "Сначала заверши или отмени активный заказ"},
                status=409,
            )
        store.clear_nickname(uid)
        return web.json_response({"ok": True, "registered": False, "nickname": None})

    async def orders(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        items = store.latest(int(user["id"]), 20)
        return web.json_response({
            "orders": [
                {
                    "id": o["id"],
                    "item_title": o["item_title"],
                    "price": o["price"],
                    "status": o["status"],
                    "payment_method": o.get("payment_method"),
                    "created_at": o.get("created_at"),
                }
                for o in items
            ]
        })

    async def promo(request: web.Request):
        try:
            auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        body = await request.json()
        code = (body.get("code") or "").strip()
        p = store.get_promo(code)
        if not p:
            return web.json_response({"error": "Промокод не найден"}, status=400)
        if p["max_uses"] > 0 and p["used_count"] >= p["max_uses"]:
            return web.json_response({"error": "Лимит исчерпан"}, status=400)
        return web.json_response({"code": p["code"], "percent": p["percent"]})

    async def order_create(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        uid = int(user["id"])
        if not store.is_registered(uid):
            return web.json_response({"error": "Сначала регистрация"}, status=403)
        nick = store.get_nickname(uid)
        if not nick:
            return web.json_response({"error": "Ник не привязан"}, status=400)
        body = await request.json()
        product_id = body.get("product_id")
        method = body.get("payment_method") or "stars"
        if method not in ("stars", "crypto"):
            return web.json_response({"error": "Неверный способ оплаты"}, status=400)
        if method == "crypto" and not ctx.get("cryptobot_token"):
            return web.json_response({"error": "CryptoBot не настроен"}, status=400)

        stars_per_usd = int(ctx.get("stars_per_usd") or 50)
        money_per_usd = int(ctx.get("money_per_usd") or 100000)
        money_min_usd = float(ctx.get("money_min_usd") or 1)

        units_override = None
        final_usd = None
        item = None

        if product_id == "money_custom":
            try:
                usd = float(body.get("usd") or 0)
                units = int(body.get("units") or 0)
            except (TypeError, ValueError):
                return web.json_response({"error": "Неверная сумма"}, status=400)
            if usd < money_min_usd:
                return web.json_response({"error": f"Минимум ${money_min_usd}"}, status=400)
            if units <= 0:
                units = int(round(usd * money_per_usd))
            item = {
                "id": "money_custom",
                "title": f"{units:,} игровой валюты".replace(",", " "),
                "kind": "money",
                "units": units,
                "price": usd,
                "category": "money",
            }
            final_usd = usd
            units_override = units
        else:
            item = store.product(product_id)
            if not item:
                return web.json_response({"error": "Товар не найден"}, status=404)
            final_usd = float(item["price"])

        if store.active(uid):
            return web.json_response({"error": "Есть незавершённый заказ"}, status=409)

        promo_code = body.get("promo")
        discount = 0
        if promo_code:
            promo = store.get_promo(promo_code)
            if promo and (promo["max_uses"] == 0 or promo["used_count"] < promo["max_uses"]):
                discount = int(promo["percent"])
                final_usd = max(0.01, final_usd * (100 - discount) / 100)
            else:
                promo_code = None

        try:
            order = store.create(
                uid, nick, item["id"], item,
                payment_method=method,
                promo_code=promo_code,
                discount=discount,
                final_price=final_usd,
                units_override=units_override,
            )
            if promo_code:
                store.use_promo(promo_code)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=409)

        stars = int(order.get("stars") or max(1, round(float(final_usd) * stars_per_usd)))
        pay_url = None
        invoice_link = None

        if method == "crypto":
            try:
                inv = await ctx["asyncio"].to_thread(ctx["create_crypto_invoice"], order)
                invoice_id = str(inv.get("invoice_id") or inv.get("invoiceId"))
                pay_url = inv.get("bot_invoice_url") or inv.get("pay_url") or inv.get("payUrl")
                store.set_crypto_invoice(order["id"], invoice_id)
            except Exception as e:
                logging.exception("web crypto")
                store.cancel(order["id"], uid)
                return web.json_response({"error": f"CryptoBot: {e}"}, status=502)

        if method == "stars":
            create_link = ctx.get("create_stars_invoice_link")
            if create_link:
                try:
                    invoice_link = await create_link(order)
                except Exception:
                    logging.exception("stars invoice link")
                    invoice_link = None

            bot = ctx.get("bot")
            if bot and not invoice_link:
                stars_user = ctx.get("stars_user") or "mukhammad_im"
                try:
                    await bot.send_message(
                        uid,
                        f"<b>Заказ #{order['id']}</b>\n"
                        f"{item['title']}\n"
                        f"К оплате: <b>{stars} Stars</b> (${final_usd})\n\n"
                        f"Отправь подарок (Gift) на <b>@{stars_user}</b> "
                        f"на сумму <b>{stars} Stars</b>.\n"
                        f"Скриншоты не нужны — после отправки подтверди в приложении.",
                    )
                except Exception:
                    logging.exception("notify stars")

        return web.json_response({
            "order_id": order["id"],
            "price": stars,
            "usd": final_usd,
            "stars": stars,
            "status": order["status"],
            "pay_url": pay_url,
            "invoice_link": invoice_link,
        })

    async def order_status(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        uid = int(user["id"])
        try:
            oid = int(request.query.get("id") or 0)
        except ValueError:
            return web.json_response({"error": "bad id"}, status=400)
        order = store.get(oid)
        if not order or order["telegram_id"] != uid:
            return web.json_response({"error": "Не найден"}, status=404)
        return web.json_response({
            "id": order["id"],
            "status": order["status"],
            "item_title": order["item_title"],
            "price": order["price"],
        })

    async def stars_confirm(request: web.Request):
        """Пользователь подтвердил отправку подарка — ставим в processing и уведомляем владельца.
        Выдача автоматически, если включён AUTO_GRANT_STARS_GIFT, иначе owner может нажать ok.
        """
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        uid = int(user["id"])
        body = await request.json()
        try:
            oid = int(body.get("order_id") or 0)
        except (TypeError, ValueError):
            return web.json_response({"error": "bad order_id"}, status=400)
        order = store.get(oid)
        if not order or order["telegram_id"] != uid:
            return web.json_response({"error": "Не найден"}, status=404)
        if order["status"] not in ("awaiting_stars", "awaiting_receipt"):
            return web.json_response({"error": "Заказ не ждёт оплату"}, status=409)

        # Переводим в processing / auto-grant
        auto = bool(ctx.get("auto_grant_stars_gift"))
        with store.db:
            store.db.execute(
                "UPDATE orders SET status=?, updated_at=datetime('now') WHERE id=?",
                ("processing" if auto else "review", oid),
            )
        order = store.get(oid)
        bot = ctx.get("bot")
        owner_id = ctx.get("owner_id")

        if auto and bot:
            try:
                grant_fn = ctx.get("grant_to_game")
                game_cfg = ctx["get_game_config"]()
                result = await ctx["asyncio"].to_thread(grant_fn, order, game_cfg)
                store.delivered(oid)
                if isinstance(result, str) and result.startswith("pwd:"):
                    notify_pwd = ctx.get("notify_password")
                    if notify_pwd:
                        await notify_pwd(bot, order, result[4:])
                else:
                    await bot.send_message(
                        uid,
                        f"<b>Оплата принята</b>\nЗаказ #{oid} — {order['item_title']}\n"
                        f"Ник: <code>{order['nickname']}</code>",
                    )
                if owner_id:
                    try:
                        await bot.send_message(
                            owner_id,
                            f"Stars gift auto #{oid} → {order['nickname']} · {order['item_title']}",
                        )
                    except Exception:
                        pass
            except Exception as e:
                logging.exception("auto grant stars gift")
                with store.db:
                    store.db.execute(
                        "UPDATE orders SET status='review', error_text=?, updated_at=datetime('now') WHERE id=?",
                        (str(e)[:500], oid),
                    )
                if owner_id and bot:
                    try:
                        await bot.send_message(owner_id, f"Stars gift paid but grant failed #{oid}: {e}")
                    except Exception:
                        pass
                return web.json_response({"error": "Оплата отмечена, выдача с ошибкой — админ проверит"}, status=500)
        elif bot and owner_id:
            try:
                from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Выдать", callback_data=f"ok:{oid}"),
                    InlineKeyboardButton(text="Отклонить", callback_data=f"no:{oid}"),
                ]])
                await bot.send_message(
                    owner_id,
                    f"<b>Stars gift #{oid}</b>\n"
                    f"{order['item_title']} · {order['price']} Stars\n"
                    f"Ник: <code>{order['nickname']}</code>\n"
                    f"User: <code>{uid}</code>",
                    reply_markup=kb,
                )
            except Exception:
                logging.exception("notify owner stars gift")

        return web.json_response({"ok": True, "status": store.get(oid)["status"]})

    async def health(_request: web.Request):
        return web.json_response({"ok": True})

    async def index(_request: web.Request):
        return web.FileResponse(WEBAPP_DIR / "index.html")

    app.router.add_get("/", index)
    app.router.add_get("/webapp", index)
    app.router.add_get("/webapp/", index)
    app.router.add_static("/webapp/", WEBAPP_DIR, show_index=False)
    app.router.add_get("/api/bootstrap", bootstrap)
    app.router.add_post("/api/check_sub", check_sub)
    app.router.add_post("/api/register", register)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/orders", orders)
    app.router.add_post("/api/promo", promo)
    app.router.add_post("/api/order", order_create)
    app.router.add_get("/api/order_status", order_status)
    app.router.add_post("/api/stars_confirm", stars_confirm)
    app.router.add_get("/health", health)
    return app


async def start_web_server(ctx: dict, host: str = "0.0.0.0", port: int = 8080):
    app = create_app(ctx)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host or "0.0.0.0", int(port or 8080))
    await site.start()
    logging.info("WebApp listening on %s:%s", host, port)
    return runner
