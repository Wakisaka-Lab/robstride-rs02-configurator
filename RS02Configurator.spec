# Build on Windows x64 using Python 3.11: pyinstaller --clean RS02Configurator.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
usb_data, usb_binaries, usb_hidden = collect_all('libusb_package')
a = Analysis(
    [str(root / 'desktop_main.py')],
    pathex=[str(root / 'src'), str(root / 'vendor')],
    binaries=usb_binaries,
    datas=usb_data + [(str(root / 'vendor' / 'LICENSE.robstride'), 'licenses'),
                      (str(root / 'vendor' / 'PROVENANCE.md'), 'licenses')],
    hiddenimports=usb_hidden + ['can.interfaces.gs_usb', 'usb.backend.libusb1',
                               'gs_usb.gs_usb', 'gs_usb.gs_usb_frame'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='RS02Configurator',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='RS02Configurator')
