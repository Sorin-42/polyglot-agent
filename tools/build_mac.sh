#!/bin/sh
# Builds "dist/Polyglot Agent.app" and dist/PolyglotAgent-<version>-mac-<apple-silicon|intel>.dmg.
# Run on a Mac from the project folder:  sh tools/build_mac.sh   (needs Python 3.10+)
# The app is built for the Mac it is built on; GitHub Actions builds both kinds.
set -eu
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}

"$PY" -m venv .build-venv-mac
. .build-venv-mac/bin/activate
python -m pip install --disable-pip-version-check --upgrade pip
python -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean PolyglotAgent-mac.spec

VERSION=$(python -c "import app; print(app.__version__)")
case "$(uname -m)" in
    arm64) KIND=apple-silicon ;;
    *) KIND=intel ;;
esac

# Disk image: the app, a shortcut to Applications to drag it onto, and the first-open guide.
STAGE=build/dmg
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "dist/Polyglot Agent.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "tools/mac/Open me first.txt" "$STAGE/"
DMG="dist/PolyglotAgent-$VERSION-mac-$KIND.dmg"
rm -f "$DMG"
for attempt in 1 2 3; do          # hdiutil now and then fails with "Resource busy"
    hdiutil create -volname "Polyglot Agent" -srcfolder "$STAGE" -ov -format UDZO "$DMG" && break
    [ "$attempt" = 3 ] && exit 1
    sleep 5
done
echo "Built $DMG"
