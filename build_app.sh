#!/bin/bash
# build_app.sh — Build Samba.app for macOS (Apple Silicon)
set -e

echo "╔══════════════════════════════════════╗"
echo "║        Building Samba.app            ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. Install PyInstaller ──────────────────────────────────────────────────
echo "→ Installing PyInstaller..."
pip install pyinstaller --quiet

# ── 2. Clean previous builds ────────────────────────────────────────────────
echo "→ Cleaning previous builds..."
rm -rf build dist

# ── 3. Build the .app bundle ────────────────────────────────────────────────
echo "→ Running PyInstaller..."
pyinstaller samba.spec --noconfirm

# ── 4. Ad-hoc code sign ─────────────────────────────────────────────────────
# Uses '-' (ad-hoc identity) — works on your own Mac without an Apple Developer
# account. Users on other Macs will need to right-click → Open to bypass Gatekeeper.
echo "→ Ad-hoc code signing..."
codesign --deep --force --sign - \
    --entitlements entitlements.plist \
    dist/Samba.app

echo ""
echo "✓ Build complete: dist/Samba.app"
echo ""

# ── 5. Optional: create a distributable .dmg ────────────────────────────────
read -p "Create a .dmg for distribution? [y/N] " make_dmg
if [[ "$make_dmg" =~ ^[Yy]$ ]]; then
    echo "→ Creating Samba.dmg..."
    # Create a temporary directory with the app and an Applications symlink
    mkdir -p dist/dmg_staging
    cp -R dist/Samba.app dist/dmg_staging/
    ln -sf /Applications dist/dmg_staging/Applications

    hdiutil create \
        -volname "Samba" \
        -srcfolder dist/dmg_staging \
        -ov -format UDZO \
        dist/Samba.dmg

    rm -rf dist/dmg_staging
    echo "✓ DMG ready:  dist/Samba.dmg"
fi

echo ""
echo "To install: drag dist/Samba.app into /Applications"
echo "First launch: right-click → Open (bypasses Gatekeeper for unsigned apps)"
