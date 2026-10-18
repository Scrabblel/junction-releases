# Builds Junction.exe (a folder: dist/Junction/Junction.exe plus what it needs). From this folder:
#
#     python -m PyInstaller Junction.spec --noconfirm
#
# Only the app itself goes in: the page and its icon, never local_settings.json or the snapshot data in web/.
import re

from PyInstaller.utils.hooks import copy_metadata
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

VERSION = re.search(r'^VERSION = "([^"]+)"', open("junction/config.py", encoding="utf-8").read(), re.M).group(1)
nums = tuple(int(n) for n in VERSION.split(".")) + (0,) * (4 - len(VERSION.split(".")))

# What Windows shows under the exe's Properties > Details.
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=nums, prodvers=nums),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("ProductName", "Junction"),
            StringStruct("FileDescription", "Junction"),
            StringStruct("FileVersion", VERSION),
            StringStruct("ProductVersion", VERSION),
            StringStruct("InternalName", "Junction"),
            StringStruct("OriginalFilename", "Junction.exe"),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(
    ["Junction.pyw"],
    pathex=["."],
    datas=[("web/index.html", "web"), ("web/icon.ico", "web")] + copy_metadata("pywebview"),
    excludes=["tkinter", "PIL", "numpy", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "cefpython3"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Junction",
    icon="web/icon.ico",
    version=version_info,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Junction", upx=False)
