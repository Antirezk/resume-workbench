# PyInstaller spec. Bundle resources and local OCR models; exclude user data and keys.
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / 'packaging'))
from collect_licenses import license_data
datas = [(str(root / 'templates'), 'templates'), (str(root / 'static'), 'static'),
         (str(root / 'fonts' / 'NotoSansSC-Regular.ttf'), 'fonts'),
         (str(root / 'fonts' / 'OFL.txt'), 'fonts'), (str(root / 'LICENSE'), '.')]
datas += license_data(root)
datas.append((str(root / 'packaging' / '使用说明.txt'), '.'))
binaries, hiddenimports = [], ['pystray._win32', 'rapidocr.inference_engine.onnxruntime', 'onnxruntime']
for package in ['rapidocr', 'onnxruntime', 'pypdfium2', 'pypdfium2_raw', 'pyclipper']:
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
for package in ['rapidocr', 'onnxruntime', 'pypdfium2', 'pystray', 'waitress']:
    datas += copy_metadata(package)

a = Analysis([str(root / 'desktop.py')], pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['tkinter', 'pytest', 'torch', 'paddle', 'openvino', 'matplotlib', 'IPython'],
             noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='简历工作台', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False, version=str(root / 'packaging' / 'version.txt'))
