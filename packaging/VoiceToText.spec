# PyInstaller folder distribution: no console and no multi-GB extraction at launch.
from pathlib import Path
import importlib.util
from PyInstaller.utils.hooks import collect_all, collect_data_files

root = Path(SPECPATH).parent
datas = [(str(root / 'voice_to_text' / 'assets'), 'voice_to_text/assets')]
binaries = []
hiddenimports = []
for package in ('faster_whisper', 'pyaudiowpatch', 'opencc'):
    data, libs, imports = collect_all(package)
    datas += data
    binaries += libs
    hiddenimports += imports
for package in ('cublas', 'cudnn', 'cuda_nvrtc', 'cuda_runtime'):
    spec = importlib.util.find_spec('nvidia.' + package)
    if spec and spec.submodule_search_locations:
        for location in spec.submodule_search_locations:
            binaries += [(str(file), f'nvidia/{package}/bin') for file in (Path(location) / 'bin').glob('*.dll')]

a = Analysis([str(root / 'app.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=['yt_dlp', 'PySide6', 'PyQt5', 'tkinter'],
             noarchive=False, optimize=0)
# Qt's unversioned ICU imports use Windows 10/11's system ICU. Conda's ICU 73
# has the same filename but versioned exports and must not shadow the OS DLL.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() != 'icuuc.dll']
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='VoiceToText',
          icon=str(root / 'build' / 'windows-app.ico'), console=False,
          debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='VoiceToText')
