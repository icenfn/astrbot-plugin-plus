"""astrbot_plugin_plus 管理 API 自测（不依赖真实 AstrBot）。

用法：
    python3 tests/admin_api_test.py

覆盖 ``/{PLUGIN_NAME}/admin/*`` 九条路由的注册与行为：
  GET  stats / users / agents
  POST users/create | update | delete
  POST agents/create | update | delete

设计要点：
* stub 掉 ``astrbot.api`` 家族，避免引入真实 AstrBot 运行时；
* 用 ``object.__new__`` 绕过 ``__init__``，不启动 IM 端口；
* 用 ``db.init_db`` 指向临时 SQLite，不污染环境；
* 用假 request 模拟 ``await request.json()``。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("admin_api_test")

# ---------------------------------------------------------------- 1. stub astrbot
fake_astrbot = types.ModuleType("astrbot")
fake_api = types.ModuleType("astrbot.api")
fake_api.logger = logger

fake_event = types.ModuleType("astrbot.api.event")


class AstrMessageEvent:  # noqa: D101
    pass


class _AnyFilter:
    """`filter.command` / `filter.on_xxx` 之类的装饰器占位。"""

    def __getattr__(self, _name: str):
        return lambda *a, **k: (lambda fn: fn)


fake_event.AstrMessageEvent = AstrMessageEvent
fake_event.filter = _AnyFilter()

fake_star = types.ModuleType("astrbot.api.star")


class Context:  # noqa: D101
    pass


class Star:  # noqa: D101
    def __init__(self, context=None):
        self.context = context


def register(*_a, **_k):
    def deco(cls):
        return cls

    return deco


fake_star.Context = Context
fake_star.Star = Star
fake_star.register = register

fake_web = types.ModuleType("astrbot.api.web")
fake_web.error_response = None
fake_web.json_response = None
fake_web.request = None

fake_astrbot.api = fake_api
fake_api.event = fake_event
fake_api.star = fake_star
fake_api.web = fake_web

sys.modules["astrbot"] = fake_astrbot
sys.modules["astrbot.api"] = fake_api
sys.modules["astrbot.api.event"] = fake_event
sys.modules["astrbot.api.star"] = fake_star
sys.modules["astrbot.api.web"] = fake_web

import plus_db as db  # noqa: E402
import main as plugin_mod  # noqa: E402


# ------------------------------------------------------------------ 2. 测试替身
class FakeConfig(dict):
    """模拟 AstrBot 全局配置：既是 dict，又带保存方法。"""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.save_calls = 0

    async def save_config_async(self):
        self.save_calls += 1


class FakeContext:
    def __init__(self, config: FakeConfig):
        self._config = config
        self.routes: list[tuple] = []

    def get_config(self):
        return self._config

    def register_web_api(self, route, view_handler, methods, desc):
        # 官方签名：register_web_api(route, view_handler, methods, desc)
        self.routes.append((route, view_handler, tuple(methods), desc))


class FakeRequest:
    def __init__(self, payload: dict | None = None):
        self.payload = payload or {}

    async def json(self, default=None):
        return self.payload


def make_plugin(config: FakeConfig) -> plugin_mod.AstrBotPlusPlugin:
    """绕过 __init__（不启动 IM 端口），只装测试所需属性。"""
    obj = object.__new__(plugin_mod.AstrBotPlusPlugin)
    obj.context = FakeContext(config)
    obj._socket = None
    obj._rescue_task = None
    return obj


async def call(plugin, handler_name: str, payload: dict | None = None):
    """设置假请求体并调用 handler。"""
    plugin_mod.request = FakeRequest(payload)
    try:
        return await getattr(plugin, handler_name)()
    finally:
        plugin_mod.request = None


def is_error(res) -> bool:
    return isinstance(res, dict) and res.get("status") == "error"


def data(res):
    """兼容两种返回：AstrBot 信封 {status,data} 或直接返回业务 dict/list。"""
    if isinstance(res, dict) and "data" in res and "status" in res:
        return res.get("data")
    return res


# ---------------------------------------------------------------------- 3. 用例
async def run() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = ""):
        logger.info("[%s] %s %s", "PASS" if cond else "FAIL", name, detail)
        if not cond:
            failures.append(name)

    # ---- 隔离数据库 ----
    tmpdir = Path(tempfile.mkdtemp(prefix="aplus_admin_test_"))
    # 管理端走 sqlite3 同步短连接，路径由 set_db_path 指定；
    # init_db 负责异步引擎（本测试不涉及），两者指向同一个库文件。
    db_path = tmpdir / "test.db"
    db.set_db_path(db_path)
    db.admin_ensure_schema()

    config = FakeConfig({"platform": []})
    plugin = make_plugin(config)

    # ---- 路由注册 ----
    plugin._register_admin_routes()
    routes = plugin.context.routes
    check("register 9 routes", len(routes) == 9, f"got {len(routes)}")
    check(
        "route prefix",
        all(r[0].startswith(f"/{plugin_mod.PLUGIN_NAME}/admin") for r in routes),
        str([r[0] for r in routes][:3]),
    )
    check(
        "route methods are GET/POST only",
        all(set(r[2]) <= {"GET", "POST"} for r in routes),
        str({r[2] for r in routes}),
    )
    check(
        "writes use POST, reads use GET",
        {r[0].rsplit("/", 1)[-1] for r in routes if "GET" in r[2]} == {"stats", "users", "agents"},
    )

    # ---- stats（空库） ----
    res = await call(plugin, "_h_admin_stats")
    st = data(res)
    check("stats empty users=0", st.get("users") == 0, str(st))
    check("stats has version", bool(st.get("version")), str(st.get("version")))

    # ---- 创建用户：缺字段 / 邮箱非法 / 密码过短 ----
    res = await call(plugin, "_h_admin_create_user", {})
    check("create user missing -> error", is_error(res), str(res))
    res = await call(plugin, "_h_admin_create_user", {"email": "bad", "password": "123456"})
    check("create user bad email -> error", is_error(res), str(res))
    res = await call(plugin, "_h_admin_create_user", {"email": "a@b.com", "password": "123"})
    check("create user short pw -> error", is_error(res), str(res))

    # ---- 创建用户：成功，返回快连链接且不回传哈希 ----
    res = await call(
        plugin,
        "_h_admin_create_user",
        {"email": "Alice@Test.com", "username": "alice", "password": "test1234"},
    )
    created = data(res)
    check("create user ok", isinstance(created, dict) and "user" in created, str(res)[:160])
    check(
        "email normalized to lowercase",
        created["user"]["email"] == "alice@test.com",
        created["user"]["email"],
    )
    check("quickLink present", str(created.get("quickLink")).startswith("ap://"), created.get("quickLink"))
    check("no password hash leaked", "password_hash" not in created["user"])

    uid = created["user"]["id"]

    # ---- 重复邮箱 ----
    res = await call(
        plugin, "_h_admin_create_user", {"email": "alice@test.com", "password": "test1234"}
    )
    check("duplicate email -> error", is_error(res), str(res))

    # ---- 列表：允许智能体被解析为数组，不含哈希 ----
    res = await call(plugin, "_h_admin_list_users")
    rows = data(res)
    check("list users len=1", isinstance(rows, list) and len(rows) == 1, str(rows)[:160])
    check("allowed_agents is list", isinstance(rows[0].get("allowed_agents"), list))
    check("list users hides hash", "password_hash" not in rows[0])
    check("tokenUsage present", rows[0].get("tokenUsage") == 0, str(rows[0].get("tokenUsage")))

    # ---- 更新用户：改用户名 + 禁用 ----
    res = await call(plugin, "_h_admin_update_user", {"id": uid, "username": "alice2", "status": 0})
    check("update user ok", data(res).get("updated") == 1, str(res))
    row = db.admin_get_user(uid)
    check("username updated", row["username"] == "alice2", row["username"])
    check("status disabled", int(row["status"]) == 0, str(row["status"]))

    # ---- 更新用户：密码过短被拒 ----
    res = await call(plugin, "_h_admin_update_user", {"id": uid, "password": "123"})
    check("update short pw -> error", is_error(res), str(res))

    # ---- 更新用户：邮箱冲突 ----
    await call(
        plugin, "_h_admin_create_user", {"email": "other@test.com", "password": "test1234"}
    )
    other = db.admin_find_user_by_email("other@test.com")
    res = await call(plugin, "_h_admin_update_user", {"id": uid, "email": "other@test.com"})
    check("update to taken email -> error", is_error(res), str(res))

    # ---- 更新用户：不存在的 id ----
    res = await call(plugin, "_h_admin_update_user", {"id": 99999, "username": "ghost"})
    check("update unknown user -> error", is_error(res), str(res))
    res = await call(plugin, "_h_admin_update_user", {})
    check("update without id -> error", is_error(res), str(res))

    # ---- 智能体：新建 ----
    res = await call(plugin, "_h_admin_create_agent", {"botname": "助手A", "id": "botA"})
    agent = data(res)
    check("create agent ok", isinstance(agent, dict) and agent.get("id") == "botA", str(res)[:160])
    check("agent type is adapter", agent.get("type") == "astrbot_plus", str(agent.get("type")))
    check("agent botdf 32 chars", len(str(agent.get("botdf"))) == 32, str(agent.get("botdf")))
    check("agent written to config", len(config["platform"]) == 1, str(config["platform"]))
    check("config saved", config.save_calls >= 1, str(config.save_calls))

    # ---- 智能体：重名 id ----
    res = await call(plugin, "_h_admin_create_agent", {"botname": "重复", "id": "botA"})
    check("duplicate agent id -> error", is_error(res), str(res))
    # ---- 智能体：缺 botname ----
    res = await call(plugin, "_h_admin_create_agent", {"id": "botB"})
    check("agent missing botname -> error", is_error(res), str(res))

    # ---- 智能体：列表 ----
    res = await call(plugin, "_h_admin_list_agents")
    agents = data(res)
    check("list agents len=1", isinstance(agents, list) and len(agents) == 1, str(agents))
    check("agent enable default true", agents[0]["enable"] is True)

    # ---- 智能体：改名 + 停用 ----
    res = await call(plugin, "_h_admin_update_agent", {"id": "botA", "botname": "助手B", "enable": False})
    check("update agent ok", data(res).get("botname") == "助手B", str(res)[:160])
    check("agent disabled", data(res).get("enable") is False, str(data(res).get("enable")))
    check("agent persisted", config["platform"][0]["botname"] == "助手B", str(config["platform"][0]))

    # ---- 智能体：不存在 ----
    res = await call(plugin, "_h_admin_update_agent", {"id": "nope", "botname": "x"})
    check("update unknown agent -> error", is_error(res), str(res))
    res = await call(plugin, "_h_admin_update_agent", {})
    check("update agent without id -> error", is_error(res), str(res))

    # ---- 智能体：删除 ----
    res = await call(plugin, "_h_admin_delete_agent", {"id": "botA"})
    check("delete agent ok", data(res).get("deleted") == 1, str(res))
    check("agent removed from config", len(config["platform"]) == 0, str(config["platform"]))
    res = await call(plugin, "_h_admin_delete_agent", {"id": "botA"})
    check("delete unknown agent -> error", is_error(res), str(res))

    # ---- stats 反映真实数量 ----
    res = await call(plugin, "_h_admin_stats")
    st = data(res)
    check("stats users=2", st.get("users") == 2, str(st))
    check("stats agents=0", st.get("agents") == 0, str(st))
    check("stats online=0 (no socket)", st.get("online") == 0, str(st))

    # ---- 删除用户 ----
    res = await call(plugin, "_h_admin_delete_user", {"id": uid})
    check("delete user ok", data(res).get("deleted") == 1, str(res))
    check("user gone", db.admin_get_user(uid) is None)
    res = await call(plugin, "_h_admin_delete_user", {"id": uid})
    check("delete unknown user -> error", is_error(res), str(res))
    res = await call(plugin, "_h_admin_delete_user", {})
    check("delete user without id -> error", is_error(res), str(res))

    print()
    if failures:
        print(f"==== 失败 {len(failures)} 项 ====")
        for f in failures:
            print(" -", f)
        return 1
    print("==== ALL PASS ✅ ====")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
