/* AstrBot+ 管理视图 —— 通过 window.AstrBotPluginView 与 WebUI 桥接。
 * 所有请求走 WebUI 自带的插件 API 代理（同源），不直连 IM 端口。
 */
(function () {
  const BASE = "/api/v1/plugins/extensions/astrbot_plugin_plus/admin";

  async function api(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const r = await fetch(BASE + path, opt);
    const text = await r.text();
    try { return JSON.parse(text); } catch { return text; }
  }

  // ---- tabs ----
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".pane").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
      refresh();
    });
  });

  async function refresh() {
    refreshStats();
    refreshUsers();
    refreshAgents();
  }

  async function refreshStats() {
    const s = await api("GET", "/stats");
    if (!s || s.error) return;
    document.getElementById("stat-users").textContent = s.users ?? "-";
    document.getElementById("stat-agents").textContent = s.agents ?? "-";
    document.getElementById("stat-today").textContent = s.todayMessages ?? "-";
    document.getElementById("stat-online").textContent = s.online ?? "-";
  }

  async function refreshUsers() {
    const rows = await api("GET", "/users");
    const tb = document.querySelector("#users-table tbody");
    tb.innerHTML = "";
    (rows || []).forEach((u) => {
      const tr = document.createElement("tr");
      const created = new Date((u.create_at || 0) * 1000).toLocaleString();
      tr.innerHTML = `
        <td>${u.email}</td>
        <td>${u.username}</td>
        <td>${created}</td>
        <td>${u.status === 1 ? "正常" : "禁用"}</td>
        <td>${(u.allowed_agents || []).length}</td>
        <td>${u.tokenUsage || 0}</td>
        <td>
          <button data-act="toggle" data-id="${u.id}">${u.status === 1 ? "禁用" : "启用"}</button>
          <button data-act="del" data-id="${u.id}">删除</button>
        </td>`;
      tb.appendChild(tr);
    });
    tb.querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", async () => {
        const id = b.dataset.id;
        if (b.dataset.act === "del") {
          if (!confirm("确认删除该用户？")) return;
          await api("DELETE", "/users/" + id);
        } else if (b.dataset.act === "toggle") {
          const u = (await api("GET", "/users")).find((x) => String(x.id) === String(id));
          await api("PATCH", "/users/" + id, { status: u.status === 1 ? 0 : 1 });
        }
        refresh();
      });
    });
  }

  async function refreshAgents() {
    const rows = await api("GET", "/agents");
    const tb = document.querySelector("#agents-table tbody");
    tb.innerHTML = "";
    (rows || []).forEach((a) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${a.id}</td>
        <td>${a.botname}</td>
        <td>${a.enable ? "✅" : "⛔"}</td>
        <td>
          <button data-act="toggle" data-id="${a.id}">${a.enable ? "停用" : "启用"}</button>
          <button data-act="del" data-id="${a.id}">删除</button>
        </td>`;
      tb.appendChild(tr);
    });
    tb.querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", async () => {
        const id = b.dataset.id;
        if (b.dataset.act === "del") {
          if (!confirm("确认删除该 agent？")) return;
          await api("DELETE", "/agents/" + id);
        } else if (b.dataset.act === "toggle") {
          const a = (await api("GET", "/agents")).find((x) => String(x.id) === String(id));
          await api("PATCH", "/agents/" + id, { enable: !a.enable });
        }
        refresh();
      });
    });
  }

  // ---- new user modal ----
  document.getElementById("btn-new-user").addEventListener("click", () => {
    document.getElementById("modal-title").textContent = "新建用户";
    document.getElementById("modal-content").innerHTML = `
      <label>邮箱</label><input id="f-email" />
      <label>用户名</label><input id="f-username" />
      <label>密码</label><input id="f-password" type="password" />
      <div id="quick-slot"></div>`;
    openModal(async () => {
      const body = {
        email: document.getElementById("f-email").value,
        username: document.getElementById("f-username").value,
        password: document.getElementById("f-password").value,
      };
      const r = await api("POST", "/users", body);
      if (r && r.quickLink) {
        document.getElementById("quick-slot").innerHTML =
          `<div class="quicklink" title="点击复制">${r.quickLink}</div>`;
        document.getElementById("quick-slot").querySelector(".quicklink")
          .addEventListener("click", (e) => navigator.clipboard.writeText(e.target.innerText));
        document.getElementById("modal-ok").style.display = "none";
      } else {
        alert("创建失败：" + (r && r.error ? r.error : JSON.stringify(r)));
      }
      refresh();
    });
  });

  // ---- new agent modal ----
  document.getElementById("btn-new-agent").addEventListener("click", () => {
    document.getElementById("modal-title").textContent = "新建 Agent";
    document.getElementById("modal-content").innerHTML = `
      <label>Botname（显示名）</label><input id="f-botname" />`;
    openModal(async () => {
      await api("POST", "/agents", { botname: document.getElementById("f-botname").value });
      closeModal();
      refresh();
    });
  });

  function openModal(onOk) {
    document.getElementById("modal").classList.remove("hidden");
    document.getElementById("modal-ok").style.display = "";
    document.getElementById("modal-ok").onclick = onOk;
  }
  function closeModal() {
    document.getElementById("modal").classList.add("hidden");
  }
  document.getElementById("modal-cancel").addEventListener("click", closeModal);

  refresh();
})();
