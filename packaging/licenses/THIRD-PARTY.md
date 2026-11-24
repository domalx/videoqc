# 第三方组件清单（随包分发的 licenses/ 目录内容来源）

构建时更新本文件；报告附录的「ffmpeg 版本」取自 `bin/ffmpeg.exe -version` 首行，必须与本文件一致。

## ffmpeg / ffprobe

| 项 | 值 |
|---|---|
| 版本 | `ffmpeg version 9.1-essentials_build-www.gyan.dev`（2026-11-20 构建，gcc 16.2.0） |
| 来源 | https://www.gyan.dev/ffmpeg/builds/ （`ffmpeg-9.1-essentials_build.7z`） |
| 配置 | `--enable-gpl --enable-version3 --enable-static`（静态链接，含 libx264/x265 等） |
| 许可证 | **GPL v3**（因 `--enable-gpl --enable-version3`） |
| 义务 | 随包分发 GPLv3 全文 + 提供源码获取方式（对应 ffmpeg 9.1 源码 tarball 或 https://ffmpeg.org/download.html 的 9.1 链接）；不得声称本项目自有 |
| 文件 | `bin/ffmpeg.exe` sha256 `ed8f66cb877087a96f5bbb40a2944f0e764f2ad8870d5cb647360d912e62bc07`（105.5 MB）<br>`bin/ffprobe.exe` sha256 `df0199f606faa88e9ad192732a7c3987acfb647663534c297772e17730611a08`（105.3 MB）<br>源包 `ffmpeg-9.1-essentials_build.7z` sha256 `835b9a733445ba7a56a37a26cf7bde84e2f3ce7da2a8ec984276bb2abb1b922c`（35.0 MB） |

注：静态构建让 ffmpeg.exe 与 ffprobe.exe 各 ~105 MB（两者代码重复，共 ~210 MB 未压缩）。压缩后仅 ~35 MB，符合包体积预算。

## PySide6

| 项 | 值 |
|---|---|
| 版本 | PySide6-Essentials 6.9.3 + PySide6-Addons 6.9.3 + shiboken6 6.9.3（Qt 6.9.3） |
| 来源 | PyPI |
| 许可证 | **LGPL v3**（Qt/PySide6 均为 LGPLv3；shiboken6 为 LGPLv3） |
| 义务 | 动态链接即可满足（onedir 打包，DLL 可被替换）；随包附 LGPLv3 全文 + Qt/PySide6 源码获取方式；不修改 Qt DLL |
| 播放后端 | Addons 自带 `plugins/multimedia/ffmpegmediaplugin.dll`（Qt 内部 FFmpeg，独立于 `bin/ffmpeg.exe`）+ `windowsmediaplugin.dll` |

## Python 运行时

| 项 | 值 |
|---|---|
| 版本 | Python 3.13.15（PyInstaller 打包 `python313.dll`） |
| 许可证 | PSF License 2.0（随包附全文） |
| 其它 | PyInstaller 6.22.3（GPL v2 + bootloader 例外）、MSVC 运行库 `vcruntime140.dll`/`msvcp140.dll`（Microsoft 可再分发条款） |

## 待办（打包发布前）

- [x] 拷贝 GPLv3、LGPLv3、PSF 全文到 `packaging/licenses/`
- [ ] 填写本文件后，`packaging/build_portable.py` 校验 `bin/ffmpeg.exe -version` 首行与本文件一致
- [ ] 确认 `bin/` 用到的 ffmpeg 构建是 essentials（GPL）还是 lgpl-shared；换成 LGPL 构建则本节许可证改写

## 许可证全文

| 文件 | 覆盖组件 | 来源 |
|------|----------|------|
| `gpl-3.0.txt` | FFmpeg 9.1 essentials（静态链接，GPLv3） | https://www.gnu.org/licenses/gpl-3.0.txt |
| `PYTHON-3.13.txt` | Python 3.13.15 | CPython 安装目录 LICENSE.txt |
| `lgpl-3.0.txt` | Qt 6.9.3 / PySide6 6.9.3 / shiboken6（LGPLv3，动态链接） | 取自 setuptools 内置 `autocommand-2.2.2.dist-info/LICENSE`（逐字 LGPLv3 正文，sha256 `ade78d04…0df2d`）；上游 https://www.gnu.org/licenses/lgpl-3.0.txt |

> Qt 为动态链接且以未修改的 DLL 形式随包分发，符合 LGPLv3 §4(d) 的 relink 要求；
> 用户可自行替换 `_internal/PySide6/*.dll`。
