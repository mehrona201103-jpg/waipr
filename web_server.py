"""HTTP API + static WebApp for WAIP SHOP (Telegram Mini App)."""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import math
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from tenants import MAX_TENANTS_PER_USER, TenantError


def _shade(hex_color: str, factor: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (int(r * factor), int(g * factor), int(b * factor))


def _theme_css(hex_color: str) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (f":root{{--purple:{hex_color};--purple2:{_shade(hex_color, 0.8)};"
            f"--line:rgba({r},{g},{b},0.25);--glow:0 0 28px rgba({r},{g},{b},0.35)}}")

WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"


def validate_init_data(init_data: str, bot_token: str) -> dict:
    if not init_data or not bot_token:
        raise ValueError("Нет initData — открой через кнопку бота")
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    if len(init_data) > 8192:
        raise ValueError("initData слишком длинный")
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("Нет hash")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        raise ValueError("Неверная подпись initData")
    try:
        auth_date = int(parsed.get("auth_date") or 0)
    except ValueError:
        auth_date = 0
    if not auth_date or time.time() - auth_date > 24 * 3600:
        raise ValueError("Сессия устарела — закрой и открой приложение заново")
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

    async def read_json(request: web.Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

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
                    "price": int(store.get_price(p["id"])),
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
        configured = (ctx.get("channel_url") or "").strip()
        if configured.startswith("https://t.me/"):
            return configured
        un = ctx.get("channel_username") or ""
        if un:
            return f"https://t.me/{un.lstrip('@')}"
        return None

    def extras(uid: int) -> dict:
        out = {}
        try:
            info = ctx["ref_info"](uid)
            out["ref"] = {
                "link": ctx["ref_link"](uid),
                "bonus": info["bonus"],
                "refs": info["refs"],
                "percent": info["percent"],
            }
            out["daily"] = {"amount": ctx["daily_amount"], "left": ctx["daily_left"](uid)}
        except Exception:
            logging.exception("extras")
        return out

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
            "stars_user": ctx.get("stars_user") or "",
            "apk_url": ctx.get("apk_url") or None,
            "money_per_star": int(ctx.get("money_per_star") or 2000),
            "money_min_units": int(ctx.get("money_min_units") or 100000),
            "can_create_tenant": bool(ctx.get("tenant_manager")),
            "tenant_limit": MAX_TENANTS_PER_USER,
            **extras(uid),
        })

    async def check_sub(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        subscribed = await check_subscribed(int(user["id"]))
        return web.json_response({"subscribed": subscribed})

    _fails: dict[int, list[float]] = {}

    def _too_many(uid: int) -> bool:
        t = time.time()
        recent = [x for x in _fails.get(uid, []) if t - x < 900]
        _fails[uid] = recent
        return len(recent) >= 5

    async def register(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        uid = int(user["id"])
        body = await read_json(request)
        # Подписка проверяется всегда — в том числе при смене аккаунта (switch)
        if not await check_subscribed(uid):
            return web.json_response({"error": "Сначала подпишись на канал"}, status=403)
        nick = (body.get("nickname") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_\[\]-]{3,24}", nick):
            return web.json_response({"error": "Неверный формат ника"}, status=400)
        try:
            exists = await ctx["asyncio"].to_thread(ctx["account_exists"], nick, ctx["get_game_config"]())
        except ctx["GrantError"] as e:
            logging.error("register check: %s", e)
            return web.json_response({"error": "Не удалось проверить аккаунт, попробуй позже"}, status=500)
        if not exists:
            return web.json_response({"error": f"Аккаунт «{nick}» не найден в базе сервера"}, status=404)
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
        body = await read_json(request)
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
        body = await read_json(request)
        product_id = body.get("product_id")
        if not isinstance(product_id, str):
            return web.json_response({"error": "Товар не найден"}, status=404)
        method = body.get("payment_method") or "stars"
        if method != "stars":
            return web.json_response({"error": "Доступна только оплата Telegram Stars"}, status=400)

        money_per_star = int(ctx.get("money_per_star") or 2000)

        units_override = None
        final_stars = None
        item = None

        if product_id == "money_custom":
            try:
                units = int(body.get("units") or 0)
            except (TypeError, ValueError):
                return web.json_response({"error": "Неверная сумма"}, status=400)
            min_units = int(ctx.get("money_min_units") or 100000)
            if units < min_units:
                return web.json_response({"error": f"Минимум {min_units:,}".replace(",", " ")}, status=400)
            if units > 10**12:
                return web.json_response({"error": "Слишком большая сумма"}, status=400)
            # Цену считаем на сервере по курсу (целые Stars, округление вверх)
            stars_price = max(1, math.ceil(units / money_per_star))
            item = {
                "id": "money_custom",
                "title": f"{units:,} игровой валюты".replace(",", " "),
                "kind": "money",
                "units": units,
                "price": stars_price,
                "category": "money",
            }
            final_stars = stars_price
            units_override = units
        else:
            item = store.product(product_id)
            if not item:
                return web.json_response({"error": "Товар не найден"}, status=404)
            final_stars = int(item["price"])

        if store.active(uid):
            return web.json_response({"error": "Есть незавершённый заказ"}, status=409)

        promo_code = body.get("promo")
        discount = 0
        if promo_code:
            promo = store.get_promo(promo_code)
            if promo and (promo["max_uses"] == 0 or promo["used_count"] < promo["max_uses"]):
                discount = int(promo["percent"])
                final_stars = max(1, math.ceil(final_stars * (100 - discount) / 100))
            else:
                promo_code = None

        try:
            order = store.create(
                uid, nick, item["id"], item,
                payment_method=method,
                promo_code=promo_code,
                discount=discount,
                final_price=final_stars,
                units_override=units_override,
            )
            if promo_code:
                store.use_promo(promo_code)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=409)

        stars = int(order.get("stars") or final_stars)
        pay_url = None
        invoice_link = None

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
                stars_user = ctx.get("stars_user") or ""
                try:
                    await bot.send_message(
                        uid,
                        f"<b>Заказ #{order['id']}</b>\n"
                        f"{html.escape(str(item['title']))}\n"
                        f"К оплате: <b>{stars} Stars</b>\n\n"
                        f"Отправь подарок (Gift) на <b>@{stars_user}</b> "
                        f"на сумму <b>{stars} Stars</b>.\n"
                        f"Скриншоты не нужны — после отправки подтверди в приложении.",
                    )
                except Exception:
                    logging.exception("notify stars")

        return web.json_response({
            "order_id": order["id"],
            "price": stars,
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
        body = await read_json(request)
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
        # Нажатие кнопки «я отправил» НЕ доказывает оплату — только ручная проверка.
        # (Настоящая оплата Stars выдаётся сама по successful_payment в боте.)
        auto = False
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
                    f"{html.escape(order['item_title'])} · {order['price']} Stars\n"
                    f"Ник: <code>{html.escape(order['nickname'])}</code>\n"
                    f"User: <code>{uid}</code>",
                    reply_markup=kb,
                )
            except Exception:
                logging.exception("notify owner stars gift")

        return web.json_response({"ok": True, "status": store.get(oid)["status"]})

    async def daily(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        uid = int(user["id"])
        try:
            amount = await ctx["claim_daily"](uid)
        except ctx["GrantError"] as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception:
            logging.exception("daily")
            return web.json_response({"error": "Ошибка выдачи бонуса"}, status=500)
        return web.json_response({"ok": True, "amount": amount, "left": ctx["daily_left"](uid)})

    async def ref_claim(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        uid = int(user["id"])
        body = await read_json(request)
        pid = body.get("product_id")
        if not isinstance(pid, str):
            return web.json_response({"error": "Выбери товар"}, status=400)
        try:
            oid = await ctx["claim_free"](uid, pid)
        except ctx["GrantError"] as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception:
            logging.exception("ref_claim")
            return web.json_response({"error": "Ошибка"}, status=500)
        return web.json_response({"ok": True, "order_id": oid, **extras(uid)})

    async def health(_request: web.Request):
        return web.json_response({"ok": True})

    async def index(request: web.Request):
        prefix = request.headers.get("X-Forwarded-Prefix", "")  # /t/<id> у дочернего магазина
        if request.path == "/webapp":
            raise web.HTTPFound(prefix + "/webapp/")
        page = (WEBAPP_DIR / "index.html").read_text(encoding="utf-8")
        # База для относительных ссылок (style.css, app.js, логотип), иначе страница открывается без стилей
        page = page.replace("<head>", f'<head>\n  <base href="{prefix}/webapp/" />', 1)
        title = ctx.get("shop_title") or "WAIP SHOP"
        if title != "WAIP SHOP":
            page = page.replace("WAIP SHOP", html.escape(title))
        page = page.replace("<head>", "<head>\n  <script>window.SHOP_TITLE=" + json.dumps(title).replace("<", "\\u003c") + ";</script>", 1)
        theme = ctx.get("shop_theme") or ""
        if re.fullmatch(r"#[0-9a-fA-F]{6}", theme):
            page = page.replace("</head>", f"<style>{_theme_css(theme)}</style></head>", 1)
        return web.Response(text=page, content_type="text/html",
                            headers={"Cache-Control": "no-cache"})

    async def custom_logo(_request: web.Request):
        path = ctx.get("shop_logo_path") or ""
        if path and Path(path).is_file():
            return web.FileResponse(Path(path))
        return web.FileResponse(WEBAPP_DIR / "waip-shop-logo.webp")

    # ── создание дочерних магазинов (только на мастере) ─────────────────────
    tm = ctx.get("tenant_manager")

    async def tenant_mine(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        if not tm:
            return web.json_response({"enabled": False, "tenants": []})
        return web.json_response({
            "enabled": True,
            "tenants": tm.list_for_owner(int(user["id"])),
            "limit": MAX_TENANTS_PER_USER,
        })

    async def tenant_validate(request: web.Request):
        try:
            auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        if not tm:
            return web.json_response({"error": "Создание ботов на этом сервере выключено"}, status=403)
        body = await read_json(request)
        try:
            return web.json_response(await tm.validate(body))
        except TenantError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def tenant_create(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        if not tm:
            return web.json_response({"error": "Создание ботов на этом сервере выключено"}, status=403)
        body = await read_json(request)
        try:
            job_id = tm.start_create(int(user["id"]), body, body.get("logo"))
        except TenantError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"job_id": job_id})

    async def tenant_job(request: web.Request):
        try:
            user = auth_user(request)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=401)
        job = tm.job_for(request.query.get("id", ""), int(user["id"])) if tm else None
        if not job:
            return web.json_response({"error": "Задача не найдена"}, status=404)
        return web.json_response(job)

    async def internal_sale(request: web.Request):
        # Принимаем отчёты только с localhost и только с правильным секретом магазина
        if request.remote not in ("127.0.0.1", "::1") or not tm:
            return web.json_response({"error": "forbidden"}, status=403)
        body = await read_json(request)
        try:
            tm.record_sale(int(body.get("tenant_id")), request.headers.get("X-Tenant-Secret", ""),
                           str(body.get("external_order_id")), int(body.get("stars")))
        except (TenantError, TypeError, ValueError):
            return web.json_response({"error": "forbidden"}, status=403)
        return web.json_response({"ok": True})

    async def tenant_proxy(request: web.Request):
        if not tm:
            raise web.HTTPNotFound()
        return await tm.proxy(request)

    async def tenant_redirect(request: web.Request):
        raise web.HTTPFound(f"/t/{request.match_info['tid']}/webapp/")

    if ctx.get("studio"):
        ctx["studio"].add_routes(app)
    app.router.add_get("/", index)
    app.router.add_get("/webapp", index)
    app.router.add_get("/webapp/waip-shop-logo.webp", custom_logo)
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
    app.router.add_post("/api/daily", daily)
    app.router.add_post("/api/ref_claim", ref_claim)
    app.router.add_get("/health", health)
    app.router.add_get("/api/tenant/mine", tenant_mine)
    app.router.add_post("/api/tenant/validate", tenant_validate)
    app.router.add_post("/api/tenant/create", tenant_create)
    app.router.add_get("/api/tenant/job", tenant_job)
    app.router.add_post("/internal/sale", internal_sale)
    app.router.add_get("/t/{tid}/webapp", tenant_redirect)
    app.router.add_route("*", "/t/{tid}/{tail:.*}", tenant_proxy)
    return app


async def start_web_server(ctx: dict, host: str = "0.0.0.0", port: int = 8080):
    app = create_app(ctx)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host or "0.0.0.0", int(port or 8080))
    await site.start()
    logging.info("WebApp listening on %s:%s", host, port)
    return runner
