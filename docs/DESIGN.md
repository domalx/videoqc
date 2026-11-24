# domaMedia · 录像质量检测工具（videoqc）设计文档

版本：v0.3.2（检测口径修正） · 目标平台：Windows 10/11 x64 · 交付形态：免安装压缩包（自带运行时与 ffmpeg）

> 实现规格：`docs/SPEC.md`（接口、命令、正则、判定伪代码、测试夹具、验收清单）。本文与 SPEC 冲突时以 SPEC 为准并回改本文。
>
> 变更：v0.1 命令行检测工具 → v0.2 加桌面 GUI 与打包交付 → v0.3 按已确认约束收敛范围（H.264/MP4、<20 文件/目录、报告自用、不做缓存、阈值只改 config）→ v0.3.1 评审修订（时间基准含滤镜、结论单一来源、Session 接受规则、黑场/静音降级边界、配置补键、验收标准可执行化）→ v0.3.2 检测口径修正（D14 基准改为视频流时长、冻结与静音重叠降级、曝光/纯色按段报并新增纯色屏、新增时长过短/空音轨/采样率与声道检查）。修订理由见 §18 ADR D17–D20。

---

## 0. 当前进度（截至 2026-11-24）

| 项 | 现状 |
|----|------|
| 里程碑 | M1–M6 全部完成：探测/包统计 → D01–D14 → 报告（MD+JSON）→ CLI → GUI → PyInstaller 打包 |
| 代码 | `videoqc/` 35 个文件、3910 行，无占位代码；`ruff check` 零发现，`mypy`（全包 `videoqc/`，`check_untyped_defs`）零发现 |
| 测试 | `pytest -q` 122 项全绿；含 `tests/test_ui.py` 的 GUI 冒烟（offscreen：加载/时长预填/扫描/列宽稳定/导出/重扫先清色/30 行滚动）与 `tests/test_cli.py` 的退出码契约；素材 = 真实录像 2 个 + 生成夹具 10 个 + `bulk_*` 24 个（列表滚动用） |
| 性能基准 | 全量扫描（ffmpeg 解码 + 抽帧 + 14 项检测）：705.6s 素材 29.9s，735.0s 素材 14.8s ≈ **24–50 倍速**；快检（只读包头）约 0.3s/文件 |
| 交付 | `dist/videoqc-0.3.2-win64.zip` 132MB（解包 348MB）+ `.sha256`；另有 `dist/videoqc/videoqc.exe` 可直接运行（免解压）；`bin/` 为 ffmpeg/ffprobe 9.1 essentials（GPLv3） |
| 已知缺口 | ① 真实 VFR 素材上的 D03 跳变尚未肉眼核实（阈值已加 MAD 自适应，仍有残留误报的可能）；② 逐帧步进为关键帧级（误差 0–2s，已定不修）；③ 无 CI；④ 画面采样对 ffmpeg stderr 文案仍强耦合，升级 ffmpeg 前须跑 `tests/data/*.log` 金样 |

> 本节只记录「此刻真实存在的东西」；代码或交付物变了就改日期与数字，不写流水账。

---

## 目录

- [0. 当前进度（截至 2026-11-24）](#0-当前进度截至-2026-11-24)
- [1. 目标与范围](#1-目标与范围)
  - [1.1 一句话目标](#11-一句话目标)
  - [1.2 需求](#12-需求)
  - [1.3 不做的事（划界，避免范围膨胀）](#13-不做的事划界避免范围膨胀)
  - [1.4 术语](#14-术语)
  - [1.5 已确认的约束（用户 2025-11-23 确认）](#15-已确认的约束用户-2025-11-23-确认)
- [2. 交付形态与产品结构](#2-交付形态与产品结构)
  - [2.1 压缩包结构](#21-压缩包结构)
  - [2.2 体积预算（M4 构建时回填实测）](#22-体积预算m4-构建时回填实测)
  - [2.3 许可证合规（必须做，否则不能对外分发）](#23-许可证合规必须做否则不能对外分发)
  - [2.4 为什么有两套 ffmpeg（设计取舍）](#24-为什么有两套-ffmpeg设计取舍)
  - [2.5 运行环境](#25-运行环境)
- [3. GUI 设计](#3-gui-设计)
  - [3.1 布局（三区 + 顶栏）](#31-布局三区--顶栏)
  - [3.2 交互流程](#32-交互流程)
  - [3.3 控件与实现映射](#33-控件与实现映射)
  - [3.4 线程模型（关键，决定顺不顺手）](#34-线程模型关键决定顺不顺手)
  - [3.5 播放器细节](#35-播放器细节)
- [4. 功能架构（检测项矩阵）](#4-功能架构检测项矩阵)
- [5. 总体架构](#5-总体架构)
- [6. 数据流](#6-数据流)
  - [6.0 一致性约定（先定规矩，再讲流程）](#60-一致性约定先定规矩再讲流程)
  - [6.1 单文件分析（四阶段，一次解码原则）](#61-单文件分析四阶段一次解码原则)
  - [6.2 单文件数据结构](#62-单文件数据结构)
  - [6.3 扫描流程（两种模式）](#63-扫描流程两种模式)
  - [6.4 问题定位回放](#64-问题定位回放)
  - [6.5 导出](#65-导出)
- [7. 检测算法设计](#7-检测算法设计)
  - [7.1 D01 完整性 / 可解码性](#71-d01-完整性--可解码性)
  - [7.2 D02 音画时长差](#72-d02-音画时长差)
  - [7.3 D03 跳秒 / 丢帧（核心）](#73-d03-跳秒--丢帧核心)
  - [7.4 D04 帧率与时序异常](#74-d04-帧率与时序异常)
  - [7.5 D05 冻结 + D09 黑场（同一进程）](#75-d05-冻结--d09-黑场同一进程)
  - [7.6 D06 / D07 / D08 音频](#76-d06--d07--d08-音频)
  - [7.7 D10 偏色 / D11 过曝欠曝（关键帧采样）](#77-d10-偏色--d11-过曝欠曝关键帧采样)
  - [7.8 D12 花屏 / 码流损坏](#78-d12-花屏--码流损坏)
  - [7.9 D14 尾部截断](#79-d14-尾部截断)
  - [7.10 合并规则（merge_issues()）](#710-合并规则merge_issues)
- [8. 配置设计](#8-配置设计)
- [9. 报告产物](#9-报告产物)
  - [9.1 report.md（GUI 导出的主产物）](#91-reportmdgui-导出的主产物)
  - [9.2 report.json](#92-reportjson)
  - [9.3 退出码（headless 模式）](#93-退出码headless-模式)
- [10. headless CLI（次要入口，同包内）](#10-headless-cli次要入口同包内)
- [11. 性能与并发](#11-性能与并发)
- [12. 错误处理与健壮性](#12-错误处理与健壮性)
- [13. 打包与构建](#13-打包与构建)
  - [13.1 技术选型对比（为什么是 PySide6 + Qt Multimedia）](#131-技术选型对比为什么是-pyside6--qt-multimedia)
  - [13.2 选型定案（2025-11-23）](#132-选型定案2025-11-23)
  - [13.3 构建目录与步骤](#133-构建目录与步骤)
  - [13.4 发布前自检清单（打包脚本自动跑）](#134-发布前自检清单打包脚本自动跑)
  - [13.5 分发、升级与回滚](#135-分发升级与回滚)
- [14. 可扩展性](#14-可扩展性)
- [15. 实施计划](#15-实施计划)
- [16. 风险与限制](#16-风险与限制)
- [17. 附录：命令与组件速查](#17-附录命令与组件速查)
- [18. 决策记录（ADR）](#18-决策记录adr)



## 1. 目标与范围

### 1.1 一句话目标

一个**解压即用的压缩包**：双击 exe 打开窗口，选一个录像目录，列表中挑文件边看边看诊断，也能一键把整个目录扫一遍，最后导出可定位到时间点的 Markdown 报告。

### 1.2 需求

| # | 需求 | 验收标准 |
|---|------|----------|
| R1 | 递归扫描目录下的视频文件 | mp4/mov/mkv/avi/flv/ts/m2ts/mpg/webm/m4v/wmv，扩展名可配置 |
| R2 | 定位「跳秒」 | 报出跳变起止时间与缺失帧数 |
| R3 | 定位「卡顿」 | 报出冻结区间与时长 |
| R4 | 检出「无声」 | 无音轨 / 全程静音 / 中途长静音分别报告 |
| R5 | 检出「偏色」 | 报出方向、强度、时间区间 |
| R6 | 检出「画面异常」 | 黑场、过曝/欠曝、纯色帧、花屏/码流损坏 |
| R7 | 产物 | GUI 内可看；可导出 `report.md` + `report.json`，均能定位问题时间 |
| R8 | 失败隔离 | 单个文件解析/解码失败记一条问题，不影响整批 |
| R9 | **零环境依赖** | 目标机不装 Python、不装 ffmpeg、不装 VC 运行库，解压即可运行 |
| R10 | **GUI 三分区** | 左：目录与文件列表；右上：播放框；右下：该文件的异常信息文本 |
| R11 | **内嵌播放** | 选中文件即可播放，支持拖动、倍速、逐帧；点异常条目跳到对应时间 |
| R12 | **一键全扫** | 「快速扫描」（包头级，秒级出全局结论）与「深度扫描」（全量解码）两个按钮，带进度与取消 |
| R13 | 结构清晰 | 解压后 exe、`config.json`、`bin/`、`reports/`、`licenses/` 一目了然 |

### 1.3 不做的事（划界，避免范围膨胀）

- 不做内容理解（人脸/车牌/物体识别）、不做主观画质评分（VMAF）。
- 不做码率合规/分辨率规格核对、不做实时监控、不做 Web 服务。
- 不做自动修复/转码输出（播放若遇不支持编码，给提示而不是转码）。
- 不做多语言（v1 仅中文 UI）、不做安装包（只发绿色 zip）、不做代码签名。

### 1.4 术语

| 词 | 定义 |
|----|------|
| 跳秒 | 时间轴不连续：相邻包 PTS 间隔远大于该文件中位帧间隔，即时间轴上少了若干帧 |
| 卡顿 | 时间轴正常推进但画面内容长时间不变（冻结/重复帧） |
| 无声 | 无音轨 / 全程音量低于静音阈值 / 连续静音超阈值 |
| 偏色 | 画面整体色度（U/V）持续偏离中性，按灰世界假设判定 |
| 画面异常 | 黑场、纯色/过曝帧、解码错误（花屏、马赛克） |

### 1.5 已确认的约束（用户 2025-11-23 确认）

| 约束 | 结论 | 影响 |
|------|------|------|
| 素材编码 | **H.264 + MP4** | 播放不再依赖特殊 codec 支持，Qt/Media Foundation/Chromium 都能放；HDR、`.ts`、私有封装的风险从「必然遇到」降为「边缘情况」 |
| 包体积 | **zip ≤ 200MB 可接受**（实测 80–160MB） | 不用为体积裁 codec/换 7z；ffmpeg 用 gyan.dev essentials 全量即可 |
| 平台 | Windows 10/11 x64 | 单一构建目标，不做跨平台抽象 |
| 单次扫描规模 | **单个目录 < 20 个文件** | 不需要断点续扫、不需要跨会话缓存；并发 2 足够；进度用「已完成/总数」文本即可 |
| 报告读者 | **自己排查用** | 简洁 Markdown 即可：不做封面、不做问题截图、不做客户版排版 |
| 结果留存 | **不需要，每次重扫** | 结果只存内存；去掉缓存层（少一个文件格式、少一类 bug） |
| 阈值调整 | **手改 config.json 就够** | 界面只展示当前阈值快照；不做设置对话框、不做预设档位 |

**由此带来的一个必须解决的时间问题**：20 个 1 小时录像全量分析 ≈ 20 × 10–25min = 3–8 小时。因此「全扫」必须拆成两种模式（§6.3）：`快速扫描` 先出全局结论（几十秒），`深度扫描` 只对可疑/需要的文件跑全量解码。

---

## 2. 交付形态与产品结构

### 2.1 压缩包结构

```
videoqc-0.3-win64.zip
└─ videoqc/                       ← 解压后可直接拷走，绿色免安装
   ├─ videoqc.exe                 ← 唯一入口（GUI）
   ├─ config.json                 ← 唯一需要用户编辑的配置（阈值/扩展名/并发）
   ├─ README.md                   ← 三步上手 + 常见问题
   ├─ CHANGELOG.md
   ├─ bin/
   │  ├─ ffmpeg.exe               ← 自带，用于检测与抽帧
   │  └─ ffprobe.exe
   ├─ reports/                    ← 默认导出位置（初始为空，含 .gitkeep）
   ├─ licenses/
   │  ├─ THIRD-PARTY.md           ← 组件清单与版本
   │  ├─ LGPL-3.0-Qt.txt
   │  └─ GPL-ffmpeg.txt
   └─ _internal/                  ← 打包运行时，用户不用碰
      ├─ python313.dll
      ├─ Qt6Core.dll / Qt6Widgets.dll / Qt6Multimedia.dll / Qt6MultimediaWidgets.dll ...
      ├─ PySide6/plugins/{platforms,multimedia,imageformats,styles}/
      └─ ffmpeg 相关 DLL（Qt 自带解码后端，与 bin/ 下的是两套，见 2.4）
```

要点：

- **仓库与交付包同构**：源码仓库根也有 `bin/ffmpeg.exe`、`bin/ffprobe.exe`（**不入库**，进 `.gitignore`；构建前放 gyan.dev essentials 静态包）。`build_portable.py` 只把整个 `bin/` 复制进 zip，不存在第二套路径；源码运行时 `ROOT/bin` 同样生效，开发与交付不搜 PATH。详见 SPEC §1.1。
- **exe 与 config 同级**：用户只看到 5 个顶层条目，配置无路径参数，程序以自身目录为根（`sys.executable` 所在目录，PyInstaller 下同理）。
- **程序目录即工作目录**：不写注册表、不写 `%APPDATA%`、不留系统痕迹；删目录即卸载。
- **相对路径优先**：`config.json` 中所有路径都相对程序根；用户也可以填绝对路径。
- **首次启动自检**：`bin/ffmpeg.exe` 缺失或不可执行 → 弹窗给出修复提示（重新解压），不进入主界面。
- **运行后会多一个 `logs/`**：`ROOT/logs/videoqc.log`（2MB × 3 轮转，首次运行自动建）。出问题只要让他们发这个文件 + 报告 md，不必远程。规格见 SPEC §1.2。

### 2.2 体积预算（M4 构建时回填实测）

| 组成 | 解压后 | 说明 |
|------|--------|------|
| PyInstaller 运行时 + Python 3.13 + Qt6 依赖模块 | ~180–260 MB | 只保留 Core/Gui/Widgets/Multimedia/Network，排除 Qml/WebEngine/3D/DBus |
| `bin/ffmpeg.exe` + `bin/ffprobe.exe` | **~210 MB**（已实测：9.1 essentials 静态构建，各 ~105 MB；源包 7z 35 MB） | gyan.dev essentials 静态构建 |
| Qt 自带 FFmpeg 解码 DLL | 含在上行 | 播放用（见 2.4） |
| 其他（config/README/licenses） | < 1 MB | |
| **zip 压缩后** | **~80–160 MB** | 验收上限 200MB；首次解压约 10–20s |

### 2.3 许可证合规（必须做，否则不能对外分发）

| 组件 | 许可 | 义务与做法 |
|------|------|-----------|
| ffmpeg 静态构建 | GPL v3（essentials 构建含 GPL 组件） | 随包附 `GPL-ffmpeg.txt` + 构建来源与源码获取链接；不修改 ffmpeg 源码；仅在独立子进程中调用（不链接） |
| Qt / PySide6 | LGPL v3（动态链接） | 动态链接 + 允许用户替换 Qt DLL（`_internal/` 下即为独立 DLL）；附 `LGPL-3.0-Qt.txt` 与源码获取链接 |
| Python | PSF | 附 THIRD-PARTY 说明 |
| 本项目代码 | 内部/私有，随包附 LICENSE | |

`THIRD-PARTY.md` 记录：ffmpeg 版本号与下载地址、Qt/PySide6 版本、Python 版本、各自的许可证要点。

### 2.4 为什么有两套 ffmpeg（设计取舍）

- `bin/ffmpeg.exe`：分析用，命令行、可复现、便于在报告里给出复现命令。
- Qt Multimedia 在 Windows 上自 6.5 起默认使用 **FFmpeg 后端**（`plugins/multimedia/ffmpegmediaplugin.dll` + `avcodec/avformat` DLL）。这是**播放**能覆盖 H.264/HEVC/VP9/AV1 等格式、不依赖系统解码器的关键。
- 两套不共享（Qt 用的是 `av*.dll`，我们用的是静态 exe），代价是约 30–60 MB 体积，换来「分析不依赖 Qt、播放不依赖系统 codec」的解耦。**这点在 v1 不优化。**

### 2.5 运行环境

- Windows 10 1809+ / Windows 11，x64。不支持 Win7/32 位（Qt6 与 ffmpeg 均无 32 位官方构建需求）。
- 无需管理员权限；首次运行可能触发 SmartScreen（未签名），README 说明「更多信息 → 仍要运行」。

---

## 3. GUI 设计

### 3.1 布局（三区 + 顶栏）

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ [选择目录…]  D:/videos/2024-11-20    [快速扫描] [深度扫描] [停止]   [导出报告]  │
│ 共 12 个文件 · 通过 7 · 警告 3 · 失败 2                        ⏳ 深度 3/12     │
├─────────────────────────────────────────────────────────────────────────────────┤
│ 文件                          │  ┌────────────────────────────────────────┐     │
│ ┌───────────────────────────┐ │  │                                        │     │
│ │ 名称       时长    结论 问题│ │  │              播放框                    │   │
│ │ cam01.mp4  42:10  ❌   3   │ │  │        （QVideoWidget 16:9）           │    │
│ │ cam02.mp4  38:02  ⚠️   1   │ │  │                                        │    │
│ │ cam03.mp4  40:55  ✅   0   │ │  └────────────────────────────────────────┘    │
│ │ ...                       │ │  ▶ ━━━━━●─────────── 00:12:03 / 00:42:10        │
│ │                           │ │  1x ▾  [逐帧◀][逐帧▶]  🔊 ───────               │
│ └───────────────────────────┘ │ ┌────────────────────────────────────────┐      │
│                               │ │ cam02.mp4 · 发现 1 个问题（1 警告）      │    │
│                               │ │ 00:12:03.200→ 卡顿  ⚠️  画面冻结16s      │    │
│                               │ │ 00:12:19.400  冻结      「疑似静止」     │    │
│                               │ └────────────────────────────────────────┘      │
├─────────────────────────────────────────────────────────────────────────────────┤
│ 就绪 · ffmpeg 6.1.1 · 阈值档：默认                                              │
└─────────────────────────────────────────────────────────────────────────────────┘
```

- 左栏（约 320–420px，可拖拽分隔）：文件表 + 该目录操作按钮。
- 右上：播放框 + 控制条（播放/暂停、进度条、时间显示、倍速 0.5/1/2/4、逐帧、音量、无音轨时音量置灰）。
- 右下：异常面板（标题行给结论汇总，下面是问题表）。
- 顶栏：选目录、快速扫描、深度扫描、停止、导出报告；完成计数常驻。
- 底部状态栏：ffmpeg 版本、当前阈值档、最近一次操作结果。

### 3.2 交互流程

```
启动 ── 自检(ffmpeg/config/写权限) ──┬─失败─► 错误弹窗(说明+修复建议) ─► 退出
                                    └─成功─► 主窗口(空态：大字提示「选择目录」)

选择目录 ──► 递归列文件（仅 fs，<1s）──► 文件表填充（状态「未分析」，时长异步补）
                │
                ├─ 单击行 ──► 右侧异常面板显示该文件已有结果 / 「未分析」提示
                │            └─ 双击（或按 Enter）──► 载入播放器并开始播放
                │
                └─ 「快速扫描」──► 后台只读包头（每文件 3–10s）──► 全部行秒级出结论
                └─ 「深度扫描」──► 后台队列逐个全量分析 ──► 行状态/计数实时刷新
                                        └─「停止」──► 终止当前子进程，已完成结果保留

异常表内单击某行 ──► 播放器 seek 到该问题开始前 1s 并暂停（便于逐帧确认落点）
异常表双击 / Enter ──► seek 到开始前 1s 并播放（连续回放）
```

细节规则：

- 选中一个**未分析**文件时，自动做一次「包级快检」（只跑 ffprobe 包头，秒级），让面板立刻有内容（跳秒/无音轨/时长）；解码级检测只在「深度扫描」里跑（每次全部重扫：开工先把列表里的结论清空，扫完一个填一个颜色）。不做「只分析选中文件」的独立按钮——上一版有，实际没人用，还让「哪些文件已深度分析」多出一个来源。
- 文件表**不提供排序**：行序 = `iter_videos()` 顺序，扫描中不重排（重排会让列宽抖动）；「失败」行标红底、「警告」行浅绿底，结论靠行底色 + ToolTip 表达。表列为 `# / 名称 / 时长 / 问题数`。
- 播放失败（编码不支持）：播放框内显示错误文案 + 「该文件编码 Qt 无法播放，但检测仍可进行」提示，不弹窗打断。
- 拖入目录/文件到窗口 = 选择目录/播放该文件。
- 全扫中允许继续操作其他文件（播放、看已有结果）；扫描在后台，不卡 UI。

### 3.3 控件与实现映射

| 区域 | Qt 控件 | 备注 |
|------|---------|------|
| 顶栏 | `QToolBar` + `QPushButton` + `QProgressBar` + `QLabel` | 扫描进度用 `QProgressBar` 不定长+文本形式 |
| 文件表 | `QTableWidget`（4 列）或 `QTableView + QStandardItemModel` | v1 用 `QTableWidget` 足够 |
| 播放 | `QVideoWidget` + `QMediaPlayer` + `QAudioOutput` | Qt Multimedia，FFmpeg 后端 |
| 控制条 | `QSlider`(seek) + `QComboBox`(倍速) + 自定义逐帧按钮 | 逐帧 = 暂停后 `setPosition(pos ± 1000/fps)` |
| 异常面板 | `QTableWidget`（5 列，含隐藏的「复现命令」） | 单击 seek；右键「复制时间点」 |
| 分隔 | `QSplitter` ×2 | 左右、上下均可拖 |
| 状态栏 | `QStatusBar` | |

### 3.4 线程模型（关键，决定顺不顺手）

```
主线程(UI) ──┬── QMediaPlayer（Qt 内部自有线程，平台后端）
             ├── ScanController (QObject) ── 持有 ThreadPoolExecutor(max_workers=jobs)
             │        └── 每个 worker 线程跑一个 pipeline.analyze_file()
             │            stage 经 on_stage 回调 → Signal 排队回主线程
             │               └── 信号：file_started / file_stage / file_done(FileResult) / scan_finished
             └── 只做 UI 更新，绝不做 subprocess 等待、绝不 sleep
（不用 QThread 子类：不需要事件循环，executor 更简单；取消靠 cancel_event + kill 子进程）
```

规则：

1. 所有 `subprocess` 调用都在 worker 线程；`ffmpeg` 输出逐行读取，避免管道阻塞。
2. worker → UI 只用 `Signal`（`FileResult` 是纯 dataclass，可跨线程传）。
3. 播放中扫描：并发从 `jobs`(默认 2) 降为 1（固定策略，不做开关）——解码子进程抢 CPU 会直接导致播放卡顿。
4. 取消：置取消标志 → 读线程退出循环 → `proc.kill()` + `wait(2s)`（绝不 `QThread.terminate()`）；在跑的文件记 `cancelled`（C5），排队未开始的记 `pending`（未分析）。
5. 退出：停止扫描、终止所有子进程；不保存结果（已确认每次重扫）。
6. 同一文件不双跑（C3）：选中即快检时，若该文件已在深度队列中，只显示其进度，不另起任务。
7. 结果带 `generation`（C4）：任务**提交时**从全局计数器自增分配（不按完成顺序），随 `FileResult` 回传；`Session.put()` 先比 mode 等级（full > fast > none），再比代次；UI 按 `path` 覆盖行。

### 3.5 播放器细节

- 载入：`player.setSource(QUrl.fromLocalFile(path))`，`audio.setVolume(0.7)`；自动播放。
- 支持：H.264 / HEVC / VP9 / AV1 / MPEG-4（Qt FFmpeg 后端能力）+ AAC/MP3/AC3 等；容器 mp4/mov/mkv/avi/flv/ts/webm。
   - 注意：`.ts`/`.m2ts` 某些封装 Qt 可能不支持播放，**改为**提示「无法直接播放，需要转码预览？」——v1 不做转码，仅提示可检测。
- 已知无音轨：音量控件禁用 + 面板直接给「无音轨」结论。
- 帧率取自分析结果（`avg_frame_rate`）；未知时逐帧步长用 40ms。
- **逐帧不要求帧精确（已定，不阻塞）**：`QMediaPlayer.setPosition` 会吸附到关键帧/时间基，步进一次约前进 1/4～1 帧，落点可能差 0–2s（GOP 长度）。验收标准 = 「能定位到问题点附近并逐步观察」，**不做 ffmpeg 抽单帧通道**（v1 无此需求，附录命令 #5 仅供手工排查用）。
- 时间显示：`HH:MM:SS`，与报告格式一致（报告里多一位毫秒）。
- 播放期间不做色彩/冻结检测（避免与用户的观察冲突），检测结果只来自显式分析。

---

## 4. 功能架构（检测项矩阵）

| ID | 检测项 | 默认严重度 | 执行阶段 | 原始信号 |
|----|--------|-----------|----------|----------|
| D01 | 文件完整性 / 可解码性 | high | 探测 | ffprobe 退出码、`format.duration`、`nb_frames` |
| D02 | 音视频时长不一致（音画不同步） | medium | 探测 | 各流 `duration` 差值 |
| D03 | 跳秒 / 丢帧（PTS 跳变） | high | 包级 | 视频包 `pts_time` 序列 |
| D04 | 帧率异常 / 时间戳非单调 | medium | 包级 | `pts/dts` 单调性、间隔分布 |
| D05 | 卡顿 / 画面冻结 | medium | 解码 | `freezedetect` |
| D06 | 无音轨（`require_audio` 可关） | high | 探测 | `codec_type=audio` 计数 |
| D07 | 中途长静音 | medium | 解码 | `silencedetect` |
| D08 | 全程静音 / 音量过低 / 削波 | high/medium | 解码 | `volumedetect` |
| D09 | 黑场 | medium | 解码 | `blackdetect` |
| D10 | 偏色 | medium | 采样 | 关键帧 RGB 均值（灰世界） |
| D11 | 过曝 / 欠曝 / 纯色帧 / 纯色屏 | low/high | 采样 | 关键帧 RGB 均值、饱和度、通道极差 |
| D12 | 解码错误 / 花屏 | high | 解码 | ffmpeg stderr 错误计数、包 `flags` 含 `C`（Corrupt；`D`=Discard 不计） |
| D13 | 元数据异常（HDR/旋转/SAR/采样率/声道） | info | 探测 | `color_transfer`、`rotate`、`pix_fmt`、`sample_rate` |
| D14 | 录制截断 / 尾部异常 | medium | 探测+解码 | 尾部黑场、视频流时长 vs 包末端 |

**快速模式（`--fast` / 面板上的「快检」）** 只跑 D01–D04、D06、D13、D14，需要解码/采样的 D05、D07–D12 跳过，面板顶部标注「未检画面与声音」。其中 D14 只跑「视频流时长 vs 末包」分支，尾部黑场分支需解码，随 D05 一起跳过。

D13 的 HDR 标记是 D10/D11 的前置条件：HDR（`smpte2084`/`arib-std-b67`）时采样链前置 `zscale=t=linear,tonemap=hable`，失败则跳过偏色并记 info。

---

## 5. 总体架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│ ui/            main_window · file_table · player_panel · issue_panel    │
│                (Qt 控件与信号；只做展示与交互)                          │
├─────────────────────────────────────────────────────────────────────────┤
│ app/           controller(QObject/线程池) · config · session            │
│                (调度、取消、进度、内存结果表)                           │
├─────────────────────────────────────────────────────────────────────────┤
│ core/          pipeline(单文件编排) · detectors/* · model               │
│                (纯 Python，无 Qt 依赖 → 可被 CLI 与测试复用)            │
├─────────────────────────────────────────────────────────────────────────┤
│ media/         probe.py(ffprobe) · runners.py(ffmpeg+stderr 解析)       │
│                color.py(rawvideo 统计) · report.py(md/json) · paths     │
└─────────────────────────────────────────────────────────────────────────┘
```

依赖方向严格单向：`ui → app → core → media`。`core` 不许 import Qt（保证 headless CLI 与单元测试可用）。

模块清单：

| 模块 | 职责 | 关键 API |
|------|------|----------|
| `videoqc/__main__.py` | GUI 入口（`--headless` 转 CLI） | `main()` |
| `ui/main_window.py` | 主窗口、顶栏、状态栏、快捷键 | `MainWindow` |
| `ui/file_table.py` | 文件表模型、状态着色、排序 | `FileTableModel` |
| `ui/player_panel.py` | 播放器与控制器、逐帧、seek 接口 | `PlayerPanel.seek_to(sec)` |
| `ui/issue_panel.py` | 异常表格、单击定位、复制时间点 | `IssuePanel.load(FileResult)` |
| `app/controller.py` | 线程池、单文件去重、代次号、取消 | `start(paths, mode)`, `stop()` |
| `app/config.py` | 默认值 ⊕ config.json ⊕ CLI，校验与迁移 | `load_config()`, `DEFAULTS` |
| `app/session.py` | 内存结果表（仅本会话，不落盘） | `get/put/clear`, `summary` |
| `core/pipeline.py` | 单文件四阶段编排、异常隔离 | `analyze_file(path, cfg, mode, generation, cancel_event)` |
| `core/detectors/*` | D01–D14，纯函数 | `check(ctx) -> list[Issue]`（`cfg` 在 `ctx.cfg`） |
| `core/model.py` | `Issue`/`FileResult`/`Report`、`fmt_tc` | — |
| `media/probe.py` | 流与包信息、版本 | `probe_streams`, `probe_packets` |
| `media/runners.py` | ffmpeg 执行、**双通道**（stdout 像素 / stderr 日志）读取、超时、部分结果；事件与采样同一趟产出 | `run_decode_pass` |
| `media/color.py` | 帧统计纯计算（RGB 均值/偏色/曝光）与样本聚合 | `frame_stats`, `aggregate` |
| `media/report.py` | MD/JSON 渲染（GUI 导出与 CLI 共用） | `render_markdown`, `dump_json` |

---

## 6. 数据流

### 6.0 一致性约定（先定规矩，再讲流程）

数据流最容易错的地方不是检测算法，而是**几条管道之间的时间与状态对齐**。先立六条：

| # | 约定 | 不这么做会怎样 |
|---|------|----------------|
| C1 | **时间基准唯一**：Issue 一律存「视频流内秒」（0 = 视频流首帧）。**两路来源机制不同**：① 包级 `probe_packets` 用 ffprobe 读到的是绝对 PTS，需 `pts − start_time`；② ffmpeg 解码滤镜（`freezedetect`/`blackdetect`/`silencedetect`/`showinfo`）因命令未加 `-copyts`，输出已被 ffmpeg 默认归一化到流起点，**不再减 `origin_sec`**（重复扣减会让 TS/监控录像出现负时间、与包级时间轴错位，实测 start_time=10s 的文件 freeze_start 仍报 4s）。`core` 只见流内秒。渲染用 `fmt_tc`，seek 时 `PlayerPanel` 再加回 `origin_sec` | MP4 edit list / TS 的 1.4s 基准若被重复相减，报告与播放器会差一个 start_time。音频侧残留 = 音视频起点差，由 D02 单独报 |
| C2 | **采样必须带真实时间戳**：`fps` 滤镜丢时间信息，「样本序号 × 5s」在跳帧/GOP 不齐时会漂移。采样命令加 `showinfo`，stderr 每样本一行 `pts_time:`，与 stdout 的像素帧**按顺序配对**；配不上就丢该样本（宁缺勿错） | 偏色区间整体偏移，定位失效 |
| C3 | **同一文件同一刻只跑一个任务**：`dict[path, future]` 去重。选中即快检遇到已有深度任务 → 只订阅进度，不另起 | 快检与深度并发跑同一文件，白烧 CPU 且结果互相覆盖 |
| C4 | **结果单调提升 + 代次号**：`full` 覆盖 `fast`，`fast` 不覆盖已有 `full`；每次分析带自增 `generation`，UI 只接受比当前更新的结果 | 快速点文件时旧异步结果回写，覆盖新选中文件的面板 |
| C5 | **中断不算通过**：`cancelled`/`timeout` 写入 `status`，结论显示「未完成」，不进「通过」统计 | 空结果 = `ok=True`，报告把没分析的文件说成没问题 |
| C6 | **阶段产物独立**：probe→packets→decode→color 各段单独产出，任一段异常不丢弃前段 Issue | 解码 20 分钟后崩了，包头级结论也被一并丢掉 |
| C7 | **时间钳位**：所有 Issue 的 `start/end` 落在 `[0, duration]`（采样窗口跨片尾、滤镜末端都会越界） | 报告出现「20 分钟片子」的 00:20:05 问题点，seek 越界后播放器无反应 |
| C8 | **结论唯一来源**：`FileResult.conclusion`（passed/warned/failed/unfinished）在 model 里算一次，UI/报告/CLI/统计全部读它 | 三处各写一遍严重度规则，改一处漏两处，报告与列表对不上 |

文件状态机（UI 行底色按 status/conclusion，报告「范围」列按 mode）：

```
未分析（status=pending）──选中自动快检──► 快检完成 ──深度──► 分析完成
   └──────────深度扫描────────────────┘
任何状态 ──取消/超时/崩溃──► 未完成（保留已得 Issue，标「未完成」）
```

### 6.1 单文件分析（四阶段，一次解码原则）

```
path ─► ① probe_streams(ffprobe -show_format -show_streams) ──► 流摘要 │失败→D01
      ─► ② probe_packets(ffprobe -show_entries packet=pts_time,…) ─► pts[]（已减 start_time）─► D03/D04
      ─► ③ run_decode_pass(ffmpeg 一次解码, 事件流式解析, ffmpeg 已归一化时间不再减 start_time) ─► freeze/black/silence/volume/errors ─► D05/D07/D08/D09/D12
      ─► ④ 同一趟的采样支路(完整帧流 + fps + showinfo, pts_time 已归一化不再减 start_time) ─► [(流内秒, RGB均值)] ─► D10/D11
      ─► merge_issues() ─► FileResult(generation) ─► UI 表格/面板 · 报告
```

- 阶段 ③ 是唯一的重活：1 小时 1080p 约 8–25 分钟，因此快检跳过它，深度扫描按文件逐个进入。
- 阶段 ③ 中途崩溃：`runners` 返回 `PartialDecodeResult(events, exit_code, stderr_tail)`，已解析事件照常交给检测器，`pipeline` 追加 `decode_aborted`，**不重跑、不丢前段**（C6）。
- 阶段 ④ 必须**同时消费 stdout 与 stderr**（像素 + showinfo），否则任一管道写满会让 ffmpeg 阻塞到超时。
- 阶段 ② 与 ③ 之间不落盘，全部内存传递；任何阶段异常由 `pipeline` 捕获成 Issue，不向上抛。

### 6.2 单文件数据结构

```python
@dataclass
class Issue:
    kind: str            # "pts_gap" | "freeze" | "silence" | "color_cast" | ...
    severity: str        # "high" | "medium" | "low" | "info"
    start: float | None  # 秒，流内相对时间（渲染时转 HH:MM:SS.mmm）
    end: float | None
    title: str           # 一行结论："PTS 跳变，缺失约 24 帧"
    detail: str          # 可复核的测量证据
    evidence: dict       # 原始数值（进 JSON、进面板「证据」列）

@dataclass
class FileResult:
    path: str
    conclusion: str          # "passed" | "warned" | "failed" | "unfinished"（C8，只在 model 算一次）
    ok: bool                 # == (conclusion == "passed")
    mode: str                # "fast" | "full" | "none"
    status: str              # "done" | "cancelled" | "timeout" | "failed" | "pending"
    generation: int          # 提交时分配，单调递增，UI 丢弃过期结果（C4）
    streams: dict
    stats: dict              # fps、时长、平均帧间隔、mean/max volume、采样点数
    issues: list[Issue]
    error: str | None
```

时间统一存「秒（float，相对流起点）」；渲染时格式化，避免精度与时区问题。

结论规则（唯一实现在 `model.conclusion`，见 SPEC §1A）：high→failed；medium/low→warned；仅 info→passed；`status != "done"`→unfinished。**`low` 也算不通过**。

### 6.3 扫描流程（两种模式）

**为什么分两种**：单目录 <20 个文件，但全量解码一个 1 小时录像要 10–25 分钟，20 个就是 3–8 小时。先用包级检测（秒级）把明显有问题的挑出来，再只对需要的文件做深度分析。

```
「快速扫描」──► mode=fast，全部文件只跑 D01–D04/D06/D13/D14（读包头，每文件 3–10s）
   └─ 结束 ──► 列表全亮：结论/问题数/时长；状态栏「快速扫描完成：12 个，用时 46s」

「深度扫描」──► mode=full，对当前目录全部文件跑完整四阶段
   ├─ 跳过已在本会话内 full 分析过的文件（复用内存结果，不重复解码）
   ├─ ThreadPoolExecutor(jobs=2) ── 每文件 analyze_file()
   │      ├─ Signal file_started(idx, path) → 行状态「分析中（解码）」
   │      └─ Signal file_done(FileResult)   → 行/计数刷新；若正在选中 → 面板实时刷新
   └─ 结束 ──► 状态栏「深度扫描完成：12 个，失败 2，用时 1h12m」

「停止」──► 置取消标志，terminate 当前子进程，未开始的丢弃，已完成保留
```

- 进度只显示「已完成/总数 + 当前文件名」，不做单文件百分比（ffmpeg 无可靠进度）。
- 快速扫描结果不会覆盖已有的 full 结果（full 更全），在行上标记「快检」。
- 深度扫描中允许继续播放/查看其他文件；面板对正在分析的文件显示「分析中…」，不阻塞 UI。

### 6.4 问题定位回放

```
用户点异常行 ──► issue_panel.clicked(Issue)
             ──► player_panel.seek_to(max(0, issue.start - 1.0)); player.play()
             ──► 播放框角标显示「正在回放：卡顿 00:12:03.200 – 00:12:19.400」
```

- **seek 精度限制（必须写进 UI 预期）**：Qt 后端的 `setPosition` 对 H.264 通常跳到**最近关键帧**，实际落点可能差 0–2s（GOP 长度）；要看到跳变那一帧，用「逐帧」按钮或 seek 后再小步前进。
- 因此跳转前先定位到 `start - 1.0s`（留出 1s 上下文），而不是裸 `start`。
- seek 时 `PlayerPanel` 内部加回 `origin_sec`（= `video.start_time`），否则带 edit list 的文件会稳定偏 1.4s（C1）。
- 若该问题 `start` 为 None（如「无音轨」），只高亮不 seek。
- 单击=跳转并暂停，双击/Enter=跳转并播放；逐帧按钮用于精确认跳变前后一帧。

### 6.5 导出

- 「导出报告」→ 保存对话框，默认 `reports/report_YYYYMMDD_HHMM.md`，同时写同名 `.json`。
- 报告包含：概览表（全部文件）、详情（失败/警告在前）、阈值快照、ffmpeg 版本、复现命令。
- 只有整批导出：单文件导出已删除（结果都在整批报告里，多一个入口只会让用户犹豫该点哪个）。

---

## 7. 检测算法设计

> 命令即实现依据，参数即默认阈值（括号内为 `config.json` 键）。

### 7.1 D01 完整性 / 可解码性

`ffprobe -v error -print_format json -show_format -show_streams FILE`

- `-v error` 下 `-show_format` JSON 解析失败 / 退出码非 0 / 无视频流 / `format.duration` 缺失或 0 → high。
- 容器 `format.duration` 为准；「视频末帧明显早于视频流时长」的截断判定只归 D14（`truncation_gap_sec`，默认 2.0s）。D01 只负责「时长缺失/无效」，不重复报同一现象。
- 容器格式与扩展名不符 → info。

### 7.2 D02 音画时长差

`|video.duration − audio.duration| > 0.5s` → medium（证据写两流时长与差值）。音频 `start_time` 偏离视频 > 0.5s → 同项报「音画整体偏移」。

### 7.3 D03 跳秒 / 丢帧（核心）

```
ffprobe -v error -select_streams v:0 \
  -show_entries packet=pts_time,duration_time,flags -of csv=p=0 FILE
```

1. 取 `pts_time`，排序去重，算相邻差 `delta[]`。
2. 中位帧间隔 `p50`；阈值 `gap_thr = max(p50 * pts_gap_factor, p50 + pts_gap_min, p50 + 6·1.4826·MAD)`（默认 3.0 / 0.2s）。
   - 第三项是**自适应地板**：用帧间隔的 MAD 估常态抖动（分位数会被跳变自己污染，MAD 不会——即使 5% 以上的帧间隔本身就是跳变）。
     手机屏录/VFR 的常态抖动不该被当成丢帧；固定帧率的源 MAD≈0，阈值仍是原来的 0.233s。
3. `delta > gap_thr` → Issue：`start=pts[i]`、`end=pts[i+1]`、`缺失帧数 ≈ round(delta/p50) − 1`。
4. 相邻跳变（间隔 < 2s）合并为一段。
5. 严重度：单次缺失 ≥ 1s 或累计缺失 > 片长 0.5% → high；否则 medium。

> 用包级而非帧级：`-show_frames` 要解全片像素，慢一个数量级；包 PTS 已足够定位时间轴不连续。

### 7.4 D04 帧率与时序异常

- `dts_time` 非单调 / 重复 `pts_time` → medium（时间戳损坏）。
- 帧间隔标准差 > 0.3·`p50` → info「疑似可变帧率」，detail 注明帧率结论仅供参考。
- 声明帧率与实测（`1/p50`）偏差 > 20% → low；**VFR 源不报这条**（1/p50 在 VFR 下没有意义）。

### 7.5 D05 冻结 + D09 黑场（同一进程）

```
ffmpeg -nostdin -hide_banner -nostats -v info \
  -i FILE -map 0:v:0 -map 0:a:0? \
  -vf "freezedetect=n=0.003:d=2,blackdetect=d=0.5:pic_th=0.98:pix_th=0.10" \
  -af "silencedetect=n=-50dB:d=3,volumedetect" \
  -f null -
```

stderr 白名单解析：

| 行形态 | 含义 |
|--------|------|
| `lavfi.freezedetect.freeze_start: 12.34` | 冻结起点 |
| `lavfi.freezedetect.freeze_duration: 5.0` / `freeze_end` | 冻结时长 / 终点 |
| `black_start:1.2 black_end:2.3 black_duration:1.1` | 黑场区间 |
| `silence_start: 5.1` / `silence_end: 8.2 \| silence_duration: 3.1` | 静音区间 |
| `mean_volume: -38.2 dB` / `max_volume: -0.0 dB` | 整段音量 |
| `error while decoding` / `corrupt` / `Invalid NAL` … | 计入 D12 |

> **必须 `-v info`**：上述结论行都是 INFO 级日志，`-v warning` 会全部丢光。因此解析器只认白名单前缀（`lavfi.*`、`black_`、`silence_`、`*_volume:`、已知错误串），其余行丢弃，避免 `-v info` 的噪音。

判定：

- 冻结 ≥ `freeze_min_sec`(2s) → Issue；≥ 10s → high；覆盖 ≥ 90% 片长 → 降 info（几乎必是静态监控画面）。
- 冻结区间与黑场重叠 > 80% → 只留黑场，冻结降 info。
- 冻结区间与静音重叠 > 80% → 降 info 并标注「同期静音，可能为暂停/加载画面」：录屏/游戏的加载画面、暂停菜单本身就是静态的，不该判失败。
- 整片静音已由 `silent_audio` high 一条覆盖时，不再逐段报 `silence`（避免报告刷屏）。
- 黑场：中段 > 1s → medium，> 5s → high；**触碰 t≈0 / 片尾且时长 < 30% 片长** 才降 info（常规片头片尾）。覆盖 ≥ 80% 片长 → medium 并措辞「几乎全片黑场」（镜头遮挡/未开机），不能降成 info。
- 无 `freeze_start` 不代表「无冻结检测」——但界面不再为此加固定清单：标题行写「未发现异常」即可，检测项清单放报告附录，避免面板被塞满。

### 7.6 D06 / D07 / D08 音频

- 无音频流 → `no_audio` high（`require_audio=false` 时不报，适合有意不录声音的素材）；有音轨但解不出任何声音 → `empty_audio` high。
- `mean_volume < silent_mean_db`(-45dB)、片长 > 10s，**且 ≥ 80% 时长被 silencedetect 判为静音** → 全程静音 high；
  静音覆盖率不到 80%（安静但一直有底噪）→ 只报音量偏低 medium，避免把安静的正常片子判成 failed。
- `mean_volume < mean_volume_low_db`(-30dB) → 音量过低 medium。
- `max_volume ≥ clipping_db`(-0.1dB) → 削波/爆音风险 low。
- 静音区间 ≥ 3s（非首尾）→ D07 medium；首尾静音 → info；静音累计 > 60% 片长 → high。

### 7.7 D10 偏色 / D11 过曝欠曝（等间隔采样）

```
ffmpeg -nostdin -v info -i FILE -an \
  -vf "fps=1/5,showinfo,scale=32:18:flags=area" -pix_fmt rgb24 -f rawvideo pipe:1
```

- **不加 `-skip_frame nokey`**：只解关键帧时 `fps` 会把稀疏关键帧（0/8.33/16.66s）贴成名义时间（0/5/10s），造成时间戳错位、尾部采样点丢失、偏色覆盖率失真（见 OPTIMIZATION #1）。`fps` 需完整帧流才能取每个网格点最近的真实帧；准确性优先于那点解码加速。每帧 32×18×3 = 1728 B，Python 直接算均值，无 numpy。
- `showinfo` 每输出一帧在 stderr 打一行 `n:12 pts:610000 pts_time:6.1`，与 stdout 的帧**按顺序配对**得到真实时间（C2）；配对不上就丢该样本。
- `fps` 滤镜会丢时间，不能靠「序号 × 5s」推算（GOP 不齐/跳帧时会漂）；`showinfo` 的 pts_time 已被 ffmpeg 归一化到流起点，不再减 start_time（C1）。
- 每样本：`mean_r/g/b`、`Y=.299r+.587g+.114b`、`sat=(max−min)/max`。
- 灰世界：`dr=r−Y, dg=g−Y, db=b−Y`，`cast=max(|dr|,|dg|,|db|)`。
  - 帧级 `cast > color_cast_threshold`(25/255) 且 `Y > 24` → 疑似偏色；方向按符号组合命名（`+ + −` → 偏黄）。
  - **三通道同号（整体提亮/压暗）不算偏色**，归 D11：`dr,dg,db` 同号时 `is_color_cast()` 返回 False，否则夜景/曝光偏移会被误报成偏色。
  - 连续 ≥ 3 个样本（≥15s）→ D10 Issue，区间取首末样本；覆盖 ≥ 80% 片长 → 「整片偏色」high；`cast > 60` → high。
- 曝光：`Y < 16` 且非黑场 → 欠曝 low；`Y > 235` → 过曝 low；`sat < 0.03` 且 `Y∈[32,224]` → 疑似纯色/白屏 low；`sat ≥ 0.6` 且 `cast ≥ 60/255` → 纯色屏（绿屏/蓝屏）medium，连续 ≥ 2 样本升 high。
- 曝光/纯色类按**连续命中段**各自成条（`start/end` 只在段内），不把相隔很远的两个采样点连成一条横跨全片的区间；纯色屏样本不再计入 D10 偏色。
- HDR：采样链前置 `zscale=t=linear:npl=100,tonemap=hable,zscale=t=bt709:m=bt709:p=bt709`；失败则跳过并记 info。

### 7.8 D12 花屏 / 码流损坏

- stderr 匹配 `error while decoding|corrupt|Invalid NAL|no frame!|missing picture|Invalid data found` 计数 `n_err`，保留前 20 条原文（截断 200 字符）。
- `n_err > 0` → medium；`n_err > 50` 或存在 `flags` 含 `C`（Corrupt）的包 → high。`D`=Discard 是可丢弃包，不计坏包。
- 面板「证据」列给前 3 条错误原文。

### 7.9 D14 尾部截断

基准是**视频流自己声明的时长**（`video.duration`，缺失时回退容器时长），与视频末包时间差 > `truncation_gap_sec`(2.0s) → 「疑似录制中断/未正常收尾」medium。用容器时长会误报：音轨比视频长（如 `-map 1:a` 封装的 10s 视频 + 14s 音轨）时容器时长由音频撑起，旧口径会把完整文件判成截断（已修，见 `tests/test_detectors.py::test_d14_not_fooled_by_long_audio`）。片尾 5s 内黑场写入 detail。D01 只报「容器时长缺失」，截断判定只归 D14。

### 7.10 合并规则（`merge_issues()`）

1. 同 kind、时间重叠 > 50% 的区间合并。
2. `freeze` 被 `black` 包含 → freeze 降 info。
3. 跨项收敛（同一处故障只留一条主问题）：
   - `freeze` 与任一 `pts_gap` 重叠 ≥ 50% → freeze 降 info，detail 注明「与跳秒同段，可能是同一处丢帧」
     （真实素材上 5 处跳秒与 5 条冻结完全重合：丢帧是根因，画面不动是表现）。
   - 片长 ≥ 10s、`black` 覆盖 ≥ 90% 且同时存在 `silent_audio`/`no_audio` → black 升 high，注明「可能整段没录到画面」。
3. 排序：严重度 → start → kind。
4. `FileResult.conclusion` / `ok` 由 model 统一计算（C8：high→failed，medium/low→warned，仅 info→passed，`status != done`→unfinished）。

---

## 8. 配置设计

`config.json` 是唯一需要用户碰的文件（程序根目录）：

```json
{
  "config_version": 1,
  "extensions": ["mp4","mov","mkv","avi","flv","ts","m2ts","mpg","webm","m4v","wmv","3gp"],
  "jobs": 2,
  "quick_check_on_select": true,
  "timeout_sec": 3600,
  "pts_gap_factor": 3.0,
  "pts_gap_min": 0.2,
  "freeze_noise": 0.003,
  "freeze_min_sec": 2.0,
  "black_min_sec": 0.5,
  "silence_db": -50,
  "silence_min_sec": 3.0,
  "mean_volume_low_db": -30,
  "silent_mean_db": -45,
  "clipping_db": -0.1,
  "require_audio": true,
  "av_offset_sec": 0.5,
  "truncation_gap_sec": 2.0,
  "color_cast_threshold": 25,
  "color_sample_sec": 5.0,
  "color_window_samples": 3,
  "hdr_tonemap": true,
  "report_dir": "reports"
}
```

规则：

- **容错**：解析失败/键类型错 → 该项用默认值，状态栏逐条提示（`color_cast_threshold 无效(abc)，已用默认 25`），绝不崩溃。
- **版本迁移**：`config_version` 小于当前 → 内存里补齐新键，**不自动改写用户文件**。v1 界面没有配置编辑，因此也没有「保存配置」按钮（改完 config.json 重启生效）。
- **未知键**：忽略并记日志（保留向前兼容）。
- **优先级**：`默认 < config.json`。命令行**不覆盖阈值**（headless 的 `--config` 只换文件路径，`--jobs`/`--timeout` 只作用于本次执行）；GUI 不提供阈值编辑（v1）。报告头写 `config_hash` 前 8 位，保证两份报告可比、可复核。
- **内部常量**：其余判定常量（freeze ≥10s 升 high、冻结/黑场重叠 80%、黑场覆盖 30%、缺失占比 0.5%、采样覆盖 ≥80% 等）集中在 `core/constants.py`，不开放给用户；报告不列出。

---

## 9. 报告产物

### 9.1 `report.md`（GUI 导出的主产物）

```markdown
# 录像质量检测报告
扫描目录：D:/videos/2024-11-20     生成：2025-11-23 21:40:12
工具：videoqc 0.3 · ffmpeg 6.1.1 · 模式：全量 · 阈值：a1b2c3d4（config 哈希前 8 位，见 SPEC §8.2）
合计 12 个：通过 7 · 警告 3 · 失败 2

## 概览
| # | 文件 | 时长 | 结论 | 问题 |
|---|------|------|------|------|
| 1 | cam01_1130.mp4 | 00:42:10 | ❌ 失败 | 跳秒×2、无音轨、偏色 |
| 2 | cam02_1130.mp4 | 00:38:02 | ⚠️ 警告 | 卡顿 00:12:03–00:12:19 |
| 3 | cam03_1130.mp4 | 00:40:55 | ✅ 通过 | — |

## 详情

### 1. cam01_1130.mp4 — ❌ 失败
容器 MP4 · H.264 1920×1080 25fps 3.9Mbps · 音频：无

| 时间 | 类型 | 级别 | 描述 | 证据 |
|------|------|------|------|------|
| 00:07:12.400 → 00:07:13.360 | 跳秒 | ❌ | PTS 跳变，缺约 24 帧（0.96s） | delta=0.960s, p50=0.040s |
| — | 无音轨 | ❌ | 文件不含音频流 | streams=[video] |
| 00:02:00.000 → 00:19:59.000 | 偏色 | ⚠️ | 疑似整片偏黄，cast=38/255 | dr=+31 dg=+27 db=-10 |

<details><summary>复现命令</summary>

```bash
ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,duration_time,flags -of csv=p=0 cam01_1130.mp4
ffmpeg -nostdin -v info -i cam01_1130.mp4 -vf "freezedetect=n=0.003:d=2,blackdetect=d=0.5" -af "silencedetect=n=-50dB:d=3,volumedetect" -f null -
```
</details>
```

- 排序：失败 → 警告 → 通过；组内按问题数降序。
- **范围标注**：每个文件标题里标「快检」或「全量」；未完成的标「未完成（已取消/超时）」，并且在概览统计里单独一列，不混进「通过」。
- 时间 `HH:MM:SS.mmm`；JSON 里并存 `start_sec` 浮点。
- 末尾附录：只有检测项说明（检查了什么/没检查什么）。阈值表、ffmpeg 路径、环境信息不进报告——报告是给人扫结论的，复现靠头部的 `config_hash`。

### 9.2 `report.json`

与 md 同源（`Report`），`schema_version: 2`（v0.3 新增 status/generation/conclusion），字段同 §6.2，另含 `config_effective` 与 `config_hash`（`config_effective` 的 canonical JSON 取 sha256 前 8 位）。用于看板/回归。

### 9.3 退出码（headless 模式）

| 码 | 含义 |
|----|------|
| 0 | 全部通过（`conclusion == "passed"`，即无 high/medium/low） |
| 1 | 存在未通过的文件（warned 或 failed，**low 也算**） |
| 2 | 运行错误（参数、ffmpeg 缺失、目录不可读） |

---

## 10. headless CLI（次要入口，同包内）

用途：批量跑夜班、回归测试、CI；GUI 与 CLI 共用 `core/`，行为一致。

```bash
videoqc.exe --headless <目录或文件...> \
  [-o report.md] [--json report.json] [--jobs 2] [--fast] \
  [--min-severity low] [--timeout 3600]
```

- 无参数启动 = GUI；带 `--headless` = 只跑分析，不弹窗。
- GUI 里的「快速/深度扫描」不调用 CLI，两者都调 `core.pipeline`，避免双实现。
- **坑（已知）**：PyInstaller `console=False` 的 exe 没有 stdout/stderr。处理：`--headless` 时用 `AttachConsole(ATTACH_PARENT_PROCESS)`（失败则 `AllocConsole`）接管父控制台；同时结果始终写 `-o report.md` / `--json`，**报告文件是权威产物**，控制台输出只是方便。

---

## 11. 性能与并发

| 阶段 | 成本（1080p30，1h，机械盘） | 优化 |
|------|------------------------------|------|
| 探测 | < 0.2s | 一次 ffprobe 取 format+streams |
| 包级 | 3–10s | 只取 3 个字段，不 `-show_frames` |
| 解码级 | 8–25 min | 一次解码跑全部滤镜 |
| 色彩采样 | 10–30s | 完整帧流 + `fps=1/5` 取最近帧 + 32×18 缩略（不再 `-skip_frame nokey`，见 OPTIMIZATION #1） |

- 并发按文件（子进程 CPU 密集，GIL 无影响）：默认 2，机械盘 1，SSD 可 4；播放时降到 1（§3.4）。
- 内存常量级：stderr 逐行消费；采样只累加统计量，不存帧。
- **双管道必须并行消费**（阶段 ④）：stdout 读像素、stderr 读 `showinfo`，用 `subprocess.Popen` + 两个读者线程（或 `selectors`）+ 队列；只读一边会让另一边的管道缓冲区（约 64KB）写满，ffmpeg 阻塞，表现为「采样卡到超时」。
- 超时：`timeout_sec` 到点 kill，`status="timeout"`（不额外造 high Issue，结论走 unfinished），继续下一个。
- 不做跨会话缓存（已确认每次重扫）：同一会话内已分析的文件直接复用内存结果。

---

## 12. 错误处理与健壮性

| 场景 | 处理 |
|------|------|
| ffmpeg/ffprobe 缺失或不可执行 | 启动自检失败，弹窗给「重新解压」指引，退出码 2 |
| config.json 损坏 | 逐项回退默认 + 状态栏提示，正常启动 |
| ffprobe 解析失败（损坏/加密/非视频） | 该文件 D01 high，`error` 存 stderr 前 500 字 |
| 无视频流（纯音频/图片） | D01 high，跳过后续 |
| ffmpeg 中途崩溃 | 已解析事件保留，补 `decode_aborted` medium |
| 播放不支持（如部分 .ts 封装） | 播放框内提示，不弹窗；检测照常 |
| 中文/空格/特殊字符路径 | 全程参数列表调用（不经 shell）；报告与 UI 保留原名 |
| Windows 260 字符路径 | 内部加 `\\?\` 前缀 |
| 磁盘满/无写权限 | 导出时捕获，提示换目录，不阻断扫描 |
| 同时扫描 + 播放卡顿 | 播放期间并发降 1；状态栏可提示「扫描中，播放可能受影响」 |
| 用户关闭窗口时仍在扫描 | 询问「停止扫描并退出？」，确认后 terminate 子进程 |

---

## 13. 打包与构建

### 13.1 技术选型对比（为什么是 PySide6 + Qt Multimedia）

| 方案 | 播放覆盖 | 体积 | 复杂度 | 结论 |
|------|----------|------|--------|------|
| **PySide6 + QtMultimedia（FFmpeg 后端）** | H.264/HEVC/VP9/AV1（Qt 自带 av*.dll） | 中（~250MB） | 低（一个 pip 依赖搞定 GUI+播放） | **采用** |
| Electron + `<video>` | 无 HEVC（Chromium 不支持），H.264/MP4 可用 | 大 | 中高（Node + Python 双运行时） | 淘汰 |
| tkinter + libvlc | 最全（VLC） | 大（+150MB plugins） | 中高（libvlc 打包坑多） | 备选 |
| 本地 web + ffmpeg 实时转码 | 全（转码后） | 小 | 高（seek/切片） | 淘汰：seek 体验差 |
| 本地 web + HTML5 直放 | **H.264/MP4 直接可用**，无转码、seek 走 Range | **最小** | 低 | 决赛选手，因「要一个 exe 窗口、不是浏览器标签」而放弃 |

- 依赖装 `PySide6-Essentials` + `PySide6-Addons`：**QtMultimedia 在 Addons 里，不在 Essentials**（实测 6.9.3：装 Essentials 后 `import PySide6.QtMultimedia` → `ModuleNotFoundError`）。Addons wheel 160MB，但 PyInstaller 只收被引用的 Qt 模块，实测产物再定。
- 不装/不打包 QtWebEngine、QtCharts、Qt3D、QtQml 等重模块（QtWebEngine 单模块就上百 MB）。
- 播放后端已实测：`plugins/multimedia/` 含 `ffmpegmediaplugin.dll` + `windowsmediaplugin.dll`；`Qt6Multimedia.dll` 只 import `Qt6Core/Qt6Gui/Qt6Network`（**不依赖 QtQml**），所以 excludes 里排掉 QtQml/QtQuick 是安全的；但它依赖 `MSVCP140/VCRUNTIME140`，这两个 DLL PySide6 自带，打包时要一起收。
- Qt6 ≥ 6.5 在 Windows 默认 FFmpeg 后端；构建时校验 `_internal/PySide6/plugins/multimedia/ffmpegmediaplugin.dll` 存在，否则回退 Windows Media Foundation 并降低播放承诺。

### 13.2 选型定案（2025-11-23）

素材确认为 **H.264 + MP4**、包体积 ≤200MB 可接受。因此：

- Qt 的「codec 覆盖」优势不再是决定性理由；决定性理由变为 **单运行时 + 单语言 + 成熟打包 + 真桌面窗口**。
- 「本地 web + HTML5 直放」方案对 H.264/MP4 完全可行（视频走 HTTP Range 直放，秒 seek，包最小），被否决的唯一原因是交付形态：需要的是一个 exe 窗口，而不是让用户开浏览器。若将来要减体积/上 Linux，这是首选回退路径。
- Electron 被淘汰：播放能力没有增益，却引入 Node + Python 双运行时。
- 结论：**PySide6（Essentials + Addons）+ QtMultimedia，按本文实施。**（Addons 仅为 QtMultimedia 而装）

### 13.3 构建目录与步骤

仓库根新增一个**打包专用目录 `packaging/`**（入库）：`videoqc.spec`、`build_portable.py`、`version_info.txt`、`licenses/`。产物一律进 `dist/`（不入库）。

```
packaging/               # 打包源码
├─ videoqc.spec          # PyInstaller onedir 描述
├─ build_portable.py     # 组装 green 包 + 自检 + zip
├─ version_info.txt      # exe 资源版本（取 videoqc/version.py）
└─ licenses/             # THIRD-PARTY.md / LGPL-3.0-Qt.txt / GPL-ffmpeg.txt
dist/                    # 产物：videoqc/、zip、_work/（均 gitignored）
```

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt   # 依赖已钉版本，见 requirements.txt
python packaging/build_portable.py    # 内部调 PyInstaller，见 SPEC §9.2
```

- **不能用 `build/` 做目录名**：PyInstaller 默认 `--workpath` 就叫 `build/`，`--clean` 会连源码一起清。中间产物固定 `dist/_work/`。
- `videoqc.spec` 要点：`console=False`；`icon`/`version`/`datas` 用 `os.path.join(ROOT, ...)` 绝对路径（脚本路径按 CWD 解析、icon 按 SPECPATH 解析，两套基准，见 SPEC §9.1）；`excludes=['PySide6.QtQml','PySide6.QtWebEngine*','PySide6.Qt3D*','PySide6.QtDBus','tkinter','test']`；`datas=[('assets/app.ico','.')]`；版本资源 `version_info.txt`（产品名/版本/版权）。
- 构建后脚本 `build_portable.py`：
  1. 复制 `config.json` / `README.md` / `CHANGELOG.md` / `packaging/licenses/` / `bin/ffmpeg*.exe` / 空 `reports/`；
  2. 校验必需文件清单（见 §13.4）；
  3. 生成 `THIRD-PARTY.md`（写入实际 ffmpeg/Qt 版本号）；
  4. `zip -9` 打包为 `dist/videoqc-<ver>-win64.zip`。
- 版本号单一来源：`videoqc/version.py`，同时写入 exe 资源与 CHANGELOG。

### 13.4 发布前自检清单（打包脚本自动跑）

- [ ] `bin/ffmpeg.exe -version`、`bin/ffprobe.exe -version` 可执行
- [ ] `_internal/PySide6/plugins/multimedia/ffmpegmediaplugin.dll` 存在
- [ ] `config.json` 可被 `app.config` 解析，无告警
- [ ] 干净 Windows 10 虚拟机：解压 → 双击 → 选目录 → 播放 → 全扫 → 导出，全通过
- [ ] 无管理员权限运行通过；断网运行通过
- [ ] zip 内无 `*.py`、无 `__pycache__`、无测试素材
- [ ] `_internal/vcruntime140.dll` 存在（R9「不装 VC 运行库」的前提）
- [ ] 同目录产出 `videoqc-<ver>-win64.zip.sha256` 并随包分发

### 13.5 分发、升级与回滚

| 环节 | 做法 |
|------|------|
| 渠道 | 内网共享盘 / U 盘，只发整包 zip + `.sha256`；不发单文件、不发源码 |
| 版本命名 | `videoqc-<semver>-win64.zip`；内容有任何修改必须升版本号，同号不重发 |
| 升级 | 解压到**新目录** → 把旧 `config.json` 拷到新目录（覆盖默认）→ 确认能跑 → 删旧目录；不做增量补丁 |
| 回滚 | 保留上一版 zip；回滚 = 解压旧包 + 拷回 `config.json`。程序无状态（不写注册表/APPDATA），回滚无残留 |
| 发布说明 | CHANGELOG 必写「检测规则/默认阈值是否变化」；阈值一变 `config_hash` 就变，旧报告不能直接对比 |
| 签名 | v1 不签名（已定），SmartScreen 提示按 README 指引「更多信息 → 仍要运行」；若后续需要，内部自签证书 |
| 杀软误报 | PyInstaller 产物容易被拦，分发时附来源说明与 sha256，必要时加白名单 |

---

## 14. 可扩展性

```python
class Detector(Protocol):
    id: str                                    # "D03"
    stage: Literal["probe", "packets", "decode", "color"]
    def check(self, ctx: "FileContext") -> list[Issue]: ...   # cfg 在 ctx.cfg
```

- 新检测项：新增 `core/detectors/xxx.py` + 注册，不改管线。
- 需要像素的新检测：优先在现有解码链 `-vf` 追加只读滤镜；确需二次解码时显式声明并加入「快检跳过列表」。
- 预留（不在 v1）：码率合规、GOP 长度、音频采样率/声道异常、画面抖动（`vidstabdetect`）、缩略图墙、HTML 报告、多目录标签页。
- 明确不实现（需求已排除）：跨会话结果缓存、问题截图、阈值设置界面、预设档位。

---

## 15. 实施计划

| 里程碑 | 内容 | 产出 | 预估 |
|--------|------|------|------|
| M0 | Spike（半天内）：装 PySide6，用 5–10 个真实 H.264/MP4 录像验证「播放 / seek 跟手 / 逐帧能步进 / 首帧不黑 / 中文路径」 | 选型确认，无交付物 | 0.5 天 |
| M1 | `core` + `media`：model/config/probe/runners/pipeline + D01/D03 | headless 能跑通并出 md | 1 天 |
| M2 | 解码链 D05/D07/D08/D09/D12 + 合并规则 + D02/D04 | 「卡顿/无声/黑场/花屏」可用 | 1 天 |
| M3 | 色彩采样 D10/D11 + HDR 处理 | 「偏色/曝光」可用 | 0.5 天 |
| M4 | GUI：主窗口/文件表/播放器/异常面板/扫描线程 | 三分区界面可用，点问题跳转播放 | 2 天 |
| M5 | 打包：spec + build_portable + 许可证 + 干净机验证 | `videoqc-0.3-win64.zip` | 1 天 |

不做（需求已排除）：结果缓存、问题截图、阈值设置界面、预设档位、多语言。

**验证素材（无真实问题视频时自造）**：

```bash
# 基线
ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30 -f lavfi -i sine=frequency=440 \
       -t 20 -pix_fmt yuv420p -c:v libx264 -c:a aac base.mp4
# 跳秒：保留原 PTS 删掉 5–6s → 时间轴空洞
ffmpeg -i base.mp4 -vf "select='not(between(t,5,6))'" -fps_mode passthrough -an -c:v libx264 gap.mp4
# 冻结：5–8s 重复同一帧；黑场：drawbox 填黑；静音：anullsrc；偏色：colorbalance=rs=.5
# 无音轨：-an；花屏：删除部分包并 -c copy
```

回归断言：每个自造样本命中对应 kind，且不误报其他 kind。

---

## 16. 风险与限制

| 风险 | 影响 | 对策 |
|------|------|------|
| 本机当前无 ffmpeg（已实测 PATH 无 `ffmpeg`/`ffprobe`） | 无法开发验证 | 构建前下载 gyan.dev essentials 静态构建放入 `bin/` |
| 未签名 exe 触发 SmartScreen | 用户不敢运行 | README 说明「更多信息→仍要运行」；后续可自签（内部用） |
| Qt 播放边缘情况（畸形 MP4、超长时长、极低码率） | 个别文件不能播或 seek 卡 | M0 spike 用真实素材兜底；播放框内提示，检测不受影响；不做转码（划界） |
| 全解码耗时（1h 1080p 约 10–25min，20 个文件 = 3–8h） | 深度扫描很慢 | 快检与深度扫描分离（先几十秒出全局，再按需深挖）；并发 2；夜间批跑。不做缓存（已排除） |
| 灰世界偏色误报（大面积单色场景） | 假阳性 | 限 medium、时间窗、措辞「疑似」、点击跳转人工确认 |
| 冻结 vs 静止画面不可分 | 假阳性 | 长冻结降级、重叠黑场合并、跳转回放确认 |
| VFR（手机/录屏） | 跳秒误报 | p50 自适应 + 最小间隔 0.2s |
| HDR/LOG 素材 | 偏色误报 | 检测 `color_transfer` 先 tonemap，失败则跳过并记 info |
| 包体积：实测可能 80–160MB（上限 200MB） | 传输多等几秒 | 接受，不裁 codec；超 200MB 再换 7z 或去掉不用的 codec |
| 逐帧落点误差 0–2s（Qt `setPosition` 吸附关键帧） | 「逐帧」不是真的逐帧 | 已定不修：需求只到「定位到附近并观察」，验收按 keyframe 误差 ≤2s（R11） |
| 许可证合规（GPL/LGPL） | 对外分发风险 | 随包 `licenses/` + THIRD-PARTY 清单；仅子进程调用 ffmpeg、动态链接 Qt |

---

## 17. 附录：命令与组件速查

- ffmpeg ≥ 4.2（`freezedetect` 起始）；推荐 ≥ 6.0。用到 `ffprobe`、`ffmpeg`，滤镜 `freezedetect` `blackdetect` `silencedetect` `volumedetect` `zscale`（HDR，需 libzimg，官方构建含）。
- PySide6 ≥ 6.5，且必须装 `PySide6-Essentials` **与** `PySide6-Addons`（QtMultimedia 只在 Addons 里；实测 6.9.3）；Python 3.13；PyInstaller ≥ 6.10。版本一律以 `requirements.txt` / `requirements-dev.txt` 为准（已钉 6.9.3 / 6.22.3 / pytest 9.2.0）。

```bash
# 1 流信息
ffprobe -v error -print_format json -show_format -show_streams F
# 2 包时序
ffprobe -v error -select_streams v:0 -show_entries packet=pts_time,duration_time,flags -of csv=p=0 F
# 3 一次解码：冻结 + 黑场 + 静音 + 音量 + 错误
ffmpeg -nostdin -v info -i F -map 0:v:0 -map 0:a:0? \
  -vf "freezedetect=n=0.003:d=2,blackdetect=d=0.5:pic_th=0.98:pix_th=0.10" \
  -af "silencedetect=n=-50dB:d=3,volumedetect" -f null -
# 4 等间隔色彩采样（32×18 RGB24 原始帧；不用 -skip_frame nokey，见 OPTIMIZATION #1）
ffmpeg -nostdin -hide_banner -nostats -v info -i F -an -vf "fps=1/5,showinfo,scale=32:18:flags=area" -pix_fmt rgb24 -f rawvideo pipe:1
# 5 问题点抽帧（备用：当前需求不做截图，保留命令供人工复查）
ffmpeg -nostdin -v error -ss 431.4 -i F -frames:v 1 -q:v 3 shot.jpg
```

---

## 18. 决策记录（ADR）

| # | 决策 | 理由（为什么不是另一个） |
|---|------|--------------------------|
| D1 | GUI = PySide6（Essentials+Addons）+ QtMultimedia | 素材确认 H.264/MP4 后，codec 优势不再是关键；关键是**单语言单运行时**（分析引擎也是 Python）、真桌面窗口、PyInstaller 打包成熟。Electron 无增益且引入双运行时；本地 web 方案最小但交付形态不符 |
| D2 | 只装 Essentials + Addons，排除 QtWebEngine/QtCharts/Qt3D/QtQml | Addons 里只有 QtMultimedia 被用到（`Qt6Multimedia.dll` 不依赖 QtQml，已核实）；QtWebEngine 单模块就上百 MB，省下来的是它 |
| D3 | onedir 打包（非 onefile） | 启动快、结构清晰、Qt DLL 可被用户替换（LGPL 合规）；onefile 每次启动解压到临时目录，几十秒 |
| D4 | `bin/ffmpeg.exe` 与 Qt 自带 `av*.dll` 并存 | 分析走 CLI 子进程（可复现、不依赖 Qt）；播放走 Qt 后端（不依赖系统 codec）。多花 30–60MB 换解耦，v1 不优化 |
| D5 | 一次解码原则：`freezedetect+blackdetect` / `silencedetect+volumedetect` 同进程 | 解码是最贵环节，四个检测项共用一次像素扫描 |
| D6 | 跳秒用 ffprobe 包级 PTS，不用 `-show_frames` | 帧级要解全片像素，慢一个数量级；包 PTS 已能定位时间轴不连续 |
| D7 | 偏色用完整帧流 `fps` 采样 + 32×18 RGB 均值 + 灰世界 | 早期用 `-skip_frame nokey` 求快，但实测时间戳错位/丢采样点会把全片偏色从 high 压成 medium；准确性优先（见 OPTIMIZATION #1）。纯标准库算均值；误报靠「时间窗 + 点击回放确认」控制 |
| D8 | 扫描拆「快速/深度」两模式 | 20 个 1h 录像全量 = 3–8h；必须先几十秒得出全局结论 |
| D9 | 不做跨会话缓存，结果只在内存 | 用户确认每次重扫；省掉一个文件格式与一类过期 bug |
| D10 | 阈值只在 `config.json`，界面只展示快照 | 用户确认手改即可；不做设置对话框与预设档位 |
| D11 | 报告简洁版（无封面/截图/客户排版） | 报告读者是自己 |
| D12 | 播放中并发固定降为 1（无开关） | 解码子进程抢 CPU 会直接导致播放抖动；开关只会增加误配 |
| D13 | 关闭窗口时若在扫描 → 询问后 terminate 子进程 | 不留孤儿 ffmpeg 进程占 CPU/句柄 |
| D14 | 采样阶段加 `showinfo` 并用双通道配对时间 | 否则偏色时间靠序号×5s 推算，会漂（C2） |
| D15 | 时间基准统一到视频流内秒，seek 时再加回 `start_time` | MP4 edit list / TS 基准会让报告与播放器差 1.4s（C1） |
| D16 | 中断/取消的「空结果」不算通过 | 否则未分析的文件在报告里显示为「没有问题」（C5） |
| D17 | 包级时间减 `video.start_time`；ffmpeg 滤镜输出不再减 | 早期认为滤镜输出是绝对 PTS，实测 ffmpeg 未加 `-copyts` 已归一化到流起点；重复相减会让 TS/监控录像（start_time≠0）出现负时间与时间轴错位（C1，见 OPTIMIZATION 专家复核） |
| D18 | `conclusion` 在 model 里算一次，UI/报告/CLI 都读它 | 「low 也算不通过」这条规则原来在 3 处复述，且 DESIGN 写成「无 high/medium」，改一处必漏两处（C8） |
| D19 | Session 接受规则「mode 等级优先，其次代次」 | 纯比代次时，先提交后完成的 full 结果会被后提交的 fast 挡住，深度扫描白跑 |
| D20 | 无音轨时不加 `-af` / `-map 0:a:0?` | 「ffmpeg 对无音频流会忽略 -af」未写进文档且跨版本不可赌；probe 已知 has_audio，直接分支最简单 |

**待定（不阻塞开发，实现时随手定）**：exe 图标与文件描述、产品显示名（videoqc / domaMedia）、报告目录名（`reports/`）、是否需要「打开报告所在目录」按钮、界面字号与主题（默认系统深色浅色跟随）。
