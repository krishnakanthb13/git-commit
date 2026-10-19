#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import re
import urllib.request
import urllib.error
import time
import shutil

# ANSI colors for rich CLI interface
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_CYAN = "\033[96m"
COLOR_MAGENTA = "\033[95m"
COLOR_BLUE = "\033[94m"
COLOR_SKY_BLUE = "\033[38;5;39m"
COLOR_WHITE = "\033[97m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"

AI_PROMPT_EXCLUDED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", 
    ".webp", ".ico", ".svg", ".heic", ".heif", ".raw", ".cr2",
    ".psd", ".ai", ".eps", ".pdf", ".mp4", ".mov", ".avi",
    ".mkv", ".mp3", ".wav", ".flac", ".ogg", ".m4a"
}

MAX_RESTARTS = 10
restart_count = 0

# Absolute path of the repository's .git directory. Resolved at startup by
# init_git_dir() so that session state and commit templates keep working when
# the tool is launched from a subdirectory.
GIT_DIR = ".git"

# (category, pattern) pairs for high-confidence secret formats. Only the AI
# prompt is redacted; the diff that is actually committed is never modified.
SECRET_PATTERNS = [
    ("AWS access key id", re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ("AWS secret/access key", re.compile(r'(?i)\b(aws_secret_access_key|aws_access_key_id)\b\s*[:=]\s*\S+')),
    ("GitHub token", re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b')),
    ("API key (sk-/gsk-)", re.compile(r'\b(?:gsk|sk)-[A-Za-z0-9_\-]{20,}\b')),
    ("Google API key", re.compile(r'\bAIza[0-9A-Za-z_\-]{30,}\b')),
    ("Bearer token", re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}')),
    ("Credentials in URL", re.compile(r'(?i)\b[a-z][a-z0-9+.\-]*://[^:/\s]+:[^@/\s]+@')),
]

try:
    USE_COLORS = os.getenv("NO_COLOR") is None and sys.stdout.isatty()
except Exception:
    USE_COLORS = False

def c(color_code):
    """Return color code only if colors are enabled."""
    return color_code if USE_COLORS else ""

_orig_print = print

def print(*args, **kwargs):
    # Convert all arguments to strings
    args_str = [str(arg) for arg in args]
    text = " ".join(args_str)
    try:
        _orig_print(text, **kwargs)
    except UnicodeEncodeError:
        replacements = {
            "├──": "|--",
            "└──": "\\--",
            "│": "|",
            "🤖": "[AI]",
            "📁": "[Dir]",
            "📂": "[Repo]",
            "📄": "[File]",
            "🔧": "[Patch]",
            "✨": "[Minor]",
            "🚀": "[Major]",
            "⏸️": "[None]",
            "✏️": "[Custom]",
            "🎉": "Success!",
            "↑": "^",
            "↓": "v",
            "⬆️": "^",
            "⬇️": "v",
            "📊": "[Tokens]",
            "⏱️": "[Time]",
            "💾": "[Cache]",
            "⚡": "[Speed]",
            "➕": "+",
            "➖": "-",
            "📦": "[Bin]",
            "📝": "[Diff]",
            "📍": "[Path]",
        }
        for uni, asc in replacements.items():
            text = text.replace(uni, asc)
        try:
            _orig_print(text, **kwargs)
        except Exception:
            try:
                _orig_print(text.encode('ascii', errors='replace').decode('ascii'), **kwargs)
            except Exception:
                # Last resort: suppress output rather than re-raising the same error
                pass

def flush_input_buffer():
    """Flush pending user keystrokes from the console input buffer.
    
    Prevents accidental double-enters or premature keystrokes from bleeding
    into subsequent prompts before they are displayed.
    """
    if not sys.stdin.isatty():
        return
    try:
        if os.name == 'nt':
            import msvcrt
            import ctypes
            while msvcrt.kbhit():
                try:
                    msvcrt.getwch()
                except Exception:
                    break
            try:
                handle = ctypes.windll.kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE = -10
                if handle and handle != -1:
                    ctypes.windll.kernel32.FlushConsoleInputBuffer(handle)
            except Exception:
                pass
        else:
            import termios
            try:
                termios.tcflush(sys.stdin, termios.TCIFLUSH)
            except Exception:
                pass
    except Exception:
        pass

_orig_input = input

def input(prompt=""):
    """Wrapped input that flushes unconsumed keystrokes before displaying prompt."""
    flush_input_buffer()
    return _orig_input(prompt)

def prompt_restart(message="Would you like to restart CommitGen? (y/n) [n]:"):
    """Ask whether to restart, returning False safely on EOF or interrupt.

    Prevents an unhandled EOFError crash loop when stdin is closed (piped
    input or CI) while still honouring a real 'y' answer.
    """
    try:
        answer = input(f"\n{c(COLOR_CYAN)}{message}{c(COLOR_RESET)} ").strip().lower()
        return answer == 'y'
    except (EOFError, KeyboardInterrupt):
        return False

def print_success(msg):
    if USE_COLORS:
        print(f"{COLOR_GREEN}{COLOR_BOLD}[OK] {msg}{COLOR_RESET}")
    else:
        print(f"[OK] {msg}")

def print_info(msg):
    if USE_COLORS:
        print(f"{COLOR_CYAN}{COLOR_BOLD}[INFO] {msg}{COLOR_RESET}")
    else:
        print(f"[INFO] {msg}")

def print_warn(msg):
    if USE_COLORS:
        print(f"{COLOR_YELLOW}{COLOR_BOLD}[WARN] {msg}{COLOR_RESET}")
    else:
        print(f"[WARN] {msg}")

def print_error(msg):
    if USE_COLORS:
        print(f"{COLOR_RED}{COLOR_BOLD}[ERROR] {msg}{COLOR_RESET}")
    else:
        print(f"[ERROR] {msg}")

def print_token_usage(ai_res):
    """Print token usage and latency with clean colors and simple arrows."""
    tokens = ai_res.get("_tokens") if isinstance(ai_res, dict) else None
    if tokens:
        sent = tokens.get("prompt", 0)
        received = tokens.get("candidates", 0)
        total = tokens.get("total", sent + received)
        latency = tokens.get("latency")
        cached = tokens.get("cached", 0)

        # Clean typography with distinct colors and simple symbols
        sent_str = f"{c(COLOR_SKY_BLUE)}↑ {sent:,} sent{c(COLOR_RESET)}"
        recv_str = f"{c(COLOR_GREEN)}↓ {received:,} received{c(COLOR_RESET)}"
        total_str = f"{c(COLOR_MAGENTA)}{total:,} total{c(COLOR_RESET)}"

        parts = [sent_str, recv_str, total_str]
        if cached:
            parts.append(f"{c(COLOR_YELLOW)}{cached:,} cached{c(COLOR_RESET)}")
        if latency is not None:
            parts.append(f"{c(COLOR_YELLOW)}{latency:.2f}s{c(COLOR_RESET)}")

        details = f" {c(COLOR_BOLD)}│{c(COLOR_RESET)} ".join(parts)
        print_info(f"AI Tokens: {details}")

def load_dotenv():
    """Load .env variables without external dependencies.

    Precedence (lowest to highest):
      1. Real environment variables (never overridden)
      2. Tool-directory .env
      3. Project .env in the current working directory

    .env files use first-wins semantics, so the project .env (collected
    first) overrides the tool-directory .env. Variables already present in
    the real environment always take precedence over any .env file.
    """
    file_vars = {}

    def collect_from_path(env_path):
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        # Strip quotes if present
                        val = val.strip().strip('"').strip("'")
                        # First .env file to define a key wins
                        file_vars.setdefault(key.strip(), val)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    cwd_is_script_dir = os.path.abspath(os.getcwd()) == os.path.abspath(script_dir)

    # The project .env (cwd) takes precedence over the tool-directory .env,
    # so it is collected first.
    if not cwd_is_script_dir:
        collect_from_path(".env")
    collect_from_path(os.path.join(script_dir, ".env"))

    # Real environment variables always win over any .env file.
    for key, val in file_vars.items():
        if key and key not in os.environ:
            os.environ[key] = val

def run_git_cmd(args, strip=True):
    """Run a git command and return its stdout, or None on failure."""
    try:
        res = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # never crash on binary / non-UTF-8 bytes
            check=True
        )
        return res.stdout.strip() if strip else res.stdout
    except subprocess.CalledProcessError:
        return None

def init_git_dir():
    """Resolve the absolute path of the repository's .git directory.

    Session state and commit templates live under .git, so using an absolute
    path keeps them working when the tool is launched from a subdirectory.
    Returns True when a Git repository was detected.
    """
    global GIT_DIR
    git_dir = run_git_cmd(["rev-parse", "--absolute-git-dir"])
    if git_dir:
        GIT_DIR = git_dir
        return True
    return False

def _mask_secret(value):
    """Return a short, non-reversible preview used to identify a redaction.

    Keeps only a tiny prefix (enough to recognise the pattern family, e.g.
    'AKIA' or 'post') and masks the rest, so diagnostics never re-expose the
    secret itself.
    """
    if len(value) <= 4:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 4)

def redact_secrets(text):
    """Redact likely secrets before text is sent to the third-party AI API.

    Only the prompt is affected; the diff that gets committed is untouched.
    Reports the total count plus a masked preview per category, so genuine
    detections can be told apart from fixtures (for example, example keys that
    live in test files).
    """
    redacted = text
    found = []

    def _replacer(category):
        def _replace(match):
            found.append((category, _mask_secret(match.group(0))))
            return "[REDACTED]"
        return _replace

    for category, pattern in SECRET_PATTERNS:
        redacted, _ = pattern.subn(_replacer(category), redacted)

    if found:
        print_warn(f"Redacted {len(found)} possible secret(s) from the AI prompt (commit content is unchanged).")
        for category, preview in found[:5]:
            print_info(f"  - {category}: {preview}")
        if len(found) > 5:
            print_info(f"  - ... and {len(found) - 5} more")
    return redacted

def get_consolidated_diff_stats(staged=None, commit_mode='new'):
    """Get consolidated diff breakdown: additions (+), deletions (-), and files changed."""
    args = ["diff", "--cached", "--numstat"]
    if commit_mode == 'fresh_amend':
        args += ["HEAD~1"]
    if staged:
        args += ["--"] + staged
    
    out = run_git_cmd(args)
    if not out:
        return None
    
    insertions = 0
    deletions = 0
    binary_files = 0
    text_files = 0
    
    for line in out.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) >= 3:
            ins, dels, _ = parts[0], parts[1], parts[2]
            if ins == '-' or dels == '-':
                binary_files += 1
            else:
                try:
                    insertions += int(ins)
                    deletions += int(dels)
                    text_files += 1
                except ValueError:
                    pass
    
    total_files = text_files + binary_files
    return {
        "insertions": insertions,
        "deletions": deletions,
        "text_files": text_files,
        "binary_files": binary_files,
        "total_files": total_files
    }

def print_diff_breakdown(staged=None, commit_mode='new'):
    """Print consolidated diff statistics with clean colors and simple symbols."""
    stats = get_consolidated_diff_stats(staged, commit_mode)
    if not stats or (stats["total_files"] == 0 and stats["insertions"] == 0 and stats["deletions"] == 0):
        return
    
    ins = stats["insertions"]
    dels = stats["deletions"]
    files_cnt = stats["total_files"]
    binary_cnt = stats["binary_files"]
    net = ins - dels
    
    files_str = f"{c(COLOR_SKY_BLUE)}{files_cnt} file{'s' if files_cnt != 1 else ''}{c(COLOR_RESET)}"
    ins_str = f"{c(COLOR_GREEN)}+{ins:,} added{c(COLOR_RESET)}"
    dels_str = f"{c(COLOR_RED)}-{dels:,} deleted{c(COLOR_RESET)}"
    
    if net > 0:
        net_str = f"{c(COLOR_GREEN)}+{net:,} net{c(COLOR_RESET)}"
    elif net < 0:
        net_str = f"{c(COLOR_RED)}{net:,} net{c(COLOR_RESET)}"
    else:
        net_str = f"{c(COLOR_YELLOW)}0 net{c(COLOR_RESET)}"
        
    parts = [files_str, ins_str, dels_str, f"({net_str})"]
    if binary_cnt > 0:
        parts.append(f"{c(COLOR_YELLOW)}{binary_cnt} binary{c(COLOR_RESET)}")
        
    details = f" {c(COLOR_BOLD)}│{c(COLOR_RESET)} ".join(parts)
    print_info(f"Diff Stats: {details}")

def detect_version():
    """Detect current version from git tags, commit messages, or project files."""
    # 1. Check git tags
    tags = run_git_cmd(["tag", "-l"])
    semver_regex = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
    tag_version = None
    if tags:
        valid_versions = []
        for tag in tags.split("\n"):
            m = semver_regex.match(tag.strip())
            if m:
                valid_versions.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), tag))
        if valid_versions:
            # Sort by version tuple
            valid_versions.sort()
            tag_version = valid_versions[-1][3]

    # 2. Check git commit messages for version patterns
    commit_version = None
    commit_source = None
    try:
        # Look for version patterns in recent commit messages
        log = run_git_cmd(["log", "--oneline", "-50"])  # Check last 50 commits
        if log:
            # Pattern for version in commit message PREFIX (e.g. "v1.2.3 - " at the start)
            # Only match versions in the prefix, not in the body/description text
            prefix_version_pattern = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)\s*-\s*', re.IGNORECASE)
            found_versions = []
            for line in log.split('\n'):
                # Remove the commit hash (first word)
                parts = line.split(' ', 1)
                if len(parts) > 1:
                    message = parts[1]
                    match = prefix_version_pattern.match(message)
                    if match:
                        major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
                        full_match = message[:match.end()].strip().rstrip(' -')
                        found_versions.append((major, minor, patch, full_match, message[:50]))
            
            if found_versions:
                # Get the highest version found in commits
                found_versions.sort()
                latest = found_versions[-1]
                commit_version = latest[3]  # The full version string
                commit_source = f"commit message ('{latest[4]}...')"
    except Exception:
        pass

    # 3. Check package.json
    file_version = None
    file_source = None
    if os.path.exists("package.json"):
        try:
            with open("package.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if "version" in data:
                    file_version = data["version"]
                    file_source = "package.json"
        except Exception:
            pass

    # 4. Check pyproject.toml
    pyproject_version = None
    pyproject_source = None
    if os.path.exists("pyproject.toml"):
        try:
            with open("pyproject.toml", "r", encoding="utf-8") as f:
                for line in f:
                    m = re.match(r'^\s*version\s*=\s*["\']([^"\']+)["\']', line)
                    if m:
                        pyproject_version = m.group(1)
                        pyproject_source = "pyproject.toml"
                        break
        except Exception:
            pass

    # List all search targets and search results
    print_info("Searching for versions:")
    search_results = [
        ("git tag", tag_version),
        ("git commit", commit_version),
        ("package.json", file_version),
        ("pyproject.toml", pyproject_version)
    ]

    for name, ver in search_results:
        ver_str = ver if ver else "not found"
        print(f"  - {name}: {c(COLOR_GREEN) if ver else c(COLOR_YELLOW)}{ver_str}{c(COLOR_RESET)}")

    # Build selectable list of valid versions
    valid_choices = []
    for name, ver in search_results:
        if ver:
            valid_choices.append((ver, name))

    if not valid_choices:
        print_info("No versions found. Defaulting to 0.0.0.")
        return "0.0.0", "default"

    # Find max length of version key tuples to normalize length for consistent comparisons
    parsed_keys = [semver_key(v[0]) for v in valid_choices]
    max_len = max(len(k) for k in parsed_keys) if parsed_keys else 3

    def parse_normalized_key(v_str):
        key = semver_key(v_str)
        return key + (0,) * (max_len - len(key))

    # Filter unique version entries and sort by version descending (highest version first)
    valid_choices.sort(key=lambda x: parse_normalized_key(x[0]), reverse=True)
    highest_version = valid_choices[0][0]
    highest_key = parse_normalized_key(highest_version)

    non_interactive = "--non-interactive" in sys.argv or any(
        os.getenv(v) for v in ['CI', 'GITHUB_ACTIONS', 'GITLAB_CI', 'JENKINS_URL', 'TRAVIS']
    )

    if non_interactive:
        # Auto-select the highest available version
        return valid_choices[0][0], valid_choices[0][1]

    while True:
        # Present list to user to select
        print(f"\n{c(COLOR_CYAN)}Select base version for bumping:{c(COLOR_RESET)}")
        for idx, (ver, name) in enumerate(valid_choices, 1):
            print(f"  {c(COLOR_BOLD)}{idx}{c(COLOR_RESET)}) {ver} (from {name})")
        
        # Allow custom version as well
        print(f"  {c(COLOR_BOLD)}{len(valid_choices)+1}{c(COLOR_RESET)}) Custom base version")

        choice = input(f"Choice [1-{len(valid_choices)+1}] [1]: ").strip()
        if not choice:
            choice = "1"
        try:
            val = int(choice)
            if 1 <= val <= len(valid_choices):
                selected_ver, selected_name = valid_choices[val-1]
                selected_key = parse_normalized_key(selected_ver)
                if selected_key < highest_key:
                    print_warn(f"Selected version '{selected_ver}' is lower than the highest available version '{highest_version}'.")
                    confirm = input("Are you sure you want to proceed with this lower version? (y/N) [n]: ").strip().lower()
                    if confirm != 'y':
                        print_info("Returning to version selection...")
                        continue
                return selected_ver, selected_name
            elif val == len(valid_choices) + 1:
                custom_base = input("Custom base version (e.g. 1.0.0): ").strip()
                if not custom_base:
                    custom_base = "0.0.0"
                if not custom_base.lower().startswith('v'):
                    custom_base = "v" + custom_base
                custom_key = parse_normalized_key(custom_base)
                if custom_key < highest_key:
                    print_warn(f"Selected custom version '{custom_base}' is lower than the highest available version '{highest_version}'.")
                    confirm = input("Are you sure you want to proceed with this lower version? (y/N) [n]: ").strip().lower()
                    if confirm != 'y':
                        print_info("Returning to version selection...")
                        continue
                return custom_base, "custom user entry"
        except ValueError:
            pass
        print_error(f"Invalid selection. Please enter a number between 1 and {len(valid_choices)+1}.")

def validate_commit_message(message):
    """Validate commit message format."""
    issues = []
    
    first_line = message.split('\n')[0]
    if len(first_line) > 72:
        issues.append("First line exceeds 72 characters")
    
    # Strip any potential version prefix (e.g. 'v1.2.3 - ') to check conventional format
    content_line = first_line
    if " - " in first_line:
        content_line = first_line.split(" - ", 1)[1]
        
    if not re.match(r'^(feat|fix|docs|style|refactor|test|chore|perf|ci|build|revert|update)(\(.+\))?:', content_line, re.IGNORECASE):
        issues.append("Doesn't follow conventional commit format (e.g., feat:, fix:, docs:)")
    
    lines = message.split('\n')
    if len(lines) > 1 and lines[1] != '':
        issues.append("Missing blank line after title")
    
    return issues

def get_branch_version_info():
    """Get branch name for version context."""
    branch = run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"])
    if branch and branch not in ['main', 'master', 'HEAD']:
        branch = re.sub(r'[^a-zA-Z0-9.]', '-', branch)
        return branch
    return None

def is_valid_semver(version: str) -> bool:
    """Validate semver string."""
    pattern = r'^v?(\d+)\.(\d+)\.(\d+)(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$'
    return bool(re.match(pattern, version))

def semver_key(version_str):
    """Extract a numeric tuple from a version string (e.g. 'v1.2.3.4' -> (1, 2, 3, 4))."""
    clean = version_str.lstrip("vV")
    parts = re.findall(r"\d+", clean)
    if parts:
        return tuple(int(p) for p in parts)
    return (0, 0, 0)

def is_version_regression(proposed, current):
    """Return True when `proposed` is numerically lower than `current`."""
    proposed_key = semver_key(proposed)
    current_key = semver_key(current)
    max_len = max(len(proposed_key), len(current_key))
    pad = lambda key: key + (0,) * (max_len - len(key))
    return pad(proposed_key) < pad(current_key)

def check_remote_tag(tag_name):
    """Check if an exact tag ref exists on the remote.

    The full 'refs/tags/<name>' pattern is passed so ls-remote's glob handling
    cannot match unrelated tags (e.g. 'v1.2.3' matching 'v1.2.34').
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", "origin", f"refs/tags/{tag_name}"],
            capture_output=True, text=True, check=True
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False

def version_already_tagged(version: str) -> bool:
    """Check if a version is already tagged."""
    clean = version.lstrip("vV")
    existing = run_git_cmd(["tag", "-l", f"v{clean}", f"{clean}"])
    return bool(existing)

def move_tag_to_head(tag):
    """Move a tag to HEAD, preserving annotated tags (type and message)."""
    tag_type = run_git_cmd(["cat-file", "-t", f"refs/tags/{tag}"])
    if tag_type == "tag":
        tag_message = run_git_cmd(["for-each-ref", f"refs/tags/{tag}", "--format=%(contents)"])
        if tag_message:
            subprocess.run(["git", "tag", "-f", "-a", tag, "-m", tag_message, "HEAD"], check=True)
            return
    subprocess.run(["git", "tag", "-f", tag, "HEAD"], check=True)

def get_recent_commits(n=3):
    """Get last N commit messages for context."""
    log = run_git_cmd(["log", f"-{n}", "--oneline", "--no-decorate"])
    return log if log else ""

def get_last_commit_message():
    """Get the full message of the last commit."""
    return run_git_cmd(["log", "-1", "--format=%B"])

def load_commit_template():
    """Load commit template if exists."""
    template_path = os.path.join(GIT_DIR, 'COMMIT_TEMPLATE')
    if os.path.exists(template_path):
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    
    github_pr_template = os.path.join('.github', 'PULL_REQUEST_TEMPLATE.md')
    if os.path.exists(github_pr_template):
        with open(github_pr_template, 'r', encoding='utf-8') as f:
            return f.read().strip()
            
    return None

def extract_issue_references():
    """Extract issue references from branch name and recent commits."""
    refs = set()
    
    branch = run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"])
    if branch:
        issues = re.findall(r'#(\d+)', branch)
        refs.update(issues)
        branch_issues = re.findall(r'/(\d+)-', branch)
        refs.update(branch_issues)
    
    log = run_git_cmd(["log", "--oneline", "-5"])
    if log:
        issues = re.findall(r'#(\d+)', log)
        refs.update(issues)
    
    return list(refs)

def update_changelog(version, summary, description):
    """Update CHANGELOG.md if it exists.

    Inserts a new version entry, or replaces the existing entry for the same
    version in place. Replacing prevents duplicate entries when re-committing
    or fresh-amending the same version.
    """
    changelog_path = "CHANGELOG.md"
    if not os.path.exists(changelog_path):
        return
    
    print_info("Updating CHANGELOG.md...")
    date_str = time.strftime("%Y-%m-%d")
    clean_version = version.lstrip("vV")
    
    entry = f"## [v{clean_version}] - {date_str}\n\n### Changes\n\n- {summary}\n"
    if description:
        # Indent the description bullets under the main summary bullet to preserve hierarchy
        # Empty or whitespace-only lines are normalized to empty strings to prevent trailing spaces
        indented_desc = "\n".join(f"  {line}" if line.strip() else "" for line in description.splitlines())
        entry += f"{indented_desc}\n"
    
    with open(changelog_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Replace an existing entry for this exact version, if present. An entry
    # runs from its '## ' heading to the next '## ' heading (or end of file).
    heading_re = re.compile(r"^## \[v" + re.escape(clean_version) + r"\].*$", re.MULTILINE)
    heading_match = heading_re.search(content)
    if heading_match:
        next_heading = re.search(r"^## ", content[heading_match.end():], re.MULTILINE)
        block_end = heading_match.end() + next_heading.start() if next_heading else len(content)
        content = content[:heading_match.start()] + entry + "\n" + content[block_end:]
    else:
        # Insert before the first existing version heading; append when none exist.
        first_heading = re.search(r"^## ", content, re.MULTILINE)
        if first_heading:
            content = content[:first_heading.start()] + entry + "\n" + content[first_heading.start():]
        else:
            if content and not content.endswith("\n"):
                content += "\n"
            content += "\n" + entry
    
    with open(changelog_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    run_git_cmd(["add", changelog_path])

def increment_version(version_str, bump_type):
    """Increment version by bump type (patch, minor, major). Supports N-part versions."""
    if bump_type.startswith("custom:"):
        return bump_type.split(":", 1)[1]

    prefix = "v" if version_str.lower().startswith("v") else ""
    clean_version = version_str.lstrip("vV")
    parts_raw = clean_version.split(".")

    # Ensure integer components
    try:
        int_parts = [int(p) for p in parts_raw]
    except ValueError:
        return version_str

    if not int_parts:
        return version_str

    # Pad with zeros if fewer than 3 components (e.g. 1.2 -> 1.2.0)
    while len(int_parts) < 3:
        int_parts.append(0)

    if bump_type == "major":
        int_parts[0] += 1
        for i in range(1, len(int_parts)):
            int_parts[i] = 0
    elif bump_type == "minor":
        if len(int_parts) > 1:
            int_parts[1] += 1
            for i in range(2, len(int_parts)):
                int_parts[i] = 0
        else:
            int_parts[0] += 1
    elif bump_type == "patch":
        int_parts[-1] += 1

    return f"{prefix}{'.'.join(map(str, int_parts))}"

def update_version_in_files(new_version):
    """Update version in package.json and pyproject.toml if they exist."""
    clean_ver = new_version.lstrip("vV")
    
    if os.path.exists("package.json"):
        try:
            with open("package.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            data["version"] = clean_ver
            with open("package.json", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            print_success(f"Updated package.json to version {clean_ver}")
        except Exception as e:
            print_warn(f"Failed to update package.json: {e}")

    if os.path.exists("pyproject.toml"):
        try:
            lines = []
            updated = False
            with open("pyproject.toml", "r", encoding="utf-8") as f:
                for line in f:
                    if not updated and re.match(r'^\s*version\s*=\s*["\']([^"\']+)["\']', line):
                        line = re.sub(r'(["\'])([^"\']+)["\']', f'\\1{clean_ver}\\1', line)
                        updated = True
                    lines.append(line)
            with open("pyproject.toml", "w", encoding="utf-8") as f:
                f.writelines(lines)
            print_success(f"Updated pyproject.toml to version {clean_ver}")
        except Exception as e:
            print_warn(f"Failed to update pyproject.toml: {e}")


def snapshot_files(paths):
    """Read file contents into a rollback snapshot (None for missing files)."""
    snapshot = {}
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    snapshot[path] = f.read()
            except Exception:
                snapshot[path] = None
    return snapshot

def restore_files_snapshot(snapshot):
    """Restore snapshotted files and unstage them (rollback for failed commits).

    Prevents version files and the changelog from drifting upward when a
    commit fails and the session is retried, while preserving the user's own
    working-tree edits. Files whose content is unchanged since the snapshot
    (i.e. the tool never rewrote them) are left completely alone, so manual
    staging performed by the user is never discarded.
    """
    for path, content in snapshot.items():
        if content is None:
            continue
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    if f.read() == content:
                        continue
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            run_git_cmd(["restore", "--staged", path])
        except Exception as e:
            print_warn(f"Could not roll back {path}: {e}")


def is_env_file(filepath):
    """Check if a file is a secret .env file or a variant (excluding safe templates like .env.template, .env.example, etc.)."""
    name = os.path.basename(filepath).lower()
    # Safe templates/examples containing no secrets are intended for git tracking
    if name.endswith((".template", ".example", ".sample", ".dist")):
        return False
    return (
        name == ".env"
        or name.startswith(".env.")
        or name == ".envrc"
    )

def drop_env_files(files):
    """Filter .env files out of a file list, warning about anything dropped.

    Used on resumed sessions, which rebuild their staged file list from saved
    state instead of re-reading git status.
    """
    kept = []
    dropped = []
    for f in files:
        if is_env_file(f):
            dropped.append(f)
        else:
            kept.append(f)
    if dropped:
        print_warn(f"Security: Ignoring .env file(s): {', '.join(dropped)}")
    return kept

def should_exclude_from_ai(filepath):
    """Check if a file should be excluded from AI diff analysis (images, media, binary)."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in AI_PROMPT_EXCLUDED_EXTENSIONS:
        return True
    if os.path.exists(filepath) and is_binary_file(filepath):
        return True
    return False

def perform_git_pull():
    """Execute git pull --rebase with automatic fallback resolution for multi-branch tracking ambiguity."""
    res = subprocess.run(["git", "pull", "--rebase"], capture_output=True, text=True)
    if res.returncode == 0:
        return res
    
    stderr_lower = res.stderr.lower()
    if "cannot rebase onto multiple branches" in stderr_lower:
        branch_name = run_git_cmd(["branch", "--show-current"])
        remote = run_git_cmd(["config", f"branch.{branch_name}.remote"]) if branch_name else ""
        if not remote:
            remotes = run_git_cmd(["remote"])
            remote = remotes.split()[0] if remotes else "origin"
        
        # Prefer the current branch, then the remote's default branch
        # (refs/remotes/<remote>/HEAD), then init.defaultBranch, then 'main'.
        target_branch = branch_name or None
        if not target_branch:
            remote_head = run_git_cmd(["symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"])
            if remote_head and "/" in remote_head:
                target_branch = remote_head.split("/", 1)[1]
        if not target_branch:
            target_branch = run_git_cmd(["config", "init.defaultBranch"]) or "main"
        
        fallback_res = subprocess.run(["git", "pull", "--rebase", remote, target_branch], capture_output=True, text=True)
        return fallback_res
        
    return res

def get_git_files(include_details=False):
    """Get staged, unstaged, and untracked files using null-terminated porcelain output."""
    staged = []
    unstaged = []
    untracked = []
    details = {}

    status_out = run_git_cmd(["status", "--porcelain", "-z"], strip=False)
    if not status_out:
        if include_details:
            return staged, unstaged, untracked, details
        return staged, unstaged, untracked

    entries = status_out.split("\0")
    i = 0
    staged_envs = []
    while i < len(entries):
        entry = entries[i]
        if not entry:
            i += 1
            continue
        
        if len(entry) < 3:
            i += 1
            continue
            
        xy = entry[:2]
        filename = entry[3:]
        orig_path = None

        # For renames (R) or copies (C), the old path takes up the next NUL-separated entry
        if xy[0] in ["R", "C"] or xy[1] in ["R", "C"]:
            if i + 1 < len(entries):
                orig_path = entries[i + 1]

        # Completely ignore .env files to prevent staging/committing
        if is_env_file(filename) or (orig_path and is_env_file(orig_path)):
            if xy[0] in ["M", "A", "D", "R", "C"]:
                staged_envs.append(filename)
            i += 1
            if xy[0] in ["R", "C"] or xy[1] in ["R", "C"]:
                i += 1
            continue

        file_detail = details.setdefault(filename, {})
        if orig_path:
            file_detail["orig_path"] = orig_path

        # Staged status in X slot
        if xy[0] in ["M", "A", "D", "R", "C"]:
            staged.append(filename)
            if xy[0] == "M":
                file_detail["staged"] = "staged: modified"
            elif xy[0] == "A":
                file_detail["staged"] = "staged: untracked"
            elif xy[0] == "R":
                file_detail["staged"] = "staged: renamed"
            elif xy[0] == "D":
                file_detail["staged"] = "staged: deleted"
            elif xy[0] == "C":
                file_detail["staged"] = "staged: copied"

        # Unstaged status in Y slot (or untracked)
        if xy[1] in ["M", "A", "D"]:
            unstaged.append(filename)
            if xy[1] == "M":
                file_detail["unstaged"] = "modified"
            elif xy[1] == "D":
                file_detail["unstaged"] = "deleted"
            elif xy[1] == "A":
                file_detail["unstaged"] = "added"
        elif xy == "??":
            untracked.append(filename)
            file_detail["untracked"] = "untracked"

        if xy[0] in ["R", "C"] or xy[1] in ["R", "C"]:
            i += 1
            
        i += 1

    if staged_envs:
        print_warn(f"Security: Staged .env file(s) detected: {', '.join(staged_envs)}")
        print_info("Automatically unstaging them to prevent accidental credential leaks...")
        for env_f in staged_envs:
            run_git_cmd(["restore", "--staged", env_f])
        # Recursively call get_git_files to get clean lists after unstaging
        return get_git_files(include_details=include_details)

    if include_details:
        return staged, unstaged, untracked, details
    return staged, unstaged, untracked

def parse_index_selection(input_str, max_len):
    """Parse comma-separated numbers, ranges (e.g. 1-3, 5), and 'all'/'*' into 0-based indices."""
    cleaned = input_str.strip().lower()
    if cleaned in ["all", "*"]:
        return list(range(max_len))
    
    indices = []
    parts = [p.strip() for p in input_str.split(",") if p.strip()]
    for part in parts:
        if "-" in part:
            range_parts = part.split("-")
            if len(range_parts) == 2 and range_parts[0].strip().isdigit() and range_parts[1].strip().isdigit():
                start = int(range_parts[0].strip())
                end = int(range_parts[1].strip())
                if start > end:
                    start, end = end, start
                for num in range(start, end + 1):
                    if 1 <= num <= max_len and (num - 1) not in indices:
                        indices.append(num - 1)
            else:
                raise ValueError(f"Invalid range: {part}")
        elif part.isdigit():
            num = int(part)
            if 1 <= num <= max_len and (num - 1) not in indices:
                indices.append(num - 1)
        else:
            raise ValueError(f"Invalid item: {part}")
    return indices

def prompt_amend_or_new():
    """Ask user whether to amend last commit or create new one."""
    print(f"\n{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}Commit Action:{c(COLOR_RESET)}")
    print(f"  {c(COLOR_GREEN)}{c(COLOR_BOLD)}n{c(COLOR_RESET)}) New commit")
    print(f"  {c(COLOR_YELLOW)}{c(COLOR_BOLD)}a{c(COLOR_RESET)}) Amend previous commit (add files, update message)")
    print(f"  {c(COLOR_CYAN)}{c(COLOR_BOLD)}f{c(COLOR_RESET)}) Fresh amend (add files, generate new AI message)")
    
    choice = input(f"\n{c(COLOR_CYAN)}Action [n/a/f] [n]:{c(COLOR_RESET)} ").strip().lower()
    if choice == 'a':
        return 'amend'
    elif choice == 'f':
        return 'fresh_amend'
    else:
        return 'new'

def prompt_stage_files(staged, unstaged, untracked):
    """Always show full file picker so user can review/add files before committing."""
    while True:
        staged, unstaged, untracked, details = get_git_files(include_details=True)
        all_available = []
        for f in staged:
            st_info = details.get(f, {}).get("staged", "staged")
            orig = details.get(f, {}).get("orig_path")
            display_name = f"{orig} -> {f}" if orig else f
            all_available.append((f, st_info, display_name))

        for f in unstaged:
            unst_info = details.get(f, {}).get("unstaged", "modified")
            all_available.append((f, unst_info, f))

        for f in untracked:
            all_available.append((f, "untracked", f))

        if not all_available:
            return False

        print()
        print_info("Select files to stage/commit:")
        print(f"  ({c(COLOR_GREEN)}staged: green{c(COLOR_RESET)}, {c(COLOR_YELLOW)}modified: yellow{c(COLOR_RESET)}, {c(COLOR_RED)}untracked: red{c(COLOR_RESET)})")
        print(f"  {'-' * 45}")
        for idx, (f, status, display_name) in enumerate(all_available, 1):
            if status.startswith("staged"):
                color = c(COLOR_GREEN)
            elif status == "modified":
                color = c(COLOR_YELLOW)
            elif status == "deleted":
                color = c(COLOR_MAGENTA)
            else:
                color = c(COLOR_RED)
            print(f"  {c(COLOR_BOLD)}{idx}{c(COLOR_RESET)}) [{color}{status}{c(COLOR_RESET)}] {display_name}")
        
        print(f"  {'-' * 45}")
        print(f"  {c(COLOR_GREEN)}{c(COLOR_BOLD)}a{c(COLOR_RESET)}) Stage all files")
        print(f"  {c(COLOR_YELLOW)}{c(COLOR_BOLD)}u{c(COLOR_RESET)}) Unstage specific files")
        print(f"  {c(COLOR_MAGENTA)}{c(COLOR_BOLD)}r{c(COLOR_RESET)}) Refresh status")
        print(f"  {c(COLOR_CYAN)}{c(COLOR_BOLD)}p{c(COLOR_RESET)}) Proceed with staged files")
        print(f"  {c(COLOR_RED)}{c(COLOR_BOLD)}q{c(COLOR_RESET)}) Abort")

        choice = input("\nAction [comma-separated numbers/ranges (e.g. 1,3-5) or a/u/r/p/q] [a]: ").strip().lower()
        if choice == '':
            choice = 'a'

        if choice == 'q':
            return "quit"
        elif choice == 'r':
            print_info("Refreshed file status.")
            continue
        elif choice == 'a':
            for f, _, _ in all_available:
                run_git_cmd(["add", f])
            print_success("Staged all files.")
            return True
        elif choice == 'p':
            if staged:
                return True
            print_warn("No files are currently staged to commit.")
            continue
        elif choice == 'u':
            if not staged:
                print_warn("No staged files to unstage.")
                continue  # Loop back to re-display the picker
            
            print_info("Unstage files:")
            for idx, f in enumerate(staged, 1):
                st_info = details.get(f, {}).get("staged", "staged")
                orig = details.get(f, {}).get("orig_path")
                display_name = f"{orig} -> {f}" if orig else f
                print(f"  {c(COLOR_BOLD)}{idx}{c(COLOR_RESET)}) [{c(COLOR_GREEN)}{st_info}{c(COLOR_RESET)}] {display_name}")
            
            unstage_choice = input("\nSelect files [comma-separated numbers/ranges (e.g. 1,3-5) or all] (press Enter to cancel): ").strip()
            if not unstage_choice:
                print_info("Cancelled unstaging.")
                continue
            try:
                unstage_indices = parse_index_selection(unstage_choice, len(staged))
                for idx in unstage_indices:
                    run_git_cmd(["restore", "--staged", staged[idx]])
                    print_success(f"Unstaged: {staged[idx]}")
            except ValueError:
                print_error("Invalid selection.")
            
            continue
        else:
            try:
                indices = parse_index_selection(choice, len(all_available))
            except ValueError:
                print_error("Invalid selection. No files staged.")
                return False

            if not indices:
                print_warn("No files selected.")
                return False

            # An unstaged rename appears as a deleted file plus an untracked
            # file (or an untracked directory). Stage matching deletions
            # alongside newly added files so the rename is recorded atomically
            # instead of duplicating the file.
            deleted_by_name = {}
            for candidate in unstaged:
                if details.get(candidate, {}).get("unstaged") == "deleted":
                    deleted_by_name.setdefault(os.path.basename(candidate), []).append(candidate)

            for idx in indices:
                f, _, _ = all_available[idx]
                # Expand untracked directories before staging: once 'git add'
                # runs, the files are no longer reported as untracked.
                is_untracked = bool(details.get(f, {}).get("untracked"))
                new_names = set()
                if is_untracked:
                    if f.endswith("/"):
                        listed = run_git_cmd(["ls-files", "--others", "--exclude-standard", "-z", "--", f], strip=False) or ""
                        new_names = {os.path.basename(p) for p in listed.split("\0") if p}
                    else:
                        new_names = {os.path.basename(f)}
                run_git_cmd(["add", f])
                print_success(f"Staged: {f}")
                for name in sorted(new_names):
                    for old_path in list(deleted_by_name.get(name, [])):
                        run_git_cmd(["add", old_path])
                        deleted_by_name[name].remove(old_path)
                        print_success(f"Staged deletion: {old_path} (rename pair of {f})")
            return True

def monitor_ci():
    """Prompt and monitor CI using GitHub CLI if available."""
    ci_choice = input(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}Monitor GitHub Actions CI? (y/N) [n]:{c(COLOR_RESET)} ").strip().lower()
    if ci_choice != "y":
        return
        
    if not shutil.which("gh"):
        print_warn("GitHub CLI (gh) is not installed. Please install it to monitor CI (https://cli.github.com/).")
        return

    print_info("Checking for active CI runs...")
    try:
        # Give GitHub a second to register the push event
        time.sleep(2)
        
        commit_hash = run_git_cmd(["rev-parse", "HEAD"])
        if commit_hash:
            res = subprocess.run(["gh", "run", "list", "--commit", commit_hash, "--json", "databaseId,status,name,conclusion"], 
                                 capture_output=True, text=True, check=True)
        else:
            res = subprocess.run(["gh", "run", "list", "--limit", "1", "--json", "databaseId,status,name,conclusion"], 
                                 capture_output=True, text=True, check=True)
                                 
        output = res.stdout.strip()
        if not output or output == "[]":
            print_warn("No CI runs found for this commit. It might take a moment to trigger, or no CI is configured.")
            return
            
        runs = json.loads(output)
        if not runs:
            print_warn("No CI runs found.")
            return
            
        latest_run = runs[0]
        run_id = str(latest_run["databaseId"])
        status = latest_run["status"]
        name = latest_run["name"]
        conclusion = latest_run.get("conclusion", "")
        
        if status == "completed":
            if conclusion == "success":
                print_success(f"CI Run '{name}' already completed successfully! 🎉")
            else:
                print_error(f"CI Run '{name}' completed with status: {conclusion}")
            return
            
        print_info(f"Monitoring CI Run '{name}' (Status: {status})...")
        subprocess.run(["gh", "run", "watch", run_id])
        
        # Check final status
        res_final = subprocess.run(["gh", "run", "view", run_id, "--json", "conclusion"], 
                                   capture_output=True, text=True)
        if res_final.returncode == 0:
            final_data = json.loads(res_final.stdout.strip())
            if final_data.get("conclusion") == "success":
                print_success(f"CI Pipeline '{name}' passed successfully! 🎉")
            else:
                print_error(f"CI Pipeline '{name}' failed ({final_data.get('conclusion')}).")
                
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.lower() if e.stderr else ""
        if "not a git repository" in err_msg or "resolve" in err_msg or "remote" in err_msg:
            print_warn("GitHub CLI error. Ensure this repository is on GitHub and you are authenticated.")
        else:
            print_warn("Failed to fetch CI runs. Are you authenticated with 'gh auth login'?")
    except Exception as e:
        print_warn(f"Could not monitor CI: {e}")

def call_gemini_api(api_key, model, prompt_text):
    """Call Gemini API to generate structured commit message."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    
    # Configure generation config for JSON Schema structured output
    payload = {
        "contents": [{
            "parts": [{
                "text": prompt_text
            }]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "summary": {
                        "type": "STRING", 
                        "description": "A concise summary/headline of the changes (max 60-70 characters)."
                    },
                    "description": {
                        "type": "STRING", 
                        "description": "Detailed list of changes and reasoning. Each bullet point must be on a new line starting with '- '. Do NOT write them as a single paragraph."
                    }
                },
                "required": ["summary", "description"]
            }
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key
        },
        method="POST"
    )

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            start_t = time.time()
            with urllib.request.urlopen(req, timeout=60) as response:
                latency = time.time() - start_t
                res_data = json.loads(response.read().decode("utf-8"))
                
                # Better error recovery
                if not res_data.get("candidates"):
                    raise ValueError("Empty response from API or blocked by safety filters.")
                    
                text_response = res_data["candidates"][0]["content"]["parts"][0]["text"]
                if not text_response:
                    raise ValueError("Empty text part from API")
                    
                parsed_response = json.loads(text_response)
                if not parsed_response.get("summary"):
                    raise ValueError("API returned invalid structure (missing summary)")
                
                # Extract token usage metadata if available
                usage = res_data.get("usageMetadata", {})
                prompt_tokens = usage.get("promptTokenCount")
                candidates_tokens = usage.get("candidatesTokenCount")
                total_tokens = usage.get("totalTokenCount")
                cached_tokens = usage.get("cachedContentTokenCount", 0)
                if prompt_tokens is not None or candidates_tokens is not None:
                    parsed_response["_tokens"] = {
                        "prompt": prompt_tokens or 0,
                        "candidates": candidates_tokens or 0,
                        "total": total_tokens or ((prompt_tokens or 0) + (candidates_tokens or 0)),
                        "cached": cached_tokens,
                        "latency": latency,
                    }
                    
                return parsed_response
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8")
            if e.code == 400:
                err_lower = err_msg.lower()
                if "token" in err_lower or "context" in err_lower:
                    print_error("Input exceeds model's context window. Try reducing diff size.")
                    print_info("Solutions:")
                    print_info("  1. Commit smaller, more focused changes")
                    print_info("  2. Decrease max_diff_length in config")
                    raise RuntimeError(f"Context window exceeded: {err_msg}")
            if e.code in [429, 503] and attempt < max_retries:
                backoff = 5 * (2 ** attempt)  # 10s, 20s
                print_warn(f"API rate limited ({e.code}). Retrying in {backoff}s ({attempt}/{max_retries})...")
                time.sleep(backoff)
                continue
            raise RuntimeError(f"HTTP {e.code}: {err_msg}")
        except Exception as e:
            if attempt < max_retries:
                backoff = 5 * (2 ** attempt)  # 10s, 20s
                err_msg_str = "Connection timed out" if "timed out" in str(e).lower() else str(e)
                print_warn(f"{err_msg_str}. Retrying in {backoff}s ({attempt}/{max_retries})...")
                time.sleep(backoff)
                continue
            raise RuntimeError(f"Connection failed: {e}")

def check_spelling(text):
    """Basic spell check using system spell checker."""
    if not shutil.which("aspell"):
        return []
    
    try:
        process = subprocess.run(
            ["aspell", "list"], 
            input=text, 
            text=True, 
            capture_output=True
        )
        misspelled = [w for w in process.stdout.strip().split('\n') if w]
        return misspelled
    except Exception:
        return []

def show_commit_stats(staged):
    """Show detailed statistics about staged changes."""
    stats = run_git_cmd(["diff", "--cached", "--stat"])
    if stats:
        print_info("Commit Statistics:")
        for line in stats.split('\n'):
            if not line:
                continue
            if "|" in line:
                parts = line.split("|", 1)
                fname = parts[0].lstrip()
                rest = parts[1]
                colored_rest = ""
                for char in rest:
                    if char == "+":
                        colored_rest += f"{c(COLOR_GREEN)}+{c(COLOR_RESET)}"
                    elif char == "-":
                        colored_rest += f"{c(COLOR_RED)}-{c(COLOR_RESET)}"
                    else:
                        colored_rest += char
                print(f"  {fname}|{colored_rest}")
            elif "changed" in line:
                colored_summary = line
                colored_summary = re.sub(r'(\d+ insertions?\(\+\))', rf"{c(COLOR_GREEN)}\1{c(COLOR_RESET)}", colored_summary)
                colored_summary = re.sub(r'(\d+ deletions?\(-\))', rf"{c(COLOR_RED)}\1{c(COLOR_RESET)}", colored_summary)
                print(f"  {c(COLOR_BOLD)}{colored_summary.strip()}{c(COLOR_RESET)}")
            else:
                print(f"  {line.strip()}")
        
        langs = {}
        for f in staged:
            ext = os.path.splitext(f)[1]
            if ext:
                langs[ext] = langs.get(ext, 0) + 1
        if langs:
            print_info("Files by extension:")
            for ext, count in sorted(langs.items(), key=lambda x: x[1], reverse=True):
                print(f"  {c(COLOR_CYAN)}{ext}{c(COLOR_RESET)}: {count} file{'s' if count != 1 else ''}")

def detect_conventional_scope(files):
    """Detect conventional commit scope from file paths."""
    if not files:
        return None
    
    scope_patterns = {
        'src/': 'core',
        'tests/': 'test',
        'test/': 'test',
        'docs/': 'docs',
        'config/': 'config',
        'api/': 'api',
        'ui/': 'ui',
        'components/': 'ui',
        'db/': 'database',
        'database/': 'database',
        'utils/': 'utils',
        'lib/': 'lib',
        'scripts/': 'scripts',
        'ci/': 'ci',
        '.github/': 'ci',
    }
    
    scopes = set()
    for file in files:
        for prefix, scope in scope_patterns.items():
            if file.startswith(prefix):
                scopes.add(scope)
    
    return list(scopes) if scopes else None

def estimate_tokens(text):
    """Rough token estimation: ~4 characters per token for English text."""
    return len(text) // 4

def truncate_diff_for_context(diff, max_chars_limit, max_tokens, prompt_overhead=2000):
    """
    Intelligently truncate diff to fit within limits (both character/cost limits and token limits).
    """
    available_tokens = max_tokens - prompt_overhead
    max_tokens_chars = available_tokens * 4
    
    # Use the stricter limit between configured max chars and model token limit
    actual_max_chars = min(max_chars_limit, max_tokens_chars)
    
    if len(diff) <= actual_max_chars:
        return diff, False
    
    if actual_max_chars == max_chars_limit:
        print_warn(f"Diff is large (> {max_chars_limit:,} chars). Optimizing to save cost/latency...")
    else:
        print_warn(f"Diff exceeds context window (~{estimate_tokens(diff):,} tokens). Optimizing...")
    
    lines = diff.split('\n')
    original_tokens = estimate_tokens(diff)
    optimized_lines = []
    current_size = 0
    budget = max(actual_max_chars - 500, 0)
    
    for line in lines:
        line_size = len(line) + 1
        if current_size + line_size > budget:
            break
        optimized_lines.append(line)
        current_size += line_size
    
    # If the very first line already exceeds the budget, keep as much of it as
    # possible so the AI still receives some content instead of a bare notice.
    if not optimized_lines and lines and budget > 0:
        optimized_lines = [lines[0][:budget]]
            
    diff = '\n'.join(optimized_lines)
    if optimized_lines:
        diff += f"\n... [diff truncated from ~{original_tokens:,} tokens to fit limits] ..."
    
    print_info(f"Diff optimized: ~{len(diff):,} chars (~{estimate_tokens(diff):,} tokens) remaining")
    return diff, True

def format_co_author_trailer(model):
    """Generate standardized Co-Authored-By git trailer for the AI model."""
    return f"Co-authored-by: Custom Tool By BKK ({model}) <{model}>"

def load_config():
    """Load config from .commitgenrc (JSON) in the repo or home directory."""
    defaults = {
        "default_bump": "patch",
        "max_diff_length": 500000,  # Increased from 20k to 500k as model supports 1M+ tokens
        "max_input_tokens": 900000,  # Reserve some tokens for response (1M - 100K safety margin)
        "auto_push": False,
        "auto_pull": True,
        "model": "gemini-3.5-flash-lite",
        "auto_tag": False,  # Set to True to skip the tagging confirmation prompt
        "co_author": False  # Set to True to enable Co-Authored-By attribution by default
    }
    for path in [".commitgenrc", os.path.expanduser("~/.commitgenrc")]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    defaults.update(json.load(f))
                print_info(f"Loaded config from {path}")
                break
            except Exception as e:
                print_warn(f"Could not load config from {path}: {e}")
    return defaults

def print_stat_block(stats_text):
    """Pretty-print `git --stat` output, colourising file names and +/- bars."""
    for line in stats_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if "|" in line and not stripped.startswith("commit"):
            name, rest = line.split("|", 1)
            colored = ""
            for ch in rest:
                if ch == "+":
                    colored += f"{c(COLOR_GREEN)}+{c(COLOR_RESET)}"
                elif ch == "-":
                    colored += f"{c(COLOR_RED)}-{c(COLOR_RESET)}"
                else:
                    colored += ch
            print(f"  {c(COLOR_SKY_BLUE)}{name.rstrip()}{c(COLOR_RESET)} |{colored}")
        elif re.search(r"\d+ files? changed", line):
            summary = re.sub(r"(\d+ insertions?\(\+\))", rf"{c(COLOR_GREEN)}\1{c(COLOR_RESET)}", line)
            summary = re.sub(r"(\d+ deletions?\(-\))", rf"{c(COLOR_RED)}\1{c(COLOR_RESET)}", summary)
            print(f"  {c(COLOR_BOLD)}{summary.strip()}{c(COLOR_RESET)}")
        elif stripped.startswith("commit "):
            print(f"  {c(COLOR_MAGENTA)}{c(COLOR_BOLD)}{stripped}{c(COLOR_RESET)}")
        elif stripped.startswith(("Author:", "Date:", "Merge:")):
            print(f"  {c(COLOR_SKY_BLUE)}{stripped}{c(COLOR_RESET)}")
        else:
            print(f"  {line}")

def show_changed_files_viewer():
    """List staged, unstaged and untracked files (no file contents)."""
    staged, unstaged, untracked, details = get_git_files(include_details=True)
    total = len(staged) + len(unstaged) + len(untracked)
    if total == 0:
        print_success("Working tree is clean - no changed files.")
        return

    print_info(f"Changed files: {total}")

    if staged:
        print(f"\n  {c(COLOR_GREEN)}{c(COLOR_BOLD)}Staged ({len(staged)}):{c(COLOR_RESET)}")
        for f in staged:
            info = details.get(f, {})
            orig = info.get("orig_path")
            label = f"{orig} -> {f}" if orig else f
            print(f"    {c(COLOR_GREEN)}+{c(COLOR_RESET)} {label}  {c(COLOR_YELLOW)}({info.get('staged', 'staged')}){c(COLOR_RESET)}")

    if unstaged:
        print(f"\n  {c(COLOR_YELLOW)}{c(COLOR_BOLD)}Unstaged / modified ({len(unstaged)}):{c(COLOR_RESET)}")
        for f in unstaged:
            info = details.get(f, {})
            print(f"    {c(COLOR_YELLOW)}~{c(COLOR_RESET)} {f}  {c(COLOR_YELLOW)}({info.get('unstaged', 'modified')}){c(COLOR_RESET)}")

    if untracked:
        print(f"\n  {c(COLOR_RED)}{c(COLOR_BOLD)}Untracked ({len(untracked)}):{c(COLOR_RESET)}")
        for f in untracked:
            print(f"    {c(COLOR_RED)}?{c(COLOR_RESET)} {f}")

    staged_stats = run_git_cmd(["diff", "--cached", "--stat"])
    if staged_stats:
        print(f"\n  {c(COLOR_BOLD)}Staged change summary (+/-):{c(COLOR_RESET)}")
        print_stat_block(staged_stats)

def show_commit_detail(commit_hash):
    """Show one commit's metadata and per-file +/- stats (not the full diff)."""
    out = run_git_cmd(["show", "--stat", "--no-color", "--date=local", commit_hash])
    if not out:
        print_warn(f"Could not read commit {commit_hash}.")
        return
    print()
    print(f"{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}================ COMMIT {commit_hash[:12]} ================{c(COLOR_RESET)}")
    print_stat_block(out)
    input("\nPress Enter to return to the commit list...")

def browse_commit_history(per_page=20):
    """Page through the commit log; open any commit to inspect files changed."""
    total_raw = run_git_cmd(["rev-list", "--count", "HEAD"])
    try:
        total = int(total_raw) if total_raw else 0
    except ValueError:
        total = 0
    if total == 0:
        print_info("No commits found in this repository yet.")
        return

    page_count = (total + per_page - 1) // per_page
    page = 0
    fmt = "%h%x1f%ad%x1f%an%x1f%s"

    while True:
        out = run_git_cmd(
            ["log", f"-n{per_page}", f"--skip={page * per_page}", f"--pretty=format:{fmt}", "--date=short"],
            strip=False
        ) or ""
        commits = []
        for record in out.replace("\r", "").split("\n"):
            if not record.strip():
                continue
            parts = record.split("\x1f")
            if len(parts) == 4:
                commits.append(parts)

        print()
        print(f"{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}--- Commit History (page {page + 1}/{page_count}, {total} commits) ---{c(COLOR_RESET)}")
        if not commits:
            print("  (no commits on this page)")
        for idx, (h, date, author, subject) in enumerate(commits, 1):
            print(f"  {c(COLOR_BOLD)}{idx:>2}{c(COLOR_RESET)}) {c(COLOR_YELLOW)}{h}{c(COLOR_RESET)} {c(COLOR_SKY_BLUE)}{date}{c(COLOR_RESET)} {subject} {c(COLOR_GREEN)}({author}){c(COLOR_RESET)}")

        nav = []
        if page > 0:
            nav.append("p=prev")
        if page < page_count - 1:
            nav.append("n=next")
        hint = "  Enter a commit number to open it"
        if nav:
            hint += ", " + ", ".join(nav)
        print(f"\n{hint}")
        print("  b) Back to viewer menu")

        choice = input(f"\n{c(COLOR_CYAN)}History choice [n/p/b or number] [b]:{c(COLOR_RESET)} ").strip().lower()
        if choice in ("", "b"):
            return
        if choice == "n":
            if page < page_count - 1:
                page += 1
            else:
                print_info("Already on the last page.")
            continue
        if choice == "p":
            if page > 0:
                page -= 1
            else:
                print_info("Already on the first page.")
            continue
        if choice.isdigit():
            num = int(choice)
            if 1 <= num <= len(commits):
                show_commit_detail(commits[num - 1][0])
                continue
        print_warn("Invalid selection.")

def browse_git_viewer():
    """Interactive, read-only viewer for changed files and past commits.

    Returns None when the user chooses Back so the caller can continue.
    """
    while True:
        print()
        print(f"{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}================ CommitGen Viewer ================{c(COLOR_RESET)}")
        print(f"  {c(COLOR_BOLD)}1{c(COLOR_RESET)}) Changed files (staged / unstaged / untracked)")
        print(f"  {c(COLOR_BOLD)}2{c(COLOR_RESET)}) Commit history (browse past commits)")
        print(f"  {c(COLOR_BOLD)}3{c(COLOR_RESET)}) Back to start")
        choice = input(f"\n{c(COLOR_CYAN)}Viewer choice [1-3] [3]:{c(COLOR_RESET)} ").strip().lower()
        if choice == "1":
            show_changed_files_viewer()
        elif choice == "2":
            browse_commit_history()
        else:
            return None

def prompt_startup_mode():
    """Top-level startup menu: commit, open the viewer, or quit.

    Returns 'commit', 'viewer' or 'quit'. Defaults to 'commit' on Enter so the
    common path adds no friction.
    """
    print(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}What would you like to do?{c(COLOR_RESET)}")
    print(f"  {c(COLOR_GREEN)}{c(COLOR_BOLD)}1{c(COLOR_RESET)}) Commit changes (AI commit generator) {c(COLOR_GREEN)}[Default]{c(COLOR_RESET)}")
    print(f"  {c(COLOR_BOLD)}2{c(COLOR_RESET)}) Browse changes & commit history (viewer)")
    print(f"  {c(COLOR_RED)}{c(COLOR_BOLD)}0{c(COLOR_RESET)}) Quit")
    choice = input(f"\n{c(COLOR_CYAN)}Choice [1/2/0] [1]:{c(COLOR_RESET)} ").strip().lower()
    if choice in ('2', 'v', 'viewer', 'browse'):
        return 'viewer'
    if choice in ('0', 'q', 'quit', 'exit'):
        return 'quit'
    return 'commit'


def prompt_select_model(current_model="gemini-3.5-flash-lite"):
    """Ask user which Gemini model to use, defaulting to 3.5 (gemini-3.5-flash-lite), with options to select 3.1 or custom."""
    print(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}Select Gemini Model:{c(COLOR_RESET)}")
    print(f"  {c(COLOR_BOLD)}1{c(COLOR_RESET)}) gemini-3.5-flash-lite {c(COLOR_GREEN)}[Default / 3.5]{c(COLOR_RESET)}")
    print(f"  {c(COLOR_BOLD)}2{c(COLOR_RESET)}) gemini-3.1-flash-lite [3.1]")
    print(f"  {c(COLOR_BOLD)}3{c(COLOR_RESET)}) Custom model")

    choice = input(f"\n{c(COLOR_CYAN)}Choice [1-3] [1]:{c(COLOR_RESET)} ").strip().lower()
    if choice in ('1', ''):
        return "gemini-3.5-flash-lite"
    elif choice in ('2', '3.1'):
        return "gemini-3.1-flash-lite"
    elif choice in ('3', 'c', 'custom'):
        custom = input(f"{c(COLOR_CYAN)}Enter model name: {c(COLOR_RESET)}").strip()
        return custom if custom else "gemini-3.5-flash-lite"
    else:
        if choice in ("3.5", "gemini-3.5", "gemini-3.5-flash-lite"):
            return "gemini-3.5-flash-lite"
        elif choice in ("3.1", "gemini-3.1", "gemini-3.1-flash-lite"):
            return "gemini-3.1-flash-lite"
        elif choice:
            return choice
        return "gemini-3.5-flash-lite"


def check_dependencies():
    """Check required tools are available and warn about optional ones."""
    if sys.version_info < (3, 6):
        print_error("Python 3.6 or higher is required.")
        sys.exit(1)
    if not shutil.which("git"):
        print_error("Git is not installed or not in PATH.")
        sys.exit(1)
    if not shutil.which("gh"):
        print_warn("GitHub CLI (gh) not found — PR creation and CI monitoring will be unavailable.")

def is_binary_file(filepath):
    """Detect binary files by scanning the first 1 KB for null bytes."""
    try:
        with open(filepath, 'rb') as f:
            return b'\0' in f.read(1024)
    except (IOError, OSError):
        return True

def check_precommit_hooks(non_interactive=False):
    """Run pre-commit hooks if .pre-commit-config.yaml exists."""
    if not os.path.exists('.pre-commit-config.yaml'):
        return True
    print_info("Running pre-commit hooks...")
    try:
        subprocess.run(["pre-commit", "run", "--all-files"], check=True)
        print_success("Pre-commit hooks passed!")
        return True
    except subprocess.CalledProcessError:
        print_error("Pre-commit hooks failed!")
        if non_interactive:
            print_warn("Non-interactive mode: aborting commit because pre-commit hooks failed.")
            return False
        choice = input("Commit anyway? (y/n) [n]: ").strip().lower()
        return choice == 'y'
    except FileNotFoundError:
        print_warn("pre-commit not installed. Skipping hooks.")
        return True

def detect_conventional_commits_usage():
    """Return True if >50% of recent commits use conventional format."""
    log = run_git_cmd(["log", "--oneline", "-20"])
    if not log:
        return False
    pattern = re.compile(r'^[0-9a-f]+ (feat|fix|docs|style|refactor|test|chore|perf|ci|build|revert)(\(.+\))?:')
    lines = log.strip().split('\n')
    matches = sum(1 for l in lines if pattern.match(l))
    return len(lines) > 0 and matches / len(lines) > 0.5

def is_ci_environment():
    """Detect if running inside a CI/CD system."""
    return any(os.getenv(v) for v in ['CI', 'GITHUB_ACTIONS', 'GITLAB_CI', 'JENKINS_URL', 'TRAVIS'])

def session_state_path():
    """Absolute path of the crash-recovery session state file."""
    return os.path.join(GIT_DIR, 'COMMITGEN_STATE')

def save_session_state(state):
    """Persist session state under the repository's .git dir for crash recovery."""
    try:
        with open(session_state_path(), 'w', encoding='utf-8') as f:
            json.dump(state, f)
    except Exception:
        pass

def load_session_state():
    """Load a previously saved session state if it exists."""
    path = session_state_path()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None

def clear_session_state():
    """Delete the persisted session state file."""
    path = session_state_path()
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

def show_startup_banner():
    """Display a clear startup banner with project context."""
    print(f"{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}{'='*60}{c(COLOR_RESET)}")
    print(f"{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}  CommitGen - AI-Powered Git Commit Generator{c(COLOR_RESET)}")
    print(f"{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}{'='*60}{c(COLOR_RESET)}")
    dir_path = os.path.abspath(os.getcwd())
    folder_name = os.path.basename(dir_path) or dir_path
    print_info(f"📁 Folder: {c(COLOR_GREEN)}{folder_name}{c(COLOR_RESET)}")
    print_info(f"📍 Dir Path: {c(COLOR_CYAN)}{dir_path}{c(COLOR_RESET)}")

def show_folder_structure(startpath, max_depth=3, max_files_per_dir=10):
    """Display a clean tree view of the project structure."""
    ignored_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 
                    'env', '.idea', '.vscode', 'dist', 'build', '.next',
                    '.pytest_cache', '.mypy_cache', '.tox', 'egg-info', '.eggs'}
    
    total_dirs = 0
    total_files = 0
    
    def tree(dir_path, prefix="", depth=0):
        nonlocal total_dirs, total_files
        
        if depth >= max_depth:
            print(f"{prefix}└── ... (max depth reached)")
            return
        
        try:
            contents = sorted(os.listdir(dir_path))
        except PermissionError:
            print(f"{prefix}└── [Permission Denied]")
            return
        except Exception:
            return
        
        # Separate directories and files
        dirs = []
        files = []
        for item in contents:
            if item in ignored_dirs or item.startswith('.'):
                continue
            full_path = os.path.join(dir_path, item)
            if os.path.isdir(full_path):
                dirs.append(item)
            else:
                files.append(item)
        
        # Show files first (up to max_files_per_dir)
        for i, file in enumerate(files):
            if i >= max_files_per_dir:
                remaining = len(files) - max_files_per_dir
                print(f"{prefix}├── ... ({remaining} more files)")
                break
            is_last_file = (i == len(files) - 1 or i == max_files_per_dir - 1) and not dirs
            connector = "└── " if is_last_file else "├── "
            print(f"{prefix}{connector}📄 {file}")
            total_files += 1
        
        # Then show directories
        for i, dir_name in enumerate(dirs):
            total_dirs += 1
            is_last = (i == len(dirs) - 1)
            connector = "└── " if is_last else "├── "
            print(f"{prefix}{connector}📁 {c(COLOR_CYAN)}{dir_name}/{c(COLOR_RESET)}")
            
            # Calculate prefix for children
            extension = "    " if is_last else "│   "
            tree(os.path.join(dir_path, dir_name), prefix + extension, depth + 1)
    
    # Start from root
    tree(startpath)
    
    if total_dirs > 0 or total_files > 0:
        print(f"\n  {COLOR_BOLD}Summary:{COLOR_RESET} {total_dirs} directories, {total_files} files")
    else:
        print("  (empty repository)")

def prompt_exit_or_restart(non_interactive=False):
    """Prompt the user to exit or restart CommitGen."""
    global restart_count
    if non_interactive:
        return False
    if restart_count >= MAX_RESTARTS:
        print_warn(f"Maximum restarts ({MAX_RESTARTS}) reached. Exiting.")
        return False
    while True:
        choice = input(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}Press Enter to exit, or 'r' to restart: {c(COLOR_RESET)}").strip().lower()
        if choice == 'r':
            restart_count += 1
            completed_runs = restart_count
            os.system('cls' if os.name == 'nt' else 'clear')
            run_str = f"{completed_runs} run{'s' if completed_runs != 1 else ''} completed"
            print_info(f"Restarting CommitGen... ({run_str} | Max {MAX_RESTARTS} restarts)")
            return True  # Signal to restart
        else:
            return False  # Signal to exit

def main():
    load_dotenv()
    check_dependencies()

    if not init_git_dir():
        print_error("This tool must be run from inside a Git repository.")
        sys.exit(1)

    # Parse flags
    DRY_RUN = "--dry-run" in sys.argv
    NON_INTERACTIVE = "--non-interactive" in sys.argv or is_ci_environment()

    cli_model = None
    cli_co_author = None
    if "--co-author" in sys.argv:
        cli_co_author = True
    elif "--no-co-author" in sys.argv:
        cli_co_author = False

    for i, arg in enumerate(sys.argv):
        if arg.startswith("--model="):
            cli_model = arg.split("=", 1)[1]
        elif arg == "--model" and i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
            cli_model = sys.argv[i + 1]
        elif arg in ("--3.1", "--gemini-3.1"):
            cli_model = "gemini-3.1-flash-lite"
        elif arg in ("--3.5", "--gemini-3.5"):
            cli_model = "gemini-3.5-flash-lite"

    if DRY_RUN:
        print_info("DRY RUN MODE — no changes will be committed or pushed.")
    if NON_INTERACTIVE:
        print_info("Running in non-interactive mode.")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print_error("GEMINI_API_KEY not found in environment or .env file.")
        print_info("Please create a .env file containing: GEMINI_API_KEY=your_key_here")
        sys.exit(1)

    config = load_config()

    # Staged-file status details; populated by get_git_files(include_details=True)
    # and reused by the review screen. Initialized here so every code path
    # (including resumed sessions) has a safe default.
    details = {}

    show_startup_banner()

    # Interactive startup: choose between committing and browsing history.
    # Non-interactive runs, and runs with an explicit --model, skip straight to
    # committing so automation is unaffected.
    if not cli_model and not NON_INTERACTIVE:
        while True:
            mode = prompt_startup_mode()
            if mode == 'quit':
                print_info("Exiting CommitGen.")
                return False
            if mode == 'viewer':
                browse_git_viewer()
                continue
            break

    if cli_model:
        model = cli_model
    elif NON_INTERACTIVE:
        model = os.getenv("GEMINI_MODEL", config["model"])
    else:
        model_default = os.getenv("GEMINI_MODEL", config["model"])
        model = prompt_select_model(model_default)

    MAX_DIFF_SIZE = config["max_diff_length"]

    # 1. Print Google Gemini model name
    print_info(f"🤖 AI Model: {c(COLOR_GREEN)}Google Gemini - {model}{c(COLOR_RESET)}")
    
    # 2. Print active branch
    branch_name = run_git_cmd(["branch", "--show-current"])
    if not branch_name:
        branch_name = run_git_cmd(["rev-parse", "--short", "HEAD"]) or "unknown"
    upstream = run_git_cmd(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    sync_status = ""
    if upstream:
        counts = run_git_cmd(["rev-list", "--left-right", "--count", "HEAD...@{u}"])
        if counts:
            parts = counts.split()
            if len(parts) == 2:
                ahead, behind = parts[0], parts[1]
                sync_parts = []
                if int(ahead) > 0:
                    sync_parts.append(f"{c(COLOR_GREEN)}ahead {ahead}{c(COLOR_RESET)}")
                if int(behind) > 0:
                    sync_parts.append(f"{c(COLOR_YELLOW)}behind {behind}{c(COLOR_RESET)}")
                if sync_parts:
                    sync_status = f" [{', '.join(sync_parts)}]"
                else:
                    sync_status = f" [{c(COLOR_GREEN)}up to date{c(COLOR_RESET)}]"
    print_info(f"🌿 Branch: {c(COLOR_GREEN)}{branch_name}{c(COLOR_RESET)}{sync_status}")
    
    # 3. Show folder structure
    print_info("📂 Repository Structure:")
    show_folder_structure(".")
    print()  # Extra newline for spacing

    # 4. Print Configuration Summary
    print_info(f"Configuration: diff limit={MAX_DIFF_SIZE//1000}K chars, default bump={config.get('default_bump', 'patch')}")

    # Startup Git Pull Check (Stash uncommitted/staged changes, pull rebase, and restore stash)
    remotes = run_git_cmd(["remote"])
    if remotes and config.get("auto_pull", True):
        default_start_pull_prompt = "n"
        while True:
            if NON_INTERACTIVE:
                do_start_pull = False
            else:
                prompt_label = f"Pull latest changes from remote at startup? (y/n) [{default_start_pull_prompt}]:"
                raw_choice = input(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}{prompt_label}{c(COLOR_RESET)} ").strip().lower()
                pull_start_choice = raw_choice if raw_choice != "" else default_start_pull_prompt
                do_start_pull = (pull_start_choice in ['y', 'yes'])
            
            if not do_start_pull:
                break

            print_info("Checking and pulling latest changes from remote at startup (git pull --rebase)...")
            stashed = False
            # Check if there are any uncommitted / staged / untracked changes to stash
            status_out = run_git_cmd(["status", "--porcelain"])
            if status_out:
                print_info("Stashing uncommitted/staged changes before pull...")
                stash_res = run_git_cmd(["stash", "--include-untracked"])
                if stash_res and "No local changes to save" not in stash_res:
                    stashed = True
            
            pull_res = perform_git_pull()
            retry_startup = False
            if pull_res.returncode == 0:
                print_success("Startup pull complete.")
            else:
                if "no tracking information" in pull_res.stderr.lower() or "no upstream branch" in pull_res.stderr.lower():
                    print_info("No upstream tracking branch configured. Skipping startup pull.")
                elif "cannot pull with rebase: you have unstaged changes" in pull_res.stderr.lower() or "please commit or stash them" in pull_res.stderr.lower():
                    print_error(f"Startup pull failed:\n{pull_res.stderr.strip()}")
                    default_start_pull_prompt = "n"
                    retry_startup = True
                else:
                    print_warn(f"Startup pull encountered warnings/errors:\n{pull_res.stderr.strip()}")
            
            if stashed:
                print_info("Restoring stashed changes...")
                pop_res = subprocess.run(["git", "stash", "pop"], capture_output=True, text=True)
                if pop_res.returncode != 0:
                    print_error(f"Conflict encountered while restoring stashed changes:\n{pop_res.stderr.strip()}")
                    print_error("Aborting commit process. Please resolve stash conflicts manually before running CommitGen.")
                    return False
                else:
                    print_success("Stashed changes restored successfully.")

            if retry_startup:
                continue
            break
            # print()  # Visual spacing after startup pull

    # Session recovery
    saved_state = load_session_state()
    if saved_state and NON_INTERACTIVE:
        # Non-interactive mode should always start fresh — don't resume interactively
        print_info("Clearing saved session (non-interactive mode).")
        clear_session_state()
        saved_state = None
    elif saved_state:
        print_info("Found a saved session from a previous run!")
        resume = input("Resume previous session? (y/n) [n]: ").strip().lower()
        if resume == 'y':
            summary = saved_state.get('summary', '')
            description = saved_state.get('description', '')
            bump_choice = saved_state.get('bump_choice', config["default_bump"])
            staged = drop_env_files(saved_state.get('staged', []))
            curr_version = saved_state.get('curr_version', '0.0.0')
            commit_mode = saved_state.get('commit_mode', 'new')
            model = saved_state.get('model', model)
            prompt_text = saved_state.get('prompt_text', '')
            user_context = ""  # not available in resumed session
            diff = "(Diff not available — resumed from saved session)"
            print_info(f"Resumed. Staged: {staged}")
        else:
            clear_session_state()
            saved_state = None

    # 1. Git Status & Interactive Staging (always show picker)
    if not saved_state or not saved_state.get('summary'):
        staged, unstaged, untracked = get_git_files()
        if not staged and not unstaged and not untracked:
            print_success("Nothing to commit, working tree clean.")
            return prompt_exit_or_restart(NON_INTERACTIVE)

        if NON_INTERACTIVE:
            # In CI mode: auto-stage all and proceed
            for f in unstaged + untracked:
                run_git_cmd(["add", f])
            staged, _, _, details = get_git_files(include_details=True)
        else:
            if prompt_stage_files(staged, unstaged, untracked) == "quit":
                return prompt_exit_or_restart(NON_INTERACTIVE)
            staged, _, _, details = get_git_files(include_details=True)

        if not staged:
            print_warn("No files staged for commit.")
            return prompt_exit_or_restart(NON_INTERACTIVE)

        print_info("Staged files for commit:")
        for f in staged:
            st_info = details.get(f, {}).get("staged", "staged")
            orig = details.get(f, {}).get("orig_path")
            display_name = f"{orig} -> {f}" if orig else f
            print(f"  - [{c(COLOR_GREEN)}{st_info}{c(COLOR_RESET)}] {display_name}")

        # Warn about binary files
        binary_files = [f for f in staged if os.path.exists(f) and is_binary_file(f)]
        if binary_files:
            print_warn(f"Binary files detected (excluded from AI diff): {', '.join(binary_files)}")

        show_commit_stats(staged)

        # 2. Commit Mode Selection (NEW: Amend option)
        if NON_INTERACTIVE:
            commit_mode = 'new'
        else:
            # Check if there's a previous commit to amend
            last_commit = run_git_cmd(["log", "-1", "--oneline"])
            if last_commit:
                commit_mode = prompt_amend_or_new()
            else:
                commit_mode = 'new'
                print_info("No previous commit found — creating new commit.")

        # 3. Detect & Display Version Info
        if commit_mode == 'new':
            curr_version, ver_source = detect_version()
            print_info(f"Detected current version: {curr_version} (from {ver_source})")
        else:
            # For amend, get current version from last tag or files
            curr_version, ver_source = detect_version()
            print_info(f"Current version for reference: {curr_version} (from {ver_source})")
            print_info(f"Mode: {'Amending last commit (will add staged changes)' if commit_mode == 'amend' else 'Fresh amend (will replace last commit message)'}")

        branch_info = get_branch_version_info()
        if branch_info:
            print_info(f"Current branch: {branch_info}")

        # Detect repo conventions
        if detect_conventional_commits_usage():
            print_info("Repo uses conventional commits — AI will follow that format.")

        # Pre-commit hooks (skip for amend mode if no new files staged beyond what was already committed)
        if commit_mode == 'new' or staged:
            if not check_precommit_hooks(NON_INTERACTIVE):
                print_info("Commit cancelled due to hook failures.")
                return False

        # 4. User Input Context
        if NON_INTERACTIVE:
            user_context = ""
        else:
            user_context = input(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}Optional - Enter context/notes for the commit (press Enter to skip):{c(COLOR_RESET)} ").strip()

        # 5. Git Diff (exclude image/binary files)
        if commit_mode == 'fresh_amend' and run_git_cmd(["rev-parse", "--verify", "HEAD~1"]):
            # Get combined files of the last commit and currently staged changes
            amend_files = run_git_cmd(["diff", "--name-only", "--cached", "HEAD~1"])
            staged_files_for_prompt = [f.strip() for f in amend_files.split('\n') if f.strip()]
            non_binary_staged = [f for f in staged_files_for_prompt if not should_exclude_from_ai(f)]
            if non_binary_staged:
                diff = run_git_cmd(["diff", "--cached", "--diff-filter=ACMRT", "HEAD~1", "--"] + non_binary_staged)
            else:
                diff = ""
        else:
            staged_files_for_prompt = staged
            non_binary_staged = [f for f in staged if not should_exclude_from_ai(f)]
            if commit_mode == 'amend':
                # For amend, get diff of staged changes against last commit
                if non_binary_staged:
                    diff = run_git_cmd(["diff", "--cached", "--diff-filter=ACMRT", "--"] + non_binary_staged)
                else:
                    diff = ""
                
                if not diff and not staged:
                    print_warn("No new changes staged for amend. Amending message only.")
                    diff = "(No new changes — amending commit message only)"
                elif not diff:
                    diff = "(No text diff — all changes are in binary/image files or are otherwise empty.)"
            else:
                # new commit
                if non_binary_staged:
                    diff = run_git_cmd(["diff", "--cached", "--diff-filter=ACMRT", "--"] + non_binary_staged)
                else:
                    diff = ""
                
                if not diff:
                    print_warn("No text diff available (binary-only or empty diff). Proceeding with file list only.")
                    diff = "(No text diff — all changes are in binary/image files or are otherwise empty.)"

        # Truncate diff if extremely large to save tokens/cost
        max_input_tokens = config.get("max_input_tokens", 900000)
        diff, was_truncated = truncate_diff_for_context(diff, MAX_DIFF_SIZE, max_input_tokens)
        if was_truncated:
            print_warn("Some changes were omitted due to context or configured size limits.")
            print_info("Consider committing smaller, focused changes for better AI analysis.")

    if not saved_state or not saved_state.get('summary'):
        # 6. Commit Message Generation
        if commit_mode == 'amend':
            # For amend mode, preserve existing message and allow user to edit/add
            last_msg = get_last_commit_message()
            if last_msg:
                lines = last_msg.split('\n')
                summary = lines[0].strip()
                # If there's a description (separated by empty line(s)), preserve it
                description_lines = []
                found_empty = False
                for line in lines[1:]:
                    if not line.strip() and not found_empty:
                        found_empty = True
                        continue
                    if found_empty or line.strip():
                        description_lines.append(line)
                description = '\n'.join(description_lines).strip()
                
                print_info("Using existing commit message as base. You'll be able to edit it.")
                print_info(f"Current summary: {summary}")
                if description:
                    print_info(f"Current description: {description[:100]}...")
            else:
                summary = "update: code modifications"
                description = ""
        else:
            # For new commits and fresh_amend, generate via AI
            recent_commits = get_recent_commits(3)
            recent_context = f"\nRecent Commits History:\n{recent_commits}\n" if recent_commits else ""

            template = load_commit_template()
            template_context = f"\nProject Commit Template/Guidelines:\n{template}\nPlease follow these guidelines.\n" if template else ""

            issues = extract_issue_references()
            issues_context = f"\nRelated Issues: {', '.join(['#' + i for i in issues])}\n(Please include these issue numbers if relevant)\n" if issues else ""
            
            scope = detect_conventional_scope(staged_files_for_prompt)
            scope_context = f"\nDetected scope: {', '.join(scope)}\n" if scope else ""

            print_info(f"Generating commit message via Gemini ({c(COLOR_MAGENTA)}{model}{c(COLOR_RESET)})...")
            prompt_diff = redact_secrets(diff)
            prompt_text = f"""
You are a git commit message generator helper.
Analyze the following git diff and user provided context, then output a structured commit summary and description.
Use conventional commit format if possible (feat:, fix:, docs:, style:, refactor:, test:, chore:, etc.).

Formatting requirements for the description:
- Every bullet point must be on a new line starting with '- '.
- Do NOT merge multiple points/sentences into a single line or paragraph.

Staged files:
{", ".join(staged_files_for_prompt)}
{recent_context}{template_context}{scope_context}{issues_context}
User custom context / notes:
{user_context if user_context else "(None provided)"}

Git Diff:
```diff
{prompt_diff}
```
"""
            while True:
                try:
                    ai_res = call_gemini_api(api_key, model, prompt_text)
                    summary = ai_res.get("summary", "").strip()
                    description = ai_res.get("description", "").strip()
                    print_token_usage(ai_res)
                    break
                except Exception as e:
                    print_error(f"Gemini API request failed ({e})")
                    
                    if NON_INTERACTIVE:
                        # In non-interactive/CI mode, fall back to defaults directly
                        if commit_mode == 'fresh_amend':
                            last_msg = get_last_commit_message()
                            if last_msg:
                                lines = last_msg.split('\n')
                                summary = lines[0] if lines else "update: code modifications"
                                description = '\n'.join(lines[1:]) if len(lines) > 1 else "- Minor changes/updates"
                            else:
                                summary = "update: code modifications"
                                description = "- Minor changes/updates"
                        else:
                            summary = "update: code modifications"
                            description = "- Minor changes/updates"
                        break
                    
                    print(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}API call failed. How would you like to proceed?{c(COLOR_RESET)}")
                    print(f"  {c(COLOR_BOLD)}1{c(COLOR_RESET)}) Retry Gemini AI call (wait 30s)")
                    print(f"  {c(COLOR_BOLD)}2{c(COLOR_RESET)}) Enter custom summary and description manually")
                    print(f"  {c(COLOR_BOLD)}3{c(COLOR_RESET)}) Use default summary/description ('update: code modifications')")
                    
                    fail_choice = input(f"\nAction [1/2/3] [1]: ").strip()
                    if not fail_choice or fail_choice == '1':
                        print_info("Waiting 30 seconds before retrying Gemini API...")
                        time.sleep(30)
                        print_info("Retrying Gemini AI call...")
                        continue
                    elif fail_choice == '2':
                        summary = input("Enter summary: ").strip()
                        if not summary:
                            summary = "update: code modifications"
                        description = input("Enter description (optional, bullet points): ").strip()
                        break
                    else:
                        if commit_mode == 'fresh_amend':
                            last_msg = get_last_commit_message()
                            if last_msg:
                                lines = last_msg.split('\n')
                                summary = lines[0] if lines else "update: code modifications"
                                description = '\n'.join(lines[1:]) if len(lines) > 1 else "- Minor changes/updates"
                            else:
                                summary = "update: code modifications"
                                description = "- Minor changes/updates"
                        else:
                            summary = "update: code modifications"
                            description = "- Minor changes/updates"
                        break

        # Save session state in case of crash
        save_session_state({
            'summary': summary,
            'description': description,
            'bump_choice': config["default_bump"],
            'staged': staged,
            'curr_version': curr_version,
            'commit_mode': commit_mode,
            'model': model,
            'include_co_author': config.get("co_author", False),
            'prompt_text': prompt_text if 'prompt_text' in locals() else ''
        })
    else:
        # Resumed from saved session
        summary = saved_state.get('summary', 'update: code modifications')
        description = saved_state.get('description', '')
        bump_choice = saved_state.get('bump_choice', config["default_bump"])
        # Saved state may predate the .env guard or reference files that are no
        # longer staged, so re-validate before anything is committed.
        staged = drop_env_files(saved_state.get('staged', []))
        staged_files_for_prompt = staged
        curr_version = saved_state.get('curr_version', '0.0.0')
        commit_mode = saved_state.get('commit_mode', 'new')

    # 7. Interactive Review Loop
    bump_choice = config.get("default_bump", "patch") if not saved_state else saved_state.get('bump_choice', 'patch')

    # Guard: ensure curr_version is always defined (resume path may set it but fresh path skips some branches)
    if 'curr_version' not in locals():
        curr_version = '0.0.0'
    # Guard: ensure user_context, diff, and prompt_text are always defined (resume path may skip them)
    if 'user_context' not in locals():
        user_context = ""
    if 'diff' not in locals():
        diff = "(Diff not available — resumed from saved session)"
    if 'prompt_text' not in locals():
        prompt_text = ""

    # Try to extract version from user context
    ver_match = re.search(r'\b(v?\d+\.\d+\.\d+(?:-[a-zA-Z0-9\.]+)?)\b', user_context)
    if ver_match:
        bump_choice = f"custom:{ver_match.group(1)}"
        print_info(f"Auto-detected version from context: {ver_match.group(1)}")
        if commit_mode == 'new' and is_version_regression(ver_match.group(1), curr_version):
            print_warn(f"Context version '{ver_match.group(1)}' is lower than current version '{curr_version}'.")
            confirm = input("Proceed with this lower version anyway? (y/N) [n]: ").strip().lower()
            if confirm != 'y':
                bump_choice = config.get("default_bump", "patch")
                print_info(f"Reverted to default bump: {bump_choice}")
    
    # Summary of what will be committed before the review loop
    if commit_mode == 'new':
        bump_preview = increment_version(curr_version, bump_choice) if bump_choice != "none" else curr_version
        print_info(f"Preparing commit: {c(COLOR_SKY_BLUE)}{curr_version}{c(COLOR_RESET)} → {c(COLOR_GREEN)}{bump_preview}{c(COLOR_RESET)} ({bump_choice} bump)")
        print_diff_breakdown(staged_files_for_prompt, commit_mode)
        print_info(f"Commit mode: New commit with {'version tag' if bump_choice != 'none' else 'no version bump'}")
    else:
        print_diff_breakdown(staged_files_for_prompt, commit_mode)

    # Co-Authored-By attribution prompt (default: 'n')
    if saved_state and "include_co_author" in saved_state:
        include_co_author = saved_state["include_co_author"]
    elif cli_co_author is not None:
        include_co_author = cli_co_author
    elif NON_INTERACTIVE:
        env_ca = os.getenv("CO_AUTHOR") or os.getenv("CO_AUTHORED_BY")
        include_co_author = env_ca.lower() in ("true", "1", "yes") if env_ca else config.get("co_author", False)
    else:
        env_ca = os.getenv("CO_AUTHOR") or os.getenv("CO_AUTHORED_BY")
        default_ca = "y" if (env_ca and env_ca.lower() in ("true", "1", "yes")) or config.get("co_author", False) else "n"
        ca_prompt = f"Add Co-Authored-By attribution (Custom Tool By BKK ({model}))? (y/n) [{default_ca}]:"
        raw_ca = input(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}{ca_prompt}{c(COLOR_RESET)} ").strip().lower()
        if raw_ca == "":
            raw_ca = default_ca
        include_co_author = (raw_ca in ["y", "yes"])
    
    while True:
        if commit_mode == 'new':
            proposed_version = increment_version(curr_version, bump_choice) if bump_choice != "none" else curr_version
        else:
            # For amend mode, use current version
            proposed_version = curr_version
            bump_choice = "none"
        
        display_summary = summary
        # Detect and strip any existing version prefix from summary (e.g. "v1.2.3 - refactor: ...")
        # Always use the version from the prefix - that's what the user explicitly typed
        existing_ver_match = re.match(r'^(v?\d+(?:\.\d+)+)\s*-\s*', display_summary)
        if existing_ver_match:
            existing_version = existing_ver_match.group(1)
            display_summary = display_summary[existing_ver_match.end():]
            # Always use the version from the user's prefix
            proposed_version = existing_version
        
        # Add version prefix for ALL modes (but prevent duplication)
        if proposed_version:
            version_prefix = f"{proposed_version} - "
            
            # Check if version prefix already exists (avoid duplication)
            if not display_summary.startswith(version_prefix):
                max_summary_len = 72
                max_content_len = max_summary_len - len(version_prefix)
                if len(display_summary) > max_content_len:
                    display_summary = display_summary[:max_content_len-3] + "..."
                display_summary = f"{version_prefix}{display_summary}"
                
        print(f"\n{c(COLOR_MAGENTA)}================ PROPOSED COMMIT ================{c(COLOR_RESET)}")
        print(f"{c(COLOR_BOLD)}Mode:{c(COLOR_RESET)} {'AMEND' if commit_mode in ['amend', 'fresh_amend'] else 'NEW COMMIT'}")
        print(f"{c(COLOR_BOLD)}Files to commit:{c(COLOR_RESET)}")
        for f in staged:
            st_info = details.get(f, {}).get("staged", "staged") if 'details' in locals() and details else "staged"
            orig = details.get(f, {}).get("orig_path") if 'details' in locals() and details else None
            display_name = f"{orig} -> {f}" if orig else f
            print(f"  - [{c(COLOR_GREEN)}{st_info}{c(COLOR_RESET)}] {display_name}")
        if commit_mode == 'new':
            print(f"\n{c(COLOR_BOLD)}Version Bump:{c(COLOR_RESET)} {curr_version} -> {proposed_version} ({bump_choice})")
        else:
            print(f"\n{c(COLOR_BOLD)}Version:{c(COLOR_RESET)} {curr_version} (amend mode - version unchanged)")
        print(f"\n{c(COLOR_BOLD)}Commit Message:{c(COLOR_RESET)}")
        print(f"  {c(COLOR_GREEN)}{display_summary}{c(COLOR_RESET)}")
        if description:
            formatted_desc = "\n".join(f"  {line}" if line.strip() else "" for line in description.split("\n"))
            print(f"\n{formatted_desc}")
        if include_co_author:
            co_author_trailer = format_co_author_trailer(model)
            if not description or co_author_trailer not in description:
                print(f"\n  {c(COLOR_CYAN)}{co_author_trailer}{c(COLOR_RESET)}")
        print(f"{c(COLOR_MAGENTA)}================================================={c(COLOR_RESET)}")

        validation_issues = validate_commit_message(f"{display_summary}\n\n{description}" if description else display_summary)
        if commit_mode == 'new' and bump_choice != "none" and proposed_version != curr_version:
            if version_already_tagged(proposed_version):
                validation_issues.append(f"Proposed version '{proposed_version}' already exists as a local Git tag!")

        if validation_issues:
            print(f"\n{c(COLOR_YELLOW)}{c(COLOR_BOLD)}Validation Warnings:{c(COLOR_RESET)}")
            for issue in validation_issues:
                print(f"  - {c(COLOR_YELLOW)}{issue}{c(COLOR_RESET)}")

        print(f"\nOptions:")
        print(f"  {c(COLOR_GREEN)}{c(COLOR_BOLD)}c{c(COLOR_RESET)}) Commit")
        print(f"  {c(COLOR_YELLOW)}{c(COLOR_BOLD)}e{c(COLOR_RESET)}) Edit message")
        if commit_mode == 'new':
            print(f"  {c(COLOR_CYAN)}{c(COLOR_BOLD)}g{c(COLOR_RESET)}) Regenerate message (AI)")
            print(f"  {c(COLOR_MAGENTA)}{c(COLOR_BOLD)}m{c(COLOR_RESET)}) Switch Gemini model (current: {model})")
            print(f"  {c(COLOR_CYAN)}{c(COLOR_BOLD)}h{c(COLOR_RESET)}) Version history")
            print(f"  {c(COLOR_YELLOW)}{c(COLOR_BOLD)}v{c(COLOR_RESET)}) Change version bump")
        else:
            print(f"  {c(COLOR_MAGENTA)}{c(COLOR_BOLD)}m{c(COLOR_RESET)}) Switch Gemini model (current: {model})")
        print(f"  {c(COLOR_BLUE)}{c(COLOR_BOLD)}t{c(COLOR_RESET)}) Toggle Co-Authored-By attribution (currently: {'yes' if include_co_author else 'no'})")
        print(f"  {c(COLOR_CYAN)}{c(COLOR_BOLD)}d{c(COLOR_RESET)}) View diff")
        print(f"  {c(COLOR_BOLD)}w{c(COLOR_RESET)}) Browse changes & commit history")
        print(f"  {c(COLOR_MAGENTA)}{c(COLOR_BOLD)}s{c(COLOR_RESET)}) Spell check")
        print(f"  {c(COLOR_RED)}{c(COLOR_BOLD)}x{c(COLOR_RESET)}) Cancel")

        if NON_INTERACTIVE:
            action = "c"
        else:
            action = input(f"\nAction [c/e/{'g/m/h/v/' if commit_mode == 'new' else 'm/'}w/t/d/s/x] [c]: ").strip().lower()
            if not action:
                action = "c"

        if action == "c":
            break
        elif action == "x":
            print_info("Commit cancelled.")
            clear_session_state()
            return prompt_exit_or_restart(NON_INTERACTIVE)
        elif action == "t":
            include_co_author = not include_co_author
            print_info(f"Co-Authored-By attribution {'enabled' if include_co_author else 'disabled'}.")
            save_session_state({
                'summary': summary,
                'description': description,
                'bump_choice': bump_choice,
                'staged': staged,
                'curr_version': curr_version,
                'commit_mode': commit_mode,
                'model': model,
                'include_co_author': include_co_author,
                'prompt_text': prompt_text if 'prompt_text' in locals() else ''
            })
            continue
        elif action == "m":
            new_model = prompt_select_model(model)
            if new_model != model:
                model = new_model
                print_success(f"Switched model to: {model}")
                save_session_state({
                    'summary': summary,
                    'description': description,
                    'bump_choice': bump_choice,
                    'staged': staged,
                    'curr_version': curr_version,
                    'commit_mode': commit_mode,
                    'model': model,
                    'include_co_author': include_co_author,
                    'prompt_text': prompt_text if 'prompt_text' in locals() else ''
                })
                if commit_mode == 'new' and prompt_text:
                    regen = input(f"{c(COLOR_CYAN)}Regenerate commit message using {model}? (y/N) [y]:{c(COLOR_RESET)} ").strip().lower()
                    if regen != 'n':
                        print_info(f"Regenerating commit message via Gemini ({c(COLOR_MAGENTA)}{model}{c(COLOR_RESET)})...")
                        try:
                            ai_res = call_gemini_api(api_key, model, prompt_text)
                            summary = ai_res.get("summary", "").strip()
                            description = ai_res.get("description", "").strip()
                            print_token_usage(ai_res)
                            print_success("New commit message generated!")
                        except Exception as e:
                            print_error(f"Regeneration failed: {e}")
            else:
                print_info(f"Model unchanged: {model}")
            continue
        elif action == "d":
            try:
                subprocess.run(["less", "-R"], input=diff, text=True)
            except FileNotFoundError:
                try:
                    subprocess.run(["more"], input=diff, text=True)
                except FileNotFoundError:
                    print(diff)
            continue
        elif action == "w":
            browse_git_viewer()
            continue
        elif action == "s":
            misspelled = check_spelling(f"{summary}\n{description}")
            if misspelled:
                print_warn("Possible spelling issues:")
                for word in misspelled:
                    print(f"  - {word}")
            else:
                print_success("No spelling issues detected (or aspell not installed)")
            input("\nPress Enter to continue...")
            continue
        elif action == "e":
            new_summary = input(f"New summary [{summary}]: ").strip()
            if new_summary:
                summary = new_summary
            
            print("Enter description (press Enter on empty line to finish):")
            desc_lines = []
            while True:
                line = input()
                if not line and desc_lines:
                    break
                if not line and not desc_lines:
                    # Allow empty description
                    break
                desc_lines.append(line)
            
            if desc_lines:
                description = "\n".join(desc_lines)
        elif action == "h" and commit_mode == 'new':
            # Show git tag history with dates
            tags_with_dates = run_git_cmd(["tag", "-l", "--sort=-v:refname", "--format=%(refname:short)|%(creatordate:short)"])
            if tags_with_dates:
                print_info("Version History (git tags):")
                tag_lines = tags_with_dates.split('\n')[:15]
                for line in tag_lines:
                    if not line.strip():
                        continue
                    parts = line.split('|', 1)
                    tag = parts[0].strip()
                    date = parts[1].strip() if len(parts) > 1 else "unknown"
                    print(f"  {c(COLOR_GREEN)}{tag}{c(COLOR_RESET)} ({date})")
                if len(tags_with_dates.split('\n')) > 15:
                    print(f"  ... and more")
            else:
                print_info("No version tags found.")
            input("\nPress Enter to continue...")
            continue
        elif action == "g" and commit_mode == 'new':
            if not prompt_text:
                print_warn("Cannot regenerate - original prompt not available (session resumed).")
                continue
            print_info(f"Regenerating commit message via Gemini ({c(COLOR_MAGENTA)}{model}{c(COLOR_RESET)})...")
            try:
                ai_res = call_gemini_api(api_key, model, prompt_text)
                summary = ai_res.get("summary", "").strip()
                description = ai_res.get("description", "").strip()
                print_token_usage(ai_res)
                print_success("New commit message generated!")
            except Exception as e:
                print_error(f"Regeneration failed: {e}")
            continue
        elif action == "v" and commit_mode == 'new':
            # Show all bump options with previews
            print(f"\n{c(COLOR_CYAN)}Current version: {curr_version}{c(COLOR_RESET)}")
            
            patch_ver = increment_version(curr_version, "patch")
            minor_ver = increment_version(curr_version, "minor")
            major_ver = increment_version(curr_version, "major")
            
            print(f"  {c(COLOR_GREEN)}{c(COLOR_BOLD)}p{c(COLOR_RESET)}) 🔧 Patch → {c(COLOR_GREEN)}{patch_ver}{c(COLOR_RESET)}")
            print(f"  {c(COLOR_CYAN)}{c(COLOR_BOLD)}m{c(COLOR_RESET)}) ✨ Minor → {c(COLOR_CYAN)}{minor_ver}{c(COLOR_RESET)}")
            print(f"  {c(COLOR_MAGENTA)}{c(COLOR_BOLD)}j{c(COLOR_RESET)}) 🚀 Major → {c(COLOR_MAGENTA)}{major_ver}{c(COLOR_RESET)}")
            print(f"  {c(COLOR_YELLOW)}{c(COLOR_BOLD)}n{c(COLOR_RESET)}) ⏸️  None  → {c(COLOR_YELLOW)}{curr_version}{c(COLOR_RESET)} (keep current)")
            print(f"  {c(COLOR_BOLD)}c{c(COLOR_RESET)}) ✏️  Custom version")
            
            v_bump = input("\nSelect bump type [p/m/j/n/c] [p]: ").strip().lower()
            if v_bump == 'm':
                bump_choice = "minor"
            elif v_bump == 'j':
                bump_choice = "major"
            elif v_bump == 'n':
                bump_choice = "none"
            elif v_bump == 'c':
                custom_ver = input("Enter custom version (e.g. 1.2.3): ").strip()
                if custom_ver:
                    if not custom_ver.lower().startswith('v'):
                        custom_ver = "v" + custom_ver
                    if not is_valid_semver(custom_ver):
                        print_warn("Warning: custom version doesn't follow strict SemVer (X.Y.Z).")
                bump_choice = f"custom:{custom_ver}" if custom_ver else "patch"
                if custom_ver and is_version_regression(custom_ver, curr_version):
                    print_warn(f"Custom version '{custom_ver}' is lower than current version '{curr_version}'.")
                    confirm = input("Proceed with this lower version anyway? (y/N) [n]: ").strip().lower()
                    if confirm != 'y':
                        bump_choice = "patch"
                        print_info("Keeping patch bump.")
            else:
                bump_choice = "patch"
        else:
            print_warn("Invalid option selection.")

    # 8. Assemble final commit message
    if commit_mode == 'new' and bump_choice != "none":
        final_version = increment_version(curr_version, bump_choice)
    elif commit_mode in ['amend', 'fresh_amend']:
        final_version = curr_version
    else:
        final_version = None

    # Detect and strip any existing version prefix from summary
    # Always use the version from the prefix - that's what the user explicitly typed
    existing_ver_match = re.match(r'^(v?\d+(?:\.\d+)+)\s*-\s*', summary)
    if existing_ver_match:
        existing_version = existing_ver_match.group(1)
        summary = summary[existing_ver_match.end():]
        # Always use the version from the user's prefix
        final_version = existing_version

    if final_version:
        version_prefix = f"{final_version} - "
        
        # Only add version prefix if not already present
        if not summary.startswith(version_prefix):
            max_summary_len = 72
            max_content_len = max_summary_len - len(version_prefix)
            if len(summary) > max_content_len:
                summary = summary[:max_content_len-3] + "..."
            full_commit_msg = f"{version_prefix}{summary}"
        else:
            full_commit_msg = summary
    else:
        full_commit_msg = summary

    if description:
        full_commit_msg += f"\n\n{description}"

    if include_co_author:
        co_author_trailer = format_co_author_trailer(model)
        if co_author_trailer not in full_commit_msg:
            full_commit_msg += f"\n\n{co_author_trailer}"

    # Dry Run exit
    if DRY_RUN:
        print_info("DRY RUN: Would commit with message:")
        if commit_mode in ['amend', 'fresh_amend']:
            print_info("  (Would amend last commit)")
        for line in full_commit_msg.split('\n'):
            print(f"  {line}" if line.strip() else "")
        print_success("Dry run complete — no changes committed.")
        clear_session_state()
        return prompt_exit_or_restart(NON_INTERACTIVE)

    # Snapshot files that may be rewritten below so a failed commit can roll
    # them back. Prevents version/changelog drift when the session is retried.
    rollback_snapshot = snapshot_files(["package.json", "pyproject.toml", "CHANGELOG.md"])

    # Apply version changes (only for new commits)
    if commit_mode == 'new' and bump_choice != "none" and final_version:
        update_version_in_files(final_version)
        # Stage the updated version files so they're included in the commit
        for vf in ["package.json", "pyproject.toml"]:
            if os.path.exists(vf):
                run_git_cmd(["add", vf])

    # Create Commit or Amend
    amended_tags = []
    if commit_mode in ['amend', 'fresh_amend']:
        # Check if the commit we are amending has tags pointing to it
        tag_on_head = run_git_cmd(["tag", "--points-at", "HEAD"])
        if tag_on_head:
            amended_tags = [t.strip() for t in tag_on_head.strip().split('\n') if t.strip()]

        # Amend the last commit
        print_info("Amending last commit...")
        
        # Stage any remaining files
        for f in staged:
            run_git_cmd(["add", f])
        
        # Update changelog if needed
        if commit_mode == 'fresh_amend':
            # For fresh amend, update changelog with new message
            update_changelog(curr_version, summary, description)
        
        # Amend commit
        try:
            subprocess.run(["git", "commit", "--amend", "-m", full_commit_msg], check=True)
            print_success("Last commit amended successfully!")
            
            # If the original commit was tagged, ask before moving the tags
            if amended_tags:
                print_info(f"Original commit had tags: {', '.join(amended_tags)}")
                auto_tag = config.get("auto_tag", False)
                if auto_tag or NON_INTERACTIVE:
                    move_tags = 'y'
                else:
                    move_tags = input(f"{c(COLOR_CYAN)}Move these tags to the amended commit? (y/n) [y]:{c(COLOR_RESET)} ").strip().lower()
                if move_tags != 'n':  # Default to yes if user just presses Enter
                    for tag in amended_tags:
                        move_tag_to_head(tag)
                    print_success(f"Tags moved to amended commit locally.")
                else:
                    print_info("Tags left on original commit. You may need to handle them manually.")
        except subprocess.CalledProcessError as e:
            print_error(f"Amend failed: {e}")
            restore_files_snapshot(rollback_snapshot)
            print_info("Rolled back version/changelog file changes to keep them consistent.")
            return prompt_restart()
    else:
        # Normal new commit flow
        if final_version and bump_choice != "none":
            # Tag version
            tag_name = f"v{final_version.lstrip('vV')}"
            
            # Ask for user approval before tagging (unless auto_tag is enabled in config)
            auto_tag = config.get("auto_tag", False)
            if auto_tag:
                should_tag = True
            elif NON_INTERACTIVE:
                should_tag = False
                print_info("Non-interactive mode: skipping tag creation (set auto_tag=true to enable).")
            else:
                print_info(f"About to create tag: {tag_name}")
                approve_tag = input(f"{c(COLOR_CYAN)}Create git tag '{tag_name}'? (y/N) [n]:{c(COLOR_RESET)} ").strip().lower()
                should_tag = approve_tag == 'y'  # Default to no if user just presses Enter

            # Check the remote before creating the tag so a collision surfaces
            # before the commit is written rather than during the push.
            if should_tag and check_remote_tag(tag_name):
                print_warn(f"Tag '{tag_name}' already exists on the remote.")
                if NON_INTERACTIVE:
                    print_warn("Skipping tag creation (non-interactive mode).")
                    should_tag = False
                else:
                    confirm_remote_tag = input(f"{c(COLOR_YELLOW)}Create the local tag anyway? (y/N) [n]:{c(COLOR_RESET)} ").strip().lower()
                    should_tag = confirm_remote_tag == 'y'
            
            # Update changelog
            update_changelog(final_version, summary, description)
            
            commit_args = ["git", "commit", "-m", full_commit_msg]
            try:
                subprocess.run(commit_args, check=True)
                if should_tag:
                    # Create tag
                    subprocess.run(["git", "tag", "-a", tag_name, "-m", f"Version {tag_name}"], check=True)
                    print_success(f"Commit created and tagged as {tag_name}!")
                else:
                    print_success(f"Commit created without tag!")
                    print_info(f'You can tag manually later with: git tag -a {tag_name} -m "Version {tag_name}"')
            except subprocess.CalledProcessError as e:
                print_error(f"Commit failed: {e}")
                restore_files_snapshot(rollback_snapshot)
                print_info("Rolled back version/changelog file changes to keep them consistent.")
                return prompt_restart()
        else:
            commit_args = ["git", "commit", "-m", full_commit_msg]
            try:
                subprocess.run(commit_args, check=True)
                print_success("Commit created!")
            except subprocess.CalledProcessError as e:
                print_error(f"Commit failed: {e}")
                restore_files_snapshot(rollback_snapshot)
                print_info("Rolled back version/changelog file changes to keep them consistent.")
                return prompt_restart()

    # 9. Post-Commit Actions (Push, PR, CI)
    # For amend mode, warn about force push
    if commit_mode in ['amend', 'fresh_amend']:
        print_warn("Note: You've amended a commit. If this was already pushed, you'll need to force push.")
    
    force_push = None  # Initialize to prevent UnboundLocalError in amend paths
    try:
        remotes = run_git_cmd(["remote"])
        if remotes:
            default_push_prompt = "y"
            while True:
                auto_push = config.get("auto_push", False)
                if auto_push:
                    push_choice = "y"
                elif NON_INTERACTIVE:
                    print_info("Non-interactive mode: skipping push (set auto_push=true to enable).")
                    push_choice = "n"
                else:
                    prompt_label = f"Push to remote? (y/n) [{default_push_prompt}]:"
                    raw_push = input(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}{prompt_label}{c(COLOR_RESET)} ").strip().lower()
                    push_choice = raw_push if raw_push != "" else default_push_prompt
                
                if push_choice == "n":
                    print_info("Skipped push. Run 'git push' manually when ready.")
                    break

                if commit_mode in ['amend', 'fresh_amend']:
                    if NON_INTERACTIVE:
                        print_info("Amended commit detected. Skipping force push in non-interactive mode; run 'git push --force-with-lease' manually.")
                        force_push = 'n'
                        push_args = None
                    else:
                        force_push = input(f"{c(COLOR_YELLOW)}This is an amended commit. Force push? (y/n) [y]:{c(COLOR_RESET)} ").strip().lower()
                        if force_push != 'n':
                            push_args = ["git", "push", "--force-with-lease"]
                        else:
                            print_info("Skipped push. Run 'git push --force-with-lease' manually when ready.")
                            push_args = None
                else:
                    push_args = ["git", "push"]
                
                retry_push = False
                if push_args:
                    # Pull before push safety check for non-force pushes
                    if config.get("auto_pull", True) and "--force-with-lease" not in push_args:
                        if NON_INTERACTIVE:
                            pull_choice = 'y'
                        else:
                            pull_choice = input(f"{c(COLOR_CYAN)}{c(COLOR_BOLD)}Pull latest changes from remote? (y/n) [y]:{c(COLOR_RESET)} ").strip().lower()
                        if pull_choice != 'n':
                            print_info("Pulling latest changes from remote (git pull --rebase)...")
                            try:
                                pull_res = perform_git_pull()
                                if pull_res.returncode == 0:
                                    print_success("Pull complete.")
                                else:
                                    if "no tracking information" in pull_res.stderr.lower() or "no upstream branch" in pull_res.stderr.lower():
                                        print_info("No upstream tracking branch configured. Skipping pre-push pull.")
                                    elif "cannot pull with rebase: you have unstaged changes" in pull_res.stderr.lower() or "please commit or stash them" in pull_res.stderr.lower():
                                        print_error(f"Pull failed:\n{pull_res.stderr.strip()}")
                                        default_push_prompt = "n"
                                        retry_push = True
                                        push_args = None
                                    else:
                                        print_error(f"Pull failed:\n{pull_res.stderr.strip()}")
                                        if NON_INTERACTIVE:
                                            push_args = None
                                            print_warn("Aborting push due to pull failure. Please resolve the issue manually.")
                                        else:
                                            push_override = input(f"\n{c(COLOR_YELLOW)}Pull encountered an issue. Attempt push anyway? (y/N) [n]:{c(COLOR_RESET)} ").strip().lower()
                                            if push_override != 'y':
                                                push_args = None
                                                print_warn("Aborting push due to pull failure. Please resolve the issue manually.")
                            except Exception as e:
                                print_warn(f"Failed to execute git pull: {e}")
                    
                if retry_push:
                    continue
                    
                if push_args:
                    print_info("Pushing to remote...")
                    try:
                        subprocess.run(push_args, check=True)
                        if commit_mode == 'new' and final_version and bump_choice != "none" and 'tag_name' in locals() and 'should_tag' in locals() and should_tag:
                            # Check if tag already exists on remote
                            if check_remote_tag(tag_name):
                                overwrite_remote = 'n'
                                if not NON_INTERACTIVE:
                                    overwrite_remote = input(f"\n{c(COLOR_YELLOW)}Tag '{tag_name}' exists on remote. Force push? (y/N) [n]:{c(COLOR_RESET)} ").strip().lower()
                                if overwrite_remote == 'y':
                                    subprocess.run(["git", "push", "origin", tag_name, "--force"], check=True)
                                    print_success(f"Tag '{tag_name}' force-pushed to remote.")
                                else:
                                    print_warn(f"Skipped pushing tag '{tag_name}' to avoid overwriting remote tag.")
                            else:
                                subprocess.run(["git", "push", "origin", tag_name], check=True)
                                print_success(f"Tag '{tag_name}' pushed to remote.")
                        elif commit_mode in ['amend', 'fresh_amend'] and amended_tags and force_push == 'y':
                            # Also force push the amended tags
                            for tag in amended_tags:
                                print_info(f"Force-pushing amended tag '{tag}'...")
                                subprocess.run(["git", "push", "origin", tag, "--force"], check=True)
                                print_success(f"Tag '{tag}' force-pushed to remote.")
                        print_success("Push complete!")
                        
                        branch = run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"])
                        if not NON_INTERACTIVE and branch and branch not in ['main', 'master', 'HEAD']:
                            pr_choice = input(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}Create Pull Request for branch '{branch}'? (y/n) [n]:{c(COLOR_RESET)} ").strip().lower()
                            if pr_choice == 'y':
                                if shutil.which("gh"):
                                    try:
                                        print_info("Creating Pull Request...")
                                        subprocess.run([
                                            "gh", "pr", "create",
                                            "--title", summary,
                                            "--body", description if description else "Auto-generated PR from commit."
                                        ], check=True)
                                        print_success("Pull Request created!")
                                    except subprocess.CalledProcessError:
                                        print_warn("PR creation failed. It might already exist.")
                                else:
                                    print_warn("GitHub CLI (gh) is required to create a PR automatically.")
                        
                        if not NON_INTERACTIVE:
                            monitor_ci()
                        
                    except subprocess.CalledProcessError as e:
                        print_error(f"Push failed: {e}")
                        if NON_INTERACTIVE:
                            break
                        retry_push_choice = input(f"\n{c(COLOR_YELLOW)}Retry push? (Y/n) [y]:{c(COLOR_RESET)} ").strip().lower()
                        if retry_push_choice == 'n':
                            break
                        default_push_prompt = "y"
                        continue
                
                break
        else:
            if not NON_INTERACTIVE and shutil.which("gh"):
                print_info("No git remote configured.")
                print(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}Create GitHub Repository:{c(COLOR_RESET)}")
                print(f"  {c(COLOR_BOLD)}1{c(COLOR_RESET)}) Private repo")
                print(f"  {c(COLOR_BOLD)}2{c(COLOR_RESET)}) Public repo")
                print(f"  {c(COLOR_BOLD)}3{c(COLOR_RESET)}) Skip")
                
                choice = input(f"\n{c(COLOR_CYAN)}Action [1/2/3] [3]:{c(COLOR_RESET)} ").strip()
                if choice in ['1', 'private']:
                    repo_name = os.path.basename(os.getcwd())
                    custom_name = input(f"Repository name [{repo_name}]: ").strip()
                    if custom_name:
                        repo_name = custom_name
                    
                    print_info(f"Creating private repository '{repo_name}' via GitHub CLI...")
                    try:
                        subprocess.run(["gh", "repo", "create", repo_name, "--private", "--source=.", "--remote=origin", "--push"], check=True)
                        print_success("Repository created and pushed successfully!")
                        # Push tag if present
                        if commit_mode == 'new' and final_version and bump_choice != "none" and 'tag_name' in locals() and 'should_tag' in locals() and should_tag:
                            subprocess.run(["git", "push", "origin", tag_name], check=True)
                            print_success(f"Tag '{tag_name}' pushed to remote.")
                        monitor_ci()
                    except subprocess.CalledProcessError as e:
                        print_error(f"Failed to create/push repository: {e}")
                elif choice in ['2', 'public']:
                    repo_name = os.path.basename(os.getcwd())
                    custom_name = input(f"Repository name [{repo_name}]: ").strip()
                    if custom_name:
                        repo_name = custom_name
                    
                    print_info(f"Creating public repository '{repo_name}' via GitHub CLI...")
                    try:
                        subprocess.run(["gh", "repo", "create", repo_name, "--public", "--source=.", "--remote=origin", "--push"], check=True)
                        print_success("Repository created and pushed successfully!")
                        # Push tag if present
                        if commit_mode == 'new' and final_version and bump_choice != "none" and 'tag_name' in locals() and 'should_tag' in locals() and should_tag:
                            subprocess.run(["git", "push", "origin", tag_name], check=True)
                            print_success(f"Tag '{tag_name}' pushed to remote.")
                        monitor_ci()
                    except subprocess.CalledProcessError as e:
                        print_error(f"Failed to create/push repository: {e}")
                else:
                    print_info("Skipped remote repository creation.")
            else:
                print_info("No git remote configured. Skipped push.")
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to complete Git actions: {e}")
        if restart_count >= MAX_RESTARTS:
            print_warn(f"Maximum restarts ({MAX_RESTARTS}) reached. Exiting.")
            return False
        if not prompt_restart("Restart CommitGen? (y/N) [n]:"):
            return False
        restart_count += 1
        os.system('cls' if os.name == 'nt' else 'clear')
        run_str = f"{restart_count} run{'s' if restart_count != 1 else ''} completed"
        print_info(f"Restarting CommitGen... ({run_str} | Max {MAX_RESTARTS} restarts)")
        return True

    clear_session_state()
    return prompt_exit_or_restart(NON_INTERACTIVE)

if __name__ == "__main__":
    while True:
        try:
            should_restart = main()
            if not should_restart:
                break
        except (KeyboardInterrupt, EOFError):
            # EOFError also lands here when stdin closes mid-session (piped
            # input or CI); exit gracefully instead of looping forever.
            print("\n\nAborted by user.")
            break
        except Exception as e:
            print_error(f"An unexpected error occurred: {e}")
            if restart_count >= MAX_RESTARTS:
                print_warn(f"Maximum restarts ({MAX_RESTARTS}) reached. Exiting.")
                break
            if not prompt_restart("Would you like to restart? (y/n) [n]:"):
                break
            restart_count += 1
            os.system('cls' if os.name == 'nt' else 'clear')
            run_str = f"{restart_count} run{'s' if restart_count != 1 else ''} completed"
            print_info(f"Restarting CommitGen... ({run_str} | Max {MAX_RESTARTS} restarts)")
    
    print("Goodbye! 👋")
    sys.exit(0)