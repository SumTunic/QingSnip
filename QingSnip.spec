# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
from comtypes.client import GetModule
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
# Bundle Windows UIA interface wrappers even in a fresh build environment.
GetModule(str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'UIAutomationCore.dll'))
hiddenimports += collect_submodules('comtypes', filter=lambda name: not name.startswith('comtypes.test'))
tmp_ret = collect_all('uiautomation')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['comtypes.test'],
    noarchive=False,
    optimize=0,
)
# Qt 6.12 uses the unversioned ICU API shipped with Windows. Developer
# PATHs can contain an incompatible same-named DLL (e.g. Poppler/Conda).
# Let Windows resolve its system ICU rather than bundling that foreign DLL.
a.binaries = [entry for entry in a.binaries
              if not (Path(entry[0]).name.lower() == 'icuuc.dll'
                      or Path(entry[0]).name.lower().startswith('icudt'))]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='QingSnip',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/qingsnip.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='QingSnip',
)
