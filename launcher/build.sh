#!/bin/sh
# Cross-compile PolyglotAgent.exe on Linux (needs gcc-mingw-w64-x86-64).
set -e
cd "$(dirname "$0")"
x86_64-w64-mingw32-windres launcher.rc -O coff -o launcher.res
x86_64-w64-mingw32-gcc -O2 -s -Wall -municode -mwindows -o ../PolyglotAgent.exe launcher.c launcher.res -lshlwapi
rm -f launcher.res
echo "built ../PolyglotAgent.exe"
