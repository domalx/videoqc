# -*- mode: python -*-
# PyInstaller 构建描述（onedir）。由 packaging/build_portable.py 调用：
#   .venv\Scripts\python -m PyInstaller packaging/videoqc.spec \
#       --workpath dist/_work --distpath dist --clean --noconfirm
#
# 关键点：spec 里的相对路径**不**按 spec 文件所在目录解析（Analysis 用 CWD），
# 而 EXE(icon=/version=) 与 datas 又按 specpath 解析。为消除歧义，一律用
# SPECPATH 推出仓库根 ROOT 再拼绝对路径。
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

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
          console=False,
          icon=os.path.join(ROOT, 'assets', 'app.ico'),
          version=os.path.join(SPECPATH, 'version_info.txt'))
coll = COLLECT(exe, a.binaries, a.datas, name='videoqc')
