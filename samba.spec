# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Samba — Apple Silicon only

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# Collect all files (datas, binaries, hidden imports) for the MLX ecosystem
mlx_d,     mlx_b,     mlx_h     = collect_all('mlx')
whisper_d, whisper_b, whisper_h = collect_all('mlx_whisper')
lm_d,      lm_b,      lm_h      = collect_all('mlx_lm')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=mlx_b + whisper_b + lm_b,
    datas=[
        ('templates', 'templates'),
        ('static',    'static'),
    ] + mlx_d + whisper_d + lm_d,
    hiddenimports=[
        # webview
        'webview',
        'webview.platforms.cocoa',
        # flask stack
        'flask', 'flask_cors',
        'jinja2', 'jinja2.ext',
        'werkzeug', 'werkzeug.routing', 'werkzeug.serving',
        # audio / ML
        'sounddevice',
        'numpy',
        # utils
        'psutil', 'requests', 're', 'json',
        'threading', 'queue', 'logging',
    ] + mlx_h + whisper_h + lm_h,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'wx'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Samba',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX can break native Metal/MLX binaries — leave off
    console=False,      # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file='entitlements.plist',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Samba',
)

app = BUNDLE(
    coll,
    name='Samba.app',
    icon=None,          # Replace with 'Samba.icns' if you create one
    bundle_identifier='com.samba.meetingassistant',
    info_plist={
        'NSMicrophoneUsageDescription':
            'Samba needs microphone access to capture and transcribe your voice during meetings.',
        'CFBundleName': 'Samba',
        'CFBundleDisplayName': 'Samba',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1',
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,  # supports dark mode
    },
)
