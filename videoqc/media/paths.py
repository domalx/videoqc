"""路径与外部二进制定位：ROOT、BIN_DIR、ffmpeg/ffprobe、长路径、视频枚举。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# frozen（PyInstaller）时程序根是 exe 所在目录，源码运行时是仓库根。
ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parents[2]
)
BIN_DIR = ROOT / "bin"


def ffmpeg_path() -> Path:
    return BIN_DIR / "ffmpeg.exe"


def ffprobe_path() -> Path:
    return BIN_DIR / "ffprobe.exe"


def no_window_flags() -> int:
    """frozen GUI 里每次起子进程都会闪一个黑框，必须显式 CREATE_NO_WINDOW。"""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def binaries_present() -> bool:
    return ffmpeg_path().is_file() and ffprobe_path().is_file()


def long_path(p: str | Path) -> str:
    """Windows 长路径统一转 \\\\?\\ 前缀（UNC 转 \\\\?\\UNC\\）。"""
    s = os.path.abspath(str(p))
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC" + s[1:]
    # shortcut: 只给超长路径加前缀，短路径保持可读（ffmpeg 两种都接受），超过 260 仍会失败时再改为无条件前缀
    return "\\\\?\\" + s if len(s) >= 240 else s


def is_video(path: str | Path, extensions) -> bool:
    return Path(path).suffix.lower().lstrip(".") in {e.lower().lstrip(".") for e in extensions}


def iter_videos(root: str | Path, extensions) -> list[Path]:
    """递归枚举视频文件，按路径排序（顺序稳定，报告 diff 才可复现）。"""
    root = Path(root)
    return sorted(
        p for p in root.rglob("*") if p.is_file() and is_video(p, extensions)
    )
