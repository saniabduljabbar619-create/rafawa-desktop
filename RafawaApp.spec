# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Rafawa Desktop.   Build:  python -m PyInstaller RafawaApp.spec --clean
# (or run build.ps1, which also compiles the installer)
import os

block_cipher = None
ROOT = os.path.abspath(SPECPATH)

# Alembic needs migrations/ + alembic.ini at runtime (schema check on start-up),
# and the shop's logo/icon is used by the installer and window.
datas = [
    (os.path.join(ROOT, "alembic.ini"), "."),
    (os.path.join(ROOT, "migrations"), "migrations"),
    (os.path.join(ROOT, "A-rafawa.ico"), "."),
]

# Database settings travel inside the exe: the real .env (git-ignored, kept on the build PC) is bundled as ".env",
# which the app finds next to / inside the exe. Falls back to .env.template, then to the placeholder .env.example.
env_src = next((os.path.join(ROOT, n) for n in (".env", ".env.template", ".env.example")
                if os.path.exists(os.path.join(ROOT, n))), None)
if env_src:
    datas.append((env_src, "."))
    if os.path.basename(env_src) != ".env":          # PyInstaller keeps the source name; the app wants ".env"
        import shutil
        _tmp = os.path.join(ROOT, "build", "_env_bundle")
        os.makedirs(_tmp, exist_ok=True)
        shutil.copyfile(env_src, os.path.join(_tmp, ".env"))
        datas[-1] = (os.path.join(_tmp, ".env"), ".")
    print("RafawaApp.spec: bundling " + os.path.basename(env_src) + " as .env")
ca = os.path.join(ROOT, "ca.pem")
if os.path.exists(ca):
    datas.append((ca, "."))

hiddenimports = [
    "pymysql",
    "sqlalchemy.dialects.mysql.pymysql",
    "win32print",
    "openpyxl",
    "alembic.runtime.migration",
]

a = Analysis(
    [os.path.join(ROOT, "apps", "product_management_app", "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "ruff"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="RafawaApp",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(ROOT, "A-rafawa.ico"),
)
