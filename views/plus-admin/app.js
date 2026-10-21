/* AstrBot+ 管理视图 —— 通过 AstrBot WebUI 的插件扩展 API 代理访问（同源）。
 * 所有请求走 /api/v1/plugins/extensions/<plugin_name>/admin/*，不直连 IM 端口。
 */
(function () {
  const BASE = "/api/v1/plugins/extensions/astrbot_plugin_plus/admin";

  const state = { users: [], agents: [] };

  async function api(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(BASE + path, opt);
    const text = await r.text();
    let data;
    try { data = JSON.parse(text); } catch { data = text; }
    // AstrBot 统一错误信封：{status:"error", message, data}
    if (data && typeof data === "object" && data.status === "error") {
      throw new Error(data.message || "请求失败");
    }
    if (!r.ok) {
      throw new Error((data && (data.message || data.error)) || `HTTP ${r.status}`);
    }
    return data;
  }

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function fmtTime(ts) {
    const n = Number(ts) || 0;
    if (!n) return "-";
    return new Date(n * 1000).toLocaleString();
  }

  // ---- tabs ----
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".pane").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const pane = document.getElementById("tab-" + btn.dataset.tab);
      if (pane) pane.classList.add("active");
      refresh();
    });
  });

  async function refresh() {
    await Promise.all([refreshStats(), refreshUsers(), refreshAgents()]);
  }

  async function refreshStats() {
    try {
      const s = await api("GET", "/stats");
      document.getElementById("stat-users").textContent = s.users ?? "-";
      document.getElementById("stat-agents").textContent = s.agents ?? "-";
      document.getElementById("stat-today").textContent = s.todayMessages ?? "-";
      document.getElementById("stat-online").textContent = s.online ?? "-";
    } catch (e) { /* 总览失败不阻塞其它面板 */ }
  }

  // ---------------------------------------------------------------- 用户列表
  async function refreshUsers() {
    const tb = document.querySelector("#users-table tbody");
    try {
      state.users = (await api("GET", "/users")) || [];
    } catch (e) {
      tb.innerHTML = `<tr><td colspan="7" class="err">加载失败：${esc(e.message)}</td></tr>`;
      return;
    }
    tb.innerHTML = "";
    state.users.forEach((u) => {
      const tr = document.createElement("tr");
      const agents = Array.isArray(u.allowed_agents) ? u.allowed_agents : [];
      tr.innerHTML = `
        <td>${esc(u.email)}</td>
        <td>${esc(u.username)}</td>
        <td>${esc(fmtTime(u.create_at))}</td>
        <td>${u.status === 1 ? "正常" : "禁用"}</td>
        <td>${agents.length ? agents.length + " 个" : "全部"}</td>
        <td>${esc(u.tokenUsage || 0)}</td>
        <td class="ops">
          <button data-act="edit" data-id="${u.id}">编辑</button>
          <button data-act="agents" data-id="${u.id}">可用 Agent</button>
          <button data-act="toggle" data-id="${u.id}">${u.status === 1 ? "禁用" : "启用"}</button>
          <button data-act="del" data-id="${u.id}" class="danger">删除</button>
        </td>`;
      tb.appendChild(tr);
    });
    tb.querySelectorAll("button[data-act]").forEach((b) => {
      b.addEventListener("click", () => onUserAction(b.dataset.act, b.dataset.id));
    });
  }

  async function onUserAction(act, id) {
    const u = state.users.find((x) => String(x.id) === String(id));
    if (!u) return;
    try {
      if (act === "del") {
        if (!confirm(`确认删除用户 ${u.email}？该用户的私聊消息也会一并删除。`)) return;
        await api("DELETE", "/users/" + id);
        await refresh();
      } else if (act === "toggle") {
        await api("PATCH", "/users/" + id, { status: u.status === 1 ? 0 : 1 });
        await refresh();
      } else if (act === "edit") {
        openEditUser(u);
      } else if (act === "agents") {
        openAllowedAgents(u);
      }
    } catch (e) {
      alert(e.message);
    }
  }

  // ---- 新建用户（成功后展示一次快连 URL）----
  document.getElementById("btn-new-user").addEventListener("click", () => {
    document.getElementById("modal-title").textContent = "新建用户";
    document.getElementById("modal-content").innerHTML = `
      <label>邮箱</label><input id="f-email" type="email" placeholder="alice@example.com" />
      <label>用户名</label><input id="f-username" placeholder="alice" />
      <label>密码</label><input id="f-password" type="password" placeholder="至少 6 位" />
      <div id="quick-slot"></div>`;
    openModal(async () => {
      const body = {
        email: (document.getElementById("f-email").value || "").trim(),
        username: (document.getElementById("f-username").value || "").trim(),
        password: document.getElementById("f-password").value || "",
      };
      if (!body.email || !body.username || !body.password) {
        alert("邮箱、用户名、密码均为必填");
        return;
      }
      const r = await api("POST", "/users", body);
      if (r && r.quickLink) {
        showQuickLink(r.quickLink);
      } else {
        alert("创建失败");
      }
      await refresh();
    });
  });

  function showQuickLink(url) {
    const slot = document.getElementById("quick-slot");
    slot.innerHTML = `
      <div class="quick-wrap">
        <div class="quick-tip">⚠️ 快连链接只会显示这一次，请长按复制并妥善保存</div>
        <div class="quicklink" title="点击复制">${esc(url)}</div>
        <button class="primary" id="quick-copy">复制链接</button>
      </div>`;
    const box = slot.querySelector(".quicklink");
    const copy = async () => {
      try {
        await navigator.clipboard.writeText(url);
        box.classList.add("copied");
        setTimeout(() => box.classList.remove("copied"), 1200);
      } catch (e) {
        // 剪贴板不可用时提示手动长按复制
        alert("自动复制不可用，请长按上方链接手动复制");
      }
    };
    box.addEventListener("click", copy);
    slot.querySelector("#quick-copy").addEventListener("click", copy);
    // 创建成功后隐藏“确定”，避免重复提交
    const ok = document.getElementById("modal-ok");
    if (ok) ok.style.display = "none";
  }

  // ---- 编辑用户资料（邮箱 / 用户名 / 重置密码）----
  function openEditUser(u) {
    document.getElementById("modal-title").textContent = `编辑用户 #${u.id}`;
    document.getElementById("modal-content").innerHTML = `
      <label>邮箱</label><input id="f-email" type="email" value="${esc(u.email)}" />
      <label>用户名</label><input id="f-username" value="${esc(u.username)}" />
      <label>重置密码（留空则不修改）</label>
      <input id="f-password" type="password" placeholder="至少 6 位" />`;
    openModal(async () => {
      const body = {
        email: (document.getElementById("f-email").value || "").trim(),
        username: (document.getElementById("f-username").value || "").trim(),
      };
      const pw = document.getElementById("f-password").value || "";
      if (pw) body.password = pw;
      await api("PATCH", "/users/" + u.id, body);
      closeModal();
      await refresh();
    });
  }

  // ---- 设置用户可用 Agent ----
  function openAllowedAgents(u) {
    const current = Array.isArray(u.allowed_agents) ? u.allowed_agents.map(String) : [];
    const allMode = current.length === 0;
    const items = state.agents.map((a) => `
      <label class="chk">
        <input type="checkbox" value="${esc(a.id)}" ${current.includes(String(a.id)) ? "checked" : ""} />
        <span>${esc(a.botname || a.id)}</span>
      </label>`).join("") || `<div class="hint">暂无 Agent，请先到「Agent 管理」新建</div>`;
    document.getElementById("modal-title").textContent = `设置可用 Agent — ${u.username || u.email}`;
    document.getElementById("modal-content").innerHTML = `
      <label class="chk">
        <input type="checkbox" id="all-agents" ${allMode ? "checked" : ""} />
        <span>允许使用全部 Agent</span>
      </label>
      <div id="agent-list" class="${allMode ? "disabled" : ""}">${items}</div>
      <div class="hint">取消勾选「全部」后，可精确指定该用户能使用的 Agent。</div>`;
    const all = document.getElementById("agent-list");
    document.getElementById("all-agents").addEventListener("change", (e) => {
      all.classList.toggle("disabled", e.target.checked);
    });
    openModal(async () => {
      const allChecked = document.getElementById("all-agents").checked;
      let ids = [];
      if (!allChecked) {
        ids = Array.from(all.querySelectorAll('input[type="checkbox"]:checked')).map((x) => x.value);
        if (!ids.length) {
          alert("至少选择一个 Agent，或勾选「允许使用全部 Agent」");
          return;
        }
      }
      await api("PATCH", "/users/" + u.id, { allowed_agents: ids });
      closeModal();
      await refresh();
    });
  }

  // ---------------------------------------------------------------- Agent 列表
  async function refreshAgents() {
    const tb = document.querySelector("#agents-table tbody");
    try {
      state.agents = (await api("GET", "/agents")) || [];
    } catch (e) {
      tb.innerHTML = `<tr><td colspan="4" class="err">加载失败：${esc(e.message)}</td></tr>`;
      return;
    }
    tb.innerHTML = "";
    state.agents.forEach((a) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${esc(a.id)}</td>
        <td>${esc(a.botname)}</td>
        <td>${a.enable ? "✅" : "⛔"}</td>
        <td class="ops">
          <button data-act="edit" data-id="${esc(a.id)}">重命名</button>
          <button data-act="toggle" data-id="${esc(a.id)}">${a.enable ? "停用" : "启用"}</button>
          <button data-act="del" data-id="${esc(a.id)}" class="danger">删除</button>
        </td>`;
      tb.appendChild(tr);
    });
    tb.querySelectorAll("button[data-act]").forEach((b) => {
      b.addEventListener("click", () => onAgentAction(b.dataset.act, b.dataset.id));
    });
  }

  async function onAgentAction(act, id) {
    const a = state.agents.find((x) => String(x.id) === String(id));
    if (!a) return;
    try {
      if (act === "del") {
        if (!confirm(`确认删除 Agent「${a.botname || a.id}」？`)) return;
        await api("DELETE", "/agents/" + encodeURIComponent(id));
        await refresh();
      } else if (act === "toggle") {
        await api("PATCH", "/agents/" + encodeURIComponent(id), { enable: !a.enable });
        await refresh();
      } else if (act === "edit") {
        document.getElementById("modal-title").textContent = "重命名 Agent";
        document.getElementById("modal-content").innerHTML = `
          <label>Botname（显示名）</label>
          <input id="f-botname" value="${esc(a.botname)}" />`;
        openModal(async () => {
          const name = (document.getElementById("f-botname").value || "").trim();
          if (!name) { alert("Botname 不能为空"); return; }
          await api("PATCH", "/agents/" + encodeURIComponent(id), { botname: name });
          closeModal();
          await refresh();
        });
      }
    } catch (e) {
      alert(e.message);
    }
  }

  // ---- 新建 Agent ----
  document.getElementById("btn-new-agent").addEventListener("click", () => {
    document.getElementById("modal-title").textContent = "新建 Agent";
    document.getElementById("modal-content").innerHTML = `
      <label>Botname（显示名）</label><input id="f-botname" placeholder="例如：小助手" />
      <div class="hint">将创建一个 <code>astrbot_plus</code> 类型适配器。</div>`;
    openModal(async () => {
      const name = (document.getElementById("f-botname").value || "").trim();
      if (!name) { alert("Botname 不能为空"); return; }
      await api("POST", "/agents", { botname: name });
      closeModal();
      await refresh();
    });
  });

  // ---------------------------------------------------------------- modal
  let modalHandler = null;

  function openModal(onOk) {
    modalHandler = onOk;
    document.getElementById("modal").classList.remove("hidden");
    const ok = document.getElementById("modal-ok");
    ok.style.display = "";
    ok.disabled = false;
    ok.onclick = async () => {
      ok.disabled = true;
      try {
        await modalHandler();
      } catch (e) {
        alert(e.message);
      } finally {
        ok.disabled = false;
      }
    };
  }

  function closeModal() {
    document.getElementById("modal").classList.add("hidden");
    modalHandler = null;
  }

  document.getElementById("modal-cancel").addEventListener("click", closeModal);
  document.getElementById("modal").addEventListener("click", (e) => {
    if (e.target.id === "modal") closeModal();
  });

  refresh();
})();
