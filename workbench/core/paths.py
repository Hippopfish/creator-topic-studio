"""全部路径常量。整个工作台只有这里硬编码路径，其余文件一律 import 本模块。"""
from __future__ import annotations

from pathlib import Path

# workbench/core/paths.py -> workbench/
ROOT = Path(__file__).resolve().parent.parent
# AI-小红书MBTI/
BASE = ROOT.parent

# --- 工作台自身 ---
CORE_DIR = ROOT / "core"
PROMPTS_DIR = ROOT / "prompts"
WEB_DIR = ROOT / "web"
BIN_DIR = ROOT / "bin"

# --- 数据 ---
DATA_DIR = ROOT / "data"
CONTENTS_DIR = DATA_DIR / "contents"
COMMENTS_DIR = DATA_DIR / "comments"
# 自己的账号：与赛道样本分开存，绝不混库（否则选题会拿自己的旧作当竞品）
OWN_DIR = DATA_DIR / "own"
# 档案台的三份产物（风格包 / 空间记忆 / CreatorProfile）
PROFILE_DIR = DATA_DIR / "profile"
BACKUP_DIR = DATA_DIR / "backups"
JOURNAL_FILE = DATA_DIR / "_journal.ndjson"

# --- 运行期 ---
RUNTIME_DIR = ROOT / "runtime"
JOBS_DIR = RUNTIME_DIR / "jobs"
SERVER_FILE = RUNTIME_DIR / "server.json"
LOGS_DIR = ROOT / "logs"
RAW_DIR = ROOT / "data_raw"

# --- 外部依赖 ---
CRAWLER_DIR = BASE / "MediaCrawler"
CRAWLER_MAIN = CRAWLER_DIR / "main.py"
VENV_PY = CRAWLER_DIR / ".venv" / "Scripts" / "python.exe"
VENV_PYW = CRAWLER_DIR / ".venv" / "Scripts" / "pythonw.exe"

# --- 浏览器登录态（专用，绝不碰日常浏览器） ---
BROWSER_DATA = BASE / "browser_data"
# 兼容旧调用：默认目录 = 这个平台的 own（自己的号）
PROFILE_DIRS = {
    "xhs": BROWSER_DATA / "cdp_xhs",
    "dy": BROWSER_DATA / "cdp_dy",
    "bili": BROWSER_DATA / "cdp_bili",
}

# 按角色分两套，各自独立的 Cookie 罐，互不可见。
#   own   = 自己的号，只用来读创作者后台（只读接口，不爬）
#   crawl = 采集号，专门爬别人的内容（风控风险落在这里）
# 同一时刻只开一个：同 IP 双号同时在线反而是关联信号。
PROFILE_DIRS_BY_ROLE = {
    "xhs": {
        "own": BROWSER_DATA / "cdp_xhs",           # 大号登录态在此，不动
        "crawl": BROWSER_DATA / "cdp_xhs_crawl",   # 小号，新建
    },
    "dy": {
        "own": BROWSER_DATA / "cdp_dy",
        "crawl": BROWSER_DATA / "cdp_dy_crawl",
    },
    "bili": {
        "own": BROWSER_DATA / "cdp_bili",
        "crawl": BROWSER_DATA / "cdp_bili_crawl",
    },
}

ROLE_NAMES = {"own": "我的号（读后台）", "crawl": "采集号（爬内容）"}

# --- 方法论原文（prompt 模板母本，只读） ---
WORKFLOW_DIR = BASE / "选题工作流"
REFS_DIR = WORKFLOW_DIR / "refs"

PLATFORMS = ("xhs", "dy", "bili")
PLATFORM_NAMES = {"xhs": "小红书", "dy": "抖音", "bili": "B站"}

# 各平台首页，用于拉起浏览器后直接打开
PLATFORM_HOME = {
    "xhs": "https://www.xiaohongshu.com/explore",
    "dy": "https://www.douyin.com/",
    "bili": "https://www.bilibili.com/",
}


def profile_dir(platform: str = "xhs", role: str = "crawl"):
    """取某个平台某个角色的浏览器 user-data-dir。角色不认识时回退到 own。"""
    by_role = PROFILE_DIRS_BY_ROLE.get(platform) or PROFILE_DIRS_BY_ROLE["xhs"]
    return by_role.get(role) or by_role["own"]


def ensure_dirs() -> None:
    """首次运行时补齐所有目录。"""
    for d in (
        DATA_DIR, CONTENTS_DIR, COMMENTS_DIR, BACKUP_DIR, OWN_DIR, PROFILE_DIR,
        RUNTIME_DIR, JOBS_DIR, LOGS_DIR, RAW_DIR,
        BIN_DIR, WEB_DIR,
        *PROFILE_DIRS.values(),
    ):
        d.mkdir(parents=True, exist_ok=True)
    for by_role in PROFILE_DIRS_BY_ROLE.values():
        for d in by_role.values():
            d.mkdir(parents=True, exist_ok=True)


def data_file(name: str) -> Path:
    """把 'topics' / 'contents/xhs' 这类短名解析成 data 下的 .json 路径。"""
    p = DATA_DIR / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
