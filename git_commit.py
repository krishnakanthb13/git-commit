#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import re
import urllib.request
import urllib.error
import time

# ANSI colors for rich CLI interface
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_CYAN = "\033[96m"
COLOR_MAGENTA = "\033[95m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"

def print_success(msg):
    print(f"{COLOR_GREEN}{COLOR_BOLD}[OK] {msg}{COLOR_RESET}")

def print_info(msg):
    print(f"{COLOR_CYAN}{COLOR_BOLD}[INFO] {msg}{COLOR_RESET}")

def print_warn(msg):
    print(f"{COLOR_YELLOW}{COLOR_BOLD}[WARN] {msg}{COLOR_RESET}")

def print_error(msg):
    print(f"{COLOR_RED}{COLOR_BOLD}[ERROR] {msg}{COLOR_RESET}")

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
    # Clean version for files
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

def prompt_stage_files(unstaged, untracked):
    """Interactively let user stage files if nothing is staged."""
    all_available = [(f, "modified") for f in unstaged] + [(f, "untracked") for f in untracked]
    if not all_available:
        return False

    print_info("No files currently staged. Select files to stage/commit:")
    for idx, (f, status) in enumerate(all_available, 1):
        print(f"  {COLOR_BOLD}{idx}{COLOR_RESET}) [{status}] {f}")
    
    print(f"  {COLOR_BOLD}a{COLOR_RESET}) Stage all files")
    print(f"  {COLOR_BOLD}q{COLOR_RESET}) Quit")

    choice = input("\nEnter choice(s) (comma-separated numbers, 'a', or 'q'): ").strip().lower()
    if choice == 'q':
        sys.exit(0)
    elif choice == 'a':
        for f, _ in all_available:
            run_git_cmd(["add", f])
        print_success("Staged all files.")
        return True
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

def call_gemini_api(api_key, model, prompt_text):
    """Call Gemini API to generate structured commit message."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
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
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                # Extract content from response
                text_response = res_data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text_response)
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

def main():
    load_dotenv()
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print_error("GEMINI_API_KEY not found in environment or .env file.")
        print_info("Please create a .env file containing: GEMINI_API_KEY=your_key_here")
        sys.exit(1)

    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-lite")

    # 1. Git Status & Interactive Staging
    staged, unstaged, untracked = get_git_files()
    if not staged:
        if not unstaged and not untracked:
            print_success("Nothing to commit, working tree clean.")
            sys.exit(0)
        
        staged_any = prompt_stage_files(unstaged, untracked)
        if not staged_any:
            sys.exit(0)
        staged, _, _ = get_git_files()

    if not staged:
        print_error("No files staged for commit.")
        sys.exit(1)

    print_info(f"Staged files for commit:\n" + "\n".join(f"  - {f}" for f in staged))

    # 2. Detect & Display Version Info
    curr_version, ver_source = detect_version()
    print_info(f"Detected current version: {curr_version} (from {ver_source})")

    # 3. User Input Context
    user_context = input(f"\n{COLOR_CYAN}{COLOR_BOLD}Optional - Enter context/notes for the commit (press Enter to skip):{COLOR_RESET} ").strip()

    # 4. Git Diff (exclude binary files — they produce unreadable noise for the LLM)
    diff = run_git_cmd(["diff", "--cached", "--diff-filter=ACMRT"])
    if not diff:
        # Might be binary-only commit; still allow it to proceed
        print_warn("No text diff available (binary-only or empty diff). Proceeding with file list only.")
        diff = "(No text diff — changes may include binary files or are otherwise empty.)"

    # Truncate diff if extremely large to save tokens/cost
    if len(diff) > 20000:
        print_warn("Diff is large. Truncating to fit context window.")
        diff = diff[:20000] + "\n... [diff truncated for length] ..."

    # 5. Gemini API Call
    print_info("Generating commit message via Gemini...")
    prompt_text = f"""
You are a git commit message generator helper.
Analyze the following git diff and user provided context, then output a structured commit summary and description.

Staged files:
{", ".join(staged)}

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
            version_prefix = f"v{clean_proposed}+ "
            if not display_summary.startswith(version_prefix):
                display_summary = f"{version_prefix}- {display_summary}"
                
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

        print(f"\nOptions:")
        print(f"  {COLOR_BOLD}c{COLOR_RESET}) Commit as proposed")
        print(f"  {COLOR_BOLD}e{COLOR_RESET}) Edit summary and description")
        print(f"  {COLOR_BOLD}v{COLOR_RESET}) Change version bump category (patch/minor/major/none)")
        print(f"  {COLOR_BOLD}x{COLOR_RESET}) Cancel and exit")

        action = input("\nSelect option [c/e/v/x]: ").strip().lower()

        if action == "c":
            break
        elif action == "x":
            print_info("Commit cancelled.")
            sys.exit(0)
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
        version_prefix = f"v{clean_final}+ "
        if not summary.startswith(version_prefix):
            full_commit_msg = f"{version_prefix}- {summary}"
        else:
            full_commit_msg = summary
    else:
        full_commit_msg = summary

    if description:
        full_commit_msg += f"\n\n{description}"

    # Commit changes
    try:
        # Commit
        subprocess.run(["git", "commit", "-m", full_commit_msg], check=True)
        print_success("Changes committed successfully!")
        
        # Tag if version bumped
        if final_version:
            # Tag with 'v' prefix if original version had it
            tag_name = final_version
            if not tag_name.startswith("v") and curr_version.startswith("v"):
                tag_name = f"v{final_version}"
            
            subprocess.run(["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}"], check=True)
            print_success(f"Git tag created: {tag_name}")
            
    except subprocess.CalledProcessError as e:
        print_error(f"Failed to complete Git actions: {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)
        
    input("\nPress Enter to exit...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(0)
