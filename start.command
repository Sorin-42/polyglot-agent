#!/bin/sh
# Double-click to start Polyglot Agent on macOS. A Terminal window shows the progress;
# closing it quits the app. Needs Python 3.10 or newer (python.org or Homebrew).
cd "$(dirname "$0")" || exit 1
for py in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1 &&
       "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        exec "$py" start.py "$@"
    fi
done
echo "Polyglot Agent needs Python 3.10 or newer. Get it from https://www.python.org/downloads/"
printf "Press Return to close."
read -r _
exit 1
