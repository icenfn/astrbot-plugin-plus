"""astrbot_plugin_plus 本地自测脚本（不依赖真实 AstrBot）。

用法：
    python3 tests/selftest.py

它会：
1. stub 掉 astrbot.api 相关 import；
2. 在 127.0.0.1:16199 启动 PlusSocketServer；
3. 走完登录 / 鉴权 / 用户列表 / 私聊 / 漫游 / 无 token 拒连 等流程。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time
import types
from pathlib import Path

# ---- 1. stub astrbot.api ----------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("selftest")

fake_astrbot = types.ModuleType("astrbot")
fake_api = types.ModuleType("astrbot.api")
fake_api.logger = logger
fake_astrbot.api = fake_api
sys.modules["astrbot"] = fake_astrbot
sys.modules["astrbot.api"] = fake_api

import bcrypt  # noqa: E402
import socketio  # noqa: E402
import urllib.request  # noqa: E402
import urllib.error  # noqa: E402

import plus_db as db  # noqa: E402
from plus_socket import PlusSocketServer  # noqa: E402


def http(method: str, url: str, body: dict | None = None, token: str | None = None):
    data = None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        import json
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="aplus_selftest_")
    logger.info("data_dir = %s", tmp)
    srv = PlusSocketServer(
        host="127.0.0.1", port=16199, data_dir=tmp, version="0.9.0-test"
    )
    srv.start()
    # 等待服务起来
    for _ in range(40):
        try:
            st, body = http("GET", "http://127.0.0.1:16199/health")
            if st == 200:
                break
        except Exception:
            time.sleep(0.2)
    else:
        logger.error("服务未起来")
        return 1
    logger.info("health: %s %s", st, body)

    BASE = "http://127.0.0.1:16199"
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = ""):
        mark = "PASS" if cond else "FAIL"
        logger.info("[%s] %s %s", mark, name, detail)
        if not cond:
            failures.append(name)

    # 2. 未注册用户登录 -> 401
    st, body = http("POST", f"{BASE}/api/auth/login",
                    {"email": "nobody@test.com", "password": "x"})
    check("login unknown -> 401", st == 401, f"got {st} {body}")

    # 3. 直接 db 建两个用户
    def mkuser(email: str, username: str, pw: str) -> dict:
        h = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        return db.admin_create_user(email, username, h)

    alice = mkuser("alice@test.com", "alice", "test1234")
    bob = mkuser("bob@test.com", "bob", "test1234")
    logger.info("created alice=%s bob=%s", alice["id"], bob["id"])

    # 4. alice login
    st, body = http("POST", f"{BASE}/api/auth/login",
                    {"email": "alice@test.com", "password": "test1234"})
    check("alice login 200", st == 200, f"got {st}")
    import json
    alice_token = json.loads(body)["token"]

    # 5. /api/agents with alice token
    st, body = http("GET", f"{BASE}/api/agents", token=alice_token)
    check("GET /api/agents not 401", st == 200, f"got {st} {body}")

    # 6. /api/users with alice token -> 看到 bob
    st, body = http("GET", f"{BASE}/api/users", token=alice_token)
    check("GET /api/users 200", st == 200, f"got {st}")
    users = json.loads(body)
    check("alice sees bob", any(u["email"] == "bob@test.com" for u in users),
          f"users={users}")

    # 7. socket.io 连接（alice / bob）
    acli = socketio.Client()
    bcli = socketio.Client()
    bob_msgs: list[dict] = []
    bcli.on("private:message", lambda d: bob_msgs.append(d))

    acli.connect(BASE, auth={"token": alice_token}, wait_timeout=5)
    st, body = http("POST", f"{BASE}/api/auth/login",
                    {"email": "bob@test.com", "password": "test1234"})
    bob_token = json.loads(body)["token"]
    bcli.connect(BASE, auth={"token": bob_token}, wait_timeout=5)
    time.sleep(0.3)

    # 无 token 连接应被拒
    bad = socketio.Client()
    rejected_ok = False
    try:
        bad.connect(BASE, auth={"token": "bad"}, wait_timeout=2)
        time.sleep(0.3)
    except Exception:
        rejected_ok = True
    check("bad token socket rejected", rejected_ok or not bad.connected,
          f"connected={bad.connected}")

    # 8. alice 通过 private:send 给 bob 发 "hello bob"
    acli.emit("private:send", {
        "reqId": "r1",
        "toUserId": bob["id"],
        "text": "hello bob",
    })
    # 等 bob 收到
    for _ in range(30):
        if bob_msgs:
            break
        time.sleep(0.1)
    check("bob received private:message",
          len(bob_msgs) > 0 and bob_msgs[0].get("text") == "hello bob",
          f"bob_msgs={bob_msgs}")

    # 9. 漫游：alice 拉 peerType=user&peerId=bob_id
    time.sleep(0.2)
    st, body = http("GET",
                    f"{BASE}/api/messages?peerType=user&peerId={bob['id']}&limit=50",
                    token=alice_token)
    check("GET /api/messages 200", st == 200, f"got {st} {body}")
    msgs = json.loads(body)
    check("alice sees 'hello bob' in history",
          any(m.get("text") == "hello bob" for m in msgs),
          f"msgs={msgs}")

    # bob 侧也应能看到
    st, body = http("GET",
                    f"{BASE}/api/messages?peerType=user&peerId={alice['id']}&limit=50",
                    token=bob_token)
    msgs_bob = json.loads(body)
    check("bob sees 'hello bob' in history",
          any(m.get("text") == "hello bob" for m in msgs_bob),
          f"msgs_bob={msgs_bob}")

    # 无 token 访问 /api/users -> 401
    st, body = http("GET", f"{BASE}/api/users")
    check("GET /api/users no token -> 401", st == 401, f"got {st}")

    acli.disconnect()
    bcli.disconnect()
    srv.stop()

    print("\n==== 结果汇总 ====")
    if failures:
        print("FAILED:", failures)
        return 1
    print("ALL PASS ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
