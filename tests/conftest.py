"""pytest 共享夹具：路径、临时配置、ffmpeg 可用性断言（见 SPEC §1.1 / §10.1）。"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FFMPEG = ROOT / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "bin" / "ffprobe.exe"


@pytest.fixture(scope="session", autouse=True)
def require_binaries():
    """开发机与交付包同构：不搜 PATH，bin/ 缺失直接失败。"""
    missing = [p.name for p in (FFMPEG, FFPROBE) if not p.exists()]
    if missing:
        pytest.fail(f"缺少 {ROOT/'bin'} 下的 {missing}（见 SPEC §1.1）")


@pytest.fixture
def data_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / "data"
