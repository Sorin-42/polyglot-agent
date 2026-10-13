/*
 * PolyglotAgent.exe - windowed launcher (no console).
 *
 *  - Normal start: runs  .venv\Scripts\pythonw.exe -m app.desktop  next to this exe.
 *  - First start (or after requirements.txt changed): finds an installed Python
 *    (py launcher, PATH, Anaconda/Miniconda, python.org per-user installs), runs
 *    tools\bootstrap.py in a console window so the setup progress is visible,
 *    then starts the app.
 *
 * Build (MinGW):  see launcher/build.sh
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <shellapi.h>
#include <shlwapi.h>

#define APP_TITLE L"Polyglot Agent"

static WCHAR g_dir[MAX_PATH];

static BOOL file_exists(const WCHAR *p) {
    DWORD a = GetFileAttributesW(p);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static BOOL newer_than(const WCHAR *a, const WCHAR *b) {
    WIN32_FILE_ATTRIBUTE_DATA fa, fb;
    if (!GetFileAttributesExW(a, GetFileExInfoStandard, &fa)) return FALSE;
    if (!GetFileAttributesExW(b, GetFileExInfoStandard, &fb)) return TRUE;
    return CompareFileTime(&fa.ftLastWriteTime, &fb.ftLastWriteTime) > 0;
}

static BOOL run(WCHAR *cmdline, DWORD flags, BOOL wait, DWORD *exit_code) {
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    if (!CreateProcessW(NULL, cmdline, NULL, NULL, FALSE, flags, NULL, g_dir, &si, &pi))
        return FALSE;
    if (wait) {
        WaitForSingleObject(pi.hProcess, INFINITE);
        if (exit_code) GetExitCodeProcess(pi.hProcess, exit_code);
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return TRUE;
}

/* Expand %VARS% in tmpl and check the file exists. */
static BOOL try_path(const WCHAR *tmpl, WCHAR *out) {
    WCHAR buf[MAX_PATH];
    DWORD n = ExpandEnvironmentStringsW(tmpl, buf, MAX_PATH);
    if (n == 0 || n > MAX_PATH || !file_exists(buf)) return FALSE;
    wsprintfW(out, L"\"%s\"", buf);
    return TRUE;
}

/* Newest %LOCALAPPDATA%\Programs\Python\Python3xx\python.exe */
static BOOL try_python_org(WCHAR *out) {
    WCHAR base[MAX_PATH], pattern[MAX_PATH], best[MAX_PATH] = L"";
    WIN32_FIND_DATAW fd;
    HANDLE h;
    if (!ExpandEnvironmentStringsW(L"%LOCALAPPDATA%\\Programs\\Python", base, MAX_PATH)) return FALSE;
    wsprintfW(pattern, L"%s\\Python3*", base);
    h = FindFirstFileW(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return FALSE;
    do {
        /* numeric-ish ordering: Python313 > Python312 > Python39 */
        int ln = lstrlenW(fd.cFileName), lb = lstrlenW(best);
        if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && !StrStrIW(fd.cFileName, L"-32") &&
            (ln > lb || (ln == lb && lstrcmpiW(fd.cFileName, best) > 0)))
            lstrcpyW(best, fd.cFileName);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    if (!best[0]) return FALSE;
    {
        WCHAR exe[MAX_PATH];
        wsprintfW(exe, L"%s\\%s\\python.exe", base, best);
        if (!file_exists(exe)) return FALSE;
        wsprintfW(out, L"\"%s\"", exe);
    }
    return TRUE;
}

/* python.exe on PATH, skipping the Microsoft Store alias in WindowsApps. */
static BOOL try_path_env(WCHAR *out) {
    static WCHAR path[32767];
    WCHAR *p, *next;
    if (!GetEnvironmentVariableW(L"PATH", path, 32767)) return FALSE;
    for (p = path; p && *p; p = next) {
        WCHAR exe[MAX_PATH];
        next = StrChrW(p, L';');
        if (next) *next++ = 0;
        if (!*p || StrStrIW(p, L"WindowsApps")) continue;
        if (lstrlenW(p) + 12 >= MAX_PATH) continue;
        wsprintfW(exe, L"%s\\python.exe", p);
        if (file_exists(exe)) {
            wsprintfW(out, L"\"%s\"", exe);
            return TRUE;
        }
    }
    return FALSE;
}

static BOOL find_python(WCHAR *out) {
    static const WCHAR *conda[] = {
        L"%USERPROFILE%\\anaconda3\\python.exe", L"%USERPROFILE%\\miniconda3\\python.exe",
        L"%USERPROFILE%\\miniforge3\\python.exe", L"%LOCALAPPDATA%\\anaconda3\\python.exe",
        L"%LOCALAPPDATA%\\miniconda3\\python.exe", L"%ProgramData%\\anaconda3\\python.exe",
        L"%ProgramData%\\miniconda3\\python.exe", L"%ProgramFiles%\\Python312\\python.exe",
        L"%ProgramFiles%\\Python313\\python.exe", L"%ProgramFiles%\\Python311\\python.exe", NULL};
    WCHAR py[MAX_PATH];
    int i;
    if (SearchPathW(NULL, L"py.exe", NULL, MAX_PATH, py, NULL)) {  /* python.org launcher */
        wsprintfW(out, L"\"%s\" -3", py);
        return TRUE;
    }
    if (try_path_env(out) || try_python_org(out)) return TRUE;
    for (i = 0; conda[i]; i++)
        if (try_path(conda[i], out)) return TRUE;
    return FALSE;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR args, int show) {
    WCHAR pythonw[MAX_PATH], marker[MAX_PATH], req[MAX_PATH], boot[MAX_PATH], cmd[2048];
    (void)inst; (void)prev; (void)show;

    GetModuleFileNameW(NULL, g_dir, MAX_PATH);
    PathRemoveFileSpecW(g_dir);
    wsprintfW(pythonw, L"%s\\.venv\\Scripts\\pythonw.exe", g_dir);
    wsprintfW(marker, L"%s\\.venv\\polyglot-ready", g_dir);
    wsprintfW(req, L"%s\\requirements.txt", g_dir);
    wsprintfW(boot, L"%s\\tools\\bootstrap.py", g_dir);

    if (!file_exists(boot)) {
        MessageBoxW(NULL, L"tools\\bootstrap.py is missing. Keep PolyglotAgent.exe inside the "
                          L"unzipped polyglot-agent folder.", APP_TITLE, MB_ICONERROR);
        return 1;
    }

    if (!file_exists(pythonw) || !file_exists(marker) || newer_than(req, marker)) {
        WCHAR python[MAX_PATH + 8];
        DWORD code = 1;
        if (!find_python(python)) {
            if (MessageBoxW(NULL, L"Polyglot Agent needs Python 3.10 or newer (one-time install).\n\n"
                                  L"Open the Python download page now?", APP_TITLE,
                            MB_ICONINFORMATION | MB_YESNO) == IDYES)
                ShellExecuteW(NULL, L"open", L"https://www.python.org/downloads/windows/", NULL, NULL,
                              SW_SHOWNORMAL);
            return 1;
        }
        wsprintfW(cmd, L"%s \"%s\"", python, boot);
        if (!run(cmd, CREATE_NEW_CONSOLE, TRUE, &code) || code != 0)
            return 1;  /* bootstrap already explained the problem in its console */
    }

    wsprintfW(cmd, L"\"%s\" -m app.desktop %s", pythonw, args ? args : L"");
    if (!run(cmd, 0, FALSE, NULL)) {
        MessageBoxW(NULL, L"Could not start the app. Delete the .venv folder and run "
                          L"PolyglotAgent.exe again to repair it.", APP_TITLE, MB_ICONERROR);
        return 1;
    }
    return 0;
}
