"""报告渲染：render_markdown()、dump_json()、report_paths()。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from videoqc.app.config import Config, config_hash
from videoqc.app.logging_setup import get_logger
from videoqc.core.detectors import CHECKS
from videoqc.core.kinds import KIND_LABELS
from videoqc.core.model import SEV_ORDER, FileResult, Issue, StreamInfo, fmt_tc
from videoqc.media.probe import parse_rate

SCHEMA_VERSION = 2
SEV_ICON = {"high": "❌", "medium": "⚠️", "low": "⚠️", "info": "ℹ️"}
CONCLUSION_ICON = {
    "passed": "✅ 通过",
    "warned": "⚠️ 警告",
    "failed": "❌ 失败",
    "unfinished": "⏸ 未完成",
}
_ORDER = {"failed": 0, "warned": 1, "unfinished": 2, "passed": 3}


def label(kind: str) -> str:
    label_cn = KIND_LABELS.get(kind)
    if label_cn is None:  # 新增 kind 忘了登记：报告里会露英文串，日志留痕
        get_logger().warning("未登记的 issue kind：%s（报告将显示原始串）", kind)
        return kind
    return label_cn


def _evidence(i: Issue) -> str:
    """证据列：优先 detail，否则按 k=v 摆开 evidence。"""
    text = i.detail or ", ".join(f"{k}={v}" for k, v in i.evidence.items())
    return text.replace("|", chr(92) + "|")


def report_paths(root_dir: str | Path, when: datetime) -> tuple[Path, Path]:
    root = Path(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    stem = f"report_{when:%Y%m%d_%H%M}"
    n = 1
    while (root / f"{stem}.md").exists() or (root / f"{stem}.json").exists():
        n += 1
        stem = f"report_{when:%Y%m%d_%H%M}_{n}"
    return root / f"{stem}.md", root / f"{stem}.json"


def mode_label(results: list[FileResult]) -> str:
    modes = {r.mode for r in results if r.mode != "none"}
    if modes == {"full"}:
        return "全量"
    if modes == {"fast"}:
        return "快检"
    return "混合" if modes else "—"


def _short_tc(sec: float | None) -> str:
    if not sec:
        return "—"
    s = round(sec)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _summary_text(result: FileResult) -> str:
    counts: dict[str, int] = {}
    for i in result.issues:
        counts[i.kind] = counts.get(i.kind, 0) + 1
    parts = [f"{label(k)}" + (f"×{n}" if n > 1 else "") for k, n in counts.items()]
    return "、".join(parts) if parts else "—"


def _sorted(results: list[FileResult]) -> list[FileResult]:
    return sorted(results, key=lambda r: (_ORDER[r.conclusion], -len(r.issues), r.path))


def _issue_time(i: Issue) -> str:
    if i.start is None:
        return "—"
    return (
        f"{fmt_tc(i.start)} → {fmt_tc(i.end)}"
        if i.end is not None and i.end > i.start
        else fmt_tc(i.start)
    )


def _repro(path: str, cfg: Config) -> str:
    name = Path(path).name
    return (
        f"ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,duration_time,flags "
        f'-of csv=p=0 "{name}"\n'
        f'ffmpeg -nostdin -v info -i "{name}" '
        f'-vf "freezedetect=n={cfg.freeze_noise:g}:d={cfg.freeze_min_sec:g},'
        f'blackdetect=d={cfg.black_min_sec:g}:pic_th=0.98:pix_th=0.10" '
        f'-af "silencedetect=n={cfg.silence_db:g}dB:d={cfg.silence_min_sec:g},volumedetect" -f null -'
    )


def _stream_line(s: StreamInfo) -> str:
    """容器/编码那一行。ffprobe 字段可能缺失（mpegts 常见 "0/0" 帧率、无 audio 参数），逐个兜底。"""
    v = s.video or {}
    fps = v.get("fps") or parse_rate(v.get("avg_frame_rate"))
    size = (
        f"{v.get('width')}x{v.get('height')}"
        if v.get("width") and v.get("height")
        else "分辨率未知"
    )
    rate = f"{fps:.3g}fps" if fps else "帧率未知"
    if s.audio:
        bits = [s.audio.get("codec") or "未知"]
        if s.audio.get("sample_rate"):
            bits.append(f"{s.audio['sample_rate']}Hz")
        if s.audio.get("channels"):
            bits.append(f"{s.audio['channels']}ch")
        audio = " ".join(bits)
    else:
        audio = "无音轨"
    kbps = f"{s.bit_rate / 1000:.0f}kbps" if s.bit_rate else "码率未知"
    return f"容器 {s.container} · {v.get('codec') or '未知'} {size} {rate} {kbps} · 音频：{audio}"


def _detail_block(idx: int, r: FileResult, cfg: Config, issues: list[Issue]) -> list[str]:
    icon = CONCLUSION_ICON[r.conclusion]
    out = [f"### {idx}. {Path(r.path).name} — {icon}", ""]
    s = r.streams
    if s and s.video:
        out += [_stream_line(s), ""]
    if issues:
        out += ["| 时间 | 类型 | 级别 | 描述 | 证据 |", "|------|------|------|------|------|"]
        for i in issues:
            out.append(
                f"| {_issue_time(i)} | {label(i.kind)} | {SEV_ICON[i.severity]} | {i.title} | "
                f"{_evidence(i)} |"
            )
        out.append("")
    out += [
        "<details><summary>复现命令</summary>",
        "",
        "```bash",
        _repro(r.path, cfg),
        "```",
        "</details>",
        "",
    ]
    return out


def render_markdown(
    results: list[FileResult],
    *,
    root: str,
    cfg: Config,
    when: datetime,
    version: str,
    ffmpeg_ver: str,
    min_severity: str | None = None,
) -> str:
    counts = {
        k: sum(1 for r in results if r.conclusion == k)
        for k in ("passed", "warned", "failed", "unfinished")
    }
    lines = [
        "# 录像质量检测报告",
        "",
        f"扫描目录：{root}",
        f"生成时间：{when:%Y-%m-%d %H:%M:%S}",
        f"工具：videoqc {version} · ffmpeg {ffmpeg_ver} · 模式：{mode_label(results)} · "
        f"阈值：{config_hash(cfg)[:8]}",
        "",
        f"合计 {len(results)} 个文件：通过 {counts['passed']} · 警告 {counts['warned']} · "
        f"失败 {counts['failed']} · 未完成 {counts['unfinished']}",
        "",
        "## 概览",
        "",
        "| # | 文件 | 时长 | 范围 | 结论 | 问题 |",
        "|---|------|------|------|------|------|",
    ]
    ordered = _sorted(results)
    for n, r in enumerate(ordered, 1):
        rng = {"fast": "快检", "full": "全量", "none": "—"}[r.mode]
        lines.append(
            f"| {n} | {Path(r.path).name} | {_short_tc(r.stats.get('duration'))} | {rng} | "
            f"{CONCLUSION_ICON[r.conclusion]} | {_summary_text(r)} |"
        )
    lines += ["", "## 详情", ""]
    detail_idx = 0
    for r in ordered:
        issues = [
            i
            for i in r.issues
            if min_severity is None or SEV_ORDER[i.severity] >= SEV_ORDER[min_severity]
        ]
        if not issues and r.conclusion == "passed":
            continue
        detail_idx += 1
        lines += _detail_block(detail_idx, r, cfg, issues)

    lines += [
        "## 附录",
        "",
        "### 检测项",
        "",
        "| ID | 检测项 | 本次是否执行 |",
        "|----|--------|--------------|",
    ]
    used = {r.mode for r in results}
    for cid, name, _fn, full_only in CHECKS:
        ran = "—" if (full_only and "full" not in used) else "✔"
        lines.append(f"| {cid} | {name} | {ran} |")
    lines += [""]  # v1 不列阈值表与 ffmpeg 路径：报告只用于扫结论，复现靠头部 config_hash
    return "\n".join(lines)


def dump_json(
    results: list[FileResult],
    *,
    root: str,
    when: datetime,
    version: str,
    ffmpeg_ver: str,
    cfg: Config,
) -> str:
    counts = {
        k: sum(1 for r in results if r.conclusion == k)
        for k in ("passed", "warned", "failed", "unfinished")
    }
    kinds: dict[str, int] = {}
    for r in results:
        for i in r.issues:
            kinds[i.kind] = kinds.get(i.kind, 0) + 1
    payload = {
        "schema_version": SCHEMA_VERSION,
        "tool": {
            "name": "videoqc",
            "version": version,
            "ffmpeg": ffmpeg_ver,
            "config_hash": config_hash(cfg)[:8],
        },
        "generated_at": when.isoformat(timespec="seconds"),
        "root": root,
        "files": [{"ok": r.ok, **asdict(r)} for r in results],
        "summary": {"total": len(results), **counts, "kinds": kinds},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
