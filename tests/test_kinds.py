"""kind 漂移护栏：检测器产出的每个 kind 都必须在 core/kinds.py 登记。

检测器里 kind 是字符串字面量（D11 还用变量拼），漏登记时报告会露英文串、UI 分类也会错。
这里跑一遍真实夹具的全流程，把实际产出的 kind 与注册表对比。
"""

from __future__ import annotations

import threading

import pytest

from videoqc.app.config import Config
from videoqc.core.kinds import KINDS
from videoqc.core.pipeline import analyze_file

FIXTURES = (
    "base.mp4",
    "black4.mp4",
    "cast.mp4",
    "corrupt.mp4",
    "freeze3.mp4",
    "hot.mp4",
    "muted.mp4",
    "noaudio.mp4",
    "trunc.mp4",
)


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_every_emitted_kind_is_registered(data_dir, mode):
    seen: set[str] = set()
    for name in FIXTURES:
        p = data_dir / name
        if not p.exists():
            continue
        r = analyze_file(p, Config(), mode, 0, threading.Event())
        seen |= {i.kind for i in r.issues}
    unknown = seen - KINDS
    assert not unknown, f"这些 kind 未在 videoqc/core/kinds.py 登记：{sorted(unknown)}"


def test_registry_labels_are_non_empty():
    from videoqc.core.kinds import KIND_LABELS

    assert KIND_LABELS, "注册表不能为空"
    assert all(k and v.strip() for k, v in KIND_LABELS.items())
