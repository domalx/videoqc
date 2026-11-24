"""生成 tests/data 下全部合成夹具（SPEC §10.1）。用法：python tests/make_fixtures.py"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "bin" / "ffmpeg.exe"
OUT = Path(__file__).resolve().parent / "data"


def ff(*args: str) -> None:
    subprocess.run([str(FFMPEG), "-y", "-v", "error", *args], check=True, cwd=OUT)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30", "-f", "lavfi", "-i",
       "sine=f=440:sample_rate=44100", "-t", "20", "-pix_fmt", "yuv420p",
       "-c:v", "libx264", "-c:a", "aac", "-shortest", "base.mp4")

    # 跳秒：保留原 PTS 删掉 5–6s 的包
    ff("-i", "base.mp4", "-vf", "select='not(between(t,5,6))'", "-fps_mode", "passthrough",
       "-an", "-c:v", "libx264", "gap.mp4")

    ff("-f", "lavfi", "-i", "color=c=black:s=320x180:r=30", "-t", "4", "-pix_fmt", "yuv420p",
       "-c:v", "libx264", "black4.mp4")

    # 冻结：10s 片子中间 4–7s 冻住（freezeframes 需要双输入，用 trim+loop+concat 更直接）
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=30", "-t", "10", "-filter_complex",
       "[0:v]trim=0:4,setpts=PTS-STARTPTS[a];"
       "[0:v]trim=4:4.1,setpts=PTS-STARTPTS,loop=loop=89:size=1,setpts=N/30/TB[b];"
       "[0:v]trim=7:10,setpts=PTS-STARTPTS[c];"
       "[a][b][c]concat=n=3:v=1,format=yuv420p[o]",
       "-map", "[o]", "-c:v", "libx264", "freeze3.mp4")

    # 偏红：lutrgb 直接把 r 抬到 1.35、g/b 压到 0.6 左右（cast ≈ 35/255）
    ff("-i", "base.mp4", "-vf", "lutrgb=r=val*1.35:g=val*0.6:b=val*0.55",
       "-c:v", "libx264", "-c:a", "copy", "cast.mp4")
    ff("-i", "base.mp4", "-an", "noaudio.mp4")
    ff("-i", "base.mp4", "-af", "volume=0", "muted.mp4")
    ff("-i", "base.mp4", "-af", "volume=20dB", "hot.mp4")

    # 截断：faststart 把 moov 提前，再截掉尾部 1/3
    ff("-i", "base.mp4", "-c", "copy", "-movflags", "+faststart", "fs.mp4")
    data = (OUT / "fs.mp4").read_bytes()
    (OUT / "trunc.mp4").write_bytes(data[: len(data) * 2 // 3])

    # 损坏：base 的 mdat 中部抹零 4KB（不动 moov，否则变成 probe 失败）
    buf = bytearray((OUT / "base.mp4").read_bytes())
    mdat = buf.find(b"mdat")
    off = mdat + len(buf[mdat:]) // 2
    buf[off:off + 4096] = b"\0" * 4096
    (OUT / "corrupt.mp4").write_bytes(buf)

    capture_logs()
    print("夹具已写入", OUT)
    return 0


def make_bulk(n: int = 24) -> None:
    """批量小样片（3s 一条）：压文件列表的滚动/上色，也方便人工点着看。

    每 6 条一轮：正常 / 纯黑 / 冻结 / 静音 / 偏红 / 无音轨，颜色分布能看出行底色。
    """
    OUT.mkdir(exist_ok=True)
    for i in range(n):
        name = f"bulk_{i + 1:02d}.mp4"
        v = "testsrc2=s=160x90:r=15"
        match i % 6:
            case 1:
                v = "color=c=black:s=160x90:r=15"
            case 2:
                v = "testsrc2=s=160x90:r=15"          # 冻结靠下面的 -vf
            case _:
                pass
        args = ["-f", "lavfi", "-i", v]
        if i % 6 != 5:
            args += ["-f", "lavfi", "-i", f"sine=f={220 + 40 * (i % 6)}:sample_rate=44100"]
        args += ["-t", "3", "-pix_fmt", "yuv420p", "-c:v", "libx264"]
        if i % 6 == 2:
            args += ["-vf", "fps=1/3"]                # 1fps：整段几乎静止，触发冻结
        if i % 6 == 3:
            args += ["-af", "volume=0"]
        if i % 6 == 4:
            args += ["-vf", "lutrgb=r=val*1.4:g=val*0.6:b=val*0.6"]
        if i % 6 != 5:
            args += ["-c:a", "aac", "-shortest"]
        else:
            args += ["-an"]
        args.append(name)
        ff(*args)


def _has_audio(path: Path) -> bool:
    r = subprocess.run([str(ROOT / "bin" / "ffprobe.exe"), "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return bool(r.stdout.strip())


def capture_logs() -> None:
    """把真实的 ffmpeg stderr 存成夹具：`test_decode.py` 的解析器要拿真样本说话。"""
    vf = "freezedetect=n=0.003:d=2.0,blackdetect=d=0.5:pic_th=0.98:pix_th=0.10"
    af = "silencedetect=n=-50dB:d=3.0,volumedetect"
    for log, src in (("freeze.log", "freeze3.mp4"), ("black.log", "black4.mp4"),
                     ("silence.log", "muted.mp4"), ("volume.log", "base.mp4"),
                     ("errors.log", "corrupt.mp4")):
        audio = _has_audio(OUT / src)
        cmd = [str(FFMPEG), "-nostdin", "-hide_banner", "-nostats", "-v", "info", "-i", src]
        if audio:
            cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, "-af", af]
        else:
            cmd += ["-an", "-vf", vf]
        cmd += ["-f", "null", "-"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=OUT)
        (OUT / log).write_text(r.stderr, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--bulk":
        make_bulk(int(sys.argv[2]))
        print(f"已生成 {sys.argv[2]} 条 bulk_*.mp4")
        sys.exit(0)
    sys.exit(main())
