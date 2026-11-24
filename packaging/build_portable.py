"""一键生成可分发目录 + zip（SPEC §9.2）。用法：python packaging/build_portable.py"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
NAME = "videoqc"

REQUIRED = [
    "videoqc.exe",
    "config.json",
    "README.md",
    "LICENSE",
    "bin/ffmpeg.exe",
    "bin/ffprobe.exe",
    "_internal/PySide6/plugins/multimedia/ffmpegmediaplugin.dll",
    "_internal/PySide6/Qt6Core.dll",
    "_internal/vcruntime140.dll",
    "_internal/python313.dll",
    "licenses/THIRD-PARTY.md",
]


def main() -> int:
    os.chdir(ROOT)
    version = _version()
    out = DIST / NAME

    if not (ROOT / "bin" / "ffmpeg.exe").is_file():
        print("缺少 bin/ffmpeg.exe：先按 README 放入 ffmpeg/ffprobe", file=sys.stderr)
        return 1
    _write_version_info(version)

    print("== PyInstaller ==", flush=True)
    rc = subprocess.call(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--workpath",
            str(DIST / "_work"),
            "--distpath",
            str(DIST),
            str(ROOT / "packaging" / "videoqc.spec"),
        ]
    )
    if rc != 0:
        return rc

    print("== 复制运行期文件 ==", flush=True)
    shutil.copy2(ROOT / "config.json", out / "config.json")
    shutil.copy2(ROOT / "README.md", out / "README.md")
    shutil.copy2(ROOT / "LICENSE", out / "LICENSE")
    shutil.copytree(ROOT / "packaging" / "licenses", out / "licenses", dirs_exist_ok=True)
    (out / "bin").mkdir(exist_ok=True)
    for exe in ("ffmpeg.exe", "ffprobe.exe"):
        shutil.copy2(ROOT / "bin" / exe, out / "bin" / exe)

    missing = [p for p in REQUIRED if not (out / p).is_file()]
    if missing:
        print("打包校验失败，缺少：\n  " + "\n  ".join(missing), file=sys.stderr)
        return 1

    print("== 冒烟 ==", flush=True)
    if not _smoke(out / "videoqc.exe"):
        return 1

    zip_path = DIST / f"{NAME}-{version}-win64.zip"
    print(f"== 压缩 {zip_path.name} ==", flush=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                z.write(f, Path(NAME) / f.relative_to(out))

    try:  # 流式算摘要：132 MB 的 zip 没必要读进内存；磁盘满/被占用时明确失败而不是抛栈
        with zip_path.open("rb") as fh:
            digest = hashlib.file_digest(fh, "sha256").hexdigest()
        (zip_path.with_suffix(".zip.sha256")).write_text(
            f"{digest}  {zip_path.name}\n", encoding="ascii"
        )
        size_mb = zip_path.stat().st_size / 1024 / 1024
        shutil.rmtree(DIST / "_work", ignore_errors=True)  # 中间产物没用了，别留在 dist 里
    except OSError as e:
        print(f"写 sha256 失败：{e}", file=sys.stderr)
        return 1
    print(f"完成：{zip_path}  {size_mb:.1f} MB\nsha256 {digest}")
    return 0


def _version() -> str:
    sys.path.insert(0, str(ROOT))
    from videoqc.version import __version__

    return __version__


def _write_version_info(version: str) -> None:
    nums = [*version.split("."), "0", "0", "0"][:4]
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({",".join(nums)}), prodvers=({",".join(nums)}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'domaCODE'),
      StringStruct('FileDescription', 'videoqc 视频质检工具'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('ProductName', 'videoqc'),
      StringStruct('ProductVersion', '{version}'),
      StringStruct('LegalCopyright', '含 FFmpeg (GPLv3) 与 Qt/PySide6 (LGPLv3)')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    (ROOT / "packaging" / "version_info.txt").write_text(text, encoding="utf-8")


def _smoke(exe: Path) -> bool:
    ver = subprocess.run([str(exe), "--headless", "--version"], capture_output=True, text=True)
    if ver.returncode != 0 or "videoqc" not in ver.stdout:
        print(
            f"冒烟失败 --version: rc={ver.returncode} out={ver.stdout!r} err={ver.stderr!r}",
            file=sys.stderr,
        )
        return False
    fixture = ROOT / "tests" / "data" / "base.mp4"
    if not fixture.is_file():
        print("跳过扫描冒烟：tests/data/base.mp4 不存在", file=sys.stderr)
        return True
    with tempfile.TemporaryDirectory(prefix="videoqc-smoke-") as tmpdir:  # 别在 dist/ 留垃圾
        tmp = Path(tmpdir) / "smoke.md"
        scan = subprocess.run(
            [str(exe), "--headless", str(fixture), "-o", str(tmp)], capture_output=True, text=True
        )
        ok = scan.returncode == 0 and tmp.is_file()
    if not ok:
        print(f"冒烟失败 扫描: rc={scan.returncode} err={scan.stderr[-500:]!r}", file=sys.stderr)
    return ok


if __name__ == "__main__":
    raise SystemExit(main())
