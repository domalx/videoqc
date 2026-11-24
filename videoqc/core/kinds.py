"""issue kind 的唯一真值源：检测器产出 → 报告中文标签。

新增 kind 时必须在这里登记，否则报告会渲染原始英文串（label() 会告警）。
"""

from __future__ import annotations

KIND_LABELS: dict[str, str] = {
    "probe_failed": "无法解析",
    "no_video_stream": "无视频流",
    "no_duration": "无时长",
    "container_mismatch": "容器不符",
    "probe_packets_failed": "时间戳读取失败",
    "internal_error": "内部错误",
    "decode_aborted": "解码中断",
    "pts_gap": "跳秒",
    "pts_missing": "时间戳缺失",
    "av_duration_mismatch": "音视频时长差",
    "av_start_offset": "音频起点偏移",
    "freeze": "卡顿",
    "black": "黑场",
    "silence": "静音",
    "silent_audio": "整轨静音",
    "low_volume": "音量偏低",
    "clipping": "削波",
    "color_cast": "偏色",
    "no_audio": "无音轨",
    "truncated": "截断",
    "corrupt_packet": "坏包",
    "decode_errors": "解码错误",
    "audio_decode_errors": "音频解码错误",
    "dts_non_monotonic": "DTS 非单调",
    "dup_pts": "重复时间戳",
    "audio_pts_anomaly": "音频时间戳异常",
    "fps_mismatch": "帧率不符",
    "vfr_suspected": "可变帧率",
    "underexposed": "画面过暗",
    "overexposed": "画面过曝",
    "flat_frame": "纯色画面",
    "solid_screen": "纯色屏",
    "empty_audio": "空音轨",
    "low_sample_rate": "采样率偏低",
    "channel_anomaly": "声道数异常",
    "low_sample_count": "样本不足",
    "no_samples": "未取到画面样本",
    "samples_incomplete": "画面采样中断",
    "hdr": "HDR 素材",
    "rotated": "旋转元数据",
    "non_square_pixels": "非方形像素",
}

KINDS = frozenset(KIND_LABELS)
