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

USE_COLORS = os.getenv("NO_COLOR") is None and sys.stdout.isatty()

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
    env_file = ".env"
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    # Strip quotes if present
                    val = val.strip().strip('"').strip("'")
                    os.environ[key.strip()] = val

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
    """Detect current version from git tags or project files."""
    # 1. Check git tags
    tags = run_git_cmd(["tag", "-l"])
    semver_regex = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
    if tags:
        valid_versions = []
        for tag in tags.split("\n"):
            m = semver_regex.match(tag.strip())
            if m:
                valid_versions.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), tag))
        if valid_versions:
            # Sort by version tuple
            valid_versions.sort()
            return valid_versions[-1][3], "git tag"

    # 2. Check package.json
    if os.path.exists("package.json"):
        try:
            with open("package.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                if "version" in data:
                    return data["version"], "package.json"
        except Exception:
            pass

    # 3. Check pyproject.toml
    if os.path.exists("pyproject.toml"):
        try:
            with open("pyproject.toml", "r", encoding="utf-8") as f:
                for line in f:
                    m = re.match(r'^\s*version\s*=\s*["\']([^"\']+)["\']', line)
                    if m:
                        return m.group(1), "pyproject.toml"
        except Exception:
            pass

    return "0.0.0", "default"

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

def get_recent_commits(n=3):
    """Get last N commit messages for context."""
    log = run_git_cmd(["log", f"-{n}", "--oneline", "--no-decorate"])
    return log if log else ""

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

def prompt_stage_files(staged, unstaged, untracked):
    """Always show full file picker so user can review/add files before committing."""
    all_available = (
        [(f, "staged")    for f in staged] +
        [(f, "modified")  for f in unstaged] +
        [(f, "untracked") for f in untracked]
    )
    if not all_available:
        return False

    print_info("Select files to stage/commit:")
    for idx, (f, status) in enumerate(all_available, 1):
        color = COLOR_GREEN if status == "staged" else ""
        print(f"  {COLOR_BOLD}{idx}{COLOR_RESET}) [{color}{status}{COLOR_RESET}] {f}")
    
    print(f"  {COLOR_BOLD}a{COLOR_RESET}) Stage all files")
    print(f"  {COLOR_BOLD}u{COLOR_RESET}) Unstage files (select from staged)")
    print(f"  {COLOR_BOLD}q{COLOR_RESET}) Quit")
    print(f"\n  {COLOR_CYAN}(Already-staged files shown in green){COLOR_RESET}")

    choice = input("\nEnter choice(s) (comma-separated numbers, 'a', 'u', or 'q'): ").strip().lower()
    if choice == 'q':
        sys.exit(0)
    elif choice == 'a':
        for f, _ in all_available:
            run_git_cmd(["add", f])
        print_success("Staged all files.")
        return True
    elif choice == 'u':
        if not staged:
            print_warn("No staged files to unstage.")
            return prompt_stage_files(staged, unstaged, untracked)
        
        print_info("Select files to unstage:")
        for idx, f in enumerate(staged, 1):
            print(f"  {COLOR_BOLD}{idx}{COLOR_RESET}) {f}")
        
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
        
        return prompt_stage_files(*get_git_files())
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
    ci_choice = input(f"\n{COLOR_CYAN}{COLOR_BOLD}Monitor CI pipeline? (y/n) [n]:{COLOR_RESET} ").strip().lower()
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
            with urllib.request.urlopen(req) as response:
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
    except:
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

def main():
    load_dotenv()
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print_error("GEMINI_API_KEY not found in environment or .env file.")
        print_info("Please create a .env file containing: GEMINI_API_KEY=your_key_here")
        sys.exit(1)

    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")

    # 1. Git Status & Interactive Staging (always show picker)
    staged, unstaged, untracked = get_git_files()
    if not staged and not unstaged and not untracked:
        print_success("Nothing to commit, working tree clean.")
        sys.exit(0)

    staged_any = prompt_stage_files(staged, unstaged, untracked)
    if not staged_any:
        sys.exit(0)
    staged, _, _ = get_git_files()

    if not staged:
        print_error("No files staged for commit.")
        sys.exit(1)

    print_info(f"Staged files for commit:\n" + "\n".join(f"  - {f}" for f in staged))
    
    show_commit_stats(staged)

    # 2. Detect & Display Version Info
    curr_version, ver_source = detect_version()
    print_info(f"Detected current version: {curr_version} (from {ver_source})")
    
    branch_info = get_branch_version_info()
    if branch_info:
        print_info(f"Current branch: {branch_info}")

    # 3. User Input Context
    user_context = input(f"\n{COLOR_CYAN}{COLOR_BOLD}Optional - Enter context/notes for the commit (press Enter to skip):{COLOR_RESET} ").strip()

    # 4. Git Diff (exclude binary files — they produce unreadable noise for the LLM)
    diff = run_git_cmd(["diff", "--cached", "--diff-filter=ACMRT"])
    if not diff:
        # Might be binary-only commit; still allow it to proceed
        print_warn("No text diff available (binary-only or empty diff). Proceeding with file list only.")
        diff = "(No text diff — changes may include binary files or are otherwise empty.)"

    # Truncate diff if extremely large to save tokens/cost
    MAX_DIFF_SIZE = 20000
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

    # 5. Gemini API Call
    recent_commits = get_recent_commits(3)
    recent_context = f"\nRecent Commits History:\n{recent_commits}\n" if recent_commits else ""

    template = load_commit_template()
    template_context = f"\nProject Commit Template/Guidelines:\n{template}\nPlease follow these guidelines.\n" if template else ""

    issues = extract_issue_references()
    issues_context = f"\nRelated Issues: {', '.join(['#' + i for i in issues])}\n(Please include these issue numbers if relevant)\n" if issues else ""
    
    scope = detect_conventional_scope(staged)
    scope_context = f"\nDetected scope: {', '.join(scope)}\n" if scope else ""

    print_info("Generating commit message via Gemini...")
    prompt_text = f"""
You are a git commit message generator helper.
Analyze the following git diff and user provided context, then output a structured commit summary and description.
Use conventional commit format if possible (feat:, fix:, docs:, style:, refactor:, test:, chore:, etc.).

Staged files:
{", ".join(staged)}
{recent_context}{template_context}{scope_context}{issues_context}
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
        # Default fallback
        summary = "update: code modifications"
        description = "- Minor changes/updates"

    # 6. Interactive Review Loop
    bump_choice = "patch"  # default bump
    
    # Try to extract version from user context
    ver_match = re.search(r'\b(v?\d+\.\d+\.\d+(?:-[a-zA-Z0-9\.]+)?)\b', user_context)
    if ver_match:
        bump_choice = f"custom:{ver_match.group(1)}"
        print_info(f"Auto-detected version from context: {ver_match.group(1)}")
    
    while True:
        proposed_version = increment_version(curr_version, bump_choice) if bump_choice != "none" else curr_version
        
        display_summary = summary
        if proposed_version:
            clean_proposed = proposed_version.lstrip("vV")
            version_prefix = f"v{clean_proposed}"
            if not display_summary.startswith(version_prefix):
                # Ensure it fits nicely within 72 chars
                max_summary_len = 72
                max_content_len = max_summary_len - len(version_prefix) - 3
                if len(display_summary) > max_content_len:
                    display_summary = display_summary[:max_content_len-3] + "..."
                display_summary = f"{version_prefix} - {display_summary}"
                
        print(f"\n{COLOR_MAGENTA}================ PROPOSED COMMIT ================{COLOR_RESET}")
        print(f"{COLOR_BOLD}Files to commit:{COLOR_RESET}")
        for f in staged:
            print(f"  {f}")
        print(f"\n{COLOR_BOLD}Version Bump:{COLOR_RESET} {curr_version} -> {proposed_version} ({bump_choice})")
        print(f"\n{COLOR_BOLD}Commit Message:{COLOR_RESET}")
        print(f"  {COLOR_GREEN}{display_summary}{COLOR_RESET}")
        if description:
            print(f"\n  {description}")
        print(f"{COLOR_MAGENTA}================================================={COLOR_RESET}")

        validation_issues = validate_commit_message(f"{display_summary}\n\n{description}" if description else display_summary)
        if validation_issues:
            print(f"\n{COLOR_YELLOW}{COLOR_BOLD}Validation Warnings:{COLOR_RESET}")
            for issue in validation_issues:
                print(f"  - {COLOR_YELLOW}{issue}{COLOR_RESET}")

        print(f"\nOptions:")
        print(f"  {COLOR_BOLD}c{COLOR_RESET}) Commit as proposed")
        print(f"  {COLOR_BOLD}e{COLOR_RESET}) Edit summary and description")
        print(f"  {COLOR_BOLD}v{COLOR_RESET}) Change version bump category (patch/minor/major/none)")
        print(f"  {COLOR_BOLD}d{COLOR_RESET}) View detailed diff")
        print(f"  {COLOR_BOLD}s{COLOR_RESET}) Spell check commit message")
        print(f"  {COLOR_BOLD}x{COLOR_RESET}) Cancel and exit")

        action = input("\nSelect option [c/e/v/d/s/x]: ").strip().lower()

        if action == "c":
            break
        elif action == "x":
            print_info("Commit cancelled.")
            sys.exit(0)
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
        elif action == "v":
            v_bump = input("Select bump category (p=patch, m=minor, j=major, n=none, c=custom) [p]: ").strip().lower()
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

    # 7. Apply version changes
    if bump_choice != "none":
        final_version = increment_version(curr_version, bump_choice)
        update_version_in_files(final_version)
    else:
        final_version = None

    # Assemble final commit message
    if final_version:
        clean_final = final_version.lstrip("vV")
        version_prefix = f"v{clean_final}"
        if not summary.startswith(version_prefix):
            max_summary_len = 72
            max_content_len = max_summary_len - len(version_prefix) - 3
            if len(summary) > max_content_len:
                summary = summary[:max_content_len-3] + "..."
            full_commit_msg = f"{version_prefix} - {summary}"
        else:
            full_commit_msg = summary
    else:
        full_commit_msg = summary

    if description:
        full_commit_msg += f"\n\n{description}"
        
    if bump_choice != "none" and final_version:
        update_version_in_files(final_version)

    # Create Commit
    if final_version and bump_choice != "none":
        # Tag version
        tag_name = f"v{final_version.lstrip('vV')}"
        print_info(f"Tagging commit as {tag_name}...")
        
        # Update changelog
        update_changelog(final_version, summary, description)
        
        commit_args = ["git", "commit", "-m", full_commit_msg]
        if description:
            commit_args.extend(["-m", description])
            
        try:
            subprocess.run(commit_args, check=True)
            # Create tag
            subprocess.run(["git", "tag", "-a", tag_name, "-m", f"Version {tag_name}"], check=True)
            print_success(f"Commit created and tagged as {tag_name}!")
        except subprocess.CalledProcessError as e:
            print_error(f"Commit failed: {e}")
            sys.exit(1)
    else:
        commit_args = ["git", "commit", "-m", full_commit_msg]
        if description:
            commit_args.extend(["-m", description])
            
        try:
            subprocess.run(commit_args, check=True)
            print_success("Commit created!")
        except subprocess.CalledProcessError as e:
            print_error(f"Commit failed: {e}")
            sys.exit(1)

    # 8. Post-Commit Actions (Push, PR, CI)
    try:
        # Check if we have a remote to push to
        remotes = run_git_cmd(["remote"])
        if remotes:
            push_choice = input(f"\n{COLOR_CYAN}{COLOR_BOLD}Push to remote? (y/n) [n]:{COLOR_RESET} ").strip().lower()
            if push_choice == "y":
                print_info("Pushing to remote...")
                push_args = ["git", "push"]
                try:
                    subprocess.run(push_args, check=True)
                    if final_version and bump_choice != "none":
                        subprocess.run(["git", "push", "origin", tag_name], check=True)
                        print_success(f"Tag '{tag_name}' pushed to remote.")
                    print_success("Push complete!")
                    
                    branch = run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"])
                    if branch and branch not in ['main', 'master', 'HEAD']:
                        pr_choice = input(f"\n{COLOR_CYAN}{COLOR_BOLD}Create Pull Request for branch '{branch}'? (y/n) [n]:{COLOR_RESET} ").strip().lower()
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
            print_info("No git remote configured. Skipped push.")
    except Exception as e:
        print_warn(f"Failed to check for remotes: {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)
        
    input("\nPress Enter to exit...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(0)
