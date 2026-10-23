"""astrbot_plugin_plus 的持久化层（SQLite + SQLModel）。

表结构（见共享契约）：

* ``users``          —— 注册用户（邮箱 / 密码哈希 / 可用 agent 白名单 / 状态）；
* ``messages``       —— 消息漫游：用户与 agent、用户与用户之间的私聊历史；
* ``token_usage``    —— 每个用户的 token 累计用量（近似值，视图展示用）。

数据库文件位于插件数据目录 ``astrbot_plugin_plus.db``。所有读写都走
``aiosqlite`` 异步驱动，与 :mod:`plus_socket` 所在的事件循环绑定。
"""

from __future__ import annotations

import json
import re
import string
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncEngine
from sqlalchemy.pool import NullPool


class User(SQLModel, table=True):
    """注册用户。

    ``df`` 是用户的 32 位 hex 指纹（首次创建时生成），客户端据此长期识别
    同一个用户；``allowed_agents`` 是 JSON 序列化的 bot id 列表，只有列表里的
    agent 才可用；空数组 ``[]`` 表示「没有可用 agent」。
    """

    __tablename__ = "users"
    # 插件热重载时同一文件可能被以不同模块名再次导入；重复声明同名表时
    # 用 extend_existing 覆盖而不是抛 ``Table 'users' is already defined``。
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    username: str = ""
    password_hash: str = ""
    df: str = ""
    create_at: float = 0.0
    status: int = 1  # 1=正常 0=禁用
    allowed_agents: str = "[]"  # JSON array of bot id；"[]" = 无可用 agent
    avatar: str = ""  # 头像：data-URI 或图片 URL；空 = 内置默认头像
    ap_id: str = ""  # 对外唯一 ID（类 wxid）：ap_ + [A-Za-z0-9_]{6,15}


class Message(SQLModel, table=True):
    """一条漫游消息。

    ``peer_type`` 为 ``"agent"`` 或 ``"user"``：

    * agent 对话：``user_id`` = 发起用户，``peer_id`` = bot id，
      ``from_user_id`` = 用户消息时为用户 id，agent 回复时为 ``None``；
    * 私聊：每条物理消息写两行（收发双方各占一行自己的会话），
      ``user_id`` = 会话归属方，``peer_id`` = 对方 user id，
      ``from_user_id`` = 实际发送者。
    """

    __tablename__ = "messages"
    __table_args__ = {"extend_existing": True}

    id: int | None = Field(default=None, primary_key=True)
    peer_type: str = Field(index=True)  # "agent" | "user"
    user_id: int = Field(index=True)
    peer_id: str = ""
    from_user_id: int | None = None
    text: str = ""
    ts: float = 0.0


class TokenUsage(SQLModel, table=True):
    """用户 token 累计用量（近似值，视图展示用）。"""

    __tablename__ = "token_usage"
    __table_args__ = {"extend_existing": True}

    user_id: int = Field(primary_key=True)
    total_tokens: int = 0
    updated_at: float = 0.0


# --------------------------------------------------------------------------- #
# 引擎 / 会话工厂
# --------------------------------------------------------------------------- #
_engine: Any = None
_session_maker: async_sessionmaker[AsyncSession] | None = None


def init_db(db_path: str | Path) -> None:
    """初始化数据库引擎并建表。必须在插件启动、事件循环就绪前调用一次。"""
    global _engine, _session_maker
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{db_path}"
    # NullPool：不跨事件循环复用连接。插件热重载时旧事件循环已关闭，
    # 若复用池中绑定在旧循环上的 aiosqlite 连接会报「attached to a different loop」。
    _engine = create_async_engine(url, echo=False, future=True, poolclass=NullPool)
    _session_maker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def create_all() -> None:
    """建表（幂等）。在 Socket.io 服务端启动后、接受连接前调用。"""
    # create_all 在异步引擎上需要走 sync engine 的元数据创建；这里直接用
    # aiosqlite 连接执行 SQLModel.metadata.create_all。
    if _engine is None:
        raise RuntimeError("数据库引擎尚未初始化，请先调用 init_db()")
    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


def session() -> AsyncSession:
    """返回一个新的异步会话（调用方负责 close / commit）。"""
    assert _session_maker is not None  # noqa: S101
    return _session_maker()


# --------------------------------------------------------------------------- #
# 用户相关
# --------------------------------------------------------------------------- #
def new_user_df() -> str:
    """生成用户 32 位 hex 指纹。"""
    return uuid.uuid4().hex


def parse_allowed_agents(raw: str | None) -> list[str]:
    """把 ``users.allowed_agents`` 的 JSON 字符串解析成列表；空/异常返回 []。"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


def dumps_allowed_agents(ids: Iterable[str]) -> str:
    return json.dumps([str(x) for x in ids], ensure_ascii=False)


def user_public(u: User) -> dict[str, Any]:
    """对外暴露的用户字段（不含密码哈希）。"""
    return {
        "id": u.id,
        "email": u.email,
        "username": u.username,
        "df": u.df,
        "avatar": getattr(u, "avatar", "") or "",
        "ap_id": getattr(u, "ap_id", "") or "",
    }


# --------------------------------------------------------------------------- #
# 同步（sqlite3）管理端辅助：供 AstrBot WebUI 的 admin API 在主事件循环中调用。
# 这些是低频管理操作，直接开短连接即可；与异步引擎写同一个 SQLite 文件，
# SQLite 自身的文件锁能保证一致。
# --------------------------------------------------------------------------- #
import sqlite3  # noqa: E402

_DB_PATH: Path | None = None


def set_db_path(path: str | Path) -> None:
    global _DB_PATH
    _DB_PATH = Path(path)


def _sqlite() -> sqlite3.Connection:
    assert _DB_PATH is not None  # noqa: S101
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def admin_ensure_schema() -> None:
    """在 DB 文件上补齐表结构（幂等）。"""
    conn = _sqlite()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                username TEXT,
                password_hash TEXT,
                df TEXT,
                create_at REAL,
                status INTEGER DEFAULT 1,
                allowed_agents TEXT DEFAULT '[]',
                avatar TEXT DEFAULT '',
                ap_id TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                peer_type TEXT,
                user_id INTEGER,
                peer_id TEXT,
                from_user_id INTEGER,
                text TEXT,
                ts REAL
            );
            CREATE INDEX IF NOT EXISTS idx_msg_user ON messages(user_id);
            CREATE INDEX IF NOT EXISTS idx_msg_peer ON messages(peer_type, peer_id);
            CREATE TABLE IF NOT EXISTS token_usage (
                user_id INTEGER PRIMARY KEY,
                total_tokens INTEGER DEFAULT 0,
                updated_at REAL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
    _migrate_user_columns()


def _migrate_user_columns() -> None:
    """老库补 `avatar` / `ap_id` 列，并给历史用户补发 ap_id（幂等）。"""
    conn = _sqlite()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "avatar" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT ''")
        if "ap_id" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN ap_id TEXT")
        conn.commit()
        rows = conn.execute(
            "SELECT id FROM users WHERE ap_id IS NULL OR ap_id = ''"
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE users SET ap_id=? WHERE id=?",
                (generate_ap_id(conn), r["id"]),
            )
        if rows:
            conn.commit()
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ap_id ON users(ap_id)"
        )
        conn.commit()
    finally:
        conn.close()


AP_ID_ALPHABET = string.ascii_letters + string.digits + "_"


def generate_ap_id(conn: sqlite3.Connection | None = None, length: int = 10) -> str:
    """生成唯一的 ap_id：`ap_` + 6~15 位 [A-Za-z0-9_]。"""
    import secrets

    length = max(6, min(15, int(length or 10)))
    for _ in range(200):
        candidate = "ap_" + "".join(secrets.choice(AP_ID_ALPHABET) for _ in range(length))
        if conn is None:
            return candidate
        hit = conn.execute(
            "SELECT 1 FROM users WHERE ap_id=? LIMIT 1", (candidate,)
        ).fetchone()
        if not hit:
            return candidate
    # 极端兜底：加时间戳后缀
    return f"ap_{int(time.time() * 1000) % 10**12:012d}"[:18]


AP_ID_PATTERN = re.compile(r"^ap_[A-Za-z0-9_]{6,15}$")


def normalize_ap_id(value: Any) -> str | None:
    """校验 ap_id 格式：`ap_` + 6~15 位 [A-Za-z0-9_]。

    返回规范化后的字符串；空值返回 ""；格式非法返回 None。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not AP_ID_PATTERN.match(raw):
        return None
    return raw


def admin_list_users() -> list[dict[str, Any]]:
    conn = _sqlite()
    try:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def admin_get_user(uid: int) -> dict[str, Any] | None:
    conn = _sqlite()
    try:
        r = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def admin_find_user_by_email(email: str) -> dict[str, Any] | None:
    """按邮箱查用户（管理端改资料时做唯一性校验）。"""
    conn = _sqlite()
    try:
        r = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def admin_find_user_by_ap_id(ap_id: str) -> dict[str, Any] | None:
    """按 ap_id 查用户（管理端唯一性校验）。"""
    conn = _sqlite()
    try:
        r = conn.execute("SELECT * FROM users WHERE ap_id=?", (ap_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def admin_create_user(
    email: str,
    username: str,
    password_hash: str,
    allowed_agents: str = "[]",
    avatar: str = "",
    ap_id: str | None = None,
) -> dict[str, Any]:
    now = time.time()
    df = new_user_df()
    conn = _sqlite()
    try:
        # ap_id 唯一：未指定或冲突时自动生成
        final_ap_id = (ap_id or "").strip() or generate_ap_id(conn)
        if conn.execute(
            "SELECT 1 FROM users WHERE ap_id=? LIMIT 1", (final_ap_id,)
        ).fetchone():
            final_ap_id = generate_ap_id(conn)
        cur = conn.execute(
            "INSERT INTO users(email,username,password_hash,df,create_at,status,allowed_agents,avatar,ap_id)"
            " VALUES(?,?,?,?,?,1,?,?,?)",
            (email, username, password_hash, df, now, allowed_agents, avatar, final_ap_id),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "email": email,
            "username": username,
            "df": df,
            "create_at": now,
            "status": 1,
            "allowed_agents": allowed_agents,
            "avatar": avatar,
            "ap_id": final_ap_id,
        }
    finally:
        conn.close()


def admin_update_user(uid: int, fields: dict[str, Any]) -> None:
    allowed = {
        "email",
        "username",
        "password_hash",
        "status",
        "allowed_agents",
        "avatar",
        "ap_id",
    }
    sets = [f"{k}=?" for k in fields if k in allowed]
    if not sets:
        return
    vals = [fields[k] for k in fields if k in allowed]
    vals.append(uid)
    conn = _sqlite()
    try:
        conn.execute(f"UPDATE users SET {','.join(sets)} WHERE id=?", vals)
        conn.commit()
    finally:
        conn.close()


def admin_delete_user(uid: int) -> None:
    conn = _sqlite()
    try:
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
        conn.execute("DELETE FROM messages WHERE user_id=?", (uid,))
        conn.commit()
    finally:
        conn.close()


def admin_today_message_count() -> int:
    day_start = time.time() - (time.time() % 86400)  # UTC 日界，粗粒度即可
    conn = _sqlite()
    try:
        r = conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE ts>=?", (day_start,)
        ).fetchone()
        return int(r["c"] or 0)
    finally:
        conn.close()


def admin_token_usage(uid: int) -> int:
    conn = _sqlite()
    try:
        r = conn.execute(
            "SELECT total_tokens FROM token_usage WHERE user_id=?", (uid,)
        ).fetchone()
        return int(r["total_tokens"] or 0) if r else 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# token 用量
# --------------------------------------------------------------------------- #
async def add_token_usage(user_id: int, tokens: int) -> None:
    """累加用户真实 token 用量（来自 LLM 响应的 usage 统计）。

    旧实现按「回复字符数」累加，既漏掉 prompt 侧的输入 token，也漏掉工具调用
    与多轮内部请求，导致 AstrBot 面板已统计到几千时插件里只有十几个。这里改为
    直接接收调用方给出的真实 token 数。
    """
    try:
        tokens = int(tokens or 0)
    except (TypeError, ValueError):
        return
    if tokens <= 0:
        return
    async with session() as s:
        from sqlmodel import select

        row = (await s.exec(select(TokenUsage).where(TokenUsage.user_id == user_id))).first()
        now = time.time()
        if row is None:
            s.add(TokenUsage(user_id=user_id, total_tokens=tokens, updated_at=now))
        else:
            row.total_tokens = int(row.total_tokens or 0) + tokens
            row.updated_at = now
        await s.commit()


# 兼容旧调用名：仍然接收「token 数」，不再做字符估算。
async def bump_token_usage(user_id: int, tokens: int) -> None:
    await add_token_usage(user_id, tokens)
