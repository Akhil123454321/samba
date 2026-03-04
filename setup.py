from setuptools import setup

APP = ["app.py"]

DATA_FILES = [
    ("templates", ["templates/index.html"]),
    ("static/css", ["static/css/style.css"]),
    ("static/js", ["static/js/app.js"]),
    ("static", ["static/logo.svg", "static/logo.png"]),
]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "samba.icns",
    "packages": [
        "flask",
        "flask_cors",
        "mlx_whisper",
        "mlx",
        "sounddevice",
        "numpy",
        "psutil",
        "requests",
        "webview",
        "jinja2",
        "werkzeug",
        "click",
        "ctranslate2",
    ],
    "includes": [
        "mlx_whisper",
        "faster_whisper",
    ],
    "excludes": ["tkinter", "PyQt5", "wx"],
    "plist": {
        "CFBundleName": "Samba",
        "CFBundleDisplayName": "Samba",
        "CFBundleIdentifier": "com.samba.meetingassistant",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSMicrophoneUsageDescription": "Samba needs microphone access to transcribe your voice during meetings.",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
    },
}

setup(
    app=APP,
    name="Samba",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
