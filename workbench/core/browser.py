"""专用 Chrome 的启停与 CDP 探活。

为什么必须专用：
    MediaCrawler 收尾时无条件 browser_context.close()，
    如果连的是日常浏览器，会把正在用的窗口一起关掉。
所以固定用 browser_data/cdp_{platform} 这套独立 user-data-dir。

为什么要分角色：
    own   = 自己的号，只读创作者后台，不爬
    crawl = 采集号，专门爬别人内容，风控风险落在这里
    两套 user-data-dir 各自独立 Cookie，互不可见；同一时刻只开一个。
"""
from __future__ import annotations

import json
import subprocess
import time

import httpx

from . import paths, store

CREATE_NO_WINDOW = 0x08000000


def port_for(role: str = "crawl") -> int:
    """按角色取调试端口。settings 里没有该角色时回退到 cdpPort。"""
    s = store.settings()["crawler"]
    by_role = s.get("cdpPortByRole") or {}
    if role in by_role:
        try:
            return int(by_role[role])
        except (TypeError, ValueError):
            pass
    return int(s["cdpPort"])


def _other_role(role: str) -> str:
    return "own" if role == "crawl" else "crawl"


def cdp_alive(port: int | None = None) -> bool:
    port = port or int(store.settings()["crawler"]["cdpPort"])
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1.0)
        return r.status_code == 200 and "webSocketDebuggerUrl" in r.text
    except Exception:
        return False


def pid_on_port(port: int) -> int | None:
    """从 netstat 里找 LISTENING 在该端口的进程号。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, encoding="mbcs", errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=10,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local, state, pid = parts[1], parts[3], parts[4]
        if state.upper() == "LISTENING" and local.endswith(f":{port}"):
            try:
                return int(pid)
            except ValueError:
                continue
    return None


def status(platform: str = "xhs", role: str = "crawl") -> dict:
    s = store.settings()["crawler"]
    port = port_for(role)
    other = _other_role(role)
    other_port = port_for(other)
    return {
        "running": cdp_alive(port),
        "port": port,
        "pid": pid_on_port(port),
        "role": role,
        "roleName": paths.ROLE_NAMES.get(role, role),
        "profileDir": str(paths.profile_dir(platform, role)),
        "chromePath": s["chromePath"],
        # 另一个号是否也开着——两个同时在线是关联信号，界面要提示关掉
        "other": {
            "role": other,
            "roleName": paths.ROLE_NAMES.get(other, other),
            "port": other_port,
            "running": cdp_alive(other_port),
            "profileDir": str(paths.profile_dir(platform, other)),
        },
    }


def launch(platform: str = "xhs", role: str = "crawl",
           open_url: bool = True, wait: float = 25.0) -> dict:
    """拉起指定角色的专用 Chrome。已在本角色运行时直接返回。

    切角色前必须关掉另一个号：同 IP 双号同时在线容易被关联。
    """
    s = store.settings()["crawler"]
    port = port_for(role)
    if cdp_alive(port):
        return {"ok": True, "already": True, **status(platform, role)}

    # 另一个号开着就先拒掉，不静默关它（用户可能正在里面操作）
    other = _other_role(role)
    if cdp_alive(port_for(other)):
        return {
            "ok": False,
            "code": "OTHER_ROLE_RUNNING",
            "message": f"「{paths.ROLE_NAMES.get(other, other)}」的浏览器还开着。"
                       f"两个号同时在线容易被平台关联，请先关掉它再启动本号。",
            "other": other,
        }

    chrome = str(s.get("chromePath") or "")
    if not chrome:
        return {"ok": False, "message": "未配置 Chrome 路径"}
    from pathlib import Path

    if not Path(chrome).exists():
        return {"ok": False, "message": f"找不到 Chrome：{chrome}"}

    profile = paths.profile_dir(platform, role)
    profile.mkdir(parents=True, exist_ok=True)

    # 只清掉本角色的残留进程，绝不碰另一个号
    _kill_by_profile(profile)

    url = paths.PLATFORM_HOME.get(platform, paths.PLATFORM_HOME["xhs"])
    cmd = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
    ]
    if open_url:
        cmd.append(url)

    try:
        subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        return {"ok": False, "message": f"启动失败：{e}"}

    deadline = time.time() + wait
    while time.time() < deadline:
        if cdp_alive(port):
            return {"ok": True, "already": False, **status(platform, role)}
        time.sleep(0.4)
    return {"ok": False, "message": f"Chrome 已启动但 {port} 端口在 {int(wait)} 秒内没就绪"}


def close(role: str = "crawl") -> dict:
    port = port_for(role)
    pid = pid_on_port(port)
    if not pid:
        return {"ok": True, "message": "没有在运行的专用浏览器"}
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15,
        )
    except Exception as e:
        return {"ok": False, "message": str(e)}
    time.sleep(1.0)
    return {"ok": not cdp_alive(port), "message": "已关闭专用浏览器"}


def _list_chrome() -> list[dict]:
    """列出所有 chrome 进程的 {pid, cmdline}。拿不到就返回空列表。"""
    try:
        out = subprocess.run(
            # 先强制 PowerShell 用 UTF-8 输出：路径里有中文（AI底盒），
            # 不设的话 Python 按 UTF-8 解 GBK 字节，中文全变乱码，路径比对永远失败。
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=20,
        ).stdout.strip()
    except Exception:
        return []
    if not out:
        return []
    try:
        data = json.loads(out.lstrip("\ufeff"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    out_list = []
    for p in data:
        if not isinstance(p, dict):
            continue
        out_list.append({"pid": p.get("ProcessId"),
                         "cmdline": p.get("CommandLine") or ""})
    return out_list


def _user_data_dir_of(cmdline: str) -> str:
    """从命令行里取出 --user-data-dir 的值。带引号的按引号取，避免空格截断。"""
    import re
    m = re.search(r'--user-data-dir=(?:"([^"]*)"|(\S+))', cmdline or "")
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def _norm(p: str) -> str:
    return (p or "").replace("/", "\\").rstrip("\\").lower()


def _kill_by_profile(profile) -> None:
    """只清掉用同一 user-data-dir 的残留进程。

    旧实现按 '*cdp_*' 前缀全杀，会把另一个号的浏览器一起杀掉。
    这里改成比对 --user-data-dir：完整路径优先，路径因编码对不上时
    退一步比目录名（cdp_xhs / cdp_xhs_crawl 不同名，不会互相误伤）。
    """
    target = _norm(str(profile))
    target_base = target.rsplit("\\", 1)[-1]
    for proc in _list_chrome():
        udd = _norm(_user_data_dir_of(proc["cmdline"]))
        if not udd:
            continue
        if udd != target and udd.rsplit("\\", 1)[-1] != target_base:
            continue
        pid = str(proc.get("pid") or "").strip()
        if not pid.isdigit():
            continue
        try:
            subprocess.run(
                ["taskkill", "/PID", pid, "/T", "/F"],
                capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=10,
            )
        except Exception:
            pass
