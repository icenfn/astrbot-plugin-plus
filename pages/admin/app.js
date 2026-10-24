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

/** 用户指纹：由系统生成、只读展示，用于识别客户端 */
function userFingerprint(user) {
  const raw = user && (user.df || user.fingerprint || user.df_hash);
  return raw ? String(raw) : "—";
}

/** 内置默认头像（与插件 assets/ 下图片一致，base64 内联，无需额外请求）。 */
const DEFAULT_USER_AVATAR =
  "data:image/png;base64,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";
const DEFAULT_AGENT_AVATAR =
  "data:image/png;base64,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";

/** 按类型取默认头像 data-URI。 */
function defaultAvatarOf(type) {
  return type === "agent" ? DEFAULT_AGENT_AVATAR : DEFAULT_USER_AVATAR;
}

/** 头像选择器 HTML：头像为空时表示使用服务端内置默认头像。 */
function avatarPickerHTML(current, type) {
  const src = String(current || "").trim() || defaultAvatarOf(type);
  return `<div class="avatar-picker">
    <div class="avatar-preview" id="f-avatar-preview" style="background-image:url('${src}')"></div>
    <div class="avatar-actions">
      <input type="file" id="f-avatar-file" accept="image/png,image/jpeg,image/webp,image/gif" hidden />
      <button type="button" class="btn sm" id="f-avatar-pick">选择图片</button>
      <button type="button" class="btn sm ghost" id="f-avatar-clear">恢复默认</button>
    </div>
    <input type="hidden" id="f-avatar" value="${esc(String(current || ""))}" data-type="${type}" />
    <p class="hint">留空则使用内置默认头像；支持 PNG / JPG / WebP，图片会压缩到 256px 以内后保存。</p>
  </div>`;
}

/** 读取用户选择的图片并压缩为 data-URI（最长边 256px）。 */
function readAvatarFile(file) {
  return new Promise((resolve, reject) => {
    if (!file) return reject(new Error("没有选择文件"));
    if (!/^image\//.test(file.type)) return reject(new Error("请选择图片文件"));
    if (file.size > 8 * 1024 * 1024) return reject(new Error("图片过大（需小于 8MB）"));
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("读取图片失败"));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("图片解析失败"));
      img.onload = () => {
        const MAX = 256;
        const scale = Math.min(1, MAX / Math.max(img.width, img.height));
        const w = Math.max(1, Math.round(img.width * scale));
        const h = Math.max(1, Math.round(img.height * scale));
        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        canvas.getContext("2d").drawImage(img, 0, 0, w, h);
        try {
          resolve(canvas.toDataURL("image/png"));
        } catch (e) {
          reject(new Error("图片转换失败"));
        }
      };
      img.src = String(reader.result || "");
    };
    reader.readAsDataURL(file);
  });
}

/** 绑定头像选择器交互（drawer.open 之后调用）。 */
function bindAvatarPicker() {
  const fileInput = $("#f-avatar-file");
  const hidden = $("#f-avatar");
  const preview = $("#f-avatar-preview");
  if (!fileInput || !hidden || !preview) return;

  const fallback = defaultAvatarOf(hidden.dataset.type || "user");
  const render = (src) => {
    hidden.value = src || "";
    preview.style.backgroundImage = `url('${src || fallback}')`;
  };

  $("#f-avatar-pick")?.addEventListener("click", () => fileInput.click());
  $("#f-avatar-clear")?.addEventListener("click", () => render(""));
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    try {
      render(await readAvatarFile(file));
    } catch (err) {
      toast(err.message || String(err), "err");
    } finally {
      fileInput.value = "";
    }
  });
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
    // 抽屉内容为同步 innerHTML 渲染，创建好后立刻可绑定头像选择器。
    bindAvatarPicker();
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

function allowedText(user) {
  const ids = Array.isArray(user.allowed_agents) ? user.allowed_agents : [];
  // 列表中只显示可用智能体的数量；0 表示该用户没有任何可用智能体。
  return String(ids.length);
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
        <td>${esc(allowedText(u))}</td>
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
function agentChecksHTML(selected, defaultAll) {
  // defaultAll=true 时（新建用户）未指定白名单则默认全选；
  // 语义上「全部不勾选」代表该用户没有可用智能体。
  const picked = new Set((selected || []).map(String));
  const all = defaultAll && !picked.size;
  if (!state.agents.length) {
    return '<p class="hint">暂无智能体可选。</p>';
  }
  return state.agents
    .map(
      (a) => `<label class="check">
        <input type="checkbox" name="allowed" value="${esc(a.id)}" ${all || picked.has(String(a.id)) ? "checked" : ""} />
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
       <label for="f-ap_id">ap_id（可选）</label>
       <input id="f-ap_id" type="text" placeholder="留空自动生成，如 ap_7f3k9q2m" autocomplete="off" />
       <p class="hint">用户对外唯一标识（类似 wxid），格式 ap_ 加 6~15 位字母、数字或下划线。</p>
     </div>
     <div class="field">
       <label>用户头像</label>
       ${avatarPickerHTML("", "user")}
     </div>
     <div class="field">
       <label>可用智能体</label>
       <div class="agent-picker" id="agent-picker">${agentChecksHTML([], true)}</div>
       <p class="hint">默认全选；取消勾选后该用户将没有可用智能体。</p>
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

      const apId = ($("#f-ap_id").value || "").trim();
      const avatar = ($("#f-avatar").value || "").trim();
      const body = { email, username, password };
      // 始终提交白名单：空数组表示「没有可用智能体」。
      body.allowed_agents = collectChecked();
      if (apId) body.ap_id = apId;
      if (avatar) body.avatar = avatar;

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
       <label>指纹</label>
       <input id="f-fingerprint" type="text" value="${esc(userFingerprint(u))}" readonly class="mono" />
       <p class="hint">指纹由系统生成，仅用于识别该客户端，不可修改。</p>
     </div>
     <div class="field">
       <label for="f-username">用户名</label>
       <input id="f-username" type="text" value="${esc(u.username)}" autocomplete="off" />
     </div>
     <div class="field">
       <label for="f-ap_id">ap_id</label>
       <input id="f-ap_id" type="text" value="${esc(u.ap_id || "")}" placeholder="ap_xxxxxx" autocomplete="off" />
       <p class="hint">用户对外唯一标识（类似 wxid），可修改；格式 ap_ 加 6~15 位字母、数字或下划线。</p>
     </div>
     <div class="field">
       <label for="f-password">重置密码</label>
       <input id="f-password" type="password" placeholder="留空则不修改" autocomplete="new-password" />
     </div>
     <div class="field">
       <label>用户头像</label>
       ${avatarPickerHTML(u.avatar || "", "user")}
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

      const apId = ($("#f-ap_id").value || "").trim();
      const avatar = ($("#f-avatar").value || "").trim();
      if (!apId) throw new Error("ap_id 不能为空");
      const body = {
        id: Number(u.id), email, username, allowed_agents: collectChecked(),
        ap_id: apId, avatar,
      };
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
  tb.innerHTML = `<tr><td class="placeholder${isError ? " error" : ""}" colspan="6">${esc(text)}</td></tr>`;
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
    tb.innerHTML = '<tr><td class="placeholder" colspan="6">暂无智能体，点击右上角「新建智能体」创建。</td></tr>';
    return;
  }
  tb.innerHTML = state.agents
    .map((a) => {
      const on = a.enable !== false;
      return `<tr>
        <td class="mono">${esc(a.id)}</td>
        <td>${esc(a.botname)}</td>
        <td class="remark-cell${a.remark ? "" : " muted"}">${a.remark ? esc(a.remark) : "—"}</td>
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
     <div class="field">
       <label for="f-remark">备注（可选）</label>
       <input id="f-remark" type="text" placeholder="用于区分多个智能体，例如：客服专用" autocomplete="off" />
     </div>
     <div class="field">
       <label>智能体头像</label>
       ${avatarPickerHTML("", "agent")}
     </div>
     <p class="hint">创建后会在平台配置中追加一个 astrbot_plus 适配器并立即热加载，同时生成 32 位会话指纹。</p>`,
    "创建",
    async () => {
      const botname = $("#f-botname").value.trim();
      const id = $("#f-id").value.trim();
      if (!botname) throw new Error("请填写显示名");

      const remark = $("#f-remark").value.trim();
      const avatar = ($("#f-avatar").value || "").trim();
      const body = { botname };
      if (id) body.id = id;
      if (remark) body.remark = remark;
      if (avatar) body.avatar = avatar;

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
       <label for="f-remark">备注</label>
       <input id="f-remark" type="text" value="${esc(a.remark || "")}" placeholder="用于区分多个智能体" autocomplete="off" />
     </div>
     <div class="field">
       <label>智能体头像</label>
       ${avatarPickerHTML(a.avatar || "", "agent")}
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
      const remark = $("#f-remark").value.trim();
      const avatar = ($("#f-avatar").value || "").trim();

      await apiPost("agents/update", { id: a.id, botname, enable, remark, avatar });
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
