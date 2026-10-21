#!/bin/sh
# Start Polyglot Agent on Linux or macOS:  ./start.sh   (or: sh start.sh)
# Needs Python 3.10 or newer. The first run installs the app's components into .venv.
cd "$(dirname "$0")" || exit 1
for py in python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$py" >/dev/null 2>&1 &&
       "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        exec "$py" start.py "$@"
    fi
done
echo "Polyglot Agent needs Python 3.10 or newer: https://www.python.org/downloads/"
echo "(Ubuntu/Debian: sudo apt install python3 python3-venv)"
exit 1
