(() => {
  const tg = window.Telegram?.WebApp;
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
    cryptoEnabled: false,
    starsUser: "mukhammad_im",
    starsPerUsd: 50,
    moneyPerUsd: 100000,
    moneyMinUsd: 1,
    channelUrl: null,
    apkUrl: null,
    pendingOrderId: null,
    pollTimer: null,
  };

  const $ = (id) => document.getElementById(id);
  const show = (el) => el && el.classList.remove("hidden");
  const hide = (el) => el && el.classList.add("hidden");

  function toast(msg) {
    const el = $("toast");
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

  function starsOf(usd) {
    return Math.max(1, Math.round(Number(usd) * state.starsPerUsd));
  }

  function priceLine(usd) {
    const s = starsOf(usd);
    return `$${Number(usd).toFixed(Number(usd) % 1 ? 2 : 0)} · ${fmtNum(s)} Stars`;
  }

  async function api(path, opts = {}) {
    const headers = Object.assign(
      { "Content-Type": "application/json", "X-Telegram-Init-Data": state.initData },
      opts.headers || {}
    );
    const res = await fetch(path, { ...opts, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "Ошибка");
    return data;
  }

  /* ── Onboarding ── */
  async function runOnboarding() {
    show($("onboard"));
    hide($("shop"));
    $("obStatus").textContent = "Проверяем доступ…";
    show($("obLoader"));
    hide($("obActions"));
    hide($("obNick"));

    try {
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
    state.cryptoEnabled = !!data.crypto_enabled;
    state.starsUser = data.stars_user || "mukhammad_im";
    state.starsPerUsd = data.stars_per_usd || 50;
    state.moneyPerUsd = data.money_per_usd || 100000;
    state.moneyMinUsd = data.money_min_usd || 1;
    state.channelUrl = data.channel_url;
    state.apkUrl = data.apk_url;
    $("payStarsHint").textContent = `Авто · ${state.starsPerUsd} Stars = 1 USD`;
    $("sgUser").textContent = "@" + state.starsUser;
    $("pfStarsRate").textContent = `${state.starsPerUsd} Stars`;
    $("pfMoneyRate").textContent = `${fmtNum(state.moneyPerUsd)} валюты`;
    $("moneyRate").textContent =
      `1 USD = ${fmtNum(state.moneyPerUsd)} валюты · 1 USD = ${state.starsPerUsd} Stars`;
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
        body: JSON.stringify({ nickname: nick }),
      });
      state.me = data.me || state.me;
      state.me.nickname = nick;
      state.me.registered = true;
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
      if (b.dataset.tab === "profile") renderProfile();
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
          $${Number(p.price).toFixed(Number(p.price) % 1 ? 2 : 0)}
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

  /* ── Custom money ── */
  function syncMoneyFromUsd() {
    const usd = Math.max(state.moneyMinUsd, Number($("moneyUsd").value) || state.moneyMinUsd);
    $("moneyUsd").value = usd;
    $("moneyUnits").value = Math.round(usd * state.moneyPerUsd);
    updateMoneyPreview();
  }
  function syncMoneyFromUnits() {
    let units = Math.max(state.moneyPerUsd * state.moneyMinUsd, Number($("moneyUnits").value) || 0);
    units = Math.round(units / 1000) * 1000;
    const usd = Math.max(state.moneyMinUsd, units / state.moneyPerUsd);
    $("moneyUnits").value = Math.round(usd * state.moneyPerUsd);
    $("moneyUsd").value = Math.round(usd * 100) / 100;
    updateMoneyPreview();
  }
  function updateMoneyPreview() {
    const usd = Math.max(state.moneyMinUsd, Number($("moneyUsd").value) || state.moneyMinUsd);
    const units = Math.round(usd * state.moneyPerUsd);
    $("moneyPreview").textContent =
      `${fmtNum(units)} валюты = $${usd} = ${fmtNum(starsOf(usd))} Stars`;
  }
  $("moneyUsd").addEventListener("input", syncMoneyFromUsd);
  $("moneyUnits").addEventListener("input", syncMoneyFromUnits);
  $("btnMoneyPay").onclick = () => {
    const usd = Math.max(state.moneyMinUsd, Number($("moneyUsd").value) || state.moneyMinUsd);
    const units = Math.round(usd * state.moneyPerUsd);
    openPay({
      id: "money_custom",
      title: `${fmtNum(units)} игровой валюты`,
      kind: "money",
      units,
      price: usd,
      custom: true,
    });
  };

  /* ── Payment view ── */
  function openPay(product) {
    state.product = product;
    state.customMoney = product.custom ? product : null;
    $("payItemTitle").textContent = product.title;
    $("payItemPrice").textContent = priceLine(product.price);
    $("payCrypto").style.display = state.cryptoEnabled ? "flex" : "none";
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
      body.units = state.product.units;
      body.usd = state.product.price;
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

      if (method === "crypto") {
        if (data.pay_url) {
          if (tg?.openLink) tg.openLink(data.pay_url);
          else window.open(data.pay_url, "_blank");
        }
        showPayWait("Ожидаем оплату CryptoBot…");
        startPoll();
        return;
      }

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
        if (data.status === "cancelled" || data.status === "failed") {
          stopPoll();
          hide($("payWait"));
          toast("Заказ отменён");
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

  $("payCrypto").onclick = () => createOrder("crypto");
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
        body: JSON.stringify({ nickname: nick, switch: true }),
      });
      state.me = data.me || state.me;
      state.me.nickname = nick;
      state.me.registered = true;
      $("userChip").textContent = nick;
      $("switchNick").value = "";
      toast("Вошёл как " + nick);
      renderProfile();
      try { tg?.HapticFeedback?.notificationOccurred("success"); } catch (_) {}
    } catch (e) {
      $("switchErr").textContent = e.message || "Аккаунт не найден";
    } finally {
      $("btnSwitchIn").disabled = false;
    }
  };

  /* ── Start ── */
  runOnboarding();
})();
