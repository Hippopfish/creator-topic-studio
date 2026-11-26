"""优雅关闭工作台：读 runtime/server.json 拿真实端口，再调 /api/shutdown。"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SERVER = BASE / "runtime" / "server.json"


def main() -> int:
    if not SERVER.exists():
        print("服务没有在运行（找不到 server.json）")
        return 0
    try:
        port = int(json.loads(SERVER.read_text(encoding="utf-8")).get("port") or 0)
    except Exception as e:
        print(f"读取 server.json 失败：{e}")
        return 1
    if not port:
        print("server.json 里没有端口")
        return 1

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown", method="POST", data=b"{}",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            print(f"已发送关闭指令（端口 {port}，HTTP {r.status}）")
    except Exception as e:
        print(f"服务可能已经停了（{type(e).__name__}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
