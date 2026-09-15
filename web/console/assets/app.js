/**
 * 作战台 console (PROH-126/128/129).
 * Never posts trading_enabled / live enable.
 * Param saves only bind paper|staging.
 * Market + review are read-only.
 */
(function () {
  const PASS_KEY = "console_password";
  const AUTO_MS = 60 * 60 * 1000;

  const els = {
    gate: document.getElementById("gate"),
    gatePassword: document.getElementById("gate-password"),
    gateSubmit: document.getElementById("gate-submit"),
    gateError: document.getElementById("gate-error"),
    app: document.getElementById("app"),
    status: document.getElementById("status-line"),
    overview: document.getElementById("page-overview"),
    strategy: document.getElementById("page-strategy"),
    market: document.getElementById("page-market"),
    review: document.getElementById("page-review"),
    placeholder: document.getElementById("page-placeholder"),
    placeholderText: document.getElementById("placeholder-text"),
    paramForm: document.getElementById("param-form"),
    paramMsg: document.getElementById("param-msg"),
    reviewDate: document.getElementById("review-date"),
  };

  let password = sessionStorage.getItem(PASS_KEY) || "";
  let autoTimer = null;
  let currentPage = "overview";
  let selectedMarket = sessionStorage.getItem("console_market") || "US";
  let lastStrategy = null;
  let selectedReviewDate = null;

  function marketQuery() {
    const m = selectedMarket === "HK" ? "HK" : "US";
    return "market=" + encodeURIComponent(m);
  }

  function withMarket(path) {
    const sep = path.indexOf("?") >= 0 ? "&" : "?";
    return path + sep + marketQuery();
  }

  function headers(json) {
    const h = { Accept: "application/json" };
    if (password) h["X-Console-Password"] = password;
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  async function apiGet(path) {
    const res = await fetch(path, { headers: headers(), cache: "no-store" });
    if (res.status === 401) {
      const err = new Error("unauthorized");
      err.code = 401;
      throw err;
    }
    if (!res.ok) {
      const body = await res.text();
      throw new Error(body || `HTTP ${res.status}`);
    }
    return res.json();
  }

  async function apiPost(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: headers(true),
      body: JSON.stringify(body || {}),
      cache: "no-store",
    });
    if (res.status === 401) {
      const err = new Error("unauthorized");
      err.code = 401;
      throw err;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : JSON.stringify(data);
      throw new Error(detail || `HTTP ${res.status}`);
    }
    return data;
  }

  function showGate(msg) {
    els.app.classList.add("hidden");
    els.gate.classList.remove("hidden");
    if (msg) {
      els.gateError.textContent = msg;
      els.gateError.classList.remove("hidden");
    } else {
      els.gateError.classList.add("hidden");
    }
  }

  function showApp() {
    els.gate.classList.add("hidden");
    els.app.classList.remove("hidden");
  }

  function setText(id, text) {
    const node = document.getElementById(id);
    if (node) node.textContent = text == null ? "" : String(text);
  }

  function moneyShort(v) {
    if (v == null || typeof v !== "number" || Number.isNaN(v)) return "—";
    return "$" + v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }

  function renderMarketsBoard(board) {
    if (!board) return;
    setText("markets-headline", board.headline_zh || "双市场");
    ["US", "HK"].forEach((mid) => {
      const row = board[mid] || {};
      const prefix = mid === "US" ? "us" : "hk";
      const phaseBits = [];
      if (row.running) phaseBits.push("在跑");
      else if (row.is_active) phaseBits.push("优先");
      phaseBits.push(row.phase_zh || "—");
      setText(prefix + "-phase", phaseBits.join(" · "));
      const cap = row.capital || {};
      setText(
        prefix + "-cap",
        "本金 " +
          moneyShort(cap.total) +
          " · A " +
          moneyShort(cap.lane_a) +
          " · B " +
          moneyShort(cap.lane_b)
      );
      const wl = row.watchlist || {};
      setText(prefix + "-wl", wl.summary_zh || "名单 —");
      const card = document.querySelector(
        '.market-card[data-m="' + mid + '"]'
      );
      if (card) {
        card.classList.toggle("active", !!row.is_active || !!row.running);
        card.classList.toggle("selected", selectedMarket === mid);
      }
    });
  }

  function renderOverview(data) {
    setText("env-pill", data.env_pill || "—");
    setText("as-of", data.data_as_of_zh || "数据截至 —");
    renderMarketsBoard(data.markets_board);

    const dual = document.getElementById("dual-pill");
    if (data.dual_run && data.dual_run.label) {
      dual.textContent =
        data.dual_run.verdict
          ? `双跑对照中 · ${data.dual_run.verdict}`
          : data.dual_run.label;
      dual.classList.remove("hidden");
    } else {
      dual.classList.add("hidden");
    }

    const demo = document.getElementById("demo-banner");
    if (data.demo_banner) {
      demo.textContent = data.demo_banner;
      demo.classList.remove("hidden");
    } else {
      demo.classList.add("hidden");
    }

    const alert = document.getElementById("alert-banner");
    const level = data.verdict && data.verdict.level;
    if (level === "urgent" && data.verdict.detail) {
      alert.textContent = data.verdict.detail;
      alert.classList.remove("hidden");
    } else {
      alert.classList.add("hidden");
    }

    const rth = data.rth_resident || {};
    const rthEl = document.getElementById("rth-resident");
    if (rthEl) {
      const tone =
        rth.tone === "safe" || rth.tone === "urgent" || rth.tone === "watch"
          ? rth.tone
          : "watch";
      rthEl.className = "rth-bar " + tone;
      setText("rth-kicker", rth.kicker_zh || "现在该不该有模拟单");
      setText("rth-label", rth.label_zh || "开市常驻：未知");
      setText("rth-detail", rth.detail_zh || "");
      setText("rth-meta", rth.meta_zh || "");
    }

    const verdict = document.getElementById("verdict");
    verdict.className = "verdict";
    if (level === "watch" || level === "urgent") {
      verdict.classList.add(level === "urgent" ? "urgent" : "watch");
    }
    setText("verdict-title", (data.verdict && data.verdict.title) || "—");
    setText("verdict-detail", (data.verdict && data.verdict.detail) || "");

    const kill = document.getElementById("kill");
    const ks = data.kill_switch || {};
    kill.className = "kill" + (ks.tone === "risk" ? " risk" : "");
    setText("kill-label", ks.label || "真下单总开关：—");
    setText("kill-hint", ks.hint || "只读 · 本页不能打开");
    if (kill.querySelector("input, select, [role='switch']")) {
      kill.querySelectorAll("input, select, [role='switch']").forEach((n) => n.remove());
    }

    const drill = data.drill || {};
    setText(
      "drill-num",
      `${drill.counting_streak ?? "—"} / ${drill.required_n ?? "—"} 天`
    );
    const bar = document.getElementById("drill-bar");
    if (bar) bar.style.width = `${drill.pct || 0}%`;
    setText("drill-meta", drill.caption || "—");

    const pnl = data.pnl || {};
    const pnlEl = document.getElementById("pnl-total");
    pnlEl.className = "pnl " + (pnl.tone || "flat");
    pnlEl.textContent = pnl.total_zh || "—";
    setText(
      "pnl-split",
      `A ${pnl.lane_a_zh || "—"} · B ${pnl.lane_b_zh || "—"}`
    );
    setText(
      "pnl-date",
      pnl.session_date ? `交易日 ${pnl.session_date}` : ""
    );

    const lanes = document.getElementById("lanes");
    lanes.innerHTML = "";
    (data.lanes || []).forEach((lane) => {
      const div = document.createElement("div");
      const tone = lane.tone === "halt" ? "halt" : lane.tone === "busy" ? "" : "idle";
      div.className = "lane" + (tone ? " " + tone : "");
      div.innerHTML =
        `<p class="label"></p><p class="value"></p><p class="meta"></p>`;
      div.querySelector(".label").textContent = lane.label || lane.id;
      div.querySelector(".value").textContent = lane.status_zh || "—";
      div.querySelector(".meta").textContent = lane.meta || "";
      lanes.appendChild(div);
    });

    const fresh = document.getElementById("fresh");
    const fr = data.freshness || {};
    fresh.className = "fresh";
    if (fr.tone === "watch" || fr.tone === "risk") {
      fresh.classList.add(fr.tone);
    }
    setText("fresh-label", fr.label_zh || "数据新鲜度 · —");

    const positions = (data.secondary && data.secondary.positions) || [];
    const posBox = document.getElementById("sec-positions");
    if (!positions.length) {
      posBox.innerHTML = "<p>今天暂无持仓明细。</p>";
    } else {
      posBox.innerHTML =
        "<ul>" +
        positions
          .map((p) => {
            const lane = p.lane || "?";
            const sym = p.symbol || "未标明";
            const side = p.side_zh || p.side || "";
            return `<li>路线 ${lane}：${sym}${side ? " · " + side : ""}</li>`;
          })
          .join("") +
        "</ul>";
    }

    const sec = data.secondary || {};
    setText(
      "sec-activity",
      `成交笔数 ${sec.fills_count ?? 0} · 停手 ${sec.halts ?? 0} 次`
    );
    const tech = {
      experiment_id: sec.experiment_id,
      reports_dir: sec.reports_dir,
      data_mode: fr.data_mode,
      hosting_mode: fr.hosting_mode,
      opend_mode: fr.opend_mode,
      sync_status: fr.sync_status,
      rth_resident: rth.code || null,
      dual_run: data.dual_run || null,
      kill_writable: !!(data.kill_switch && data.kill_switch.writable),
    };
    document.getElementById("sec-tech").textContent = JSON.stringify(tech, null, 2);
  }

  function fillParamForm(defaults) {
    if (!els.paramForm || !defaults) return;
    Object.keys(defaults).forEach((key) => {
      const input = els.paramForm.elements.namedItem(key);
      if (!input || typeof input.value === "undefined") return;
      const val = defaults[key];
      input.value = val == null ? "" : String(val);
    });
  }

  function renderStrategy(data) {
    lastStrategy = data;
    setText(
      "strat-session",
      data.session_date ? `交易日 ${data.session_date}` : "暂无交易日"
    );
    setText("strat-one-liner", data.one_liner_zh || "—");

    const list = document.getElementById("decision-list");
    const decisions = data.decisions || [];
    if (!decisions.length) {
      list.innerHTML = "<p class='empty'>今天还没有可解读的买卖决策。</p>";
    } else {
      list.innerHTML = "";
      decisions.forEach((d) => {
        const card = document.createElement("article");
        card.className = "decision" + (d.kind === "halt" ? " halt" : "");
        card.innerHTML =
          `<p class="lane-tag"></p>` +
          `<div class="triple">` +
          `<div><span class="step">规则</span><p class="rule"></p></div>` +
          `<div><span class="step">证据</span><p class="evidence"></p></div>` +
          `<div><span class="step">结论</span><p class="conclusion"></p></div>` +
          `</div>` +
          `<p class="detail"></p>`;
        card.querySelector(".lane-tag").textContent =
          `路线 ${d.lane || "?"} · ${d.kind || "决策"}` +
          (d.ts ? ` · ${d.ts}` : "");
        card.querySelector(".rule").textContent = d.rule_zh || "—";
        card.querySelector(".evidence").textContent = d.evidence_zh || "—";
        card.querySelector(".conclusion").textContent = d.conclusion_zh || "—";
        card.querySelector(".detail").textContent = d.rule_detail_zh || "";
        list.appendChild(card);
      });
    }

    const params = data.params || {};
    const summary = params.summary || {};
    setText(
      "param-source",
      params.source === "active_staging"
        ? `来源：练兵绑定版本 ${params.active_version_id || ""}` +
            (params.causal_summary ? ` · ${params.causal_summary}` : "")
        : params.causal_summary || "来源：portfolio 基线（尚未写入版本）"
    );
    const sumBox = document.getElementById("param-summary");
    const rows = summary.rows_zh || [];
    if (!rows.length) {
      sumBox.innerHTML = "<p class='empty'>暂无参数摘要。</p>";
    } else {
      sumBox.innerHTML =
        "<dl>" +
        rows.map(() => `<div><dt></dt><dd></dd></div>`).join("") +
        "</dl>";
      Array.from(sumBox.querySelectorAll("div")).forEach((row, i) => {
        row.querySelector("dt").textContent = rows[i].label;
        row.querySelector("dd").textContent = rows[i].value;
      });
    }

    if (params.disclaimer_zh) {
      setText("param-disclaimer", params.disclaimer_zh);
    }
    const saveBtn = document.getElementById("btn-save-params");
    if (saveBtn && params.save_button_zh) {
      saveBtn.textContent = params.save_button_zh;
    }
    fillParamForm(params.form_defaults || {});

    const versions = data.versions || [];
    const vBox = document.getElementById("version-list");
    if (!versions.length) {
      vBox.innerHTML = "<p class='empty'>队列为空。保存后会出现新版本。</p>";
    } else {
      vBox.innerHTML = "";
      versions.forEach((v) => {
        const row = document.createElement("div");
        row.className =
          "version-row" +
          (v.status === "active_staging" ? " active" : "");
        const left = document.createElement("div");
        left.innerHTML =
          `<p class="v-id"></p><p class="v-meta"></p><p class="v-note"></p>`;
        left.querySelector(".v-id").textContent =
          `${v.status || "draft"} · ${v.target_env || "staging"}`;
        left.querySelector(".v-meta").textContent =
          `${v.id} · ${v.created_at || ""} · ${v.created_by || ""}`;
        left.querySelector(".v-note").textContent =
          v.causal_summary || v.note || "";
        row.appendChild(left);
        if (v.status !== "active_staging") {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn";
          btn.textContent = "回滚到此版";
          btn.addEventListener("click", () => rollbackTo(v.id));
          row.appendChild(btn);
        } else {
          const tag = document.createElement("span");
          tag.className = "pill";
          tag.textContent = "当前练兵";
          row.appendChild(tag);
        }
        vBox.appendChild(row);
      });
    }
  }

  function renderMarket(data) {
    setText("market-env", data.env_pill || "—");
    setText(
      "market-session",
      data.session_date ? `交易日 ${data.session_date}` : "暂无交易日"
    );
    setText("market-one-liner", data.one_liner_zh || "—");

    const demo = document.getElementById("market-demo");
    if (data.demo_banner) {
      demo.textContent = data.demo_banner;
      demo.classList.remove("hidden");
    } else {
      demo.classList.add("hidden");
    }

    const counts = data.alert_counts || {};
    setText(
      "alert-counts",
      `紧急 ${counts.urgent || 0} · 留意 ${counts.watch || 0} · 提示 ${counts.info || 0}`
    );

    const inbox = document.getElementById("alert-inbox");
    const alerts = data.alerts || [];
    if (!alerts.length) {
      inbox.innerHTML = "<p class='empty'>收件箱空着——今天没有要处理的告警。</p>";
    } else {
      inbox.innerHTML = "";
      alerts.forEach((a) => {
        const card = document.createElement("article");
        card.className = "alert-card " + (a.severity || "info");
        card.innerHTML =
          `<p class="sev"></p><p class="reason"></p><p class="next"></p>`;
        const sevLabel =
          a.severity === "urgent"
            ? "紧急"
            : a.severity === "watch"
              ? "留意"
              : "提示";
        card.querySelector(".sev").textContent = sevLabel;
        card.querySelector(".reason").textContent = a.reason_zh || "—";
        card.querySelector(".next").textContent =
          a.next_zh ? `下一步：${a.next_zh}` : "";
        inbox.appendChild(card);
      });
    }

    const wl = data.watchlist_meta || {};
    setText(
      "watchlist-meta",
      wl.reason_zh
        ? `名单：${wl.reason_zh}`
        : wl.status
          ? `名单状态：${wl.status}`
          : ""
    );

    const pulseBox = document.getElementById("pulse-list");
    const pulse = data.pulse || [];
    if (!pulse.length) {
      pulseBox.innerHTML = "<p class='empty'>暂无持仓或候选脉搏。</p>";
    } else {
      pulseBox.innerHTML = "";
      pulse.forEach((p) => {
        const card = document.createElement("article");
        card.className = "pulse-card " + (p.tone || "idle");
        card.innerHTML =
          `<p class="title"></p><p class="status"></p><p class="detail"></p>`;
        card.querySelector(".title").textContent =
          `${p.title_zh || p.symbol || "—"} · 路线 ${p.lane || "?"}`;
        card.querySelector(".status").textContent = p.status_zh || "";
        card.querySelector(".detail").textContent = p.detail_zh || "";
        pulseBox.appendChild(card);
      });
    }

    const ref = data.reference_indices || {};
    setText(
      "ref-indices",
      ref.note_zh || "参考指数非本页主线；当前不做全市场噪音。"
    );
  }

  function renderReview(data) {
    const emptyEl = document.getElementById("review-empty");
    const sheetEl = document.getElementById("review-sheet");
    const demo = document.getElementById("review-demo");

    if (data.demo_banner) {
      demo.textContent = data.demo_banner;
      demo.classList.remove("hidden");
    } else {
      demo.classList.add("hidden");
    }

    const dates = data.available_dates || [];
    if (els.reviewDate) {
      const current = data.selected_date || selectedReviewDate || "";
      els.reviewDate.innerHTML = "";
      if (!dates.length) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "暂无交易日";
        els.reviewDate.appendChild(opt);
      } else {
        dates.forEach((d) => {
          const opt = document.createElement("option");
          opt.value = d;
          opt.textContent = d;
          if (d === current) opt.selected = true;
          els.reviewDate.appendChild(opt);
        });
      }
      selectedReviewDate = current || dates[0] || null;
    }

    if (data.empty) {
      emptyEl.textContent = data.empty_zh || "还没有可复盘的日报。";
      emptyEl.classList.remove("hidden");
      sheetEl.classList.add("hidden");
    } else {
      emptyEl.classList.add("hidden");
      sheetEl.classList.remove("hidden");
      const sheet = data.sheet || {};
      setText("review-headline", sheet.headline_zh || "—");
      setText("review-drill", sheet.drill_zh || "");
      const pnl = sheet.pnl || {};
      const pnlEl = document.getElementById("review-pnl");
      pnlEl.className = "pnl " + (pnl.tone || "flat");
      pnlEl.textContent = pnl.total_zh || "—";
      setText(
        "review-pnl-split",
        `A ${pnl.lane_a_zh || "—"} · B ${pnl.lane_b_zh || "—"}`
      );
      setText(
        "review-counts",
        sheet.counts_for_promotion ? "计入" : "不计"
      );
      setText("review-excluded", sheet.excluded_reason_zh || "");

      const lanes = document.getElementById("review-lanes");
      lanes.innerHTML = "";
      (sheet.lanes || []).forEach((lane) => {
        const div = document.createElement("div");
        div.className = "lane" + (lane.halt_reason ? " halt" : "");
        div.innerHTML =
          `<p class="label"></p><p class="value"></p><p class="meta"></p>`;
        div.querySelector(".label").textContent = `路线 ${lane.lane}`;
        div.querySelector(".value").textContent = lane.status_zh || "—";
        div.querySelector(".meta").textContent = lane.halt_reason_zh
          ? `停手：${lane.halt_reason_zh}`
          : lane.symbol
            ? `标的 ${lane.symbol}`
            : "";
        lanes.appendChild(div);
      });

      const haltBox = document.getElementById("review-halts");
      const halts = sheet.halts || [];
      if (!halts.length) {
        haltBox.innerHTML = "<p class='empty'>当日无停手记录。</p>";
      } else {
        haltBox.innerHTML =
          "<ul>" +
          halts
            .map(
              (h) =>
                `<li>路线 ${h.lane}：${h.reason_zh || h.reason || "—"}` +
                (h.detail_zh ? ` · ${h.detail_zh}` : "") +
                "</li>"
            )
            .join("") +
          "</ul>";
      }

      const fillBox = document.getElementById("review-fills");
      const fills = sheet.fills_zh || [];
      if (!fills.length) {
        fillBox.innerHTML = "<p class='empty'>当日无成交摘要。</p>";
      } else {
        fillBox.innerHTML =
          "<ul>" + fills.map((f) => `<li></li>`).join("") + "</ul>";
        Array.from(fillBox.querySelectorAll("li")).forEach((li, i) => {
          li.textContent = fills[i];
        });
      }
    }

    const cal = document.getElementById("review-calendar");
    const calendar = data.calendar || [];
    if (!calendar.length) {
      cal.innerHTML = "<p class='empty'>暂无日历。</p>";
    } else {
      cal.innerHTML = "";
      calendar.forEach((d) => {
        const cell = document.createElement("button");
        cell.type = "button";
        cell.className =
          "cal-day" + (d.counts_for_promotion ? " counts" : " skip");
        if (d.date === data.selected_date) cell.classList.add("selected");
        cell.innerHTML = `<span class="d"></span><span class="p"></span>`;
        cell.querySelector(".d").textContent = d.date || "—";
        const pnl =
          typeof d.pnl_total === "number"
            ? (d.pnl_total > 0 ? "+" : "") + d.pnl_total.toFixed(0)
            : "—";
        cell.querySelector(".p").textContent =
          (d.counts_for_promotion ? "计" : "不计") + " · " + pnl;
        cell.title = d.excluded_reason_zh || "";
        cell.addEventListener("click", () => {
          if (!d.date) return;
          selectedReviewDate = d.date;
          refresh();
        });
        cal.appendChild(cell);
      });
    }

    setText("copy-hint", data.copy_hint_zh || "");
    setText("review-summary", data.summary_zh || "");
  }

  function setParamMsg(text, tone) {
    if (!els.paramMsg) return;
    els.paramMsg.textContent = text || "";
    els.paramMsg.className = "param-msg" + (tone ? " " + tone : "");
  }

  async function refreshOverview() {
    const data = await apiGet(withMarket("/api/v1/overview"));
    renderOverview(data);
  }

  async function refreshStrategy() {
    const data = await apiGet("/api/v1/strategy");
    renderStrategy(data);
  }

  async function refreshMarket() {
    const data = await apiGet(withMarket("/api/v1/market"));
    renderMarket(data);
  }

  async function refreshReview() {
    const q = selectedReviewDate
      ? `?date=${encodeURIComponent(selectedReviewDate)}`
      : "";
    const data = await apiGet("/api/v1/review" + q);
    renderReview(data);
  }

  async function refresh() {
    els.status.textContent = "刷新中…";
    try {
      if (currentPage === "strategy") {
        await refreshStrategy();
      } else if (currentPage === "market") {
        await refreshMarket();
      } else if (currentPage === "review") {
        await refreshReview();
      } else {
        await refreshOverview();
      }
      const now = new Date();
      els.status.textContent =
        "已更新 " +
        now.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    } catch (err) {
      if (err && err.code === 401) {
        password = "";
        sessionStorage.removeItem(PASS_KEY);
        showGate("密码不对或已过期，请重试。");
        return;
      }
      els.status.textContent = "刷新失败：" + (err && err.message ? err.message : err);
    }
  }

  function setPage(page) {
    currentPage = page;
    document.querySelectorAll(".nav button").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.page === page);
    });
    els.overview.classList.add("hidden");
    els.strategy.classList.add("hidden");
    if (els.market) els.market.classList.add("hidden");
    if (els.review) els.review.classList.add("hidden");
    els.placeholder.classList.add("hidden");

    if (page === "overview") {
      els.overview.classList.remove("hidden");
      refresh();
      return;
    }
    if (page === "strategy") {
      els.strategy.classList.remove("hidden");
      refresh();
      return;
    }
    if (page === "market" && els.market) {
      els.market.classList.remove("hidden");
      refresh();
      return;
    }
    if (page === "review" && els.review) {
      els.review.classList.remove("hidden");
      refresh();
      return;
    }
    els.placeholder.classList.remove("hidden");
    els.placeholderText.textContent = "后续 Stage 落地";
  }

  async function saveParams(ev) {
    ev.preventDefault();
    const confirmZh =
      (lastStrategy &&
        lastStrategy.params &&
        lastStrategy.params.confirm_zh) ||
      "只会进练兵环境，不会碰到真钱。确认保存为新版本？";
    if (!window.confirm(confirmZh)) return;

    const fd = new FormData(els.paramForm);
    const form = {};
    fd.forEach((value, key) => {
      form[key] = typeof value === "string" ? value.trim() : value;
    });
    const note = form.note || "";
    const causal = form.causal_summary || note || "co-pilot save";
    setParamMsg("保存中…");
    try {
      const res = await apiPost("/api/v1/params/versions", {
        target_env: "staging",
        created_by: "noah",
        note,
        causal_summary: causal,
        activate: true,
        form,
      });
      setParamMsg(res.message_zh || "已进 staging 队列，未开真钱。", "ok");
      await refreshStrategy();
    } catch (err) {
      setParamMsg("保存失败：" + (err && err.message ? err.message : err), "err");
    }
  }

  async function rollbackTo(versionId) {
    const ok = window.confirm(
      versionId
        ? `确认回滚到版本 ${versionId}？只会进练兵，不会碰到真钱。`
        : "确认回滚到上一参数版本？只会进练兵，不会碰到真钱。"
    );
    if (!ok) return;
    setParamMsg("回滚中…");
    try {
      let res;
      if (versionId) {
        res = await apiPost(`/api/v1/params/versions/${versionId}/rollback`, {
          created_by: "noah",
          note: "ui rollback",
        });
      } else {
        res = await apiPost("/api/v1/params/rollback", {
          created_by: "noah",
          note: "ui rollback previous",
        });
      }
      setParamMsg(res.message_zh || "已回滚（仍仅练兵）。", "ok");
      await refreshStrategy();
    } catch (err) {
      setParamMsg("回滚失败：" + (err && err.message ? err.message : err), "err");
    }
  }

  function startAutoRefresh() {
    if (autoTimer) clearInterval(autoTimer);
    autoTimer = setInterval(() => {
      if (
        currentPage === "overview" ||
        currentPage === "strategy" ||
        currentPage === "market" ||
        currentPage === "review"
      ) {
        refresh();
      }
    }, AUTO_MS);
  }

  async function boot() {
    let auth;
    try {
      auth = await apiGet("/api/v1/auth/status");
    } catch (err) {
      showGate("无法连接控制台 API。请确认已启动 futu-console。");
      els.gate.classList.remove("hidden");
      return;
    }

    if (auth.password_required && !password) {
      showGate();
      return;
    }

    showApp();
    await refresh();
    startAutoRefresh();
  }

  els.gateSubmit.addEventListener("click", async () => {
    password = (els.gatePassword.value || "").trim();
    sessionStorage.setItem(PASS_KEY, password);
    showApp();
    await refresh();
    startAutoRefresh();
  });

  els.gatePassword.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") els.gateSubmit.click();
  });

  document.getElementById("btn-refresh").addEventListener("click", () => {
    refresh();
  });

  document.querySelectorAll(".nav button[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => setPage(btn.dataset.page));
  });

  function syncMarketButtons() {
    document.querySelectorAll(".market-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.market === selectedMarket);
    });
  }

  document.querySelectorAll(".market-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const m = btn.dataset.market === "HK" ? "HK" : "US";
      if (m === selectedMarket) return;
      selectedMarket = m;
      sessionStorage.setItem("console_market", m);
      syncMarketButtons();
      refresh();
    });
  });
  syncMarketButtons();

  if (els.paramForm) {
    els.paramForm.addEventListener("submit", saveParams);
  }
  const rollbackBtn = document.getElementById("btn-rollback");
  if (rollbackBtn) {
    rollbackBtn.addEventListener("click", () => rollbackTo(null));
  }

  if (els.reviewDate) {
    els.reviewDate.addEventListener("change", () => {
      selectedReviewDate = els.reviewDate.value || null;
      refresh();
    });
  }

  const copyBtn = document.getElementById("btn-copy-summary");
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      const text = document.getElementById("review-summary").textContent || "";
      const msg = document.getElementById("copy-msg");
      try {
        await navigator.clipboard.writeText(text);
        msg.textContent = "已复制到剪贴板。";
        msg.className = "param-msg ok";
      } catch (err) {
        msg.textContent = "复制失败，请手动选中摘要。";
        msg.className = "param-msg err";
      }
    });
  }

  boot();
})();
