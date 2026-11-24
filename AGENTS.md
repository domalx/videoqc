# AGENTS.md

给在本仓库动手的 agent / 新同学：这里是**容易踩错的硬规则**。
设计取舍看 `docs/DESIGN.md`，具体规格看 `docs/SPEC.md`，改动记录看 `docs/CHANGELOG.md`。

## 门禁（改完必须全绿，缺一不可）

```bash
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m mypy videoqc          # 只查包，不含 tests
.venv/Scripts/python -m pytest -q             # 130 passed / ~12s
```

pytest 需要 `bin/ffmpeg.exe`、`bin/ffprobe.exe` 和 `tests/data/*.mp4`，**这两样都不入库**
（`.gitignore`），新环境先跑 `python tests/make_fixtures.py` 生成夹具。
`bin/` 缺文件时 `tests/conftest.py::require_binaries` 直接 fail，这不是环境噪音，别绕过。

## 结构

`ui → app → core → media`，只能向下依赖。判定逻辑只写在 `videoqc/core/detectors/`，
UI 和报告都只消费 `Issue`，不得自己算结论。`config_hash` 变了报告就不可比，别随手改默认值。

## 版本与记录

- **一批修复 = 一次 bump**，不要修一个问题改一次版本号。
- 版本号三处同步：`videoqc/version.py`、`pyproject.toml`、`packaging/version_info.txt`
  （`packaging/build_portable.py` 会从 `__version__` 重写后者）。
- `docs/CHANGELOG.md` 一行一条，只写「修了什么/优化了什么」，不要「问题→改法→验证」三段。
- 改配置项或判定口径，同步 `docs/SPEC.md` 的配置表与对应检测器伪代码。

## 别做的事

- DESIGN.md 里「需求已排除」的：跨会话结果缓存、问题截图、阈值设置界面、预设档位、多语言。
  要重开先跟人确认，别默默加。
- 不要给 ffmpeg 加 `-copyts`：解码链默认已归一化到流起点，再减 origin 会让
  `start_time≠0` 的素材出现负时间（v0.3.3 修过这个）。
- 不要用 `-skip_frame nokey` 抽帧：`fps` 滤镜会给稀疏关键帧重打名义时间戳，定位错位。
- 坏包标志是 `C`（Corrupt），不是 `D`（Discard）。
- 尾部的 PTS 断点归 D05/D14（截断），D03 不要重复报 high。
- 「连续 N 个采样点」这类判据一律按秒写（`color_window_sec`），采样密度是可配的，
  按点写会让阈值随 `color_sample_sec` 漂移。

## 画面采样与 ffmpeg 耦合

采样走 `runners.run_decode_pass` 的单趟滤镜链（freezedetect / blackdetect / fps / showinfo），
靠解析 stderr 文案配对帧，**升级 ffmpeg 前先跑 `tests/data/*.log` 金样**。
改滤镜链要同时看 `_read_stderr` 的配对逻辑和 `samples_incomplete` 分支。

## 提交前自检

新增非平凡逻辑（新检测器、新分支、解析器）至少留一条回归测试；
改了阈值/口径，用 `tests/data/` 的合成夹具和一条真实素材各跑一遍 `--headless --fast` 看结论是否合理。
