"""headless 入口：videoqc --headless PATH... [选项]。"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from videoqc.app.config import load_config
from videoqc.app.logging_setup import configure, install_excepthook
from videoqc.core.model import FileResult
from videoqc.core.pipeline import analyze_file
from videoqc.media.paths import ROOT, ffmpeg_path, ffprobe_path, iter_videos
from videoqc.media.probe import ffmpeg_version, short_version
from videoqc.media.report import dump_json, render_markdown, report_paths
from videoqc.version import __version__


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse 默认格式不符合 SPEC §6.3
        print(f"参数错误：{message}", file=sys.stderr)
        raise SystemExit(2)


def _build_parser() -> _Parser:
    p = _Parser(prog="videoqc --headless", description="录像质量检测（headless）")
    p.add_argument("paths", nargs="+", help="文件或目录")
    p.add_argument("-o", "--out", help="Markdown 报告路径")
    p.add_argument("--json", help="JSON 报告路径")
    p.add_argument("--jobs", type=int, help="并发数（仅本次）")
    p.add_argument("--fast", action="store_true", help="只做快检")
    p.add_argument(
        "--min-severity", choices=["low", "medium", "high"], help="报告只列出不低于该级别的 Issue"
    )
    p.add_argument("--timeout", type=int, help="单文件超时秒数（仅本次）")
    p.add_argument("--config", help="配置文件路径")
    p.add_argument("--headless", action="store_true", help="无 GUI 执行（兼容直接传全量 argv）")
    return p


def _ensure_console() -> None:
    """windowed exe（console=False）被 cmd 调用时没有控制台，接管父进程或新建一个。"""
    if os.name != "nt" or sys.stdout is not None:
        return
    import ctypes

    try:
        if not ctypes.windll.kernel32.AttachConsole(-1):
            ctypes.windll.kernel32.AllocConsole()
        # 故意不关：进程退出前这两句柄就是控制台本体
        sys.stdout = Path("CONOUT$").open("w", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stderr = Path("CONOUT$").open("w", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        # 既没父控制台也建不出来（如被无控制台的服务拉起）：丢弃输出，但保留退出码语义
        null = Path(os.devnull).open("w", encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stderr = null


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        print(f"videoqc {__version__}")
        return 0
    _ensure_console()
    opts = _build_parser().parse_args(args)
    log = configure(headless=True)
    install_excepthook()

    cfg, warnings = load_config(opts.config or (ROOT / "config.json"))
    for w in warnings:
        print(f"配置告警：{w}", file=sys.stderr)
    log.info(
        "启动自检：ffmpeg=%s, ffprobe=%s, 配置告警=%d 条",
        ffmpeg_path(),
        ffprobe_path(),
        len(warnings),
    )

    for exe in (ffmpeg_path(), ffprobe_path()):
        if not exe.exists():
            log.error("启动自检失败：缺少 %s", exe)
            print(f"参数错误：缺少 {exe}（见 README 的依赖安装）", file=sys.stderr)
            return 2

    files: list[Path] = []
    for raw in opts.paths:
        p = Path(raw)
        if p.is_dir():
            files += iter_videos(p, cfg.extensions)
        elif p.is_file():
            files.append(p)
        else:
            print(f"参数错误：路径不存在 {raw}", file=sys.stderr)
            return 2

    overrides = {}
    if opts.jobs:
        overrides["jobs"] = opts.jobs
    if opts.timeout:
        overrides["timeout_sec"] = opts.timeout
    if overrides:
        cfg = replace(cfg, **overrides)

    if not files:
        print("未找到可分析的文件（检查路径与 config.json 的 extensions）", file=sys.stderr)
        return 2  # 空扫描不能返回 0：CI 会把写错的路径当成“全部通过”

    mode = "fast" if opts.fast else "full"
    cancel = threading.Event()
    results: list[FileResult] = []
    done = 0

    def _one(f: Path) -> FileResult:
        return analyze_file(f, cfg, mode, 0, cancel)

    with ThreadPoolExecutor(max_workers=max(1, cfg.jobs)) as pool:
        for result in pool.map(_one, files):
            done += 1
            print(
                f"[{done}/{len(files)}] {Path(result.path).name} → {result.conclusion}",
                file=sys.stderr,
                flush=True,
            )
            log.info("[%d/%d] %s → %s", done, len(files), Path(result.path).name, result.conclusion)
            results.append(result)
    results.sort(key=lambda r: r.path)  # 并发完成顺序复原成目录顺序（报告可 diff）

    when = datetime.now()
    if opts.out:
        md_path = Path(opts.out)
        json_path = Path(opts.json) if opts.json else md_path.with_suffix(".json")
    else:
        report_root = Path(cfg.report_dir)
        md_path, json_path = report_paths(
            report_root if report_root.is_absolute() else ROOT / report_root, when
        )
        if opts.json:
            json_path = Path(opts.json)
    for p in (md_path, json_path):
        p.parent.mkdir(parents=True, exist_ok=True)

    ver = ffmpeg_version()
    scan_root = (
        str(Path(opts.paths[0]).resolve()) if len(opts.paths) == 1 else " + ".join(opts.paths)
    )
    md_path.write_text(
        render_markdown(
            results,
            root=scan_root,
            cfg=cfg,
            when=when,
            version=__version__,
            ffmpeg_ver=short_version(ver),
            min_severity=opts.min_severity,
        ),
        encoding="utf-8",
    )
    json_path.write_text(
        dump_json(results, root=scan_root, when=when, version=__version__, ffmpeg_ver=ver, cfg=cfg),
        encoding="utf-8",
    )
    print(f"报告：{md_path}\nJSON：{json_path}", file=sys.stderr)
    return 0 if all(r.conclusion == "passed" for r in results) else 1
