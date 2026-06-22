#!/usr/bin/env python3
"""
register.py - Installs or removes the 'Git Commit with Gemini' Windows
Explorer right-click context menu entry by directly writing to the
Windows Registry using Python's built-in winreg module.

Usage:
  python register.py            # Install (default)
  python register.py --install  # Install
  python register.py --uninstall # Remove
"""
import os
import sys

COLOR_GREEN  = "\033[92m"
COLOR_CYAN   = "\033[96m"
COLOR_RED    = "\033[91m"
COLOR_BOLD   = "\033[1m"
COLOR_RESET  = "\033[0m"

MENU_LABEL  = "Git Commit with Gemini"
REG_KEY     = "GitCommitAI"
# The two parent keys we need to write under
PARENT_KEYS = [
    r"Directory\shell",            # right-click on a folder
    r"Directory\Background\shell", # right-click inside a folder (uses %V)
]

# Preferred icon locations (first match wins)
def _find_icon() -> str:
    local_icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "git.ico")
    candidates = [
        local_icon,
        r"C:\Program Files\Git\mingw64\share\git\git-for-windows.ico",
        r"C:\Program Files\Git\mingw64\share\git-gui\lib\git-gui.ico",
        r"C:\Program Files (x86)\Git\mingw64\share\git\git-for-windows.ico",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return "cmd.exe"  # safe fallback

def get_command(script_path: str) -> str:
    return f'cmd.exe /k "cd /d "%V%" && python "{script_path}""'

def install(script_path: str):
    try:
        import winreg
    except ImportError:
        print(f"{COLOR_RED}[ERROR] winreg is only available on Windows.{COLOR_RESET}")
        sys.exit(1)

    command = get_command(script_path)
    icon_path = _find_icon()
    print(f"  Icon:   {icon_path}")

    for parent in PARENT_KEYS:
        # Create / open the label key
        key_path = f"{parent}\\{REG_KEY}"
        with winreg.CreateKeyEx(winreg.HKEY_CLASSES_ROOT, key_path,
                                0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, MENU_LABEL)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon_path)

        # Create / open the command sub-key
        cmd_path = f"{key_path}\\command"
        with winreg.CreateKeyEx(winreg.HKEY_CLASSES_ROOT, cmd_path,
                                0, winreg.KEY_WRITE) as cmd_key:
            winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, command)

    print(f"{COLOR_GREEN}{COLOR_BOLD}[OK] Context menu installed successfully!{COLOR_RESET}")
    print(f"Right-click any folder and look for: '{MENU_LABEL}'")

def uninstall():
    try:
        import winreg
    except ImportError:
        print(f"{COLOR_RED}[ERROR] winreg is only available on Windows.{COLOR_RESET}")
        sys.exit(1)

    import contextlib

    for parent in PARENT_KEYS:
        key_path = f"{parent}\\{REG_KEY}"
        # Delete command sub-key first, then the parent key
        for sub in [f"{key_path}\\command", key_path]:
            with contextlib.suppress(FileNotFoundError, OSError):
                winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, sub)

    print(f"{COLOR_GREEN}{COLOR_BOLD}[OK] Context menu removed successfully!{COLOR_RESET}")

def main():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(current_dir, "git_commit.py")

    if not os.path.exists(script_path):
        print(f"{COLOR_RED}[ERROR] git_commit.py not found at: {script_path}{COLOR_RESET}")
        sys.exit(1)

    mode = "--install"
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()

    if mode == "--uninstall":
        print(f"{COLOR_CYAN}Removing context menu entry...{COLOR_RESET}")
        uninstall()
    else:
        print(f"{COLOR_CYAN}Installing context menu entry...{COLOR_RESET}")
        print(f"  Script: {script_path}")
        install(script_path)

if __name__ == "__main__":
    try:
        main()
    except PermissionError:
        print(f"\n{COLOR_RED}[ERROR] Permission denied.{COLOR_RESET}")
        print("Please run this script as Administrator:")
        print("  Right-click PowerShell -> 'Run as administrator', then re-run:")
        print("  python register.py")
        sys.exit(1)
