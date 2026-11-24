# videoqc · 录像质量检测工具

批量检查录像文件（H.264/MP4），把「跳秒、卡顿、黑场、静音、坏包、截断、偏色」等录制缺陷定位到秒，输出 Markdown/JSON 报告。

- 免安装：解压即用，自带 Python 运行时与 ffmpeg，不装 VC 运行库
- 两种用法：桌面 GUI（列表 + 播放器 + 问题面板）；`--headless` 命令行（适合批处理/服务器）
- 面向场景：安防/直播/会议录像的交付前自检

## 快速开始

### 用打包版

1. **不想解压**：直接双击 `dist/videoqc/videoqc.exe`（同一份构建产物，未压缩目录形态）
2. 或者解压 `videoqc-0.3.5-win64.zip` 到任意目录（路径可以有中文和空格）
3. 双击 `videoqc.exe` → 「选择目录…」→ 选视频目录 → 「快速扫描」/「深度扫描」
4. 点问题行，右侧播放器跳到该时间点；「导出报告」生成 `.md` + 同名 `.json`

### 命令行

```bat
videoqc.exe --headless D:\录像 --fast              :: 快检：只读容器与包，秒级
videoqc.exe --headless D:\录像 -o report.md        :: 全量：解码画面与音量
videoqc.exe --headless D:\录像 --min-severity high :: 只显示 high 级问题
videoqc.exe --headless --version
```

退出码：`0` 全部通过 · `1` 有未通过文件（警告也算）· `2` 运行错误（参数/ffmpeg 缺失/目录不可读）。

## 检测项

| ID | 检测项 | 需要解码 | 说明 |
|----|--------|:--:|------|
| D01 | 文件完整性 | | 能不能探测、有没有视频流、扩展名与容器是否一致 |
| D02 | 音视频对齐 | | 两轨时长差、起点偏移超过 `av_offset_sec` |
| D03 | 跳秒 | | PTS 空洞（录制卡顿丢帧；阈值随帧间隔抖动自适应） |
| D04 | 时间戳异常 | | DTS 回退、重复 PTS、帧率不符、疑似 VFR |
| D05 | 画面冻结 | ✔ | `freezedetect`；同期也静音时只记 info（暂停/加载画面） |
| D06 | 静音段 | ✔ | `silencedetect`，≥`silence_min_sec` |
| D07 | 音量异常 | ✔ | 整体偏小、整片无声（需 ≥80% 时长判为静音）、削波 |
| D08 | 无音轨 | | 容器里没有音频流（`require_audio=false` 时不报）；有音轨但无声音也报 |
| D09 | 黑场 | ✔ | `blackdetect`，全片黑场会单独提示 |
| D10 | 偏色 | ✔ | 抽样帧的颜色偏移 |
| D11 | 曝光/纯色 | ✔ | 过暗、过曝、纯色无纹理、纯色屏（绿屏/蓝屏） |
| D12 | 坏包 | ✔ | 解码报错数量超过阈值 |
| D13 | 素材口径提示 | | HDR、旋转元数据、非方形像素、低采样率/多声道（不算缺陷） |
| D14 | 录制中断截断 | | 视频末帧比视频流时长早 `truncation_gap_sec`（音轨比视频长不算） |

「快速扫描」只做 D01–D04、D13、D14 这类容器/包级检查；「深度扫描」才解码画面与音量（D05–D12），耗时约等于视频时长。

## 配置

`config.json` 与 `videoqc.exe` 同级，缺失或某项非法时用默认值并在 stderr 提示，不会自动改写文件。常用项：

| 键 | 默认 | 含义 |
|----|------|------|
| `extensions` | 12 种常见后缀 | 扫哪些文件 |
| `jobs` | 2 | 并发分析的线程数（GUI 播放时自动降到 1） |
| `freeze_min_sec` / `black_min_sec` / `silence_min_sec` | 2.0 / 0.5 / 3.0 | 判定为缺陷的最短持续时长 |
| `silence_db` | -50 | 静音阈值 |
| `av_offset_sec` | 0.5 | 音视频对齐容差 |
| `truncation_gap_sec` | 2.0 | 判定截断的尾部空洞 |
| `color_cast_threshold` | 25 | 偏色阈值（0–255 色偏量） |
| `quick_check_on_select` | true | 单击文件就自动快检 |
| `require_audio` | true | false = 没有音轨也不算缺陷 |
| `report_dir` | `reports` | 报告默认目录（相对程序根） |

完整键表与取值范围见 [`docs/SPEC.md`](docs/SPEC.md) §3。

## 开发环境配置

只需做一次（Windows x64 + Python 3.13；PyInstaller 不支持交叉编译，Linux/macOS 上出不了包）：

1. **虚拟环境 + 开发依赖**（版本全部钉在 `requirements-dev.txt` 里）：

    ```bat
    python -m venv .venv
    .venv\Scripts\python -m pip install -r requirements-dev.txt
    ```

2. **放 ffmpeg**：把 `ffmpeg.exe`、`ffprobe.exe` 放进仓库根的 `bin/`。来源是 [gyan.dev essentials 静态包](https://www.gyan.dev/ffmpeg/builds/)，解压后取 `bin\` 下的这两个文件即可。这两个文件共 211MB，超出 GitHub 单文件 100MB 上限，所以不入库。

   网络差时用这两条路，别死等：从另一台已经配好的机器或本机已装的 ffmpeg（`where ffmpeg` 找路径）把这两个文件拷进 `bin/`；或者先挂代理再 `curl -LO https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip`。注意 `bin/` 永远不在仓库里（`.gitignore` 忽略），刚 clone 下来一定是空的，没有「从仓库里拿」这个选项。版本不必固定，9.x essentials 都能过测试（测试用的 `tests/data/*.log` 是入库的金样，不随 ffmpeg 版本变）。

3. **生成测试夹具**（需要 ffmpeg，约 30 秒）：

```bat
.venv\Scripts\python tests\make_fixtures.py
```

4. **确认环境**：

```bat
.venv\Scripts\python -m pytest -q      :: 期望 130 passed
```

`tests/data/*.mp4`、`*.png` 是生成物、不入库；`tests/data/*.log` 是 ffmpeg stderr 金样，必须入库。没有 `bin/` 或 `tests/data/` 时测试会直接失败（`conftest.require_binaries`），不是跳过。

日常命令：

```bat
.venv\Scripts\python -m ruff check . && .venv\Scripts\python -m mypy videoqc   :: 门禁
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m videoqc                    :: 启动 GUI
.venv\Scripts\python -m videoqc --headless <目录>  :: 命令行
.venv\Scripts\python packaging\build_portable.py   :: 出 zip（PyInstaller + 自检 + sha256）
```

打包前记得三处版本号一致（`videoqc/version.py`、`pyproject.toml`、`packaging/version_info.txt`），zip 名字取自版本号。

## 目录

```
videoqc/         程序包（ui → app → core → media 四层）
docs/            DESIGN.md（为什么这么做）、SPEC.md（具体怎么做）、CHANGELOG.md（各版本改了什么）
AGENTS.md        给 agent/新同学的硬规则（门禁命令、版本与改动记录约定、别做的事）
packaging/       打包脚本、spec、许可证
tests/           单元测试与夹具生成脚本
bin/             ffmpeg/ffprobe（不入库）
dist/            打包产物（不入库）
README.md        本文
```

## 已知限制

- 逐帧步进不保证帧精确：Qt 会吸附关键帧，落点可能差 0–2s（GOP 决定）
- VFR / 慢动作素材的 PTS 空洞可能被报为跳秒，报告里同时会给出 `vfr_suspected` 提示
- 单目录建议 ≤20 个文件；超大目录请按子目录分批
- 分析结果不缓存，重开程序要重新扫

## 许可

videoqc 以 **GPL-3.0-only** 发布，全文见 [`LICENSE`](LICENSE)。分发修改版或二进制时请同步提供对应源码。

随包分发的第三方组件：FFmpeg（GPLv3，静态构建、独立进程调用）、Qt/PySide6（LGPLv3，动态链接）、Python（PSF）。许可证全文见包内 `licenses/`，归属与版本见 `licenses/THIRD-PARTY.md`。
