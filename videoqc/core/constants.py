"""内部判定常量（不开放给用户，报告附录逐条列出）。"""

PROBE_TIMEOUT_SEC = 30  # 单次 ffprobe 超时
FREEZE_HIGH_SEC = 10.0  # 冻结 ≥10s 升 high
FREEZE_BLACK_OVERLAP = 0.8  # 冻结区间被黑场包含此比例 → 降为 info
BLACK_COVERAGE_RATIO = 0.3  # 黑场覆盖时长占比阈值
MISSING_PTS_RATIO = 0.01  # 缺 PTS 包占比 >1% → D04 info
SAMPLE_COVERAGE_MIN = 0.8  # 偏色采样覆盖率下限
CORRUPT_RATIO_HIGH = 0.001  # 坏包占比 >0.1% 判 high（单个 C 位在 mpegts 里很常见）
PACKETS_TIMEOUT_MAX = 600.0  # 包级扫描超时上限（随片长缩放到此为止）

# 进报告「生效阈值」附录
REPORT_CONSTANTS = {
    "probe_timeout_sec": PROBE_TIMEOUT_SEC,
    "freeze_high_sec": FREEZE_HIGH_SEC,
    "freeze_black_overlap": FREEZE_BLACK_OVERLAP,
    "black_coverage_ratio": BLACK_COVERAGE_RATIO,
    "missing_pts_ratio": MISSING_PTS_RATIO,
    "sample_coverage_min": SAMPLE_COVERAGE_MIN,
    "corrupt_ratio_high": CORRUPT_RATIO_HIGH,
    "packets_timeout_max": PACKETS_TIMEOUT_MAX,
}
