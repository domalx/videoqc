# videoqc 实现规格（SPEC v0.3.3）

配套文档：`docs/DESIGN.md`（v0.3.3）。本文件是**可直接照着写代码**的规格：接口签名、命令原文、解析正则、判定伪代码、UI 信号、报告模板、测试夹具、验收清单。
凡与 `docs/DESIGN.md` 冲突，以本文件为准并回改 DESIGN。
实现现状（截至 2026-11-24：M1–M6 完成、123 项测试绿、`dist/videoqc-0.3.3-win64.zip`）见 `docs/DESIGN.md` §0。

---

## 目录

- [0. 工程约定](#0-工程约定)
- [1. 文件清单与职责](#1-文件清单与职责)
  - [1.1 外部二进制（ffmpeg / ffprobe）的落位](#11-外部二进制ffmpeg--ffprobe的落位)
  - [1.2 运行日志与崩溃处理](#12-运行日志与崩溃处理)
- [1A. 全局状态与结论规则](#1a-全局状态与结论规则)
- [2. 数据模型（core/model.py）](#2-数据模型coremodelpy)
  - [2.1 不变式（写进断言）](#21-不变式写进断言)
  - [2.2 JSON 序列化](#22-json-序列化)
- [3. 配置规格（app/config.py）](#3-配置规格appconfigpy)
- [4. media 规格（探测、解码、纯计算）](#4-media-规格探测解码纯计算)
  - [4.1 media/probe.py :: probe_streams(path, timeout) -> StreamInfo](#41-mediaprobepy--probe_streamspath-timeout---streaminfo)
  - [4.2 media/probe.py :: probe_packets(path, start_time, timeout) -> PacketStats](#42-mediaprobepy--probe_packetspath-start_time-timeout---packetstats)
  - [4.3 media/runners.py :: run_decode_pass(path, cfg, *, has_audio, origin_sec, duration_sec, cancel_event) -> DecodeEvents](#43-mediarunnerspy--run_decode_passpath-cfg--has_audio-origin_sec-duration_sec-cancel_event---decodeevents)
  - [4.4 media/runners.py :: 采样子路（已并入 run_decode_pass）](#44-mediarunnerspy--采样子路已并入-run_decode_pass)
  - [4.5 media/color.py :: frame_stats(rgb: bytes) -> tuple[float,float,float,float,float,float]](#45-mediacolorpy--frame_statsrgb-bytes---tuplefloatfloatfloatfloatfloatfloat)
- [5. core 规格](#5-core-规格)
  - [5.1 core/pipeline.py :: analyze_file(path, cfg, mode, generation, cancel_event, *, on_stage=None) -> FileResult](#51-corepipelinepy--analyze_filepath-cfg-mode-generation-cancel_event--on_stagenone---fileresult)
  - [5.2 检测器规格（伪代码 + 边界）](#52-检测器规格伪代码--边界)
  - [5.3 core/merge.py :: merge_issues(ctx)](#53-coremergepy--merge_issuesctx)
- [6. app 规格](#6-app-规格)
  - [6.1 Session](#61-session)
  - [6.2 ScanController(QObject)](#62-scancontrollerqobject)
  - [6.3 cli.py](#63-clipy)
  - [6.4 取消、导出与路径边界](#64-取消导出与路径边界)
- [7. ui 规格](#7-ui-规格)
  - [7.1 布局（MainWindow）](#71-布局mainwindow)
  - [7.2 FileTableModel（QAbstractTableModel）](#72-filetablemodelqabstracttablemodel)
  - [7.3 PlayerPanel](#73-playerpanel)
  - [7.4 IssuePanel](#74-issuepanel)
  - [7.5 MainWindow 交互契约](#75-mainwindow-交互契约)
- [8. 报告规格（media/report.py）](#8-报告规格mediareportpy)
  - [8.1 文件名与路径](#81-文件名与路径)
  - [8.2 MD 模板（严格照此渲染，便于回归 diff）](#82-md-模板严格照此渲染便于回归-diff)
  - [8.3 复现命令模板](#83-复现命令模板)
- [9. 打包规格](#9-打包规格)
  - [9.1 packaging/videoqc.spec（onedir）](#91-packagingvideoqcspeconedir)
  - [9.2 packaging/build_portable.py 步骤](#92-packagingbuild_portablepy-步骤)
- [10. 测试规格](#10-测试规格)
  - [10.1 夹具（tests/make_fixtures.py，全部 320×180/30fps/20s，跑得快）](#101-夹具testsmake_fixturespy全部-32018030fps20s跑得快)
  - [10.2 期望结果（回归断言表）](#102-期望结果回归断言表)
  - [10.3 单元测试清单（pytest，无 Qt 依赖）](#103-单元测试清单pytest无-qt-依赖)
- [11. 验收清单（需求 → 验证动作）](#11-验收清单需求--验证动作)
- [12. 实现顺序（依赖拓扑，逐步可运行）](#12-实现顺序依赖拓扑逐步可运行)



## 0. 工程约定

- Python 3.13，标准库 only（GUI 侧 `PySide6-Essentials` + `PySide6-Addons`，QtMultimedia 在 Addons）。禁止 numpy/OpenCV/pandas。
- 源码 `videoqc/` 包，入口 `videoqc/__main__.py`。
- 命名：模块/函数 snake_case，类 PascalCase，Qt 信号 camelCase（与 Qt 一致）。
- 所有时间单位：秒（`float`），字段名后缀 `_sec` 或在 dataclass 注释中说明。
- 所有 subprocess 调用：参数列表形式，禁止 `shell=True`；`creationflags=CREATE_NO_WINDOW`（Windows，避免闪黑框）。
- 程序根目录（ROOT）：frozen 时 `Path(sys.executable).parent`，源码运行时为仓库根。**配置与报告路径一律相对 ROOT。**

---

## 1. 文件清单与职责

```
videoqc/
├─ __main__.py           # 入口: 无参 → GUI; --headless → cli
├─ version.py            # __version__ = "0.3.3"（唯一版本来源）
├─ core/
│  ├─ model.py           # Issue/FileResult/Report/StreamInfo/PacketStats/DecodeEvents/Sample + fmt_tc
│  ├─ constants.py       # 内部判定常量（不开放给用户，报告不列出）
│  ├─ context.py         # FileContext（检测器输入容器）
│  ├─ pipeline.py        # analyze_file() 三阶段编排（探测 → 包级 → 解码+采样）
│  ├─ merge.py           # merge_issues() 合并/降级/排序
│  └─ detectors/
│     ├─ __init__.py     # CHECKS（报告附录唯一来源）+ DETECTORS
│     ├─ integrity.py    # D01, D02, D14, D13
│     ├─ timing.py       # D03, D04
│     ├─ freeze.py       # D05, D09
│     ├─ audio.py        # D06, D07, D08
│     ├─ colorcast.py    # D10, D11
│     └─ corruption.py   # D12
├─ media/
│  ├─ probe.py           # probe_streams(), probe_packets(), ffmpeg_version()
│  ├─ runners.py         # run_decode_pass()（单趟：事件 + 32×18 采样），进程/双线程/配对/收尾
│  ├─ stderr_parse.py    # ffmpeg stderr 文案解析：freeze/black/silence/音量/报错（唯一耦合点）
│  ├─ color.py           # frame_stats()、样本聚合（纯计算）
│  ├─ report.py          # render_markdown(), dump_json(), report_paths()
│  └─ paths.py           # ROOT, BIN_DIR, ffmpeg_path(), ffprobe_path(), long_path(), is_video(), iter_videos()
├─ app/
│  ├─ config.py          # DEFAULTS, Config, load_config()
│  ├─ session.py         # Session（内存结果表 + generation）
│  ├─ controller.py      # ScanController(QObject) + ThreadPoolExecutor 提交
│  └─ cli.py             # headless
└─ ui/
   ├─ main_window.py     # MainWindow
   ├─ file_table.py      # FileTableModel + FileTableView
   ├─ player_panel.py    # PlayerPanel
   ├─ issue_panel.py     # IssuePanel
   └─ theme.py           # 行底色与级别配色常量
assets/app.ico
config.json
requirements.txt         # 运行时依赖（PySide6-Essentials + Addons，钉版本）
requirements-dev.txt     # 开发/打包依赖（-r requirements.txt + pyinstaller + pytest）
docs/                    # DESIGN.md（设计理由）、SPEC.md（本文）
packaging/               # 打包专用目录（源码，入库）
├─ videoqc.spec          # PyInstaller 构建描述
├─ build_portable.py     # 组装 green 包 + 自检 + zip
├─ version_info.txt      # exe 资源版本（取 videoqc/version.py）
└─ licenses/             # THIRD-PARTY.md、gpl-3.0.txt、lgpl-3.0.txt、PYTHON-3.13.txt
tests/                   # conftest.py 断言 ROOT/bin 存在；test_*.py；data/ 放夹具与录制日志
pyproject.toml           # ruff + mypy 配置（也是包的元数据）
bin/                     # ← 外部二进制，不入库（进 .gitignore）
├─ ffmpeg.exe            # 开发与打包共用，构建前放 gyan.dev essentials 静态包
└─ ffprobe.exe
dist/                    # ← 构建产物（不入库）：videoqc/、zip、_work/
logs/                    # ← 运行日志（首次运行自动建目录，不入库）
```

各包目录下的 `__init__.py` 为空（`core/detectors/__init__.py` 除外，它导出 `DETECTORS`）。

### 1.1 外部二进制（ffmpeg / ffprobe）的落位

- **仓库与交付包同构**：两处都是 `ROOT/bin/ffmpeg.exe`、`ROOT/bin/ffprobe.exe`（`ROOT` = frozen 时 exe 所在目录，源码运行时为仓库根，见 §0）。`build_portable.py` 只是把 `bin/` 原样复制进 zip，不存在第二套路径。
- `media/paths.py::ffmpeg_path()/ffprobe_path()` 固定返回 `BIN_DIR / "ffmpeg.exe"`（`BIN_DIR = ROOT/"bin"`）；`media/probe.py::ffmpeg_version()` 运行 `ffmpeg -version` 取首行版本号。
- **不回退 PATH**：`bin/` 缺失或不可执行 → 启动自检失败（弹窗「重新解压/放入 bin」），headless 退出码 2。开发机也不搜 PATH，保证本机行为与交付包一致。
- 开发/单测同样从 `ROOT/bin` 取（`tests/conftest.py` 断言其存在），不写仓库外的绝对路径。

### 1.2 运行日志与崩溃处理

- 日志文件：`ROOT/logs/videoqc.log`（首次运行 `mkdir`），`RotatingFileHandler`，2MB × 3；级别 INFO，`--headless` 同时输出到 stderr。
- 必记：启动自检结果（ffmpeg 路径/版本、config 告警条数）、每个文件的阶段失败原因与 `subprocess` 退出码、ffmpeg stderr 尾 50 行（仅当前文件失败时）、异常堆栈。
- 崩溃：`sys.excepthook` + Qt 消息处理器统一写日志，然后弹窗「已记录到 logs/videoqc.log」并保持已有结果可导出；不得静默退出。
- 日志只记事件与时间，不写完整帧数据，不影响分析耗时预算（<1s/文件）。

---

## 1A. 全局状态与结论规则

以下规则对管线、UI、报告、CLI 及 JSON 全局生效：

- 严重度分为 `info`、`low`、`medium`、`high`。
- `failed`：存在至少一个 `high` Issue。
- `warned`：不存在 `high`，但存在至少一个 `medium` 或 `low` Issue。
- `passed`：不存在 `high`、`medium` 或 `low` Issue；`info` Issue 不影响通过结论。
- `unfinished`：`status != "done"`；状态优先级为 `cancelled > timeout > failed > done`。
- `status != "done"` 时 `ok` 必须为 `false`，不得进入通过或警告统计。
- `low` Issue 属于警告，不是通过。
- `--min-severity` 只过滤展示，不改变结论、退出码、JSON 或统计。
- `conclusion` 是**唯一结论字段**（C8）：`FileResult.conclusion ∈ {passed, warned, failed, unfinished}`，`ok == (conclusion == "passed")`。上述四条规则只在 `model` 实现一次，UI/报告/CLI/统计都读它。
- `status="pending"` + `mode="none"` = 「本会话内未分析」。只计入 `unfinished` 与报告「未分析」，不计入 passed/warned/failed。

管线状态优先级：

1. 用户取消：优先返回 `cancelled`，保留已完成阶段产物。
2. 阶段超时：返回 `timeout`，保留已解析事件与包探测结果。
3. ffprobe 致命失败或无视频流：返回 `failed`，并包含对应 high Issue。
4. 非取消且非超时的解码崩溃：保留部分结果，追加 `decode_aborted`；状态保持 `done`，除非探测阶段也被标记为致命失败。

---

## 2. 数据模型（`core/model.py`）

```python
from dataclasses import dataclass, field, asdict
SEVERITIES = ("info", "low", "medium", "high")          # 升序，比较用 index
SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}

@dataclass
class Issue:
    kind: str                     # 见 §5 各检测器常量
    severity: str                 # "info"|"low"|"medium"|"high"
    title: str                    # 一句话结论（≤40 字，进表格）
    detail: str = ""              # 可复核说明（含原始数值）
    start: float | None = None    # 视频流内相对秒；None 表示无时间点（如"无音轨"）
    end: float | None = None
    evidence: dict = field(default_factory=dict)   # 原始数值，进 JSON

@dataclass
class StreamInfo:
    path: str
    duration: float | None        # format.duration
    container: str                # format.format_name
    size_bytes: int
    bit_rate: int | None
    video: dict | None            # {codec,width,height,pix_fmt,r_frame_rate,avg_frame_rate,start_time,duration,nb_frames,color_transfer,rotate,sample_aspect_ratio}
    audio: dict | None            # {codec,sample_rate,channels,start_time,duration,bit_rate}
    has_audio: bool

@dataclass
class PacketStats:
    pts: list[float]              # 已减 start_time，升序去重
    p50: float                    # 中位帧间隔
    dts_non_monotonic: int        # 逆序次数
    dup_pts: int                  # 重复 pts 次数
    corrupt_flags: int            # flags 含 C（Corrupt）的包数，D=Discard 不计
    first_ts: float
    last_ts: float
    nominal_fps: float | None     # 由 avg_frame_rate 解析

@dataclass
class DecodeEvents:
    freezes: list[tuple[float, float]]     # (start, end)
    blacks: list[tuple[float, float]]
    silences: list[tuple[float, float]]
    mean_volume: float | None              # dB
    max_volume: float | None
    errors: list[str]                      # 原始错误行（截断 200 字，最多 20 条）
    error_count: int                       # 合计（视频+音频）
    video_errors: int                      # 按 stderr 的 stream #0:N 归到视频流
    audio_errors: int                      # 归到音频流（音频报错不算花屏）
    samples: list[Sample] | None           # 同一趟解码顺带抽出的样本
    samples_timed_out: bool
    exit_code: int
    completed: bool                        # False = 中途崩溃/超时

@dataclass
class Sample:
    t: float                      # showinfo pts_time
    r: float; g: float; b: float  # 0..255 均值
    y: float                      # 0.299r+0.587g+0.114b
    sat: float                    # (max-min)/max, 0..1
    cast: float                   # max(|r-y|,|g-y|,|b-y|)

@dataclass
class FileResult:
    path: str
    ok: bool                      # == (conclusion == "passed")
    mode: str                     # "fast"|"full"|"none"
    status: str                   # "done"|"cancelled"|"timeout"|"failed"|"pending"
    generation: int = 0
    streams: StreamInfo | None = None
    stats: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    error: str | None = None
    elapsed_sec: float = 0.0

    @property
    def conclusion(self) -> str:
        """passed|warned|failed|unfinished —— 全局唯一结论实现（C8），见 §1A"""

def fmt_tc(sec: float | None) -> str:
    """None -> '—'；否则 HH:MM:SS.mmm（小时不省略）。
    先 round(sec*1000) 再进位归一化，避免 59.9996 → 60.000 这类越界表示。"""
```

### 2.1 不变式（写进断言）

1. `Issue.severity in SEVERITIES`。
2. `Issue.start is None` ⇒ `Issue.end is None`。
3. `Issue.start <= Issue.end`（相等允许：瞬时事件）。
4. `FileResult.ok == (result.conclusion == "passed")`；`conclusion` 按 §1A 规则得出，且是**唯一**结论实现（C8）。
5. `status != "done"` 时 `ok == False`。
6. `FileResult.issues` 已排序（severity 降序 → start 升序 → kind）。
7. `Issue.start/end` 已钳位到 `[0, duration]`（C7）；`duration` 未知时不钳位。

### 2.2 JSON 序列化

`dump_json(report)` → `json.dumps(..., ensure_ascii=False, indent=2)`；`Issue`/`FileResult` 用 `asdict`；`None` 保留为 `null`。
顶层 schema（`schema_version: 2`，因新增 status/generation）：

```json
{"schema_version": 2, "tool": {...}, "generated_at": "...", "root": "...",
 "files": [ {...FileResult...} ], "summary": {"total": 0, "passed": 0, "warned": 0, "failed": 0, "unfinished": 0, "kinds": {"pts_gap": 2}}}
```

---

## 3. 配置规格（`app/config.py`）

```python
@dataclass(frozen=True)
class Config:
    extensions: tuple[str, ...]; jobs: int
    timeout_sec: int; quick_check_on_select: bool
    pts_gap_factor: float; pts_gap_min: float
    freeze_noise: float; freeze_min_sec: float
    black_min_sec: float
    silence_db: float; silence_min_sec: float; mean_volume_low_db: float
    silent_mean_db: float; clipping_db: float
    require_audio: bool
    av_offset_sec: float; truncation_gap_sec: float
    color_cast_threshold: float; color_sample_sec: float; color_window_sec: float
    hdr_tonemap: bool; report_dir: str
    # 派生
    @property
    def sample_interval(self) -> float: return self.color_sample_sec
```

| 键 | 类型 | 默认 | 合法范围 | 越界处理 |
|----|------|------|----------|----------|
| extensions | str[] | 见 DESIGN §8 | 非空 | 空数组 → 用默认 |
| jobs | int | 2 | 1..8 | clamp |
| timeout_sec | int | 3600 | 60..86400 | clamp |
| quick_check_on_select | bool | true | — | 非 bool → 默认 |
| pts_gap_factor | float | 3.0 | 1.5..50 | clamp |
| pts_gap_min | float | 0.2 | 0.02..5 | clamp |
| freeze_noise | float | 0.003 | 0.0001..0.1 | clamp |
| freeze_min_sec | float | 2.0 | 0.5..60 | clamp |
| black_min_sec | float | 0.5 | 0.1..30 | clamp |
| silence_db | float | -50 | -90..-10 | clamp |
| silence_min_sec | float | 3.0 | 0.5..120 | clamp |
| mean_volume_low_db | float | -30 | -60..-5 | clamp |
| silent_mean_db | float | -45 | -90..-20 | clamp |
| clipping_db | float | -0.1 | -10..0 | clamp |
| require_audio | bool | true | — | 非 bool → 默认（false 时无音轨不报 high）|
| av_offset_sec | float | 0.5 | 0.05..5 | clamp |
| truncation_gap_sec | float | 2.0 | 0.5..30 | clamp |
| color_cast_threshold | float | 25 | 5..80 | clamp |
| color_sample_sec | float | 1.0 | 0.5..60 | clamp |
| color_window_sec | float | 15.0 | 2..300 | clamp |
| hdr_tonemap | bool | true | — | 非 bool → 默认 |
| report_dir | str | "reports" | — | 空 → 默认 |

其余判定常量（freeze ≥10s 升 high、冻结/黑场重叠 80%、黑场覆盖 30%、缺失占比 0.5%、采样覆盖 ≥80%、探测超时 30s）集中在 `core/constants.py`，不进 `config.json`（用户不需要碰），报告不列出（v1 只保留头部的 `config_hash`，用于判断两份报告是否可比）。

`load_config(path) -> tuple[Config, list[str]]`：第二个返回值是告警列表（逐项一行，形如 `color_cast_threshold 无效(abc)，已用默认 25`）。
规则：
- 文件不存在 → 全默认 + 告警 `config.json 不存在，使用默认配置`（不自动创建）。
- JSON 语法错 → 全默认 + 告警（含行列，取 `json.JSONDecodeError.lineno`）。
- 未知键 → 告警 `未知配置项 xxx（已忽略）`。
- `config_version` > 1 → 告警但不阻断。
- **GUI 不覆盖配置**（`--config` 只换文件路径）；headless 的 `--jobs`/`--timeout` 可覆盖本次执行，见 §6.3。

---

## 4. media 规格（探测、解码、纯计算）

> 所有 ffmpeg/ffprobe 子进程统一带 `creationflags=CREATE_NO_WINDOW`（`paths.no_window_flags()`）。
> 漏掉这一项时，frozen GUI 每分析一个文件就闪一个黑色控制台窗口。

### 4.1 `media/probe.py :: probe_streams(path, timeout) -> StreamInfo`

```bash
ffprobe -v error -print_format json -show_format -show_streams <path>
```

- 超时 30s；失败抛 `ProbeError(message, stderr_tail)`，由 pipeline 转 D01 high。
- 映射：
  - `duration` 缺失 → 尝试 `video.duration` → 再尝试 `audio.duration` → 全无则 `None`。
  - `r_frame_rate`/`avg_frame_rate` 解析 `a/b`，`b==0` → None。
  - `has_audio = any(s.codec_type=="audio")`。
  - `rotate`：优先 `side_data_list[].rotation`，其次 tags.rotate，统一归一化为 0/90/180/270。

### 4.2 `media/probe.py :: probe_packets(path, start_time, timeout) -> PacketStats`

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries packet=pts_time,dts_time,duration_time,flags -of csv=p=0 <path>
```

解析：逐行 `split(",")`；字段顺序即上；`""`/`"N/A"` → None。`pts=None` 的包跳过并计数 `missing_pts`。
- `pts` 归一化：`t = pts - start_time`（`start_time` 来自 StreamInfo.video.start_time，缺省 0）。
- 去重后算 `delta`，`p50 = statistics.median(deltas)`。
- `dts_non_monotonic`：相邻 dts 递减次数；`dup_pts`：重复 pts 次数；`corrupt_flags`：flags 含 `C`。flags 是 3 字符位串 `[K][D][C]`（Key/Discard/Corrupt，见 ffprobe.c 的 show_packet）：`D`=可丢弃包（B 帧重排时正常出现），只有 `C`=包内容损坏才计入坏包。
- `first_ts`/`last_ts` 取自归一化后 pts。
- 空包（0 个）→ 抛 `ProbeError("no video packets")`。
- `missing_pts` 计入 `stats.packets_missing_pts`；`missing_pts / total > 1%` 时由 D04 报 info（时间戳缺失，D03 会漏报）。

### 4.3 `media/runners.py :: run_decode_pass(path, cfg, *, has_audio, origin_sec, duration_sec, cancel_event) -> DecodeEvents`

> 内部拆成 `_filter_chain` / `_build_cmd` / `_start`（拉进程+两个读线程）/ `_collect_samples`（帧与 pts 配对）/ `_finish`（kill 与 join）。
> stderr 的文案正则与事件状态机在 `media/stderr_parse.py`，这是全项目唯一与 ffmpeg 输出文案耦合的地方。

```bash
# 有音轨（默认）
ffmpeg -nostdin -hide_banner -nostats -v info \
  -i <path> -map 0:v:0 -map 0:a:0? \
  -vf "freezedetect=n={freeze_noise}:d={freeze_min_sec},blackdetect=d={black_min_sec}:pic_th=0.98:pix_th=0.10" \
  -af "silencedetect=n={silence_db}dB:d={silence_min_sec},volumedetect" \
  -f null -

# has_audio=False：去掉 -map / -af（不赌「无音频流时 -af 被忽略」）
ffmpeg -nostdin -hide_banner -nostats -v info -i <path> -an \
  -vf "freezedetect=n={freeze_noise}:d={freeze_min_sec},blackdetect=d={black_min_sec}:pic_th=0.98:pix_th=0.10" \
  -f null -
```

- `-map 0:a:0?` 的 `?` 必须保留（有音轨时部分文件可能仍缺 audio，不能让 ffmpeg 报错退出）。
- stderr 逐行读，按**顺序**匹配下列正则（全部 `re.search`，预编译）：

| 事件 | 正则（原文） |
|------|--------------|
| freeze start | `lavfi\.freezedetect\.freeze_start:\s*([0-9.]+)` |
| freeze end | `lavfi\.freezedetect\.freeze_end:\s*([0-9.]+)` |
| freeze duration | `lavfi\.freezedetect\.freeze_duration:\s*([0-9.]+)` |
| black | `black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)\s+black_duration:\s*([0-9.]+)` |
| silence start | `silence_start:\s*(-?[0-9.]+)` |
| silence end | `silence_end:\s*(-?[0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)` |
| mean volume | `mean_volume:\s*(-?[0-9.]+)\s*dB` |
| max volume | `max_volume:\s*(-?[0-9.]+)\s*dB` |
| decode error | `error while decoding\|corrupt\|Invalid NAL\|no frame!\|missing picture\|Invalid data found\|Error while decoding stream\|Error splitting the input` |

- `freeze_end` 与 `freeze_duration` 可能只有其一：若只有 duration，则 `end = start + duration`；两者都没有（画面一直冻到文件结束，静止片尾很常见）→ `end = duration_sec`。
- `silence_start` 后未等到 `silence_end`（文件末尾）→ `silence = (start, duration_sec)`；收到 `silence_end: E | silence_duration: D` 时段是 `(E-D, E)`，**不是** `(E, E+D)`。
- black 行可能只报 `black_start` 单独一行（未结束时）→ 记 pending，遇到完整行或进程结束时补 `(start, duration_sec)`。
- 错误行：`error_count += 1`，`errors` 保留前 20 条（每行 `line.strip()[:200]`）。先跳过 `Input #`/`Output #` 开头的行——文件路径里带 `corrupt` 之类字样会被错误正则误命中。
- **时间基准（C1）**：`DecodeEvents` 出去即「视频流内秒」（0 = 视频流首帧）。ffmpeg 命令未加 `-copyts`，默认的 start-time 归一化已把滤镜事件的 PTS 平移到流起点（实测：start_time=10s 的文件 freeze_start 仍报 4s，MPEG-TS start≈1.47s 同样从 0 起），**因此出口不再减 `origin_sec`**——重复扣减会让 TS/监控录像出现负时间、与 probe_packets 时间轴错位。`origin_sec` 参数保留仅为兼容调用方。未闭合区间用 `duration_sec` 收尾；音频侧残留 = 音视频起点差（由 D02 报）。
- 结束：`completed = (exit_code == 0)`；超时（`timeout_sec`）→ kill + `completed=False, exit_code=-9`；`cancel_event.set()` → 先 kill，再 `wait(2s)` 并关管道，同样 `completed=False`。
- **返回值绝不抛异常**（除 `FileNotFoundError`）；`completed=False` 由 pipeline 统一追加 `decode_aborted`（检测器不再重复追加）。

### 4.4 `media/runners.py :: 采样子路（已并入 run_decode_pass）`

> 采样不再是独立函数：`sample_frames()` 已删除，采样作为一个视频输出分支合并进 §4.3 的单趟解码，
> 由 `DecodeEvents.samples` / `samples_timed_out` 返回。两趟全片解码曾使深度扫描耗时≈2×片长。

```bash
ffmpeg -nostdin -hide_banner -nostats -v info -i <path> -map 0:v:0 \
  -vf "<tonemap?>,freezedetect=n=..:d=..,blackdetect=..,fps=1/{color_sample_sec},showinfo,scale=32:18:flags=area" \
  -pix_fmt rgb24 -f rawvideo pipe:1 \
  -map 0:a:0? -af "silencedetect=..,volumedetect" -f null os.devnull
# 不用 -skip_frame nokey：只解关键帧时 fps 会把稀疏关键帧贴成名义时间，
# 造成时间戳错位与尾部采样点丢失（见 OPTIMIZATION #1）。fps 需完整帧流取最近帧。
# 两路都写 stdout 会互相踩，所以音频侧 `-f null os.devnull`。
```

HDR（`StreamInfo.video.color_transfer in ("smpte2084","arib-std-b67")` 且 `cfg.hdr_tonemap`）时，vf 前置：
`zscale=t=linear:npl=100,tonemap=hable,zscale=t=bt709:m=bt709:p=bt709,`（失败 → 返回 `None`，记 info，跳过 D10/D11）。

**双管道配对算法**（必须并行消费，否则互锁）：

```
frames = deque(); pts_q = deque()
thread_out: 循环 read(1728)；满 1728 → frames.append(bytes)
thread_err: 每行 if re.search(r"pts_time:\s*([0-9.]+)", line): pts_q.append(float)
主循环: while proc alive or buffers:
    当 len(frames)>0 and len(pts_q)>0: t=pts_q.popleft(); fb=frames.popleft(); 计算 Sample
    尾部余量: 丢弃（配不上，宁缺勿错）
结束: 若 pts_q 与 frames 都空且 exit_code!=0 → 返回已得样本（部分结果）
```

- 帧长常量 `FRAME_BYTES = 32*18*3 = 1728`。
- `Sample.t = showinfo pts_time`（C1）。ffmpeg 未加 `-copyts`，showinfo 的 pts_time 已被归一化到流起点（不再减 `origin_sec`，重复扣减会产生负时间）。`origin_sec` 参数保留仅为兼容调用方。
- `thread_err` 只认 `pts_time:`，其他行忽略（`-v info` 噪音大）。
- 超时/取消 → kill，保留已配对样本（可空）。`samples is None` = 采样未执行（快检 / HDR 回退等），
  `samples == []` = 执行了但一帧都没配上（异常）；`samples_timed_out` 与 `timed_out` 一起把 status 判成 `timeout`。

### 4.5 `media/color.py :: frame_stats(rgb: bytes) -> tuple[float,float,float,float,float,float]`

```
r = mean(rgb[0::3]); g = mean(rgb[1::3]); b = mean(rgb[2::3])
y = 0.299*r + 0.587*g + 0.114*b
mx = max(r,g,b); mn = min(r,g,b)
sat = 0.0 if mx == 0 else (mx-mn)/mx
cast = max(abs(r-y), abs(g-y), abs(b-y))
```

方向命名（`cast_direction`）：按 `dr,dg,db` 符号与大小：
- 单通道最大 → 偏红/偏绿/偏蓝；
- 两通道同号为正、第三为负 → 偏黄(r,g)/偏青(g,b)/偏洋红(r,b)；
- 三通道同号 → 整体偏亮/偏暗（**不算偏色**，归 D11）。

`is_color_cast(s, cfg)` = `s.cast > cfg.color_cast_threshold and s.y > 24 and not 三通道同号`；D10 必须用它做窗口判定，否则「整体提亮/压暗」会被误报成偏色。

---

## 5. core 规格

### 5.1 `core/pipeline.py :: analyze_file(path, cfg, mode, generation, cancel_event, *, on_stage=None) -> FileResult`

```
t0 = now()
ctx = FileContext(path, cfg)
origin = 0.0                                       # C1 唯一基准，probe 成功后取 video.start_time
1) on_stage("probe");   ctx.streams = probe_streams(...)   # 异常 → Issue(D01,high) → status="failed"
2) if ctx.streams.video is None: D01 high; 返回
   origin = ctx.streams.video.start_time or 0.0
3) on_stage("packets"); ctx.packets = probe_packets(path, origin, ...)   # 失败 → Issue(D01,low)，继续（快检核心）
4) on_stage("decode");  ctx.decode = run_decode_pass(path, cfg, has_audio=..., origin_sec=origin,
                                duration_sec=ctx.duration, hdr=..., video_index=..., audio_index=...,
                                cancel_event=...) if mode=="full" else None
   # 一趟解码同时产出：事件（freeze/black/silence/音量/错误）+ 32×18 采样（见 §4.4）
5) on_stage("color");   ctx.samples = ctx.decode.samples            # 与第 4 步同一趟，full 模式才有
6) for det in DETECTORS: ctx.issues += det(ctx)
   if ctx.decode is not None and not ctx.decode.completed and not cancel_event.is_set() and not 超时:
       ctx.issues.append(Issue("decode_aborted", medium, "解码中途中断，画面类结论可能不完整"))
7) merge_issues(ctx)                               # 含 C7 时间钳位
8) 状态按全局优先级确定：`cancelled > timeout > failed > done`。
   - 若 `cancel_event.is_set()`：`status="cancelled"`。
   - 否则若阶段超时标志有效：`status="timeout"`。
   - 否则若探测致命失败/无视频流：`status="failed"`。
   - 否则解码崩溃也保持 `done`（已追加 `decode_aborted` medium，结论为 warned，不会被算作 passed）。
9) `FileResult(..., ok=(result.conclusion == "passed"))`（结论只在 model 算，C8）。
```

- 每阶段 `try/except Exception`，转 Issue（`kind="internal_error"`, medium, detail=repr）并继续；唯一中断流程的是"无视频流"。`internal_error` 让 `conclusion="unfinished"`（分析没跑完不能报"通过"）。
- `on_stage` 只用于 UI 进度（controller 转 `file_stage` 信号），失败不影响流程。
- `stats` 至少包含：`duration, fps_nominal, p50_delta, packet_count, sample_count, mean_volume, max_volume, mode`。
- DETECTORS 注册顺序即输出优先级基础，最终以 `merge_issues` 排序为准。
- `mode=="fast"` 时 `decode=None, samples=None`，相关检测器自行跳过（见 §5.2 每项的 skip 条件）。

`FileContext`：

```python
@dataclass
class FileContext:
    path: Path
    cfg: Config
    streams: StreamInfo | None = None
    packets: PacketStats | None = None
    decode: DecodeEvents | None = None
    samples: list[Sample] | None = None
    issues: list[Issue] = field(default_factory=list)
    @property
    def duration(self) -> float: ...   # streams.duration or packets.last_ts
```

### 5.2 检测器规格（伪代码 + 边界）

所有检测器签名 `def check(ctx) -> list[Issue]`；`skip` 条件不满足时返回 `[]`（不产生"未检测"噪音，UI 用固定文案说明检查项）。

#### D01 `integrity.check_d01(ctx)`
```
if not ctx.streams: return [Issue("probe_failed", high, "无法解析该文件", detail=error_tail)]
if ctx.streams.video is None: return [Issue("no_video_stream", high, "文件中没有视频流")]
dur = ctx.streams.duration
if not dur or dur <= 0: Issue("no_duration", high, "容器未给出有效时长")
# 容器时长 vs 末包 的截断判定只归 D14，D01 不重复报（否则同一现象两条 Issue）
if 扩展名与 container 明显不符: Issue("container_mismatch", info)
```

#### D02 `integrity.check_d02(ctx)`
```
if not (video and audio): return []
dv, da = video.duration, audio.duration
if dv and da and abs(dv-da) > cfg.av_offset_sec:
    Issue("av_duration_mismatch", medium, f"音视频时长差 {abs(dv-da):.2f}s", evidence={dv,da})
off = (audio.start_time or 0) - (video.start_time or 0)
if abs(off) > cfg.av_offset_sec:
    Issue("av_start_offset", medium, f"音频起点偏移 {off:+.2f}s", detail="声音类时间点可能有同等偏移")
```

#### D03 `timing.check_d03(ctx)`（跳秒）
```
if not packets or len(pts) < 3: return []
deltas = diff(pts); p50 = median(deltas)
mad = median(|d - p50| for d in deltas)                    # 常态抖动（对少数真跳变免疫）
thr = max(p50*factor, p50 + min_gap, p50 + 6*1.4826*mad)   # 自适应：VFR/屏录的抖动不算丢帧
gaps = [(pts[i], pts[i+1], deltas[i]) for i if deltas[i] > thr]
# 合并：若相邻 gap 起点差 < 2.0s → 合并成一段（段内记最坏 delta）
for seg in gaps:
    missing = max(1, round(seg.max_delta / p50) - 1)
    sev = high if (seg.max_delta >= 1.0 or seg.total_missing*p50 > 0.005*dur) else medium
    Issue("pts_gap", sev, f"PTS 跳变，缺约 {missing} 帧（{seg.max_delta:.3f}s）",
          detail=f"delta={seg.max_delta:.3f}s, p50={p50:.3f}s, thr={thr:.3f}s",
          start=seg.start, end=seg.end, evidence={...})
```
边界：`p50 == 0`（全部重复 pts）→ 跳过 D03，交给 D04 报 `dup_pts`。

#### D04 `timing.check_d04(ctx)`
```
if not packets: return []
if packets.dts_non_monotonic > 0: Issue("dts_non_monotonic", medium, f"DTS 非单调 {n} 次")
if packets.dup_pts > 0: Issue("dup_pts", medium, ...)
# 帧间隔抖动（VFR 提示，info）；VFR 下 1/p50 不代表声明帧率，故 fps_mismatch 只在非 VFR 时报
vfr = stdev(deltas) > 0.3*p50
if vfr: Issue("vfr_suspected", info, "帧间隔波动大，疑似可变帧率", detail="此时帧率结论仅供参考")
if nominal_fps and p50 and not vfr and abs(1/p50 - nominal_fps)/nominal_fps > 0.2:
    Issue("fps_mismatch", low, f"声明 {nominal_fps:.2f}fps，实测 {1/p50:.2f}fps")
```

#### D05/D09 `freeze.check(ctx)`（冻结 + 黑场，含互相降级）
```
if decode is None: return []                       # fast 模式
out = []
for (s,e) in decode.blacks:
    d = e-s
    # 头尾黑场豁免只在「局部」成立：几乎全片黑场（≥80%）要报（见 §10.2 black4 用例）
    head_tail = (s <= 0.5 or e >= dur-0.5) and d < 0.3*dur
    if d >= 0.8*dur: sev = medium                # 几乎全片黑场
    elif head_tail: sev = info
    elif d > 5: sev = high
    elif d > 1: sev = medium
    else: sev = info
    out.append(Issue("black", sev, f"黑场 {d:.1f}s", start=s, end=e))
for (s,e) in decode.freezes:
    d = e-s; black = max_overlap((s,e), decode.blacks); quiet = max_overlap((s,e), decode.silences)
    if d/dur >= 0.9: sev, why = info, "（静止的监控画面）"
    elif black/d >= 0.8: sev, why = info, "（主要现象是黑场）"
    elif quiet/d >= 0.8: sev, why = info, "（同期静音，可能为暂停/加载画面）"  # 录屏/游戏加载画面
    elif d >= 10: sev, why = high, ""
    else: sev, why = medium, ""
    out.append(Issue("freeze", sev, f"画面冻结 {d:.1f}s{why}", start=s, end=e))
# decode_aborted 只在 pipeline 追加（§5.1 第 6 步），检测器不重复报
```

#### D06/D07/D08 `audio.check(ctx)`
```
if streams and not streams.has_audio and cfg.require_audio: return [Issue("no_audio", high, "文件不含音频流", detail="不会再有静音/音量结论")]
if decode is None: return []
out = []
mv, xv = decode.mean_volume, decode.max_volume
if mv is None and decode.completed: out.append(Issue("empty_audio", high, "音频流没有有效采样", detail="有音轨但解不出声音，可能是空轨道"))
if mv is not None:
    silent = mv < cfg.silent_mean_db and dur > 10
    ratio = sum(min(e,dur)-max(s,0) for s,e in decode.silences) / dur
    # 均音量低但底噪一直在（静音时长不到 80%）只算「音量偏低」，否则安静的正常片子会被判 failed
    if silent and ratio >= 0.8: out.append(Issue("silent_audio", high, f"全程近乎无声（均 {mv:.1f}dB）"))
    elif silent or mv < cfg.mean_volume_low_db: out.append(Issue("low_volume", medium, f"音量偏低（均 {mv:.1f}dB）"))
if xv is not None and xv >= cfg.clipping_db: out.append(Issue("clipping", low, f"存在削波风险（峰值 {xv:.1f}dB）"))
# 全程无声时，逐段静音只是同一现象的重复，跳过（C 类：避免同一问题报十几条）
full_silent = mv is not None and mv < cfg.silent_mean_db
silence_sec = sum(max(0.0, min(e, dur)-max(s, 0.0)) for s,e in decode.silences)
total_silence_ratio = silence_sec / dur if dur > 0 else 0.0
for (s,e) in decode.silences:
    if full_silent: continue
    d = e-s
    head_tail = (s <= 0.5 or e >= dur-0.5) and d < 0.3*dur
    sev = info if head_tail else ("high" if total_silence_ratio > 0.6 else "medium")
    if d >= cfg.silence_min_sec: out.append(Issue("silence", sev, f"静音 {d:.1f}s", start=s, end=e))
```

#### D10/D11 `colorcast.check(ctx)`
```
if not samples: return []                       # fast 模式 或 HDR 回退
# 按连续窗口找偏色段；三通道同号（整体亮/暗）不算偏色，纯色屏由 D11 报，这里排除
runs = group_runs(samples, lambda x: is_color_cast(x, cfg) and not is_solid(x))
# 判据按秒（v0.3.4）：采样间隔可调，沿用“连续 N 个采样点”会让窗口随时间网格漂移
for run in runs where span(run) >= cfg.color_window_sec:
    span = run[-1].t - run[0].t + cfg.color_sample_sec
    ratio = span / dur
    sev = high if (ratio >= 0.8 or median_cast > 60) else medium
    Issue("color_cast", sev, f"疑似偏{direction}（cast={median_cast:.0f}/255）",
          start=max(0.0, run[0].t), end=min(dur, run[-1].t + cfg.color_sample_sec),
          evidence={dr,dg,db,ratio})
# 曝光/纯色：每段连续命中各自成条（不把相隔很远的两个点连成一条长区间）
for pred, kind, sev, text in [
    (x -> x.y < 16 and 不与 black 重叠, "underexposed", low, "画面过暗"),
    (x -> x.y > 235,                  "overexposed", low, "画面过曝"),
    (x -> x.sat < 0.03 and 32<=x.y<=224, "flat_frame", low, "疑似纯色/白屏"),
    (is_solid,                        "solid_screen", high, "疑似纯色屏（绿屏/蓝屏等）"),
]:
    for run in group_runs(samples, pred):
        span = run[-1].t - run[0].t + cfg.color_sample_sec
        if kind == "solid_screen":
            if span < 5.0: sev = medium          # 短于 5s 可能只是纯色场景/转场
        elif span < 2.0: continue               # 过暗/过曝/纯色至少 2s：1s 网格下单帧不算
        Issue(kind, sev, f"{text}（{span:.0f}s）", start=run[0].t, end=run[-1].t,
              evidence={"samples": len(run), "span_sec": round(span, 1)})
# is_solid：sat >= 0.6 且 cast >= 60/255 且 24 < y < 235 —— 正常画面极少同时满足
# 采样覆盖不足提示
if len(samples) < 3: Issue("low_sample_count", info, f"仅采到 {len(samples)} 个样本，色彩结论仅供参考")
```

#### D12 `corruption.check(ctx)`
```
if decode is None: return []
# 两个来源分开报：坏包是传输层证据，解码错误是解码器证据；口径和严重度都不同
if packets and packets.corrupt_flags:
    ratio = packets.corrupt_flags / max(1, packets.packets)
    sev = high if (packets.corrupt_flags >= 4 or ratio > CORRUPT_RATIO_HIGH) else medium
    Issue("corrupt_packet", sev, f"视频包标记损坏 C/T（{ratio:.1%}）", ...)   # 1~3 个零星 C 位只算警告
if decode.video_errors:
    sev = high if decode.video_errors > 50 else medium
    Issue("decode_errors", sev, f"解码错误 {decode.video_errors} 处（疑似花屏/码流损坏）", ...)
if decode.audio_errors:                       # 音频报错单独一条，不能冒充花屏
    Issue("audio_decode_errors", medium, f"音频解码错误 {decode.audio_errors} 处", ...)
```

#### D13 `integrity.check_d13(ctx)`
```
info 级，仅在存在时输出：
- HDR（color_transfer in smpte2084/arib-std-b67）→ "HDR 素材，偏色判定使用 tonemap 后结果"
- rotate != 0 → "视频带旋转元数据 {r}°"
- SAR != 1/1 → "非方形像素 {sar}"
- audio.sample_rate < 32000 → "音频采样率偏低 {n}Hz"；audio.channels > 2 → "音频声道数异常 {n}"
```

#### D14 `integrity.check_d14(ctx)`
```
if packets and streams.duration:
    expected = video.duration or streams.duration      # 基准是视频流自己的时长
    gap = expected - packets.last_ts                    # 音轨比视频长不算截断（否则误报）
    if gap > cfg.truncation_gap_sec: Issue("truncated", medium, f"视频末帧比时长早 {gap:.1f}s，疑似录制中断")
    tail_black = 任一 black 且 end >= packets.last_ts - 5
    if tail_black: 合并进 truncated 的 detail
```

### 5.3 `core/merge.py :: merge_issues(ctx)`

```
1. 同 kind、区间重叠 > 50% 或首尾间隔 < 0.5s → 合并（start=min, end=max, detail 记「合并 n 段」）
2. freeze 区间被任一 black 包含 且 overlap/d >= 0.8 → freeze.severity = "info"
3. 跨项收敛（`_correlate`，同一处故障只留一条主问题）：
   - freeze 与任一 pts_gap 重叠 ≥ 50% 段长 → freeze.severity = "info"，detail 加「与跳秒同段，可能是同一处丢帧」
   - 片长 ≥ 10s、black 覆盖 ≥ 90% 片长、且存在 `silent_audio`/`no_audio` → black.severity = "high"，detail 加「可能整段没录到画面」
4. start=None 的 Issue 排在同类之后
5. 排序：SEV_ORDER 降序 → start(None 视为 +inf) 升序 → kind 字典序
6. 断言 §2.1 不变式
```

---

## 6. app 规格

### 6.1 `Session`

```python
class Session:
    def __init__(self): self._results: dict[str, FileResult] = {}
    def get(self, path) -> FileResult | None
    def put(self, r: FileResult) -> bool      # True=已接受；旧 generation 或已 full 被 fast 覆盖 → False
    def clear(self)
    def summary(self) -> dict                 # total/passed/warned/failed/unfinished/kinds
    def all(self) -> list[FileResult]         # 按 path 排序
```

`put` 规则（C4/D19）：`existing is None` → 接受；否则先比 mode 等级（`MODE_RANK = {"none":0, "fast":1, "full":2}`）：`r` 等级 > `existing` 等级 → 接受，< → 拒绝，相等才比 `generation`（`r.generation > existing.generation` → 接受）。**不能只比 generation**：先提交后完成的 full 结果代次更小，会被刚完成的 fast 结果拦住，深度扫描白跑。

### 6.2 `ScanController(QObject)`

信号：

```python
file_started = Signal(str, str)        # path, mode
file_stage   = Signal(str, str)        # path, stage("probe"|"packets"|"decode"|"color")
file_done    = Signal(object)          # FileResult
scan_finished= Signal(str, dict)       # mode, {"total","done","failed","elapsed"}
scan_error   = Signal(str)             # 全局错误（如 ffmpeg 缺失）
```

方法：`start(paths: list[str], mode: str)`, `stop()`, `is_running() -> bool`。
实现约定：
- 内部 `ThreadPoolExecutor(max_workers=cfg.jobs)`；`stop()` 置 `cancel_event` 并 `executor.shutdown(cancel_futures=True)`。
- 单文件去重（C3）：`self._running: dict[path, mode]`；已存在同/更高 mode → 跳过并在 `file_stage` 发 `"skipped"`（UI 文案「复用已有结果」）。
- 每文件在**提交时**生成 `generation = next(self._gen)`（全局自增 int）；不得用完成顺序，否则盘上缓存与 put 优先级会错位。
- pipeline 的 `on_stage` 回调 → `file_stage.emit(path, stage)`。
- 播放中：`set_playing(bool)` → 实际 workers 从 2 降 1（不重建线程池，只在新任务提交时排队；简化实现：`jobs = 1 if playing else cfg.jobs`）。
- 所有信号从 worker 线程 emit（Qt 自动排队到主线程，`FileResult` 为纯 dataclass，安全）。

### 6.3 `cli.py`

```
videoqc.exe --headless PATH... [-o report.md] [--json report.json] [--jobs N] [--fast]
            [--min-severity low|medium|high] [--timeout SEC] [--config FILE]
```
- 线程池（`--jobs`，默认取 config.jobs）；完成顺序打乱后按路径重排，报告可 diff。每完成一个往 stderr 打一行 `[3/12] cam03.mp4 → warned`。路径里没有可分析文件时退出码 2（不能把写错的路径当"全部通过"）。
- `-o` 默认 `reports/report_YYYYMMDD_HHMM.md`，`--json` 默认同名 `.json`。
- 退出码：0 = 全部 `conclusion=="passed"`；1 = 存在未通过文件（warned 或 failed，**low 也算**）；2 = 运行错误（参数/ffmpeg 缺失/目录不可读）。
- Windows 控制台接管：`AttachConsole(ATTACH_PARENT_PROCESS)`，失败 `AllocConsole()`；随后重开 `sys.stdout/stderr`。
- 支持 `--version`：只输出 `videoqc {__version__}` 到 stdout，退出码 0，不加载配置、不启动 ffprobe。
- 未知参数、缺少参数值、目录不存在、文件不可读：使用 stderr 输出 `参数错误：...`，退出码 2，不生成报告。
- `--min-severity` 只控制控制台与报告中是否列出低于阈值的 Issue，不改变文件结论、退出码和 JSON 内容。
- `--jobs` 只覆盖本次 headless 执行，不写回配置文件。

### 6.4 取消、导出与路径边界

- 用户停止扫描时，先设置取消事件，再停止后续任务；已提交但尚未启动的 future 标记为 `cancelled`，已完成结果保留。
- 导出报告时：
  - 只包含文件表中已经加载的路径；正在运行或未分析的文件也可列出，标记为「未分析」。
  - 未分析文件用 `FileResult(status="pending", mode="none")` 表示（ok 必须为 False），报告中显示「未分析」，绝不计入通过。
- `report_dir` 解析：若为相对路径，相对程序根 `ROOT`；若为绝对路径，原样使用。
- 所有用户路径先规范化为 `Path.resolve(strict=False)`；Windows 长路径由 `long_path()` 统一转换为 `\\?\...`。
- 重复文件名的相对显示名：在表格中保留最近一级父目录，例如 `A/cam.mp4`、`B/cam.mp4`；无歧义时只显示文件名。

---

## 7. ui 规格

### 7.1 布局（`MainWindow`）

```
QMainWindow
├─ QToolBar: [选择目录…] [快速扫描] [深度扫描] [停止] | [导出报告]
├─ central QWidget → QVBoxLayout
│   ├─ QLabel 汇总行（"共 12 个文件 · 通过 7 · 警告 3 · 失败 2"）+ 计数
│   └─ QSplitter(Horizontal, 不可折叠)
│       ├─ 左 QWidget（窗口宽 50%，下限 340px）: FileTableView（无按钮行）
│       └─ 右 QWidget（QHBoxLayout+QVBoxLayout）
│           ├─ 播放器行: PlayerPanel 占满右半区宽度、高度 50%
│           │   （高度 = 窗口 50%，下限 240；宽度铺满右半区——不要写 50%·窗口宽，
│           │    那样会超过右半区，把左侧列表挤到不足 1/2）
│           │   右半区宽度由 `_apply_layout_sizes()` 用 `setFixedWidth()` 钉死：加载文件、
│           │   滚动条进出、切换选中文件都不许挤动它（否则边界会在选目录后跳一下）
│           └─ IssuePanel（吃满剩余空间）
└─ QStatusBar: 最近操作 | {扫描进度 QProgressBar（仅扫描中显示）} | 阈值提示
```

- **高 DPI**：`QApplication.setHighDpiScaleFactorRoundingPolicy(PassThrough)`（Qt6 默认按整数缩放，125%/150% 下表格列宽与图标会错位）；表格列宽用 `QHeaderView` 比例模式，图标用 SVG/多尺寸 ico。
- 窗口最小 1100×700。左右比例与播放器尺寸是**固定规则**（左 50% 宽、播放器 50%×50%），不存 `QSettings`——可拖动的分割条会让人把比例拖坏，而这两处没有"个人偏好"的价值。

### 7.2 `FileTableModel`（`QAbstractTableModel`）

列：`# | 名称 | 时长 | 问题数`。`#` 是模型第 0 列（固定 44px），**不用垂直表头**——表头的单元格看起来像可勾选的方框，容易误解。
- 名称列 `Stretch`（宽度只随窗口变）；`时长`/`问题数` 用 **Fixed** 宽度（104 / 64）——`ResizeToContents` 会随内容（`—` → `00:11:45.633`）变宽，把名称列挤得来回跳。
- 「结论」不进列：它与「问题数」高度相关（0 个问题=通过），单独一列是重复信息；结论用整行底色表达（红/黄/灰），文字结论放 ToolTip。
- 「快检/全量」不进列，同样放 ToolTip。
`data()` 映射：
- `DisplayRole`：`#` 给 `row+1`；名称给相对路径（同一文件名去重时显示父目录），时长用 `fmt_tc`（省略毫秒），问题数直接显示 `0`（不要用 `—`，`0` 才是「查过了，没问题」）。
- `BackgroundRole`：passed→`#E9F7EF`（绿），failed→`#FDECEA`（红），warned→`#FFF6E0`（黄），unfinished→`#EEEEEE`（灰）。
- `ToolTipRole`：绝对路径 + 结论 + 状态 + 模式。
- 数据形状：`_paths`（选目录时一次性全量，**行数从加载到扫描结束都不变**） + `_results[path]` + `_durations[path]`。
  不要用「结果列表 + 待分析集合」拼行号——排序或回填后两套索引会错位，表现为少一行/空行（本项目踩过）。
- `set_paths(list)` 一次性重建；`set_result(r)` / `set_duration(path, sec)` 只发该行 `dataChanged`，不 reset 模型（避免整表闪烁）。
- 行号 `#` 直接用 `index.row()+1`，不存字段。
- **不提供排序**（`setSortingEnabled(False)`，模型无 `sort()`）：行序 = 选目录时 `iter_videos()` 的返回顺序（按路径），扫描过程中不重排——重排正是列宽抖动的来源。

### 7.3 `PlayerPanel`

```python
class PlayerPanel(QWidget):
    error = Signal(str)
    def load(self, path: str, origin_sec: float = 0.0) -> None   # origin_sec = video.start_time（C1）
    def seek_to(self, sec: float, autoplay: bool = True) -> None
    def set_frame_step(self, ms: int) -> None
    def fps(self, fps: float | None) -> None
```
- 时间标签格式 `mm:ss`（≥1h 为 `h:mm:ss`），不显示毫秒（毫秒只会让标签每帧抖动，判断落点靠问题表的毫秒时间）
- 组成：`QVideoWidget` + `QSlider(seek)` + 时间标签 + `QToolButton`(播放/暂停) + `QComboBox(倍速)` + 逐帧◀/▶ + `QSlider(volume)`。
- `seek_to`：`player.setPosition(int((sec + self._origin)*1000))`；`autoplay` 时 `play()`。Issue 时间是「视频流内秒」，而 Qt 从文件 0 点播放，不减这个偏移就到不了位（C1）。
- 逐帧：暂停态下 `setPosition(pos ± round(1000/fps))`；`fps` 未设用 25。**不要求帧精确**（需求已放松）：Qt 会吸附关键帧/时间基，误差按 GOP 计（≤2s，见 R11），不做 ffmpeg 单帧通道。
- `player.errorOccurred` → `error.emit(player.errorString())`，UI 在画面区显示文案（不弹窗）；同时禁用控制条。
- `mediaStatusChanged == EndOfMedia` → 按钮回「播放」，进度条归位。
- 无音轨（来自 FileResult）：不要把 `QAudioOutput` 置 0，保持默认；只把音量控件 `setEnabled(False)`。

### 7.4 `IssuePanel`

```python
class IssuePanel(QWidget):
    issue_activated = Signal(object)   # 双击/Enter → 跳转并播放
    issue_focused   = Signal(object)   # 单击 → 跳转并暂停（保留扩展）
    def load(self, r: FileResult | None, stage: str | None = None) -> None
```
- 标题行：`cam02.mp4 · 发现 1 个问题（1 警告）` / `未发现异常 · 已检查 14 项` / `分析中…（正在解码）` / `未分析 · 点「深度扫描」或双击行`。
- 列：`# 40 | 时间 170 | 类型 120 | 描述 拉伸`（`#` 是 1..N 行号，与文件表一致）；时间用 `start → end`（任一为 None 显示 `—`）。
- 竖向滚动条 `ScrollBarAlwaysOn`：问题条数在文件之间变化很大，滚动条进出会让「描述」列来回抖 14px。
- 级别不占列：类型文字按 severity 着色（红/橙/灰）+ 图标前缀；`detail`/证据放该行 ToolTip。
- 空态：就是**空表**（0 行），不再放第二页清单——状态全由标题行表达（未分析 / 分析中 / 未发现异常）。「检查了哪些」只在报告附录里给。
- 右键菜单：`复制时间点`、`复制复现命令`。
- `issue_activated` → `player.seek_to(start-1.0, autoplay=True)`；`issue_focused` → 同位置 `autoplay=False`（只定位不打断）。`start is None` 时不 seek，只高亮。

### 7.5 `MainWindow` 交互契约

| 事件 | 行为 |
|------|------|
| 选择目录 | `QFileDialog.getExistingDirectory` → `iter_videos()` → 清空 Session + 表格**一次性建满 N 行**；状态栏「已加载 N 个文件」，随后 2 线程轻量 `ffprobe` 补时长列 |
| 单击行 | `issue_panel.load(session.get(path), stage)`；若 `quick_check_on_select` 且无结果 → `controller.start([path], "fast")` |
| 双击行/Enter | `player.load(path)` + **立即播放**（`seek_to(0, autoplay=True)`）；若已有 full 结果 → `issue_panel.load` |
| 选择目录后 | 立刻用 2 线程跑轻量 `ffprobe` 填「时长」列（只读元数据，不解码），带代次守卫防止换目录后串行 |
| 深度扫描 | 开工先 `session.drop(paths)` + `model.reset_results(paths)` 清掉这一轮要重扫的行（颜色/问题数回到未分析），再 `controller.start(全部文件, "full")`——深度扫描总是重扫，**完成一个填一个颜色** |
| 停止 | `controller.stop()`；状态栏「已停止，保留已完成结果」 |
| 扫描进度 | 工具栏→状态栏 `QProgressBar(0..N)`：`file_done` 递增，`scan_finished` 隐藏；比例模式下按合并去重后的文件数 |
| 导出报告 | 若 `session.all()` 为空 → 提示；否则 `QFileDialog.getSaveFileName`（默认 `reports/report_时间.md`）→ `render_markdown` + `dump_json` 同目录 |
| 拖入文件/目录 | 文件→播放；目录→选择目录 |
| 关闭窗口 | 若 `controller.is_running()` → `QMessageBox` 确认 → `stop()` 后退出 |

快捷键：`Space` 播放/暂停，`←/→` ±5s，`Shift+←/→` 逐帧，`Enter` 播放选中，`Ctrl+O` 选目录，`F5` 快速扫描，`F6` 深度扫描，`Esc` 停止扫描。

---

## 8. 报告规格（`media/report.py`）

### 8.1 文件名与路径

`report_paths(root_dir, when) -> (md_path, json_path)`：`{root_dir}/{stem}.md` 与 `.json`，`stem = "report_" + when.strftime("%Y%m%d_%H%M")`。`root_dir` 默认 `ROOT/cfg.report_dir`，不存在则创建。同名已存在（同分钟导出两次）→ 追加 `_2`、`_3`，**绝不覆盖**。

### 8.2 MD 模板（严格照此渲染，便于回归 diff）

```
# 录像质量检测报告

扫描目录：{root}
生成时间：{generated_at:%Y-%m-%d %H:%M:%S}
工具：videoqc {version} · ffmpeg {ffmpeg_version} · 模式：{mode_label} · 阈值：{config_hash[:8]}
（`config_hash = sha256(canonical_json(config_effective))[:8]`，canonical = key 排序、紧凑分隔符；屏幕上的「阈值」提示行用同一哈希 ↦ 两边对得上）

合计 {total} 个文件：通过 {passed} · 警告 {warned} · 失败 {failed} · 未完成 {unfinished}

## 概览

| # | 文件 | 时长 | 范围 | 结论 | 问题 |
|---|------|------|------|------|------|
| 1 | {relpath} | {fmt_tc_short} | 全量 | ❌ 失败 | 跳秒×2、无音轨 |

## 详情

### 1. {relpath} — ❌ 失败
容器 {container} · {codec} {w}x{h} {fps}fps {kbps} · 音频：{audio_desc}

| 时间 | 类型 | 级别 | 描述 | 证据 |
|------|------|------|------|------|
| {fmt_tc(start)} → {fmt_tc(end)} | 跳秒 | ❌ | PTS 跳变，缺约 24 帧（0.960s） | delta=0.960s, p50=0.040s, thr=0.120s |

<details><summary>复现命令</summary>

```bash
{repro_cmd}
```
</details>

## 附录

### 检测项
| ID | 检测项 | 本次是否执行 |
|----|--------|--------------|
| D01 | 文件完整性 | ✔ |
...

```

> v1 报告**不列**阈值表与 ffmpeg 路径：报告是给人扫结论用的，不是审计材料。
> 需要复现时用头部的 `config_hash` 前 8 位对照 `config.json`。

- 结论图标与排序**直接读 `conclusion`（C8）**，不在这里重算：`✅ 通过`=passed、`⚠️ 警告`=warned（medium 或 low）、`❌ 失败`=failed（high）、`⏸ 未完成`=unfinished（status≠done，含未分析）。
- 排序：failed → warned → unfinished → passed；同组按问题数降序、path 升序。
- `mode_label`：全量 / 快检 / 混合。
- 详情表只放 `detail`/标题（表格内单行、`|` 需转义为 `\|`），`evidence` 不进报告。

### 8.3 复现命令模板

```
ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,duration_time,flags -of csv=p=0 "{name}"
ffmpeg -nostdin -v info -i "{name}" -vf "freezedetect=n=0.003:d=2,blackdetect=d=0.5:pic_th=0.98:pix_th=0.10" -af "silencedetect=n=-50dB:d=3,volumedetect" -f null -
```
（数值取实际 cfg，路径用文件名而非全路径。）

---

## 9. 打包规格

### 9.1 `packaging/videoqc.spec`（onedir）

```python
# -*- mode: python -*-
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))   # spec 在 packaging/ 下

a = Analysis(
    [os.path.join(ROOT, 'videoqc', '__main__.py')],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, 'assets', 'app.ico'), '.')],
    excludes=['tkinter', 'test', 'unittest',
              'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtWebEngineCore',
              'PySide6.QtWebEngineWidgets', 'PySide6.Qt3DCore', 'PySide6.QtDBus',
              'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtPdf'],
    # QtQml/QtQuick 可排：Qt6Multimedia.dll 只依赖 Qt6Core/Gui/Network（已核实）
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name='videoqc',
          console=False, icon=os.path.join(ROOT, 'assets', 'app.ico'),
          version=os.path.join(SPECPATH, 'version_info.txt'))
coll = COLLECT(exe, a.binaries, a.datas, name='videoqc')
```

**为什么不是 `'../videoqc/__main__.py'`**：`Analysis` 的脚本路径按 **CWD** 解析（`absnormpath`），而 `EXE(icon=)`、`version=`、`datas` 按 **SPECPATH** 解析——两套基准。构建脚本 `chdir(ROOT)` 后再调 PyInstaller，`'../videoqc/__main__.py'` 会指到仓库根之外。统一用 `SPECPATH` 推 `ROOT` 再拼绝对路径，就不依赖调用时的 CWD。

**注意**：`config.json` 不放进 `_internal`，由 `build_portable.py` 复制到 exe 同级。

### 9.2 `packaging/build_portable.py` 步骤

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
python packaging/build_portable.py
```

脚本内（全部路径相对仓库根，脚本自己先 `os.chdir(ROOT)`）：

```
1. subprocess.run([sys.executable, "-m", "PyInstaller",
      "--noconfirm", "--clean", "--workpath", "dist/_work",
      "--distpath", "dist", "packaging/videoqc.spec"])
2. out = dist/videoqc/  ; 复制 config.json、README.md、LICENSE、packaging/licenses/（含 gpl-3.0.txt、PYTHON-3.13.txt、lgpl-3.0.txt）
3. 复制 bin/ffmpeg.exe, bin/ffprobe.exe（源：`<repo>/bin/`，必须事先存在，否则构建失败）
4. 校验清单（缺一即非零退出）：
   videoqc.exe, config.json, bin/ffmpeg.exe, bin/ffprobe.exe,
   _internal/PySide6/plugins/multimedia/ffmpegmediaplugin.dll,
   _internal/PySide6/Qt6Core.dll, _internal/vcruntime140.dll, _internal/python313.dll,
   README.md, LICENSE, licenses/THIRD-PARTY.md   # vcruntime 必须自包，否则违背 R9「不装 VC 运行库」
5. 冒烟：`videoqc.exe --headless --version` 与 `--headless tests/data/base.mp4 -o <临时目录>/smoke.md`，断言退出码与 md 存在（无夹具时跳过并告警；报告写临时目录，不在 `dist/` 留垃圾）
6. zip: dist/videoqc-{__version__}-win64.zip（zipfile.ZIP_DEFLATED, compresslevel=9），保留目录前缀
7. 输出 sha256：写 `dist/videoqc-{__version__}-win64.zip.sha256` 并打印到控制台（分发时随包给出）
```

- **不能叫 `build/`**：PyInstaller 默认 `--workpath` 就是 `build/`，与源码目录撞名，`--clean` 会把源码一起清掉。所以源码放 `packaging/`、中间产物放 `dist/_work/`。
- `dist/` 与 `bin/` 都进 `.gitignore`；`packaging/` 入库（体积小、可复现）。

`version_info.txt` 由脚本生成：产品名 `videoqc`、版本取 `version.__version__`、公司/版权占位。

---

## 10. 测试规格

### 10.1 夹具（`tests/make_fixtures.py`，全部 320×180/30fps/20s，跑得快）

```bash
ffmpeg -f lavfi -i testsrc2=s=320x180:r=30 -f lavfi -i sine=f=440:sample_rate=44100 \
       -t 20 -pix_fmt yuv420p -c:v libx264 -c:a aac -shortest base.mp4

# 跳秒：保留原 PTS，删掉 5–6s 的包 → 时间轴空洞
ffmpeg -i base.mp4 -vf "select='not(between(t,5,6))'" -fps_mode passthrough -an -c:v libx264 gap.mp4

# 黑场：4s 全黑片（验证「全片黑场」不被误降为 info）
ffmpeg -f lavfi -i "color=c=black:s=320x180:r=30" -t 4 black4.mp4
# 冻结：10s 片子中间 4–7s 冻住（freezeframes 滤镜需双输入，这里用 trim+loop+concat）
ffmpeg -f lavfi -i testsrc2=s=320x180:r=30 -t 10 -filter_complex "\
  [0:v]trim=0:4,setpts=PTS-STARTPTS[a]; \
  [0:v]trim=4:4.1,setpts=PTS-STARTPTS,loop=loop=89:size=1,setpts=N/30/TB[b]; \
  [0:v]trim=7:10,setpts=PTS-STARTPTS[c]; \
  [a][b][c]concat=n=3:v=1,format=yuv420p[o]" \
  -map "[o]" -c:v libx264 freeze3.mp4
# 偏色：整片偏红
ffmpeg -i base.mp4 -vf "lutrgb=r=val*1.35:g=val*0.6:b=val*0.55" -c:v libx264 -c:a copy cast.mp4
# 无声 / 静音 / 削波
ffmpeg -i base.mp4 -an noaudio.mp4
ffmpeg -i base.mp4 -af "volume=0" muted.mp4          # 全静音
ffmpeg -i base.mp4 -af "volume=20dB" hot.mp4         # 削波风险（峰值打到 0dB）
# 截断：faststart 把 moov 提到头，再按字节截掉尾部 1/3 → 容器时长仍在、包不够
ffmpeg -i base.mp4 -c copy -movflags +faststart fs.mp4
python -c "d=open('fs.mp4','rb').read(); open('trunc.mp4','wb').write(d[:len(d)*2//3])"
# 损坏：base 的 mdat 中部抹零 4KB（不抹 moov，不然就变成 probe 失败；基于 base 以免带上 gap 的既有缺陷）
python -c "import pathlib; p=pathlib.Path('base.mp4'); d=bytearray(p.read_bytes()); m=d.find(b'mdat'); o=m+len(d[m:])//2; d[o:o+4096]=b'\0'*4096; pathlib.Path('corrupt.mp4').write_bytes(d)"
```

批量小样片（只为把界面列表撑出滚动条，也为人工点着看）：

```bash
python tests/make_fixtures.py --bulk 24     # 生成 bulk_01..24.mp4：3s，每 6 条一轮
                                            # 正常 / 全黑 / 1fps 近静止 / 全静音 / 偏红 / 无音轨
```

`tests/data/` 里的真实录像与 `*.mp4` 不进版本库（`.gitignore`），换机器要自己放；`bulk_*` 与 `base/gap/...` 一样是本地生成的，丢了跑一次脚本即可。

### 10.2 期望结果（回归断言表）

| 夹具 | 必须命中 | 必须不命中 |
|------|----------|-----------|
| base | （无 low/medium/high） | pts_gap, freeze, black, color_cast, silence(>=3s), decode_errors, audio_decode_errors |
| gap | `pts_gap` high（合并后 1 条，区间含 5–6s） | freeze |
| freeze3 | `freeze` medium（区间在拼接点附近，±0.3s） | pts_gap |
| black4 | `black` medium | — |
| cast | `color_cast`（偏红，ratio ≥ 0.6，severity ≥ medium） | black |
| noaudio | `no_audio` high | silence |
| muted | `silent_audio` high（静音分段被抑制，不逐段报） | silence |
| hot | `clipping` low | no_audio |
| trunc | `truncated` medium（片尾解不出来时允许同时出现 `decode_errors`） | probe_failed |
| corrupt | `decode_errors`（视频错误数 ≤50 ⇒ medium，超过才是 high；零星 C 位另报 `corrupt_packet` medium） | probe_failed |

### 10.3 单元测试清单（pytest，无 Qt 依赖）

已落地（`pytest -q` 83 passed，约 10s）：

- `test_model.py`：不变式、`fmt_tc`、结论派生（含 status=failed + high ⇒ failed）。
- `test_config.py`：缺文件/坏 JSON/越界/未知键 → 告警与 clamp；`config_hash` 稳定。
- `test_probe.py`：真实夹具的 `probe_streams`/`probe_packets`、损坏文件抛 `ProbeError`、报告路径 `_2` 不覆盖、`render_markdown` 结构断言。
- `test_pipeline.py`：端到端快检通过；损坏文件 ⇒ `status="failed"` 且只有一条 `probe_failed` high。
- `test_decode.py`：`tests/data/*.log` 真实 stderr 样本喂解析器；D02–D14 合成 Context 用例；8 个夹具的全量端到端。
- `test_frames.py`：1728 字节全 0/全 255/半红 → r,g,b,y,sat,cast；`cast_direction`；D10/D11 单元；cast 夹具端到端。
- `test_session.py`：模式深度优先 + 代次（full 不被 fast 覆盖、full 代次更小也接受）、summary 计数、`drop()`。
- `test_cli.py`：退出码 0/1/2 契约。
- `test_ui.py`（offscreen）：加载与时长预填、扫描填结果与问题面板、列宽全程不变、导出 md+json、深度扫描先清色再上色、30 行列表超出可视区（滚动条出现、列宽不变、选中末行自动滚动）。

待补：

- `test_merge.py`：freeze 被 black 包含 → info；相邻同 kind 合并（目前只有端到端间接覆盖）。
- `test_report.py`：golden 对比 `tests/data/report_expected.md`。
- `test_cli.py`：headless 退出码契约（0 通过 / 1 有问题 / 2 参数错误），用 `tests/data/base.mp4` 与一个非视频文件。
- `test_ui.py`（`QT_QPA_PLATFORM=offscreen`，conftest 统一设置）：窗口构建 + 扫描信号往返，不跑 QtMultimedia 解码；`MainWindow.ask_before_quit = False` 关掉「扫描中退出」确认框，否则 offscreen 下会卡住。

---

## 11. 验收清单（需求 → 验证动作）

| 需求 | 验证 |
|------|------|
| R1 递归扫描 | 造 `a/b/c.mp4` 三层，`iter_videos` 返回全部；隐藏文件与 `~$` 临时文件被跳过 |
| R2 跳秒 | `gap.mp4` → 报告时间落在 5–6s，缺帧数 ≈ 30 |
| R3 卡顿 | `freeze3.mp4` → 冻结区间 ≈ 3s，落在拼接位置 |
| R4 无声 | `noaudio/muted` → 无音轨 / 全程静音分别在报告出现 |
| R5 偏色 | `cast.mp4` → 偏红、high、区间覆盖 ≥ 80% |
| R6 画面异常 | `black4/corrupt` → 黑场 + 解码错误各一条 |
| R7 产物 | 导出 md/json，md 中所有问题都有 `HH:MM:SS.mmm` |
| R8 失败隔离 | 目录里放 1 个坏文件 + 2 个正常 → 全部完成，坏文件单条失败 |
| R9 零依赖 | 干净 Win10 VM 断网解压运行 |
| R10 三分区 | 目视 + 三区都可拖拽 |
| R11 播放 | 双击播放、拖动 seek、逐帧、点异常跳转（允许关键帧误差 ≤2s；逐帧能连续步进即可，不要求帧精确） |
| R12 一键全扫 | 快速扫描 20 个文件 < 5min（包级探测为主，每文件 3–10s）；深度扫描有进度、可停止、停止后已完成结果保留 |
| R13 结构清晰 | 解压后顶层 5 项 + `_internal/`，无 `.py`/`__pycache__` |

---

## 12. 实现顺序（依赖拓扑，逐步可运行）

1. `model.py` + `fmt_tc` + 不变式断言 + 单测。
2. `config.py` + 单测（可独立验证）。
3. `paths.py`（ROOT/长路径/扩展名）。
4. `probe.py` → 用真实文件手工跑通，打印 StreamInfo/PacketStats。
5. `runners.run_decode_pass` + 解析器 → 用 `tests/data/*.log` 脱机验证，再真实文件。
6. `runners.run_decode_pass` 的采样子路（双管道）→ 断言样本数与时长/采样间隔一致（±1）。
7. `pipeline.py` + `detectors/*` + `merge.py` → D01/D03 先跑通，逐项加。
8. `report.py` + CLI → `--headless` 出 md，跑 §10.2 全表。
9. `controller.py` + `session.py`（无 UI 的单测）。
10. `ui/*` → 空态 → 列表 → 播放 → 面板 → 扫描。
11. 打包 + §11 验收。

> 第 1–11 步全部落地；当前实测见 `docs/DESIGN.md` §0（只记一处，避免两份文档各写一套数字）。
