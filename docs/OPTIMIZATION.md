# videoqc 优化与完善清单

> 基于 v0.3.2 源码审查 + 视频分析口径复核（2026-11-24），聚焦录像问题分析链路 `core/` + `media/`。
> 初始基线：`pytest -q` 83 项全绿、`ruff check` 零发现、`mypy` 零发现。
> 下列 9 项均已对照代码与 SPEC 核实，且**均未被现有测试覆盖**。标记为「已实测」的项在本地复现过。
> 第 1 项为视频分析专家视角复核新增，优先级最高——它不是代码写法问题，而是**检测口径本身失真**。

> ## ✅ 执行状态：全部完成（2026-11-24）
> - **原 9 项已全部修复并补齐回归测试**；两轮视频专家视角复核又新增 2 项（见下方「专家复核新增」），同样已修复。
> - 最终基线：`pytest -q` **123 项全绿**、`ruff check` 零发现、`mypy` 零发现（`videoqc/` 全部 35 个源文件）。
> - 每个修复点都配了回归用例，避免这类口径错误回潮。
> - 同步更新了 `SPEC.md` / `DESIGN.md` 中与实现矛盾的旧描述（flags 含 D、skip_frame nokey、时间基准等）。
>
> ### 专家复核新增（两遍审视，已修）
> - **A. `origin_sec` 重复扣减（P0）**：ffmpeg 未加 `-copyts`，解码滤镜（freezedetect/showinfo 等）的输出**已被 ffmpeg 默认归一化到流起点**；管线再减一次 `origin_sec`，会让 start_time≠0 的素材（MPEG-TS start≈1.47s、监控录像、offset 文件）出现负时间戳，定位/冻结区间/D14 全部错位。实测 offset.mp4 采样变成 `[-10,-5,0,5]`。修复：解码/采样出口不再减 `origin_sec`（参数保留兼容）。C1 语义「视频流内秒」不变。**已实测**。
> - **B. D04 把跳秒误判成 VFR（P1）**：原用 `pstdev` 判帧间隔波动，单点 1s 跳秒就能把标准差拉高十几倍，把恒定帧率判成可变帧率，还连带抑制 `fps_mismatch`。改用 MAD 稳健估计（同 D03 思路），对离群点免疫。实测 gap.mp4 不再误报 VFR。**已实测**。

---

## 汇总

| # | 优先级 | 类别 | 问题 | 定位 |
|---|--------|------|------|------|
| 1 | **P0** | **视频分析口径** | **抽帧用 `-skip_frame nokey`，时间戳错位 + 采样点丢失，严重性判定失真** | `media/runners.py:201-205` |
| 2 | P0 | 检测正确性 | D12 坏包标志位判定错误（`D` 是 Discard，`C` 才是 Corrupt） | `media/probe.py:167`、`core/detectors/corruption.py:15` |
| 3 | P0 | 检测正确性 | D13 低采样率检查是死代码（字符串 vs `isinstance(int)`） | `media/probe.py:122`、`core/detectors/integrity.py:114` |
| 4 | P0 | 检测正确性 | `freeze_duration` 解析后从未使用，SPEC 要求的 fallback 缺失 | `media/runners.py:38,53-60,100-107` |
| 5 | P1 | 状态语义 | `timeout` 状态不可达，超时文件被算作 `warned` | `core/pipeline.py:30-112`、`media/runners.py:181-182` |
| 6 | P1 | 健壮性 | D05 冻结检测除零崩溃（`d / dur`，dur=0 时） | `core/detectors/freeze.py:19` |
| 7 | P1 | 可维护性 | SPEC §1.2 要求的运行日志系统完全未实现 | 全局（无 logging/excepthook），`logs/` 为空 |
| 8 | P2 | 展示 | `decode_errors` 缺中文标签，报告与界面显示英文原串 | `media/report.py:25` |
| 9 | P2 | 文档一致性 | SPEC §3 与 §6.3 对 `--jobs` 是否覆盖配置自相矛盾 | `docs/SPEC.md:323` vs `:715` |

---

## 1. 抽帧时间戳错位与采样点丢失（P0，视频分析专家复核，已实测）

**现象**：`sample_frames()` 在 `runners.py:204` 用了 `-skip_frame nokey`（只解码关键帧），随后滤镜链是 `fps=1/N,showinfo,scale`（`:202`）。

**实测证据**（`base.mp4`，20s，GOP=8.33s，关键帧在 0 / 8.33 / 16.66s）：

| 观测项 | 结果 |
|--------|------|
| 真实解码的关键帧 PTS | `0, 8.333, 16.667` |
| `sample_frames()` 返回的 `t` | `0.0, 5.0, 10.0` ← 只有 3 个，丢了 t=15 |
| 不加 `-skip_frame nokey` 时 | `0, 5, 10, 15` ← 4 个，符合预期 |

**根因**：`fps` 滤镜对稀疏的关键帧输入重新采样，并按规则时间点（0/5/10）**重新打时间戳**。而 `showinfo` 位于 `fps` 之后，取到的是被重打的"名义时间"，不是画面的真实 PTS。于是：

- 标注为 `t=5s` 的样本，画面实际来自 `8.33s` 的关键帧；
- 标注为 `t=10s` 的样本，画面实际来自 `16.66s` 的关键帧。

**连锁影响（这是本项被列为最高优先级的原因）**：

1. **采样点系统性丢失**：当 GOP > `color_sample_sec` 时必然丢点（20s 素材少 25% 采样）。`black4.mp4`、`freeze3.mp4` 实测只采到 1 个点。
2. **时间定位错误**：报告写"t=5s 偏色/黑场"，实际画面是 8.33s 的——与工具"把缺陷定位到秒"的核心卖点直接冲突，用户按报告跳过去看不到问题。
3. **严重性判定失真**（已实测）：`cast.mp4` 全片偏色，本应 `ratio = 20/20 = 1.0 → high`；因采样只到 t=10，`span = 15s`、实测 `ratio = 0.75 < 0.8`，被判成 **medium**。D10 的 high/medium 结论被采样bug直接改写。
4. **D11 同样受影响**：曝光、纯色屏、纯色画面都吃这批样本，采样稀疏会漏掉短暂的绿屏/白屏。

**修复建议**（按推荐度）：
- **首选**：去掉 `-skip_frame nokey`，让 `fps` 对完整解码帧流取最近帧。代价是解码量上升，但 `sample_frames()` 本就是深度扫描的第二步（解码已跑过一遍），准确性应优先。
- **精确定位方案**：按 `color_sample_sec` 用多次 `-ss` 逐点抽帧，时间戳与画面严格对齐，最准但最慢。
- **若必须保留关键帧加速**：不要用 `fps` 重打戳，直接用关键帧真实 PTS 构造 `Sample`，并在报告里明确"色彩采样为关键帧近似"——但需接受采样稀疏，且不能声称定位到秒。

**验证**：
- 断言 20s / GOP=8.33s 素材采到 ≥4 个点（t=0/5/10/15）。
- 断言 `cast.mp4` 的 `color_cast` 判为 `high`（ratio≥0.8）。
- 断言 `Sample.t` 与画面真实来源时间误差在一个采样间隔内。

---

## 2. D12 坏包标志位判定错误（P0，已实测）

**现象**：`probe_packets()` 用 `if "D" in flags` 统计 `corrupt_flags`。

**根因**：ffprobe `-show_entries packet=flags` 输出的是 3 字符位串，顺序为 **K / D / C**（见 FFmpeg `fftools/ffprobe.c` 的 `show_packet()`）：

```c
print_fmt("flags", "%c%c%c", pkt->flags & AV_PKT_FLAG_KEY     ? 'K' : '_',
                             pkt->flags & AV_PKT_FLAG_DISCARD ? 'D' : '_',
                             pkt->flags & AV_PKT_FLAG_CORRUPT ? 'C' : '_');
```

`D` = `AV_PKT_FLAG_DISCARD`（解码后可丢弃的包，B 帧重排时正常出现），`C` = `AV_PKT_FLAG_CORRUPT`（真正损坏的包）。代码把位置 2 当成了坏包标记。

**影响**（双向误判）：
- 真坏包（flags 含 `C`）漏报；
- 含 `D` 的正常丢弃包被累计进 `corrupt_flags`，而 `corruption.py:15` 中 `corrupt > 0` 直接判 `high`，可把正常文件误报为失败。

**修复建议**：
- `probe.py` 中改为按位置判断损坏位：`flags[2:3] == "C"`（或 `flags.endswith("C")`），并保留对 `D` 的可选统计（若要单独提示）。
- `corruption.py` 的 `evidence` 增加 `discard_flags`，便于区分。
- `docs/SPEC.md` §4.2（`probe_packets` 规格）与 §5.2（D12 伪代码）同步改正。

**验证**：构造一个 flags 为 `K_C` 的样本应触发 `decode_errors`/high；flags 为 `K_D` 不应触发。现有 `tests/data/corrupt.mp4` 的 flags 实际全是 `K__`/`___`（损坏在 mdat 字节层，靠解码错误捕获），因此现有测试无法暴露此 bug。

---

## 3. D13 低采样率检查是死代码（P0，已实测）

**现象**：`integrity.py:114` 的 `low_sample_rate` 从未触发。

**根因**：ffprobe JSON 中 `sample_rate` 是字符串（实测 `"44100"`），`probe.py:122` 原样保留；检测器却用 `isinstance(rate, int)` 守卫。实测把值强制为 `'8000'` 仍不产生任何 issue。

**影响**：规格声明的「采样率偏低」提示「不算缺陷但需口径提示」永远缺失；`channel_anomaly`（`channels` 是 int，正常）不受影响。

**修复建议**：二选一——
- 在 `probe.py` 采集时用 `_as_int(v.get("sample_rate"))` 转成 `int`（推荐，与 `channels` 类型一致）；
- 或在 `integrity.py` 用 `_float()`/`int(rate)` 兼容字符串。

**验证**：新增夹具或 monkeypatch `sample_rate="8000"`，断言产生 `kind=="low_sample_rate"` 的 info issue。

---

## 4. `freeze_duration` 解析后从未使用（P0）

**现象**：`runners.py` 的 `_State.freeze_dur` 在 `freeze_start` 时清空、在 `freeze_duration` 行赋值、在 `freeze_end` 时清空——但全文件没有任何一处读取它来计算区间。

**根因**：`parse_line()` 的 `_FREEZE_END` 分支直接使用 `freeze_end` 的时间戳；`close_open()` 对未闭合冻结统一用 `duration_sec` 收尾。

**影响**：SPEC §4.3 明确要求「`freeze_end` 与 `freeze_duration` 可能只有其一：若只有 duration，则 `end = start + duration`；两者都没有才用 `duration_sec`」。当 ffmpeg 只输出 duration 而不输出 end 时（某些版本/边界场景），实际会走到「两者都没有」的分支，把冻结区间拉长到文件末尾，可能把一个几秒的冻结误报成长时间冻结、`high` 级失败。

**修复建议**：
- `_FREEZE_END` 分支：若拿不到 end，用 `st.freeze_dur`。
- `close_open()`：未闭合冻结优先用 `st.freeze_dur` 算 `end = st.open_freeze + st.freeze_dur`，无 duration 才用 `duration_sec`。
- 注意 `freeze_dur` 是相对时长还是绝对秒：ffmpeg `freeze_duration` 输出的是持续秒数，需与 start 相加。

**验证**：给 `parse_line()` 喂只有 `freeze_start` + `freeze_duration` 两行的样本，断言得到 `(start, start+duration)` 而非 `(start, duration_sec)`。

---

## 5. `timeout` 状态不可达（P1）

**现象**：`FileResult.status` 允许 `"timeout"`，SPEC §1A / §5.1 第 8 步也要求「阶段超时 → `status="timeout"` → 结论 `unfinished`」，但 `pipeline.py` 中 `status` 只被赋为 `done`/`cancelled`/`failed`。

**根因**：`run_decode_pass()` 超时返回 `completed=False, exit_code=-9`，`sample_frames()` 超时返回部分样本——两者都没有向管线传递「这是超时而非取消/崩溃」的信号；管线只在 `pipeline.py:106` 追加一条 `decode_aborted`(medium) issue。

**影响**：超时的文件最终 `status="done"` + medium issue → `conclusion="warned"`（退出码 1），而不是 `unfinished`。SPEC 定义的 `unfinished` 在真实运行中几乎不可达（除 cancelled 外），统计口径失真。

**修复建议**：
- 给 `DecodeEvents` 增加 `timed_out: bool`；`run_decode_pass()` 在 `killed` 且非 cancel 时置 True。
- `sample_frames()` 同样需要返回该信号（可返回一个结果对象或增加 out 参数）。
- `analyze_file()` 在阶段后检查该信号，设 `status="timeout"`；注意 SPEC 的状态优先级 `cancelled > timeout > failed > done`。
- 结论规则 `conclusion` 已正确处理 `status != "done"` → `unfinished`，无需改动。

**验证**：用极小 `timeout_sec` 跑一个大文件，断言 `status=="timeout"` 且 `conclusion=="unfinished"`。

---

## 6. D05 除零崩溃（P1，已实测）

**现象**：`freeze.py:19` 的 `if d / dur >= 0.9` 在 `dur=0` 时抛 `ZeroDivisionError`。

**根因**：`FileContext.duration` 在容器无时长且包探测失败时返回 `0.0`；D06/D10 都有 `dur <= 0` 守卫，唯独 `check_d05()` 只判了 `decode is None`。实测：`ctx.duration=0` + 一个 freeze 事件直接崩。

**影响**：异常被 `pipeline.py:100-104` 的兜底吞成一条 `internal_error`(low)，导致整个 D05 检测静默失效，用户只看到模糊的「检测项内部错误」。`check_d09()` 同样使用 `dur` 但只做乘法比较，无除零风险。

**修复建议**：`check_d05()` 开头加 `if decode is None or dur <= 0: return []`；或在比例计算处写成 `if dur > 0 and d / dur >= 0.9`（保留区间本身仍按绝对秒判 high/medium）。

**验证**：新增 `test_detectors.py` 用例：`FileContext(duration=0)` + freeze 事件 → 不抛异常。

---

## 7. 运行日志系统完全未实现（P1）

**现象**：源码中无任何 `logging`/`getLogger`/`sys.excepthook`/Qt 消息处理器，`logs/` 为空目录。

**根因**：SPEC §1.2 有完整规定，实现阶段遗漏。

**影响**：`docs/DESIGN.md` §0 的「已知缺口」未列此项，属于规格与实现脱节。对排障工具而言，阶段失败原因、ffmpeg stderr 尾部、异常堆栈都没有落盘，用户报错后无法追溯。

**修复建议**（按 SPEC §1.2 逐条落地）：
- `ROOT/logs/videoqc.log`，`RotatingFileHandler`，2MB × 3，级别 INFO；`--headless` 同时输出到 stderr。
- 必记：启动自检结果（ffmpeg 路径/版本、config 告警条数）、每文件阶段失败原因与 subprocess 退出码、ffmpeg stderr 尾 50 行（仅当前文件失败时）、异常堆栈。
- 崩溃兜底：`sys.excepthook` + Qt `qInstallMessageHandler` 统一写日志，弹窗提示「已记录到 logs/videoqc.log」，保持已有结果可导出，不得静默退出。

**验证**：制造一次解码失败，断言日志文件含 stderr 尾与退出码；触发一次未捕获异常，断言有堆栈记录且 GUI 有提示。

---

## 8. `decode_errors` 缺中文标签（P2）

**现象**：`corruption.py:17` 发出的 kind 是 `decode_errors`，而 `report.py:25` 的 `KIND_LABELS` 只定义了 `corrupt_packet: "坏包"`。

**影响**：实测 Markdown 报告的概览「问题」列、详情表「类型」列均显示英文 `decode_errors`；界面 `issue_panel.py:84` 复用了 `label()`，同样受影响。

**修复建议**：在 `KIND_LABELS` 中把 `corrupt_packet` 改为 `decode_errors: "坏包/解码错误"`（`corrupt_packet` 若无任何产生方则删除，避免误导）。

**验证**：`assert label("decode_errors") != "decode_errors"`。

---

## 9. SPEC 对 `--jobs` 自相矛盾（P2）

**现象**：`docs/SPEC.md:323` 写「命令行/GUI 均不覆盖配置（`--config` 只换文件路径）」；`:715` 写「`--jobs` 只覆盖本次 headless 执行，不写回配置文件」。

**影响**：`cli.py:85-91` 实际按后者实现（覆盖但只限本次），代码行为正确，是文档表述冲突，易误导后续维护者。

**修复建议**：把 §3 那句改为「GUI 不覆盖配置；headless 的 `--jobs`/`--timeout` 可覆盖本次执行，见 §6.3」。

**验证**：文档校对，无代码改动。

---

## 建议执行顺序

1. **第一批（检测口径与正确性，均能用最小夹具回归）**：#1 抽帧错位 → #2 D12 标志位 → #3 D13 类型 → #4 freeze 时长 fallback。#1 会直接改变 D10/D11 的结论（已实测 cast.mp4 从 high 被压成 medium），应最先处理。
2. **第二批（状态与健壮性）**：#5 timeout 状态 → #6 除零守卫。
3. **第三批（配套）**：#8 标签 → #9 文档 → #7 日志系统（工作量最大，可单独排期）。

每修一项都应同步补一条回归用例——这 9 项的共同特征就是现有 83 项测试没覆盖到判定口径与边界输入。特别是 #1，现有测试用的是纯色/静态素材，恰好掩盖了时间戳错位（颜色不随时间变，看不出错位），必须补一个**画面随时间变化 + GOP > 采样间隔**的夹具。

## 尚未纳入本次范围（设计取舍，非缺陷）

- 逐帧步进为关键帧级（误差 0–2s，`DESIGN.md` §0 已定不修）。
- VFR 素材 D03 跳变未经肉眼核实，可能是误报（可用 `pts_gap_factor` 调）。
- 无 CI；`ui/`、`app/controller.py`、`app/cli.py` 无类型检查（PySide6 stub 噪音大，已记录）。
- 分析结果不缓存（按约束设计）。
