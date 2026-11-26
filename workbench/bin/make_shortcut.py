"""生成桌面快捷方式（纯 Python 写 .lnk 二进制，不调用 COM）。

为什么不用 WScript.Shell：本机安全策略禁止 COM 对象实例化。
pylnk3 是纯 Python 实现的 Shell Link 格式，能绕开这个限制。
"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PYW = BASE.parent / "MediaCrawler" / ".venv" / "Scripts" / "pythonw.exe"
DESKTOP = Path.home() / "Desktop"
ICON = BASE / "web" / "icon.ico"


def make_icon() -> None:
    """画一个 64x64 的图标：深绿圆角方块 + 白色 M。失败就跳过。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=14, fill=(15, 118, 110, 255))
    # 用矩形拼一个 M，避免依赖字体
    d.rectangle([16, 22, 21, 44], fill=(255, 255, 255, 255))
    d.rectangle([43, 22, 48, 44], fill=(255, 255, 255, 255))
    d.polygon([(16, 22), (22, 22), (32, 34), (42, 22), (48, 22), (32, 42)], fill=(255, 255, 255, 255))
    ICON.parent.mkdir(parents=True, exist_ok=True)
    img.save(ICON, sizes=[(64, 64), (32, 32), (16, 16)])
    print(f"图标已生成：{ICON}")


def main() -> int:
    if not PYW.exists():
        print(f"找不到 pythonw.exe：{PYW}")
        return 1
    if not DESKTOP.exists():
        print(f"找不到桌面目录：{DESKTOP}")
        return 1

    try:
        import pylnk3
    except ImportError:
        print("没装 pylnk3，请先装：uv pip install pylnk3")
        return 2

    make_icon()

    jobs = [
        {
            "name": "MBTI工作台.lnk",
            "args": f'"{BASE / "app.py"}" --open',
            "desc": "MBTI 自媒体工作台（采集·选题·创作·复盘）",
        },
        {
            "name": "关闭MBTI工作台.lnk",
            "args": f'"{BASE / "bin" / "stop.py"}"',
            "desc": "关闭 MBTI 工作台的后台服务",
        },
    ]

    ok = 0
    for j in jobs:
        out = DESKTOP / j["name"]
        try:
            lnk = pylnk3.for_file(
                str(PYW),
                lnk_name=str(out),
                arguments=j["args"],
                description=j["desc"],
                icon_file=str(ICON) if ICON.exists() else None,
                work_dir=str(BASE),
            )
            if hasattr(lnk, "save"):
                lnk.save()
            if out.exists():
                print(f"已创建：{out}  ({out.stat().st_size} 字节)")
                ok += 1
            else:
                print(f"创建失败（文件不存在）：{out}")
        except Exception as e:
            print(f"创建失败 {j['name']}：{type(e).__name__}: {e}")

    return 0 if ok == len(jobs) else 3


if __name__ == "__main__":
    sys.exit(main())
