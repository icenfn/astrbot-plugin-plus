// AstrBot+ 机器人管理视图：通过 window.AstrBotPluginView bridge 调用插件后端 API。
const bridge = window.AstrBotPluginView;

const els = {
  status: document.getElementById("status"),
  count: document.getElementById("count"),
  list: document.getElementById("bot-list"),
  empty: document.getElementById("empty"),
  form: document.getElementById("create-form"),
  newId: document.getElementById("new-id"),
  newNickname: document.getElementById("new-nickname"),
  newEnable: document.getElementById("new-enable"),
  refresh: document.getElementById("refresh"),
  rowTpl: document.getElementById("bot-row"),
};

let busy = false;

function setStatus(text, kind) {
  els.status.textContent = text || "";
  els.status.dataset.kind = kind || "";
}

function setBusy(value) {
  busy = value;
  els.form.querySelector("button[type=submit]").disabled = value;
  els.refresh.disabled = value;
}

function extractError(err) {
  if (!err) return "操作失败";
  return err.message || String(err);
}

async function loadBots() {
  setBusy(true);
  try {
    const data = await bridge.apiGet("bots");
    renderBots((data && data.bots) || []);
    setStatus(`已加载 ${((data && data.bots) || []).length} 个机器人`, "ok");
  } catch (err) {
    setStatus(`加载失败：${extractError(err)}`, "error");
  } finally {
    setBusy(false);
  }
}

function renderBots(bots) {
  els.list.textContent = "";
  els.count.textContent = String(bots.length);
  els.empty.hidden = bots.length > 0;
  bots.forEach((bot) => els.list.appendChild(buildRow(bot)));
}

function buildRow(bot) {
  const node = els.rowTpl.content.firstElementChild.cloneNode(true);
  node.dataset.id = bot.id;
  node.querySelector(".name").textContent = bot.nickname || bot.id;
  node.querySelector(".pid").textContent = bot.id;

  const state = node.querySelector(".state");
  const on = !!bot.enable;
  state.textContent = on ? "已启用" : "已停用";
  state.classList.toggle("on", on);
  state.classList.toggle("off", !on);

  const toggle = node.querySelector(".toggle");
  toggle.textContent = on ? "停用" : "启用";
  toggle.addEventListener("click", () => toggleBot(bot.id, !on));

  node.querySelector(".del").addEventListener("click", () => deleteBot(bot));

  return node;
}

async function createBot(event) {
  event.preventDefault();
  if (busy) return;
  const id = els.newId.value.trim();
  if (!id) {
    setStatus("请填写机器人 ID", "error");
    return;
  }
  setBusy(true);
  try {
    await bridge.apiPost("bots/create", {
      id,
      nickname: els.newNickname.value.trim(),
      enable: els.newEnable.checked,
    });
    els.form.reset();
    els.newEnable.checked = true;
    setStatus(`机器人 ${id} 创建成功`, "ok");
    await loadBots();
  } catch (err) {
    setStatus(`创建失败：${extractError(err)}`, "error");
    setBusy(false);
  }
}

async function toggleBot(id, enable) {
  if (busy) return;
  setBusy(true);
  try {
    await bridge.apiPost("bots/toggle", { id, enable });
    setStatus(`机器人 ${id} 已${enable ? "启用" : "停用"}`, "ok");
    await loadBots();
  } catch (err) {
    setStatus(`操作失败：${extractError(err)}`, "error");
    setBusy(false);
  }
}

async function deleteBot(bot) {
  if (busy) return;
  const label = bot.nickname || bot.id;
  if (!window.confirm(`确定删除机器人「${label}」吗？此操作不可撤销。`)) return;
  setBusy(true);
  try {
    await bridge.apiPost("bots/delete", { id: bot.id });
    setStatus(`机器人 ${bot.id} 已删除`, "ok");
    await loadBots();
  } catch (err) {
    setStatus(`删除失败：${extractError(err)}`, "error");
    setBusy(false);
  }
}

els.form.addEventListener("submit", createBot);
els.refresh.addEventListener("click", loadBots);

(async function init() {
  try {
    await bridge.ready();
  } catch (_) {
    /* bridge 上下文非必需 */
  }
  await loadBots();
})();
