/* =============================================================
 * AstrBot+ 控制台页面脚本
 *
 * 运行环境：AstrBot WebUI 中的插件 Pages 沙箱 iframe
 * 通信方式：window.AstrBotPluginPage 桥接（apiGet / apiPost）
 *  - endpoint 为插件内相对路径，不含插件名、不以 "/" 开头、不带 query
 *  - 桥接内部会补上 WebUI 同源前缀并注入 JWT，因此不会出现跨域 / Failed to fetch
 *
 * 若桥接缺失（例如直接以静态文件方式预览），降级为同源 fetch，
 * 便于本地调试；生产环境始终走桥接分支。
 * ============================================================= */

const bridge = window.AstrBotPluginPage || null;

/* Pages 名称，与 pages/<page_name>/ 目录一致 */
const PAGE_NAME = "admin";

/* ---------------------------- 桥接封装 ---------------------------- */

/** 等待桥接就绪，返回页面上下文；无桥接时返回 null。 */
async function waitReady() {
  if (!bridge || typeof bridge.ready !== "function") return null;
  try {
    return (await bridge.ready()) || {};
  } catch (err) {
    console.warn("[AstrBot+] 桥接就绪失败：", err);
    return null;
  }
}

/** 统一解包 AstrBot 响应信封：{status:'ok'|'error', message, data} */
function unwrap(raw) {
  if (raw == null) return null;
  if (typeof raw === "object" && !Array.isArray(raw)) {
    if (raw.status === "error") {
      throw new Error(raw.message || raw.error || "请求失败");
    }
    // 有 data 键且 status 为 ok 或缺省，则取 data
    if ("data" in raw && (raw.status === "ok" || raw.status === "success" || raw.status == null)) {
      return raw.data;
    }
  }
  return raw;
}

/* 后端注册路径为 /<plugin_name>/admin/**，桥接会自动补上 /<plugin_name>/，
 * 所以 endpoint 只保留 admin/ 之后的部分。 */
const API_ROOT = "admin";

function endpoint(path) {
  const clean = String(path).replace(/^\/+/, "");
  return clean ? `${API_ROOT}/${clean}` : API_ROOT;
}

async function apiGet(path, params) {
  if (bridge && typeof bridge.apiGet === "function") {
    return unwrap(await bridge.apiGet(endpoint(path), params || {}));
  }
  const qs = params ? "?" + new URLSearchParams(params).toString() : "";
  const res = await fetch(`/api/plugin/pages/${PAGE_NAME}/${endpoint(path)}${qs}`, {
    credentials: "same-origin",
  });
  return unwrap(await res.json());
}

async function apiPost(path, body) {
  if (bridge && typeof bridge.apiPost === "function") {
    return unwrap(await bridge.apiPost(endpoint(path), body || {}));
  }
  const res = await fetch(`/api/plugin/pages/${PAGE_NAME}/${endpoint(path)}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return unwrap(await res.json());
}

/* ---------------------------- 小工具 ---------------------------- */

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/** 秒级时间戳 → 本地可读时间 */
function fmtTime(ts) {
  const n = Number(ts) || 0;
  if (!n) return "—";
  const d = new Date(n * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 千分位 */
function fmtNum(n) {
  const v = Number(n) || 0;
  return v.toLocaleString("zh-CN");
}

/* ---------------------------- Toast ---------------------------- */

function toast(message, kind) {
  const box = $("#toasts");
  if (!box) return;
  const el = document.createElement("div");
  el.className = "toast" + (kind ? " is-" + kind : "");
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateY(4px)";
    el.style.transition = "opacity .18s ease, transform .18s ease";
    setTimeout(() => el.remove(), 200);
  }, kind === "err" ? 4200 : 2400);
}

/* ---------------------------- 确认框 ---------------------------- */
/* 沙箱内原生 confirm() 会被拦截，这里自绘一个 */

let dialogResolver = null;

function confirmBox(title, text, okLabel) {
  const layer = $("#dialog");
  $("#dialog-title").textContent = title || "确认";
  $("#dialog-text").textContent = text || "";
  $("#dialog-ok").textContent = okLabel || "确定";
  layer.hidden = false;
  $("#dialog-ok").focus();

  return new Promise((resolve) => {
    dialogResolver = (ok) => {
      dialogResolver = null;
      layer.hidden = true;
      resolve(ok);
    };
  });
}

function bindDialog() {
  const layer = $("#dialog");
  $$("[data-close]", layer).forEach((el) => {
    el.addEventListener("click", () => dialogResolver && dialogResolver(false));
  });
  $("#dialog-ok").addEventListener("click", () => dialogResolver && dialogResolver(true));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !layer.hidden) dialogResolver && dialogResolver(false);
  });
}

/* ---------------------------- 抽屉 ---------------------------- */

const drawer = {
  submit: null,

  open(title, bodyHTML, submitLabel, onSubmit) {
    $("#drawer-title").textContent = title;
    $("#drawer-body").innerHTML = bodyHTML;
    $("#drawer-submit").textContent = submitLabel || "确定";
    $("#drawer").hidden = false;
    this.submit = onSubmit;
    const first = $("#drawer-body input, #drawer-body select");
    if (first) first.focus();
  },

  close() {
    $("#drawer").hidden = true;
    $("#drawer-body").innerHTML = "";
    this.submit = null;
  },
};

function bindDrawer() {
  $$("[data-close]", $("#drawer")).forEach((el) => {
    el.addEventListener("click", () => drawer.close());
  });
  $("#drawer-submit").addEventListener("click", async () => {
    if (!drawer.submit) return;
    const btn = $("#drawer-submit");
    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "处理中…";
    try {
      await drawer.submit();
    } catch (err) {
      toast(err.message || String(err), "err");
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#drawer").hidden) drawer.close();
  });
}

/* ---------------------------- 状态 ---------------------------- */

const state = {
  view: "overview",
  users: [],
  agents: [],
  stats: null,
  ctx: null,
  loaded: { users: false, agents: false },
};

const VIEW_META = {
  overview: { title: "概览", desc: "运行概况" },
  users: { title: "用户", desc: "管理可登录 AstrBot+ 客户端的用户" },
  agents: { title: "智能体", desc: "管理可被客户端连接的 Agent 实例" },
};

/* ---------------------------- 视图切换 ---------------------------- */

function switchView(name) {
  if (!VIEW_META[name]) return;
  state.view = name;

  $$(".tab").forEach((b) => {
    const on = b.dataset.view === name;
    b.classList.toggle("is-active", on);
    b.setAttribute("aria-selected", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.dataset.view === name));

  const meta = VIEW_META[name];
  $("#view-title").textContent = meta.title;
  $("#view-desc").textContent = meta.desc;

  const createBtn = $("#btn-create");
  if (name === "users") {
    createBtn.hidden = false;
    createBtn.textContent = "新建用户";
    createBtn.onclick = () => openCreateUser();
  } else if (name === "agents") {
    createBtn.hidden = false;
    createBtn.textContent = "新建智能体";
    createBtn.onclick = () => openCreateAgent();
  } else {
    createBtn.hidden = true;
    createBtn.onclick = null;
  }

  if (name === "users" && !state.loaded.users) loadUsers();
  if (name === "agents" && !state.loaded.agents) loadAgents();
}

/* ---------------------------- 概览 ---------------------------- */

async function loadStats() {
  const set = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  try {
    const s = (await apiGet("stats")) || {};
    state.stats = s;
    set("stat-users", fmtNum(s.users));
    set("stat-agents", fmtNum(s.agents));
    set("stat-today", fmtNum(s.todayMessages));
    set("stat-online", fmtNum(s.online));
    if (s.version) set("ver", "v" + s.version);
  } catch (err) {
    ["stat-users", "stat-agents", "stat-today", "stat-online"].forEach((id) => set(id, "—"));
    toast("概览加载失败：" + (err.message || err), "err");
  }
}

/* ---------------------------- 用户 ---------------------------- */

function userPlaceholder(text, isError) {
  const tb = $("#table-users tbody");
  tb.innerHTML = `<tr><td class="placeholder${isError ? " error" : ""}" colspan="7">${esc(text)}</td></tr>`;
}

function allowedText(user, agents) {
  const ids = Array.isArray(user.allowed_agents) ? user.allowed_agents : [];
  if (!ids.length) return "全部";
  const names = ids.map((id) => {
    const a = agents.find((x) => String(x.id) === String(id));
    return a ? a.botname || a.id : id;
  });
  return names.join("、");
}

async function loadUsers() {
  userPlaceholder("加载中…");
  try {
    const rows = (await apiGet("users")) || [];
    state.users = Array.isArray(rows) ? rows : [];
    state.loaded.users = true;
  } catch (err) {
    state.loaded.users = false;
    userPlaceholder("加载失败：" + (err.message || err), true);
    return;
  }
  renderUsers();
}

function renderUsers() {
  const tb = $("#table-users tbody");
  if (!state.users.length) {
    tb.innerHTML = '<tr><td class="placeholder" colspan="7">暂无用户，点击右上角「新建用户」创建。</td></tr>';
    return;
  }
  tb.innerHTML = state.users
    .map((u) => {
      const on = Number(u.status) === 1;
      return `<tr>
        <td>${esc(u.email)}</td>
        <td>${esc(u.username)}</td>
        <td>${esc(fmtTime(u.create_at))}</td>
        <td><span class="tag ${on ? "on" : "off"}">${on ? "正常" : "禁用"}</span></td>
        <td>${esc(allowedText(u, state.agents))}</td>
        <td class="mono">${fmtNum(u.tokenUsage)}</td>
        <td class="ops">
          <button class="btn sm" data-act="edit" data-id="${esc(u.id)}">编辑</button>
          <button class="btn sm" data-act="toggle" data-id="${esc(u.id)}">${on ? "禁用" : "启用"}</button>
          <button class="btn sm danger" data-act="delete" data-id="${esc(u.id)}">删除</button>
        </td>
      </tr>`;
    })
    .join("");

  $$("button[data-act]", tb).forEach((btn) => {
    btn.addEventListener("click", () => onUserAction(btn.dataset.act, btn.dataset.id));
  });
}

function findUser(id) {
  return state.users.find((u) => String(u.id) === String(id)) || null;
}

async function onUserAction(act, id) {
  const u = findUser(id);
  if (!u) return;

  if (act === "edit") {
    openEditUser(u);
    return;
  }

  if (act === "toggle") {
    const next = Number(u.status) === 1 ? 0 : 1;
    try {
      await apiPost("users/update", { id: Number(u.id), status: next });
      toast(next === 1 ? "已启用该用户" : "已禁用该用户", "ok");
      await Promise.all([loadUsers(), loadStats()]);
    } catch (err) {
      toast(err.message || String(err), "err");
    }
    return;
  }

  if (act === "delete") {
    const ok = await confirmBox(
      "删除用户",
      `确认删除用户 ${u.email}？\n该用户的私聊消息将一并清除，此操作不可恢复。`,
      "删除"
    );
    if (!ok) return;
    try {
      await apiPost("users/delete", { id: Number(u.id) });
      toast("用户已删除", "ok");
      await Promise.all([loadUsers(), loadStats()]);
    } catch (err) {
      toast(err.message || String(err), "err");
    }
  }
}

/** 智能体多选区 HTML；未加载时提示 */
function agentChecksHTML(selected) {
  const picked = new Set((selected || []).map(String));
  if (!state.agents.length) {
    return '<p class="hint">暂无智能体可选。</p>';
  }
  return state.agents
    .map(
      (a) => `<label class="check">
        <input type="checkbox" name="allowed" value="${esc(a.id)}" ${picked.has(String(a.id)) ? "checked" : ""} />
        <span>${esc(a.botname || a.id)} <span class="mono muted">${esc(a.id)}</span></span>
      </label>`
    )
    .join("");
}

function collectChecked() {
  return $$('#drawer-body input[name="allowed"]:checked').map((el) => el.value);
}

function openCreateUser() {
  drawer.open(
    "新建用户",
    `<div class="field">
       <label for="f-email">邮箱</label>
       <input id="f-email" type="email" placeholder="user@example.com" autocomplete="off" />
     </div>
     <div class="field">
       <label for="f-username">用户名</label>
       <input id="f-username" type="text" placeholder="留空则使用邮箱" autocomplete="off" />
     </div>
     <div class="field">
       <label for="f-password">密码</label>
       <input id="f-password" type="password" placeholder="至少 6 位" autocomplete="new-password" />
     </div>
     <div class="field">
       <label>可用智能体</label>
       <div class="agent-picker" id="agent-picker">${agentChecksHTML([])}</div>
     </div>
     <p class="hint">创建成功后会返回一条快连链接，可直接填入客户端登录。</p>`,
    "创建",
    async () => {
      const email = $("#f-email").value.trim();
      const username = $("#f-username").value.trim();
      const password = $("#f-password").value;
      if (!email) throw new Error("请填写邮箱");
      if (!password) throw new Error("请填写密码");
      if (password.length < 6) throw new Error("密码至少 6 位");

      const body = { email, username, password };
      const allowed = collectChecked();
      if (allowed.length) body.allowed_agents = allowed;

      const res = (await apiPost("users/create", body)) || {};
      drawer.close();
      toast("用户创建成功", "ok");
      await Promise.all([loadUsers(), loadStats()]);
      if (res.quickLink) showQuickLink(res.quickLink);
    }
  );
}

function showQuickLink(link) {
  drawer.open(
    "创建成功",
    `<p class="hint">把下面的快连链接复制到客户端「快速连接」即可登录。</p>
     <div class="quick">
       <p>链接内含明文密码，请通过安全渠道发送，并尽快让用户修改密码。</p>
       <div class="quick-url" id="quick-url">${esc(link)}</div>
       <div class="quick-actions">
         <button class="btn sm primary" id="quick-copy">复制链接</button>
       </div>
     </div>`,
    "完成",
    async () => {
      drawer.close();
    }
  );
  const copyBtn = $("#quick-copy");
  const urlBox = $("#quick-url");
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(link);
        urlBox.classList.add("is-copied");
        copyBtn.textContent = "已复制";
        toast("链接已复制", "ok");
      } catch {
        // 沙箱可能禁用剪贴板，退化为选中文本
        const range = document.createRange();
        range.selectNodeContents(urlBox);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        toast("已选中链接，请手动复制", "err");
      }
    });
  }
}

function openEditUser(u) {
  if (!state.agents.length) loadAgents().then(() => { state.loaded.agents = true; });

  drawer.open(
    `编辑用户 #${u.id}`,
    `<div class="field">
       <label for="f-email">邮箱</label>
       <input id="f-email" type="email" value="${esc(u.email)}" autocomplete="off" />
     </div>
     <div class="field">
       <label for="f-username">用户名</label>
       <input id="f-username" type="text" value="${esc(u.username)}" autocomplete="off" />
     </div>
     <div class="field">
       <label for="f-password">重置密码</label>
       <input id="f-password" type="password" placeholder="留空则不修改" autocomplete="new-password" />
     </div>
     <div class="field">
       <label>可用智能体</label>
       <div class="agent-picker" id="agent-picker">${agentChecksHTML(u.allowed_agents)}</div>
     </div>`,
    "保存",
    async () => {
      const email = $("#f-email").value.trim();
      const username = $("#f-username").value.trim();
      const password = $("#f-password").value;
      if (!email) throw new Error("邮箱不能为空");
      if (!username) throw new Error("用户名不能为空");
      if (password && password.length < 6) throw new Error("密码至少 6 位");

      const body = { id: Number(u.id), email, username, allowed_agents: collectChecked() };
      if (password) body.password = password;

      await apiPost("users/update", body);
      drawer.close();
      toast("已保存", "ok");
      await Promise.all([loadUsers(), loadStats()]);
    }
  );

  // 智能体列表异步到达后刷新多选区
  if (!state.agents.length) {
    const timer = setInterval(() => {
      const picker = $("#agent-picker");
      if (!picker) {
        clearInterval(timer);
        return;
      }
      if (state.agents.length) {
        picker.innerHTML = agentChecksHTML(u.allowed_agents);
        clearInterval(timer);
      }
    }, 300);
  }
}

/* ---------------------------- 智能体 ---------------------------- */

function agentPlaceholder(text, isError) {
  const tb = $("#table-agents tbody");
  tb.innerHTML = `<tr><td class="placeholder${isError ? " error" : ""}" colspan="5">${esc(text)}</td></tr>`;
}

async function loadAgents() {
  agentPlaceholder("加载中…");
  try {
    const rows = (await apiGet("agents")) || [];
    state.agents = Array.isArray(rows) ? rows : [];
    state.loaded.agents = true;
  } catch (err) {
    state.loaded.agents = false;
    agentPlaceholder("加载失败：" + (err.message || err), true);
    return;
  }
  renderAgents();
}

function renderAgents() {
  const tb = $("#table-agents tbody");
  if (!state.agents.length) {
    tb.innerHTML = '<tr><td class="placeholder" colspan="5">暂无智能体，点击右上角「新建智能体」创建。</td></tr>';
    return;
  }
  tb.innerHTML = state.agents
    .map((a) => {
      const on = a.enable !== false;
      return `<tr>
        <td class="mono">${esc(a.id)}</td>
        <td>${esc(a.botname)}</td>
        <td class="mono muted">${esc(a.botdf)}</td>
        <td><span class="tag ${on ? "on" : "off"}">${on ? "启用" : "停用"}</span></td>
        <td class="ops">
          <button class="btn sm" data-act="edit" data-id="${esc(a.id)}">编辑</button>
          <button class="btn sm" data-act="toggle" data-id="${esc(a.id)}">${on ? "停用" : "启用"}</button>
          <button class="btn sm danger" data-act="delete" data-id="${esc(a.id)}">删除</button>
        </td>
      </tr>`;
    })
    .join("");

  $$("button[data-act]", tb).forEach((btn) => {
    btn.addEventListener("click", () => onAgentAction(btn.dataset.act, btn.dataset.id));
  });
}

function findAgent(id) {
  return state.agents.find((a) => String(a.id) === String(id)) || null;
}

async function onAgentAction(act, id) {
  const a = findAgent(id);
  if (!a) return;

  if (act === "edit") {
    openEditAgent(a);
    return;
  }

  if (act === "toggle") {
    const next = a.enable === false;
    try {
      await apiPost("agents/update", { id: a.id, enable: next });
      toast(next ? "已启用" : "已停用", "ok");
      await Promise.all([loadAgents(), loadStats()]);
    } catch (err) {
      toast(err.message || String(err), "err");
    }
    return;
  }

  if (act === "delete") {
    const ok = await confirmBox(
      "删除智能体",
      `确认删除智能体「${a.botname || a.id}」？\n删除后该适配器会从平台配置中移除，正在连接的客户端将断开。`,
      "删除"
    );
    if (!ok) return;
    try {
      await apiPost("agents/delete", { id: a.id });
      toast("智能体已删除", "ok");
      await Promise.all([loadAgents(), loadStats(), loadUsers()]);
    } catch (err) {
      toast(err.message || String(err), "err");
    }
  }
}

function openCreateAgent() {
  drawer.open(
    "新建智能体",
    `<div class="field">
       <label for="f-botname">显示名</label>
       <input id="f-botname" type="text" placeholder="例如：我的助手" autocomplete="off" />
     </div>
     <div class="field">
       <label for="f-id">Agent ID（可选）</label>
       <input id="f-id" type="text" placeholder="留空自动生成 12 位 ID" autocomplete="off" />
     </div>
     <p class="hint">创建后会在平台配置中追加一个 astrbot_plus 适配器并立即热加载，同时生成 32 位会话指纹。</p>`,
    "创建",
    async () => {
      const botname = $("#f-botname").value.trim();
      const id = $("#f-id").value.trim();
      if (!botname) throw new Error("请填写显示名");

      const body = { botname };
      if (id) body.id = id;

      await apiPost("agents/create", body);
      drawer.close();
      toast("智能体已创建", "ok");
      await Promise.all([loadAgents(), loadStats()]);
    }
  );
}

function openEditAgent(a) {
  const on = a.enable !== false;
  drawer.open(
    "编辑智能体",
    `<div class="field">
       <label>Agent ID</label>
       <input type="text" value="${esc(a.id)}" disabled />
     </div>
     <div class="field">
       <label for="f-botname">显示名</label>
       <input id="f-botname" type="text" value="${esc(a.botname)}" autocomplete="off" />
     </div>
     <div class="field">
       <label class="check">
         <input type="checkbox" id="f-enable" ${on ? "checked" : ""} />
         <span>启用该智能体</span>
       </label>
       <p class="hint">停用后适配器会被摘除，客户端将无法连接；重新启用会立即重新加载。</p>
     </div>`,
    "保存",
    async () => {
      const botname = $("#f-botname").value.trim();
      if (!botname) throw new Error("显示名不能为空");
      const enable = $("#f-enable").checked;

      await apiPost("agents/update", { id: a.id, botname, enable });
      drawer.close();
      toast("已保存", "ok");
      await Promise.all([loadAgents(), loadStats()]);
    }
  );
}

/* ---------------------------- 主题 ---------------------------- */

function applyTheme(theme) {
  const value = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", value);
}

/** 桥接上下文用 isDark 布尔表示主题。返回 'dark'|'light'|null。 */
function readThemeFromContext(ctx) {
  if (!ctx) return null;
  if (typeof ctx.isDark === "boolean") return ctx.isDark ? "dark" : "light";
  if (ctx.context && typeof ctx.context.isDark === "boolean") {
    return ctx.context.isDark ? "dark" : "light";
  }
  return null;
}

/* ---------------------------- 启动 ---------------------------- */

async function boot() {
  bindDialog();
  bindDrawer();

  $$(".tab").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
    btn.addEventListener("keydown", (e) => {
      const tabs = $$(".tab");
      const i = tabs.indexOf(btn);
      if (e.key === "ArrowRight" && i < tabs.length - 1) {
        tabs[i + 1].focus();
        switchView(tabs[i + 1].dataset.view);
      } else if (e.key === "ArrowLeft" && i > 0) {
        tabs[i - 1].focus();
        switchView(tabs[i - 1].dataset.view);
      }
    });
  });

  $("#btn-reload").addEventListener("click", async () => {
    const btn = $("#btn-reload");
    btn.disabled = true;
    state.loaded.users = false;
    state.loaded.agents = false;
    try {
      await Promise.all([loadStats(), loadUsers(), loadAgents()]);
    } finally {
      btn.disabled = false;
    }
    toast("已刷新", "ok");
  });

  const ctx = await waitReady();
  state.ctx = ctx;

  const theme = readThemeFromContext(ctx);
  if (theme) applyTheme(theme);

  // 订阅上下文变化（主题/语言切换）
  if (bridge && typeof bridge.onContext === "function") {
    try {
      bridge.onContext((next) => {
        state.ctx = next;
        const th = readThemeFromContext(next);
        if (th) applyTheme(th);
      });
    } catch (err) {
      console.warn("[AstrBot+] onContext 订阅失败：", err);
    }
  }

  // 系统主题兜底
  if (!theme && window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    applyTheme(mq.matches ? "dark" : "light");
    mq.addEventListener?.("change", (e) => {
      if (!readThemeFromContext(state.ctx)) applyTheme(e.matches ? "dark" : "light");
    });
  }

  switchView("overview");

  // 首屏并行拉取：概览 + 两张表（表数据供多选区复用）
  await Promise.all([loadStats(), loadUsers(), loadAgents()]);

}

document.addEventListener("DOMContentLoaded", () => {
  boot().catch((err) => {
    console.error("[AstrBot+] 初始化失败：", err);
    toast("初始化失败：" + (err.message || err), "err");
  });
});
