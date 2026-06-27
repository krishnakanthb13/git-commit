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
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"

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

def load_dotenv():
    """Simple parser to load .env variables without external dependencies."""
    def load_from_path(env_path):
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
                        os.environ[key.strip()] = val

    # Load from the directory where the script is located
    script_dir = os.path.dirname(os.path.abspath(__file__))
    load_from_path(os.path.join(script_dir, ".env"))

    # Load from the current working directory (so cwd can override script dir if they differ)
    if os.path.abspath(os.getcwd()) != os.path.abspath(script_dir):
        load_from_path(".env")

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
            # Pattern for version in commit messages like "v1.2.3" or "version 1.2.3" or "bump to 1.2.3"
            version_pattern = re.compile(r'(?:^|\s)(v?(\d+)\.(\d+)\.(\d+))(?:\s|$)', re.IGNORECASE)
            found_versions = []
            for line in log.split('\n'):
                # Remove the commit hash (first word)
                parts = line.split(' ', 1)
                if len(parts) > 1:
                    message = parts[1]
                    matches = version_pattern.findall(message)
                    for match in matches:
                        full_match = match[0]  # The full version string including 'v' if present
                        major, minor, patch = int(match[1]), int(match[2]), int(match[3])
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

    non_interactive = "--non-interactive" in sys.argv or any(
        os.getenv(v) for v in ['CI', 'GITHUB_ACTIONS', 'GITLAB_CI', 'JENKINS_URL', 'TRAVIS']
    )

    if not valid_choices:
        print_info("No versions found. Defaulting to 0.0.0.")
        return "0.0.0", "default"

    if non_interactive:
        # Auto-select the first available version
        return valid_choices[0][0], valid_choices[0][1]

    # Present list to user to select
    print(f"\n{c(COLOR_CYAN)}Select which version should be used as the base version to bump (+1):{c(COLOR_RESET)}")
    for idx, (ver, name) in enumerate(valid_choices, 1):
        print(f"  {c(COLOR_BOLD)}{idx}{c(COLOR_RESET)}) {ver} (from {name})")
    
    # Allow custom version as well
    print(f"  {c(COLOR_BOLD)}{len(valid_choices)+1}{c(COLOR_RESET)}) Use a custom base version")

    while True:
        choice = input(f"Enter choice [1-{len(valid_choices)+1}]: ").strip()
        try:
            val = int(choice)
            if 1 <= val <= len(valid_choices):
                selected = valid_choices[val-1]
                return selected[0], selected[1]
            elif val == len(valid_choices) + 1:
                custom_base = input("Enter custom base version (e.g. 1.0.0): ").strip()
                if not custom_base:
                    custom_base = "0.0.0"
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

def check_remote_tag(tag_name):
    """Check if a tag exists on the remote."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "origin", tag_name],
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

def get_recent_commits(n=3):
    """Get last N commit messages for context."""
    log = run_git_cmd(["log", f"-{n}", "--oneline", "--no-decorate"])
    return log if log else ""

def get_last_commit_message():
    """Get the full message of the last commit."""
    return run_git_cmd(["log", "-1", "--format=%B"])

def load_commit_template():
    """Load commit template if exists."""
    template_path = os.path.join('.git', 'COMMIT_TEMPLATE')
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
    """Update CHANGELOG.md if it exists."""
    changelog_path = "CHANGELOG.md"
    if not os.path.exists(changelog_path):
        return
    
    print_info("Updating CHANGELOG.md...")
    date_str = time.strftime("%Y-%m-%d")
    clean_version = version.lstrip("vV")
    
    entry = f"\n## [v{clean_version}] - {date_str}\n\n### Changes\n\n- {summary}\n"
    if description:
        entry += f"\n{description}\n"
    
    with open(changelog_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    insert_pos = content.find('\n## ')
    if insert_pos == -1:
        content += entry
    else:
        content = content[:insert_pos] + entry + content[insert_pos:]
    
    with open(changelog_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    run_git_cmd(["add", changelog_path])

def increment_version(version_str, bump_type):
    """Increment version by bump type (patch, minor, major)."""
    # Remove leading 'v' if present
    clean_version = version_str.lstrip("vV")
    parts = clean_version.split(".")
    if len(parts) != 3:
        # Fallback if not standard semver
        return clean_version

    if bump_type.startswith("custom:"):
        return bump_type.split(":", 1)[1]

    try:
        major, minor, patch = map(int, parts)
    except ValueError:
        return clean_version

    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "patch":
        patch += 1
    
    # Preserve leading 'v' if original had it
    prefix = "v" if version_str.lower().startswith("v") else ""
    return f"{prefix}{major}.{minor}.{patch}"

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


def get_git_files():
    """Get staged, unstaged, and untracked files using null-terminated porcelain output."""
    staged = []
    unstaged = []
    untracked = []

    status_out = run_git_cmd(["status", "--porcelain", "-z"], strip=False)
    if not status_out:
        return staged, unstaged, untracked

    entries = status_out.split("\0")
    i = 0
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

        # Staged status in X slot
        if xy[0] in ["M", "A", "D", "R"]:
            staged.append(filename)
        # Unstaged status in Y slot (or untracked)
        if xy[1] in ["M", "A", "D"]:
            unstaged.append(filename)
        elif xy == "??":
            untracked.append(filename)

        # For renames (R) or copies (C), the old path takes up the next NUL-separated entry
        if xy[0] in ["R", "C"]:
            i += 1
            
        i += 1

    return staged, unstaged, untracked

def prompt_amend_or_new():
    """Ask user whether to amend last commit or create new one."""
    print(f"\n{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}Commit Mode:{c(COLOR_RESET)}")
    print(f"  {c(COLOR_BOLD)}n{c(COLOR_RESET)}) Create a NEW commit")
    print(f"  {c(COLOR_BOLD)}a{c(COLOR_RESET)}) AMEND the last commit (updates message and adds staged changes)")
    print(f"  {c(COLOR_BOLD)}f{c(COLOR_RESET)}) FRESH amend (replace last commit message completely with new AI suggestion)")
    
    choice = input(f"\n{c(COLOR_CYAN)}Select mode [n/a/f]:{c(COLOR_RESET)} ").strip().lower()
    if choice == 'a':
        return 'amend'
    elif choice == 'f':
        return 'fresh_amend'
    else:
        return 'new'

def prompt_stage_files(staged, unstaged, untracked):
    """Always show full file picker so user can review/add files before committing."""
    while True:
        all_available = (
            [(f, "staged")    for f in staged] +
            [(f, "modified")  for f in unstaged] +
            [(f, "untracked") for f in untracked]
        )
        if not all_available:
            return False

        print_info("Select files to stage/commit:")
        for idx, (f, status) in enumerate(all_available, 1):
            color = c(COLOR_GREEN) if status == "staged" else ""
            print(f"  {c(COLOR_BOLD)}{idx}{c(COLOR_RESET)}) [{color}{status}{c(COLOR_RESET)}] {f}")
        
        print(f"  {c(COLOR_BOLD)}a{c(COLOR_RESET)}) Stage all files")
        print(f"  {c(COLOR_BOLD)}u{c(COLOR_RESET)}) Unstage files (select from staged)")
        print(f"  {c(COLOR_BOLD)}p{c(COLOR_RESET)}) Proceed with just the staged files")
        print(f"  {c(COLOR_BOLD)}q{c(COLOR_RESET)}) Quit")
        print(f"\n  {c(COLOR_CYAN)}(Already-staged files shown in green){c(COLOR_RESET)}")

        choice = input("\nEnter choice(s) (comma-separated numbers, 'a', 'u', 'p', or 'q'): ").strip().lower()
        if choice == 'q':
            return "quit"
        elif choice == 'a':
            for f, _ in all_available:
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
            
            print_info("Select files to unstage:")
            for idx, f in enumerate(staged, 1):
                print(f"  {c(COLOR_BOLD)}{idx}{c(COLOR_RESET)}) {f}")
            
            unstage_choice = input("\nEnter numbers to unstage (comma-separated): ").strip()
            try:
                for part in unstage_choice.split(","):
                    part = part.strip()
                    if part:
                        idx = int(part)
                        if 1 <= idx <= len(staged):
                            run_git_cmd(["restore", "--staged", staged[idx-1]])
                            print_success(f"Unstaged: {staged[idx-1]}")
            except ValueError:
                print_error("Invalid selection.")
            
            # Refresh file lists and loop back
            staged, unstaged, untracked = get_git_files()
            continue
        elif choice == '':
            # Empty input: if files are already staged, proceed without changes
            if staged:
                return True
            print_warn("No files selected.")
            return False
        else:
            indices = []
            try:
                for part in choice.split(","):
                    part = part.strip()
                    if part:
                        idx = int(part)
                        if 1 <= idx <= len(all_available):
                            indices.append(idx - 1)
            except ValueError:
                print_error("Invalid selection. No files staged.")
                return False

            if not indices:
                print_warn("No files selected.")
                return False

            for idx in indices:
                f, _ = all_available[idx]
                run_git_cmd(["add", f])
                print_success(f"Staged: {f}")
            return True

def monitor_ci():
    """Prompt and monitor CI using GitHub CLI if available."""
    ci_choice = input(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}Monitor CI pipeline? (y/n) [n]:{c(COLOR_RESET)} ").strip().lower()
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
                        "description": "Detailed bulleted list of changes and reasoning. Keep it readable and informative."
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
            with urllib.request.urlopen(req, timeout=60) as response:
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
                    
                return parsed_response
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8")
            if e.code in [429, 503] and attempt < max_retries:
                print_warn(f"Gemini API returned {e.code}. Retrying in {2 ** attempt}s ({attempt}/{max_retries})...")
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Gemini API request failed: {e.code} - {err_msg}")
        except Exception as e:
            if attempt < max_retries:
                print_warn(f"API connection error: {e}. Retrying in {2 ** attempt}s ({attempt}/{max_retries})...")
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"An error occurred: {e}")

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
            print(f"  {line}")
        
        langs = {}
        for f in staged:
            ext = os.path.splitext(f)[1]
            if ext:
                langs[ext] = langs.get(ext, 0) + 1
        if langs:
            print_info("Files by extension:")
            for ext, count in sorted(langs.items(), key=lambda x: x[1], reverse=True):
                print(f"  {ext}: {count} files")

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

def load_config():
    """Load config from .commitgenrc (JSON) in the repo or home directory."""
    defaults = {
        "default_bump": "patch",
        "max_diff_length": 20000,
        "auto_push": False,
        "model": "gemini-3.1-flash-lite",
        "auto_tag": False  # Set to True to skip the tagging confirmation prompt
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

def check_precommit_hooks():
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

def save_session_state(state):
    """Persist session state to .git/COMMITGEN_STATE for crash recovery."""
    try:
        with open('.git/COMMITGEN_STATE', 'w', encoding='utf-8') as f:
            json.dump(state, f)
    except Exception:
        pass

def load_session_state():
    """Load a previously saved session state if it exists."""
    path = '.git/COMMITGEN_STATE'
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None

def clear_session_state():
    """Delete the persisted session state file."""
    path = '.git/COMMITGEN_STATE'
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

def show_startup_banner():
    """Display a clear startup banner with project context."""
    print(f"\n{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}{'='*60}{c(COLOR_RESET)}")
    print(f"{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}  CommitGen - AI-Powered Git Commit Generator{c(COLOR_RESET)}")
    print(f"{c(COLOR_MAGENTA)}{c(COLOR_BOLD)}{'='*60}{c(COLOR_RESET)}\n")

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

def main():
    load_dotenv()
    check_dependencies()

    # Parse flags
    DRY_RUN = "--dry-run" in sys.argv
    NON_INTERACTIVE = "--non-interactive" in sys.argv or is_ci_environment()
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
    model = os.getenv("GEMINI_MODEL", config["model"])
    MAX_DIFF_SIZE = config["max_diff_length"]

    show_startup_banner()
    
    # 1. Print Google Gemini model name
    print_info(f"🤖 AI Model: {c(COLOR_GREEN)}Google Gemini - {model}{c(COLOR_RESET)}")
    
    # 2. Print folder name
    folder_name = os.path.basename(os.getcwd())
    print_info(f"📁 Project: {c(COLOR_GREEN)}{folder_name}{c(COLOR_RESET)}")
    
    # 3. Show folder structure
    print_info("📂 Repository Structure:")
    show_folder_structure(".")
    print()  # Extra newline for spacing

    # 4. Print Configuration Summary
    print_info(f"Configuration: diff limit={MAX_DIFF_SIZE//1000}K chars, default bump={config.get('default_bump', 'patch')}")

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
            staged = saved_state.get('staged', [])
            curr_version = saved_state.get('curr_version', '0.0.0')
            commit_mode = saved_state.get('commit_mode', 'new')
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
            return False

        if NON_INTERACTIVE:
            # In CI mode: auto-stage all and proceed
            for f in unstaged + untracked:
                run_git_cmd(["add", f])
            staged, _, _ = get_git_files()
        else:
            if prompt_stage_files(staged, unstaged, untracked) == "quit":
                return False
            staged, _, _ = get_git_files()

        if not staged:
            print_error("No files staged for commit.")
            sys.exit(1)

        print_info(f"Staged files for commit:\n" + "\n".join(f"  - {f}" for f in staged))

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
            if not check_precommit_hooks():
                print_info("Commit cancelled due to hook failures.")
                return False

        # 4. User Input Context
        if NON_INTERACTIVE:
            user_context = ""
        else:
            user_context = input(f"\n{COLOR_CYAN}{COLOR_BOLD}Optional - Enter context/notes for the commit (press Enter to skip):{COLOR_RESET} ").strip()

        # 5. Git Diff
        if commit_mode == 'amend':
            # For amend, get diff of staged changes against last commit
            diff = run_git_cmd(["diff", "--cached", "--diff-filter=ACMRT"])
            if not diff and not staged:
                print_warn("No new changes staged for amend. Amending message only.")
                diff = "(No new changes — amending commit message only)"
            elif not diff:
                diff = "(No text diff — changes may include binary files or are otherwise empty.)"
        else:
            diff = run_git_cmd(["diff", "--cached", "--diff-filter=ACMRT"])
            if not diff:
                print_warn("No text diff available (binary-only or empty diff). Proceeding with file list only.")
                diff = "(No text diff — changes may include binary files or are otherwise empty.)"

        # Truncate diff if extremely large to save tokens/cost
        if len(diff) > MAX_DIFF_SIZE:
            print_warn("Diff is large. Optimizing to fit context window...")
            lines = diff.split('\n')
            optimized_lines = []
            for line in lines:
                if line.startswith('diff --git'):
                    optimized_lines.append(line)
                elif len('\n'.join(optimized_lines)) > MAX_DIFF_SIZE - 1000:
                    break
                else:
                    optimized_lines.append(line)
            diff = '\n'.join(optimized_lines)
            diff += "\n... [diff truncated, showing key changes only] ..."

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
            
            scope = detect_conventional_scope(staged)
            scope_context = f"\nDetected scope: {', '.join(scope)}\n" if scope else ""

            # Prepare amend-specific context for fresh_amend
            if commit_mode == 'fresh_amend':
                last_msg = get_last_commit_message()
                if last_msg:
                    amend_context = f"""
Current Last Commit Message (for reference only - DO NOT reuse any part):
{last_msg}

IMPORTANT: Generate a COMPLETELY NEW commit message that encompasses ALL changes (both old and new).
Do NOT reference, copy, or preserve any part of the old message.
"""
                else:
                    amend_context = ""
            else:
                amend_context = ""

            print_info("Generating commit message via Gemini...")
            prompt_text = f"""
You are a git commit message generator helper.
Analyze the following git diff and user provided context, then output a structured commit summary and description.
Use conventional commit format if possible (feat:, fix:, docs:, style:, refactor:, test:, chore:, etc.).

Staged files:
{", ".join(staged)}
{recent_context}{template_context}{scope_context}{issues_context}{amend_context}
User custom context / notes:
{user_context if user_context else "(None provided)"}

Git Diff:
```diff
{diff}
```
"""
            try:
                ai_res = call_gemini_api(api_key, model, prompt_text)
                summary = ai_res.get("summary", "").strip()
                description = ai_res.get("description", "").strip()
            except Exception as e:
                print_error(f"Failed to generate commit message: {e}")
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

        # Save session state in case of crash
        save_session_state({
            'summary': summary,
            'description': description,
            'bump_choice': config["default_bump"],
            'staged': staged,
            'curr_version': curr_version,
            'commit_mode': commit_mode,
            'prompt_text': prompt_text if 'prompt_text' in locals() else ''
        })
    else:
        # Resumed from saved session
        summary = saved_state.get('summary', 'update: code modifications')
        description = saved_state.get('description', '')
        bump_choice = saved_state.get('bump_choice', config["default_bump"])
        staged = saved_state.get('staged', [])
        curr_version = saved_state.get('curr_version', '0.0.0')
        commit_mode = saved_state.get('commit_mode', 'new')

    # 7. Interactive Review Loop
    bump_choice = config.get("default_bump", "patch") if not saved_state else saved_state.get('bump_choice', 'patch')

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
    
    # Summary of what will be committed before the review loop
    if commit_mode == 'new':
        bump_preview = increment_version(curr_version, bump_choice) if bump_choice != "none" else curr_version
        print_info(f"Preparing commit: {curr_version} → {c(COLOR_GREEN)}{bump_preview}{c(COLOR_RESET)} ({bump_choice} bump)")
        print_info(f"Files to be committed: {len(staged)}")
        print_info(f"Commit mode: New commit with {'version tag' if bump_choice != 'none' else 'no version bump'}")
    
    while True:
        if commit_mode == 'new':
            proposed_version = increment_version(curr_version, bump_choice) if bump_choice != "none" else curr_version
        else:
            # For amend mode, use current version
            proposed_version = curr_version
            bump_choice = "none"
        
        display_summary = summary
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
            print(f"  {f}")
        if commit_mode == 'new':
            print(f"\n{c(COLOR_BOLD)}Version Bump:{c(COLOR_RESET)} {curr_version} -> {proposed_version} ({bump_choice})")
        else:
            print(f"\n{c(COLOR_BOLD)}Version:{c(COLOR_RESET)} {curr_version} (amend mode - version unchanged)")
        print(f"\n{c(COLOR_BOLD)}Commit Message:{c(COLOR_RESET)}")
        print(f"  {c(COLOR_GREEN)}{display_summary}{c(COLOR_RESET)}")
        if description:
            print(f"\n  {description}")
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
        print(f"  {c(COLOR_BOLD)}c{c(COLOR_RESET)}) Commit as proposed")
        print(f"  {c(COLOR_BOLD)}e{c(COLOR_RESET)}) Edit summary and description")
        if commit_mode == 'new':
            print(f"  {c(COLOR_BOLD)}g{c(COLOR_RESET)}) Regenerate commit message with AI")
            print(f"  {c(COLOR_BOLD)}h{c(COLOR_RESET)}) View version history")
            print(f"  {c(COLOR_BOLD)}v{c(COLOR_RESET)}) Change version bump category (patch/minor/major/none)")
        print(f"  {c(COLOR_BOLD)}d{c(COLOR_RESET)}) View detailed diff")
        print(f"  {c(COLOR_BOLD)}s{c(COLOR_RESET)}) Spell check commit message")
        print(f"  {c(COLOR_BOLD)}x{c(COLOR_RESET)}) Cancel and exit")

        action = input(f"\nSelect option [c/e/{'g/h/v/' if commit_mode == 'new' else ''}d/s/x]: ").strip().lower()

        if action == "c":
            break
        elif action == "x":
            print_info("Commit cancelled.")
            clear_session_state()
            return False
        elif action == "d":
            try:
                subprocess.run(["less", "-R"], input=diff, text=True)
            except FileNotFoundError:
                try:
                    subprocess.run(["more"], input=diff, text=True)
                except FileNotFoundError:
                    print(diff)
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
            print_info("Regenerating commit message...")
            try:
                ai_res = call_gemini_api(api_key, model, prompt_text)
                summary = ai_res.get("summary", "").strip()
                description = ai_res.get("description", "").strip()
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
            
            print(f"  {c(COLOR_BOLD)}p{c(COLOR_RESET)}) 🔧 Patch → {c(COLOR_GREEN)}{patch_ver}{c(COLOR_RESET)}")
            print(f"  {c(COLOR_BOLD)}m{c(COLOR_RESET)}) ✨ Minor → {c(COLOR_GREEN)}{minor_ver}{c(COLOR_RESET)}")
            print(f"  {c(COLOR_BOLD)}j{c(COLOR_RESET)}) 🚀 Major → {c(COLOR_GREEN)}{major_ver}{c(COLOR_RESET)}")
            print(f"  {c(COLOR_BOLD)}n{c(COLOR_RESET)}) ⏸️  None  → {c(COLOR_GREEN)}{curr_version}{c(COLOR_RESET)} (keep current)")
            print(f"  {c(COLOR_BOLD)}c{c(COLOR_RESET)}) ✏️  Custom version")
            
            v_bump = input("\nSelect bump type [p/m/j/n/c]: ").strip().lower()
            if v_bump == 'm':
                bump_choice = "minor"
            elif v_bump == 'j':
                bump_choice = "major"
            elif v_bump == 'n':
                bump_choice = "none"
            elif v_bump == 'c':
                custom_ver = input("Enter custom version (e.g. 1.2.3): ").strip()
                if custom_ver and not is_valid_semver(custom_ver):
                    print_warn("Warning: custom version doesn't follow strict SemVer (X.Y.Z).")
                bump_choice = f"custom:{custom_ver}" if custom_ver else "patch"
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

    # Dry Run exit
    if DRY_RUN:
        print_info("DRY RUN: Would commit with message:")
        if commit_mode in ['amend', 'fresh_amend']:
            print_info("  (Would amend last commit)")
        print(f"  {full_commit_msg}")
        print_success("Dry run complete — no changes committed.")
        clear_session_state()
        return False

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
                if auto_tag:
                    move_tags = 'y'
                else:
                    move_tags = input(f"{c(COLOR_CYAN)}Move these tags to the amended commit? (y/n) [y]:{c(COLOR_RESET)} ").strip().lower()
                if move_tags != 'n':  # Default to yes if user just presses Enter
                    for tag in amended_tags:
                        subprocess.run(["git", "tag", "-f", tag], check=True)
                    print_success(f"Tags moved to amended commit locally.")
                else:
                    print_info("Tags left on original commit. You may need to handle them manually.")
        except subprocess.CalledProcessError as e:
            print_error(f"Amend failed: {e}")
            retry = input(f"\n{c(COLOR_CYAN)}Would you like to restart CommitGen? (y/n) [n]: {c(COLOR_RESET)}").strip().lower()
            return retry == 'y'
    else:
        # Normal new commit flow
        if final_version and bump_choice != "none":
            # Tag version
            tag_name = f"v{final_version.lstrip('vV')}"
            
            # Ask for user approval before tagging (unless auto_tag is enabled in config)
            auto_tag = config.get("auto_tag", False)
            if auto_tag:
                should_tag = True
            else:
                print_info(f"About to create tag: {tag_name}")
                approve_tag = input(f"{c(COLOR_CYAN)}Create git tag '{tag_name}'? (y/n) [y]:{c(COLOR_RESET)} ").strip().lower()
                should_tag = approve_tag != 'n'  # Default to yes if user just presses Enter
            
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
                    print_info(f"You can tag manually later with: git tag -a {tag_name} -m 'Version {tag_name}'")
            except subprocess.CalledProcessError as e:
                print_error(f"Commit failed: {e}")
                retry = input(f"\n{c(COLOR_CYAN)}Would you like to restart CommitGen? (y/n) [n]: {c(COLOR_RESET)}").strip().lower()
                return retry == 'y'
        else:
            commit_args = ["git", "commit", "-m", full_commit_msg]
            try:
                subprocess.run(commit_args, check=True)
                print_success("Commit created!")
            except subprocess.CalledProcessError as e:
                print_error(f"Commit failed: {e}")
                retry = input(f"\n{c(COLOR_CYAN)}Would you like to restart CommitGen? (y/n) [n]: {c(COLOR_RESET)}").strip().lower()
                return retry == 'y'

    # 9. Post-Commit Actions (Push, PR, CI)
    # For amend mode, warn about force push
    if commit_mode in ['amend', 'fresh_amend']:
        print_warn("Note: You've amended a commit. If this was already pushed, you'll need to force push.")
    
    force_push = None  # Initialize to prevent UnboundLocalError in amend paths
    try:
        # Check if we have a remote to push to
        remotes = run_git_cmd(["remote"])
        if remotes:
            push_choice = input(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}Push to remote? (y/n) [y]:{c(COLOR_RESET)} ").strip().lower()
            if push_choice != "n":
                if commit_mode in ['amend', 'fresh_amend']:
                    force_push = input(f"{c(COLOR_YELLOW)}This is an amended commit. Force push? (y/n) [y]:{c(COLOR_RESET)} ").strip().lower()
                    if force_push != 'n':
                        push_args = ["git", "push", "--force-with-lease"]
                    else:
                        print_info("Skipped push. Run 'git push --force-with-lease' manually when ready.")
                        push_args = None
                else:
                    push_args = ["git", "push"]
                
                if push_args:
                    print_info("Pushing to remote...")
                    try:
                        subprocess.run(push_args, check=True)
                        if commit_mode == 'new' and final_version and bump_choice != "none" and 'tag_name' in locals() and should_tag:
                            # Check if tag already exists on remote
                            if check_remote_tag(tag_name):
                                overwrite_remote = input(f"\n{c(COLOR_YELLOW)}Tag '{tag_name}' already exists on remote. Force push/overwrite? (y/n) [n]:{c(COLOR_RESET)} ").strip().lower()
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
                        if branch and branch not in ['main', 'master', 'HEAD']:
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
                        
                        monitor_ci()
                        
                    except subprocess.CalledProcessError as e:
                        print_error(f"Push failed: {e}")
            else:
                print_info("Skipped push. Run 'git push' manually when ready.")
        else:
            if not NON_INTERACTIVE and shutil.which("gh"):
                print_info("No git remote configured.")
                print(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}Create a new GitHub repository for this project?{c(COLOR_RESET)}")
                print(f"  {c(COLOR_BOLD)}1{c(COLOR_RESET)}) Create a PRIVATE GitHub repository")
                print(f"  {c(COLOR_BOLD)}2{c(COLOR_RESET)}) Create a PUBLIC GitHub repository")
                print(f"  {c(COLOR_BOLD)}3{c(COLOR_RESET)}) Skip / Do nothing")
                
                choice = input(f"\n{c(COLOR_CYAN)}Select option [1/2/3] [3]:{c(COLOR_RESET)} ").strip()
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
                        if commit_mode == 'new' and final_version and bump_choice != "none" and 'tag_name' in locals() and should_tag:
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
                        if commit_mode == 'new' and final_version and bump_choice != "none" and 'tag_name' in locals() and should_tag:
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
        retry = input(f"\n{c(COLOR_CYAN)}Would you like to restart CommitGen? (y/n) [n]: {c(COLOR_RESET)}").strip().lower()
        if retry == 'y':
            return True
        return False

    clear_session_state()
    
    while True:
        choice = input(f"\n{c(COLOR_CYAN)}{c(COLOR_BOLD)}Press Enter to exit, or type 'r' to run again: {c(COLOR_RESET)}").strip().lower()
        if choice == 'r':
            os.system('cls' if os.name == 'nt' else 'clear')
            print_info("Restarting CommitGen...\n")
            return True  # Signal to restart
        else:
            return False  # Signal to exit

if __name__ == "__main__":
    restart_count = 0
    MAX_RESTARTS = 10
    
    while True:
        if restart_count >= MAX_RESTARTS:
            print_warn(f"Maximum restarts ({MAX_RESTARTS}) reached. Exiting.")
            break
            
        try:
            should_restart = main()
            if not should_restart:
                break
            restart_count += 1
        except KeyboardInterrupt:
            print("\n\nAborted by user.")
            break
        except Exception as e:
            print_error(f"An unexpected error occurred: {e}")
            retry = input(f"\n{c(COLOR_CYAN)}Would you like to restart? (y/n) [n]: {c(COLOR_RESET)}").strip().lower()
            if retry != 'y':
                break
            restart_count += 1
    
    print("Goodbye! 👋")
    sys.exit(0)