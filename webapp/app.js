(() => {
  const tg = window.Telegram?.WebApp;
  // Optional custom accent: /webapp/?theme=%23ff5500
  const themeCode = new URLSearchParams(location.search).get("theme") || "";
  if (/^#[0-9a-fA-F]{6}$/.test(themeCode)) {
    document.documentElement.style.setProperty("--purple", themeCode);
    document.documentElement.style.setProperty("--line", themeCode + "66");
    document.documentElement.style.setProperty("--glow", "0 0 24px " + themeCode + "55");
  }
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#0d0a16");
      tg.setBackgroundColor("#07060c");
    } catch (_) {}
  }

  const ICONS = {
    level: '<svg viewBox="0 0 24 24" class="ico"><path d="M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3z" fill="currentColor"/></svg>',
    admin: '<svg viewBox="0 0 24 24" class="ico"><path d="M12 2l8 3v6c0 5-3.5 9.5-8 11-4.5-1.5-8-6-8-11V5l8-3z" stroke="currentColor" stroke-width="2" fill="none"/><path d="M9 12l2 2 4-4" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
    skin: '<svg viewBox="0 0 24 24" class="ico"><circle cx="12" cy="8" r="3.5" stroke="currentColor" stroke-width="2" fill="none"/><path d="M6 20c1.5-4 4-6 6-6s4.5 2 6 6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>',
    money: '<svg viewBox="0 0 24 24" class="ico"><rect x="3" y="6" width="18" height="12" rx="2" stroke="currentColor" stroke-width="2" fill="none"/><path d="M12 9v6M9.5 12h5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    password: '<svg viewBox="0 0 24 24" class="ico"><rect x="5" y="11" width="14" height="10" rx="2" stroke="currentColor" stroke-width="2" fill="none"/><path d="M8 11V8a4 4 0 018 0v3" stroke="currentColor" stroke-width="2" fill="none"/></svg>',
    box: '<svg viewBox="0 0 24 24" class="ico"><path d="M4 8l8-4 8 4v8l-8 4-8-4V8z" stroke="currentColor" stroke-width="2" fill="none"/><path d="M4 8l8 4 8-4M12 12v8" stroke="currentColor" stroke-width="2" fill="none"/></svg>',
  };

  const state = {
    initData: tg?.initData || "",
    catalog: {},
    category: null,
    product: null,
    customMoney: null,
    me: null,
    promo: null,
    starsUser: "",
    moneyPerStar: 2000,
    moneyMinUnits: 100000,
    canTenant: false,
    ref: null,
    daily: null,
    channelUrl: null,
    apkUrl: null,
    pendingOrderId: null,
    pollTimer: null,
  };

  const $ = (id) => document.getElementById(id);
  // Путь API: /webapp/ или /t/<id>/webapp/ (дочерний магазин через мастер)
  const BASE = (location.pathname.match(/^(.*?)\/webapp(\/|$)/) || ["", ""])[1];
  const showEl = (el) => el && el.classList.remove("hidden");
  const hideEl = (el) => el && el.classList.add("hidden");
  const show = (el) => el && el.classList.remove("hidden");
  const hide = (el) => el && el.classList.add("hidden");

  function toast(msg) {
    const el = $("toast");
    if (!el) return;
    el.textContent = msg;
    show(el);
    clearTimeout(toast._t);
    toast._t = setTimeout(() => hide(el), 2800);
  }

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtNum(n) {
    return Number(n).toLocaleString("ru-RU");
  }

  function starsOf(v) {
    return Math.max(1, Math.ceil(Number(v)));
  }

  function priceLine(v) {
    return `${fmtNum(starsOf(v))} Stars`;
  }

  async function api(path, opts = {}) {
    if (!state.initData) {
      throw new Error("Нет данных Telegram. Открой магазин через кнопку Web App в боте, не через обычную ссылку.");
    }
    const headers = Object.assign(
      { "Content-Type": "application/json", "X-Telegram-Init-Data": state.initData },
      opts.headers || {}
    );
    const res = await fetch(BASE + path, { ...opts, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "Ошибка");
    return data;
  }

  /* ── Onboarding ── */
  async function runOnboarding() {
    if (!state.initData) {
      // Открыто обычной ссылкой в браузере — данных Telegram нет
      show($("onboard"));
      hide($("shop"));
      hide($("obLoader"));
      hide($("obActions"));
      hide($("obNick"));
      $("obStatus").innerHTML = "Открой магазин кнопкой <b>WAIP SHOP</b><br>внутри бота в Telegram";
      return;
    }
    show($("onboard"));
    hide($("shop"));
    $("obStatus").textContent = "Проверяем доступ…";
    show($("obLoader"));
    hide($("obActions"));
    hide($("obNick"));

    try {
      if (!state.initData) {
        throw new Error("Приложение открыто вне Telegram. Закрой страницу и открой WAIP SHOP кнопкой «ОТКРЫТЬ МАГАЗИН» в боте.");
      }
      const data = await api("/api/bootstrap");
      applyBootstrap(data);

      if (data.me?.registered && data.me?.nickname) {
        $("obStatus").textContent = "Добро пожаловать!";
        hide($("obLoader"));
        setTimeout(openShop, 450);
        return;
      }

      if (!data.subscribed) {
        $("obStatus").innerHTML = "Подпишись на канал,<br>чтобы открыть магазин";
        hide($("obLoader"));
        if (data.channel_url) {
          $("subLink").href = data.channel_url;
          show($("subLink"));
        } else {
          hide($("subLink"));
        }
        show($("obActions"));
        return;
      }

      showNickStep();
    } catch (e) {
      hide($("obLoader"));
      $("obStatus").textContent = e.message || "Ошибка загрузки";
      show($("obActions"));
    }
  }

  function applyBootstrap(data) {
    state.catalog = data.catalog || {};
    state.me = data.me;
    state.starsUser = data.stars_user || "";
    state.moneyPerStar = data.money_per_star || 2000;
    state.moneyMinUnits = data.money_min_units || 100000;
    state.canTenant = !!data.can_create_tenant;
    state.canTenant ? showEl($("tenantCard")) : hideEl($("tenantCard"));
    state.ref = data.ref || null;
    state.daily = data.daily || null;
    state.channelUrl = data.channel_url;
    state.apkUrl = data.apk_url;
    $("payStarsHint").textContent = "Авто · оплата Telegram Stars";
    $("sgUser").textContent = "@" + state.starsUser;
    $("pfMoneyRate").textContent = `${fmtNum(state.moneyPerStar)} валюты = 1 Star`;
    $("moneyRate").textContent = `1 Star = ${fmtNum(state.moneyPerStar)} валюты`;
  }

  function openApk() {
    if (!state.apkUrl) {
      toast("Ссылка на APK не настроена");
      return;
    }
    if (tg?.openLink) tg.openLink(state.apkUrl);
    else window.open(state.apkUrl, "_blank");
  }

  function showNickStep() {
    $("obStatus").textContent = "Регистрация аккаунта";
    hide($("obLoader"));
    hide($("obActions"));
    show($("obNick"));
    $("nickInput").focus();
  }

  $("btnCheckSub").onclick = async () => {
    $("obStatus").textContent = "Проверяем подписку…";
    show($("obLoader"));
    hide($("obActions"));
    try {
      const data = await api("/api/check_sub", { method: "POST", body: "{}" });
      if (!data.subscribed) {
        hide($("obLoader"));
        $("obStatus").innerHTML = "Подписка не найдена.<br>Подпишись и нажми снова";
        show($("obActions"));
        toast("Подписка не найдена");
        return;
      }
      toast("Подписка подтверждена");
      showNickStep();
    } catch (e) {
      hide($("obLoader"));
      $("obStatus").textContent = e.message;
      show($("obActions"));
    }
  };

  $("btnReg").onclick = async () => {
    const nick = $("nickInput").value.trim();
    $("nickErr").textContent = "";
    if (!/^[A-Za-z0-9_\[\]-]{3,24}$/.test(nick)) {
      $("nickErr").textContent = "Ник 3–24: латиница, цифры, _ - []";
      return;
    }
    $("btnReg").disabled = true;
    $("obStatus").textContent = "Ищем аккаунт в базе…";
    show($("obLoader"));
    try {
      const data = await api("/api/register", {
        method: "POST",
        body: JSON.stringify({ nickname: nick, password: $("obPass").value }),
      });
      state.me = data.me || state.me;
      state.me.nickname = nick;
      state.me.registered = true;
      $("obPass").value = "";
      hide($("obLoader"));
      $("obStatus").textContent = "Аккаунт подтверждён";
      toast("Добро пожаловать, " + nick);
      setTimeout(openShop, 500);
    } catch (e) {
      hide($("obLoader"));
      $("obStatus").textContent = "Введи игровой ник";
      $("nickErr").textContent = e.message || "Аккаунт не найден";
    } finally {
      $("btnReg").disabled = false;
    }
  };

  function openShop() {
    hide($("onboard"));
    show($("shop"));
    $("userChip").textContent = state.me?.nickname || ("ID " + (state.me?.telegram_id || "?"));
    showView("cats");
    renderCats();
    renderProfile();
    try { tg?.HapticFeedback?.notificationOccurred("success"); } catch (_) {}
  }

  $("btnApk").onclick = () => openApk();

  /* ── Tabs ── */
  document.querySelectorAll(".tab").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
      document.querySelectorAll(".panel").forEach((p) => {
        p.classList.toggle("active", p.id === "tab-" + b.dataset.tab);
      });
      if (b.dataset.tab === "orders") loadOrders();
      if (b.dataset.tab === "profile") { renderProfile(); refreshProfileData(); }
      if (b.dataset.tab === "catalog") showView("cats");
    })
  );

  function showView(name) {
    ["view-cats", "view-products", "view-pay"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      if (id === "view-" + name) show(el);
      else hide(el);
    });
    hide($("starsGuide"));
    hide($("payWait"));
  }

  /* ── Categories ── */
  function renderCats() {
    const box = $("catList");
    box.innerHTML = "";
    const keys = Object.keys(state.catalog);
    if (!keys.length) {
      box.innerHTML = '<div class="empty">Каталог пуст</div>';
      return;
    }
    keys.forEach((k, i) => {
      const sec = state.catalog[k];
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "item-card";
      btn.style.animationDelay = i * 0.04 + "s";
      btn.innerHTML = `
        <div class="item-icon">${ICONS[sec.icon] || ICONS.box}</div>
        <div class="item-body">
          <strong>${escapeHtml(sec.title)}</strong>
          <small>${sec.custom ? "Своя сумма" : (sec.products?.length || 0) + " позиций"}</small>
        </div>
        <svg viewBox="0 0 24 24" class="ico" style="opacity:.5"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>`;
      btn.onclick = () => openCategory(k);
      box.appendChild(btn);
    });
  }

  function openCategory(key) {
    state.category = key;
    state.product = null;
    state.customMoney = null;
    const sec = state.catalog[key];
    $("prodTitle").textContent = sec?.title || key;
    renderProducts();
    showView("products");
  }

  $("backToCats").onclick = () => {
    showView("cats");
    renderCats();
  };

  function renderProducts() {
    const box = $("prodList");
    const moneyBox = $("moneyCustom");
    box.innerHTML = "";
    const sec = state.catalog[state.category];
    if (!sec) return;

    if (sec.custom || state.category === "money") {
      show(moneyBox);
      updateMoneyPreview();
    } else {
      hide(moneyBox);
    }

    (sec.products || []).forEach((p, i) => {
      const el = document.createElement("button");
      el.type = "button";
      el.className = "item-card";
      el.style.animationDelay = i * 0.04 + "s";
      el.innerHTML = `
        <div class="item-icon">${ICONS[p.kind] || ICONS.box}</div>
        <div class="item-body">
          <strong>${escapeHtml(p.title)}</strong>
          <small>${escapeHtml(p.kind)}</small>
        </div>
        <div class="item-price">
          <span>${fmtNum(starsOf(p.price))} Stars</span>
        </div>`;
      el.onclick = () => openPay({
        id: p.id,
        title: p.title,
        kind: p.kind,
        units: p.units,
        price: p.price,
      });
      box.appendChild(el);
    });
  }

  /* ── Custom money: вводишь количество валюты, цена считается сама ── */
  function moneyUnits() {
    return Math.floor(Number(String($("moneyUnits").value).replace(/\s/g, "")) || 0);
  }
  function moneyStars(units) {
    return Math.max(1, Math.ceil(units / state.moneyPerStar));
  }
  function updateMoneyPreview() {
    const units = moneyUnits();
    const ok = units >= state.moneyMinUnits;
    $("moneyPreview").textContent = ok
      ? `${fmtNum(units)} валюты = ${fmtNum(moneyStars(units))} Stars`
      : `Минимум ${fmtNum(state.moneyMinUnits)} валюты`;
    $("btnMoneyPay").disabled = !ok;
  }
  $("moneyUnits").min = state.moneyMinUnits;
  $("moneyUnits").addEventListener("input", updateMoneyPreview); // не трогаем ввод — можно печатать любое число
  $("btnMoneyPay").onclick = () => {
    const units = moneyUnits();
    if (units < state.moneyMinUnits) {
      toast("Минимум " + fmtNum(state.moneyMinUnits));
      return;
    }
    openPay({
      id: "money_custom",
      title: `${fmtNum(units)} игровой валюты`,
      kind: "money",
      units,
      price: moneyStars(units),
      custom: true,
    });
  };

  /* ── Payment view ── */
  function openPay(product) {
    state.product = product;
    state.customMoney = product.custom ? product : null;
    $("payItemTitle").textContent = product.title;
    $("payItemPrice").textContent = priceLine(product.price);
    hide($("starsGuide"));
    hide($("payWait"));
    showView("pay");
  }

  $("backToProds").onclick = () => {
    stopPoll();
    showView("products");
    renderProducts();
  };

  async function createOrder(method) {
    if (!state.product) return;
    const body = {
      payment_method: method,
      promo: state.promo?.code || null,
    };
    if (state.product.custom || state.product.id === "money_custom") {
      body.product_id = "money_custom";
      body.units = state.product.units; // цену сервер считает сам
    } else {
      body.product_id = state.product.id;
    }

    try {
      const data = await api("/api/order", {
        method: "POST",
        body: JSON.stringify(body),
      });
      state.pendingOrderId = data.order_id;
      try { tg?.HapticFeedback?.impactOccurred("medium"); } catch (_) {}

      // Stars
      if (data.invoice_link && tg?.openInvoice) {
        showPayWait("Открываем оплату Stars…");
        tg.openInvoice(data.invoice_link, (status) => {
          if (status === "paid") {
            toast("Оплата прошла");
            showPayWait("Выдаём товар…");
            startPoll(true);
          } else if (status === "cancelled") {
            hide($("payWait"));
            toast("Оплата отменена");
          } else {
            showPayWait("Ожидаем подтверждение…");
            startPoll();
          }
        });
        return;
      }

      // Fallback: gift instructions
      show($("starsGuide"));
      hide($("btnStarsPay"));
      show($("sgGift"));
      hide($("sgNative"));
      $("sgStars").textContent = fmtNum(data.stars || starsOf(state.product.price));
      $("sgUser").textContent = "@" + state.starsUser;
    } catch (e) {
      toast(e.message || "Ошибка заказа");
    }
  }

  function showPayWait(text) {
    show($("payWait"));
    hide($("starsGuide"));
    $("payWaitText").textContent = text || "Ожидаем оплату…";
  }

  function startPoll(fast) {
    stopPoll();
    const tick = async () => {
      if (!state.pendingOrderId) return;
      try {
        const data = await api("/api/order_status?id=" + state.pendingOrderId);
        if (data.status === "delivered" || data.status === "done") {
          stopPoll();
          toast("Товар выдан");
          showPayWait("Готово! Товар выдан на аккаунт.");
          setTimeout(() => {
            document.querySelector('.tab[data-tab="orders"]').click();
            loadOrders();
          }, 900);
          return;
        }
        if (data.status === "cancelled" || data.status === "failed" || data.status === "rejected") {
          stopPoll();
          hide($("payWait"));
          toast(data.status === "rejected" ? "Платёж не подтверждён" : "Заказ отменён");
        }
      } catch (_) {}
    };
    tick();
    state.pollTimer = setInterval(tick, fast ? 1500 : 4000);
  }

  function stopPoll() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  $("payStars").onclick = () => createOrder("stars");

  $("btnStarsPay").onclick = () => createOrder("stars");
  $("btnStarsOpen").onclick = () => {
    const u = "https://t.me/" + state.starsUser;
    if (tg?.openTelegramLink) tg.openTelegramLink(u);
    else if (tg?.openLink) tg.openLink(u);
    else window.open(u, "_blank");
  };
  $("btnStarsDone").onclick = async () => {
    if (!state.pendingOrderId) return;
    try {
      await api("/api/stars_confirm", {
        method: "POST",
        body: JSON.stringify({ order_id: state.pendingOrderId }),
      });
      showPayWait("Проверяем и выдаём…");
      startPoll(true);
      toast("Заявка отправлена");
    } catch (e) {
      toast(e.message || "Ошибка");
    }
  };

  /* ── Orders ── */
  async function loadOrders() {
    const box = $("ordersList");
    box.innerHTML = '<div class="empty">Загрузка…</div>';
    try {
      const data = await api("/api/orders");
      if (!data.orders?.length) {
        box.innerHTML = '<div class="empty">Заказов пока нет</div>';
        return;
      }
      box.innerHTML = "";
      data.orders.forEach((o, i) => {
        const el = document.createElement("div");
        el.className = "order-card";
        el.style.animationDelay = i * 0.03 + "s";
        const st = statusClass(o.status);
        el.innerHTML = `
          <div class="row">
            <strong>#${o.id} · ${escapeHtml(o.item_title)}</strong>
            <span class="st ${st.cls}">${st.label}</span>
          </div>
          <div class="order-meta">${fmtNum(o.price)} Stars · ${escapeHtml(o.payment_method || "")} · ${escapeHtml(o.created_at || "")}</div>`;
        box.appendChild(el);
      });
    } catch (e) {
      box.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`;
    }
  }

  function statusClass(s) {
    if (s === "delivered" || s === "done") return { cls: "ok", label: "Выдано" };
    if (s === "cancelled" || s === "failed") return { cls: "err", label: "Отмена" };
    if (String(s).startsWith("awaiting")) return { cls: "wait", label: "Ожидание" };
    if (s === "processing" || s === "review") return { cls: "wait", label: "Обработка" };
    return { cls: "", label: s || "?" };
  }

  /* ── Profile ── */
  function renderProfile() {
    const nick = state.me?.nickname;
    $("pfNick").textContent = nick || "—";
    const p = state.me?.player;
    $("pfLevel").textContent = p?.level ?? "—";
    $("pfAdmin").textContent = p?.admin ?? "—";
    $("pfSkin").textContent = p?.skin ?? "—";
    $("pfMoney").textContent = p?.money != null ? fmtNum(p.money) : "—";

    renderRefDaily();
    if (nick) {
      show($("btnLogout"));
      hide($("switchBox"));
      $("switchHint").textContent = "Можно выйти и привязать другой ник. Подписка не требуется.";
    } else {
      hide($("btnLogout"));
      show($("switchBox"));
      $("switchHint").textContent = "Введи игровой ник, чтобы войти. Подписка не нужна.";
    }
  }

  function renderRefDaily() {
    const r = state.ref;
    if (r) {
      $("refPct").textContent = "+" + r.percent + "%";
      $("refCount").textContent = r.refs;
      $("refBonus").textContent = r.bonus + "%";
      $("refBar").style.width = Math.min(100, r.bonus) + "%";
      $("refLink").value = r.link || "";
      if (r.bonus >= 100) {
        const sel = $("refFreeSel");
        sel.innerHTML = "";
        Object.values(state.catalog).forEach((sec) =>
          (sec.products || []).forEach((p) => {
            const o = document.createElement("option");
            o.value = p.id;
            o.textContent = p.title;
            sel.appendChild(o);
          })
        );
        show($("refFree"));
      } else {
        hide($("refFree"));
      }
    }
    const d = state.daily;
    if (d) {
      const left = d.left || 0;
      $("btnDaily").disabled = left > 0;
      $("btnDaily").textContent = left > 0 ? "Следующий бонус через " + fmtLeft(left) : "Забрать +" + fmtNum(d.amount);
    }
  }
  function fmtLeft(s) {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h ? `${h} ч ${m} мин` : `${Math.max(1, m)} мин`;
  }
  async function refreshProfileData() {
    try {
      const data = await api("/api/bootstrap");
      state.ref = data.ref || state.ref;
      state.daily = data.daily || state.daily;
      state.me = data.me || state.me;
      renderProfile();
    } catch (_) {}
  }
  $("btnDaily").onclick = async () => {
    $("btnDaily").disabled = true;
    try {
      const data = await api("/api/daily", { method: "POST", body: "{}" });
      state.daily = { amount: data.amount, left: data.left };
      toast("+" + fmtNum(data.amount) + " валюты!");
      try { tg?.HapticFeedback?.notificationOccurred("success"); } catch (_) {}
      refreshProfileData();
    } catch (e) {
      toast(e.message || "Ошибка");
      refreshProfileData();
    }
  };
  $("btnCopyRef").onclick = async () => {
    const link = $("refLink").value;
    try { await navigator.clipboard.writeText(link); }
    catch (_) { $("refLink").select(); document.execCommand?.("copy"); }
    toast("Ссылка скопирована");
  };
  $("btnShareRef").onclick = () => {
    const link = $("refLink").value;
    const u = "https://t.me/share/url?url=" + encodeURIComponent(link) +
      "&text=" + encodeURIComponent("Заходи в WAIP SHOP!");
    if (tg?.openTelegramLink) tg.openTelegramLink(u); else window.open(u, "_blank");
  };
  $("btnRefFree").onclick = async () => {
    $("btnRefFree").disabled = true;
    try {
      await api("/api/ref_claim", { method: "POST", body: JSON.stringify({ product_id: $("refFreeSel").value }) });
      toast("Бесплатный товар выдаётся!");
      refreshProfileData();
    } catch (e) {
      toast(e.message || "Ошибка");
    } finally {
      $("btnRefFree").disabled = false;
    }
  };

  $("btnLogout").onclick = async () => {
    $("btnLogout").disabled = true;
    try {
      await api("/api/logout", { method: "POST", body: "{}" });
      state.me = Object.assign({}, state.me, {
        nickname: null,
        registered: false,
        player: null,
      });
      $("userChip").textContent = "ID " + (state.me?.telegram_id || "?");
      toast("Вышел из аккаунта");
      renderProfile();
      $("switchNick").focus();
    } catch (e) {
      toast(e.message || "Ошибка выхода");
    } finally {
      $("btnLogout").disabled = false;
    }
  };

  $("btnSwitchIn").onclick = async () => {
    const nick = $("switchNick").value.trim();
    $("switchErr").textContent = "";
    if (!/^[A-Za-z0-9_\[\]-]{3,24}$/.test(nick)) {
      $("switchErr").textContent = "Ник 3–24: латиница, цифры, _ - []";
      return;
    }
    $("btnSwitchIn").disabled = true;
    try {
      const data = await api("/api/register", {
        method: "POST",
        body: JSON.stringify({ nickname: nick, switch: true, password: $("switchPass").value }),
      });
      state.me = data.me || state.me;
      state.me.nickname = nick;
      state.me.registered = true;
      $("userChip").textContent = nick;
      $("switchNick").value = "";
      $("switchPass").value = "";
      toast("Вошёл как " + nick);
      renderProfile();
      try { tg?.HapticFeedback?.notificationOccurred("success"); } catch (_) {}
    } catch (e) {
      $("switchErr").textContent = e.message || "Аккаунт не найден";
    } finally {
      $("btnSwitchIn").disabled = false;
    }
  };

  /* ── Свой бот: мастер-создание дочернего магазина ── */
  const T = { agreed: false, validated: false, logo: null, jobTimer: null };
  const RING_LEN = 2 * Math.PI * 52;

  function tenantPayload() {
    return {
      title: $("tTitle").value.trim(),
      theme: $("tColor").value,
      token: $("tToken").value.trim(),
      admin_id: $("tAdmin").value.trim(),
      db_host: $("tDbHost").value.trim(),
      db_port: $("tDbPort").value.trim() || "3306",
      db_user: $("tDbUser").value.trim(),
      db_password: $("tDbPass").value,
      db_name: $("tDbName").value.trim(),
      hash_method: $("tHash").value,
    };
  }

  function tenantSteps(step) {
    hideEl($("tStepAgree")); hideEl($("tStepForm")); hideEl($("tStepProgress"));
    hideEl($("tMyList"));
    if (step === "agree") showEl($("tStepAgree"));
    if (step === "form") { showEl($("tStepForm")); showEl($("tMyList")); }
    if (step === "progress") showEl($("tStepProgress"));
  }

  function setRing(p) {
    $("ringFg").style.strokeDasharray = RING_LEN;
    $("ringFg").style.strokeDashoffset = RING_LEN * (1 - Math.min(100, Math.max(0, p)) / 100);
    $("ringPct").textContent = Math.round(p) + "%";
  }

  function renderMyTenants(list) {
    const box = $("tMyList");
    box.innerHTML = "";
    if (!list.length) return;
    const title = document.createElement("p");
    title.className = "card-label";
    title.textContent = "Мои боты";
    box.appendChild(title);
    list.forEach((t) => {
      const card = document.createElement("div");
      card.className = "card";
      const statusText = { active: "работает", suspended: "приостановлен (не оплачена комиссия)", stopped: "остановлен" }[t.status] || t.status;
      card.innerHTML = `<b></b><p class="muted"></p>`;
      card.querySelector("b").textContent = `${t.title} · @${t.bot_username}`;
      card.querySelector("p").textContent = statusText;
      box.appendChild(card);
    });
  }

  async function openTenant() {
    hideEl($("shop"));
    showEl($("tenantScreen"));
    window.scrollTo(0, 0);
    if (!T.agreed) { tenantSteps("agree"); return; }
    tenantSteps("form");
    try {
      const data = await api("/api/tenant/mine");
      renderMyTenants(data.tenants || []);
    } catch (_) { /* список не критичен */ }
  }

  function closeTenant() {
    clearInterval(T.jobTimer);
    $("tDbPass").value = "";
    showEl($("shop"));
    hideEl($("tenantScreen"));
  }

  $("btnTenant").onclick = openTenant;
  $("tenantBack").onclick = closeTenant;

  $("btnAgree").onclick = () => {
    if (!$("tAgreeBox").checked) {
      toast("Отметь галочку «Я согласен»");
      return;
    }
    T.agreed = true;
    tenantSteps("form");
    api("/api/tenant/mine").then((d) => renderMyTenants(d.tenants || [])).catch(() => {});
  };

  tenantFieldIds().forEach((id) => {
    $(id).addEventListener("input", () => {
      T.validated = false;
      hideEl($("btnTenantCreate"));
    });
  });
  function tenantFieldIds() {
    return ["tTitle", "tColor", "tToken", "tAdmin", "tDbHost", "tDbPort", "tDbUser", "tDbPass", "tDbName", "tHash"];
  }

  $("tLogo").addEventListener("change", () => {
    const file = $("tLogo").files[0];
    T.logo = null;
    $("tLogoName").textContent = "не выбран — будет стандартный";
    if (!file) return;
    if (!/^image\/(png|jpeg|webp)$/.test(file.type)) { toast("Нужен PNG, JPG или WEBP"); $("tLogo").value = ""; return; }
    if (file.size > 1500000) { toast("Логотип больше 1.5 МБ"); $("tLogo").value = ""; return; }
    const reader = new FileReader();
    reader.onload = () => { T.logo = reader.result; $("tLogoName").textContent = file.name; };
    reader.readAsDataURL(file);
  });

  function renderChecks(checks) {
    const box = $("tCheck");
    box.innerHTML = "";
    checks.forEach((c) => {
      const row = document.createElement("div");
      row.className = "profile-row";
      const left = document.createElement("span");
      left.textContent = (c.ok ? "✅ " : "❌ ") + c.name;
      const right = document.createElement("b");
      right.textContent = c.msg;
      row.appendChild(left);
      row.appendChild(right);
      box.appendChild(row);
    });
    showEl(box);
  }

  $("btnTenantValidate").onclick = async () => {
    $("tErr").textContent = "";
    $("btnTenantValidate").disabled = true;
    try {
      const data = await api("/api/tenant/validate", { method: "POST", body: JSON.stringify(tenantPayload()) });
      renderChecks(data.checks || []);
      T.validated = !!data.ok;
      data.ok ? showEl($("btnTenantCreate")) : hideEl($("btnTenantCreate"));
      if (!data.ok) $("tErr").textContent = "Исправь ошибки выше и проверь ещё раз.";
    } catch (e) {
      $("tErr").textContent = e.message || "Ошибка проверки";
    } finally {
      $("btnTenantValidate").disabled = false;
    }
  };

  $("btnTenantCreate").onclick = async () => {
    if (!T.validated) return;
    const body = { ...tenantPayload(), agree: true, logo: T.logo };
    $("tDbPass").value = "";  // пароль не остаётся в браузере
    hideEl($("btnTenantCreate")); hideEl($("btnTenantValidate")); hideEl($("tCheck"));
    tenantSteps("progress");
    $("tResult").innerHTML = ""; hideEl($("tResult")); hideEl($("btnTenantRetry"));
    $("tLogs").textContent = "";
    setRing(0);
    $("ringTitle").textContent = "Создаём магазин…";
    try {
      const data = await api("/api/tenant/create", { method: "POST", body: JSON.stringify(body) });
      pollTenantJob(data.job_id);
    } catch (e) {
      failTenantJob(e.message || "Не удалось начать создание");
    }
  };

  function failTenantJob(message) {
    clearInterval(T.jobTimer);
    $("ringTitle").textContent = "Не получилось";
    $("tLogs").textContent += `\nОшибка: ${message}`;
    showEl($("btnTenantRetry"));
    $("btnTenantRetry").onclick = () => {
      tenantSteps("form");
      showEl($("btnTenantValidate"));
    };
  }

  function pollTenantJob(jobId) {
    clearInterval(T.jobTimer);
    T.jobTimer = setInterval(async () => {
      try {
        const job = await api("/api/tenant/job?id=" + encodeURIComponent(jobId));
        setRing(job.percent || 0);
        $("tLogs").textContent = (job.logs || []).join("\n");
        $("tLogs").scrollTop = $("tLogs").scrollHeight;
        if (job.status === "done") {
          clearInterval(T.jobTimer);
          $("ringTitle").textContent = "Магазин создан";
          const r = job.result || {};
          $("tResult").innerHTML = "";
          const p = document.createElement("p");
          p.textContent = `Бот @${r.bot_username || ""} работает. Открой его и нажми меню → WAIP SHOP.`;
          const btn = document.createElement("button");
          btn.className = "btn btn-primary btn-block";
          btn.type = "button";
          btn.textContent = "Открыть бота";
          btn.onclick = () => (tg?.openTelegramLink ? tg.openTelegramLink(r.bot_url) : window.open(r.bot_url, "_blank"));
          $("tResult").appendChild(p);
          $("tResult").appendChild(btn);
          showEl($("tResult"));
          T.validated = false;
          renderMyTenants([]);
          api("/api/tenant/mine").then((d) => renderMyTenants(d.tenants || [])).catch(() => {});
        } else if (job.status === "failed") {
          failTenantJob(job.error || "Ошибка");
        }
      } catch (e) {
        // временная ошибка сети — продолжаем опрос
      }
    }, 900);
  }

  /* ── Start ── */
  runOnboarding();
})();
