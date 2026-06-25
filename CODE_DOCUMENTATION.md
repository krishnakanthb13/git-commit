# Code Documentation

This document describes the technical implementation details of the AI Git Commit & Version Bumper Tool.

## File Structure

```
git-commit/
├── git_commit.py              # Core CLI tool logic (1,611 lines)
├── register.py                # Windows Registry integration helper
├── .env.template              # Configuration environment template
├── .env                       # Local configuration (contains API key, gitignored)
├── .gitignore                 # Git ignore configuration
├── git.ico                    # Windows icon for context menu
├── README.md                  # Setup and usage guide
├── CODE_DOCUMENTATION.md      # Technical implementation details
└── DESIGN_PHILOSOPHY.md       # Architecture decisions
```

## 1. git_commit.py

This script implements the main execution loop. It is designed to be fully self-contained and run on any machine with Python 3.

### Main Functions

**Core helpers**
- `c(color_code)`: Conditional color helper that returns the color code if `USE_COLORS` is enabled, otherwise returns an empty string. Prevents ANSI color code leaking on non-TTY environments.
- `print_success(msg)`, `print_info(msg)`, `print_warn(msg)`, `print_error(msg)`: Colored output helpers with NO_COLOR/c() support.
- `load_dotenv()`: Parses `.env` file without external dependencies. It looks in the directory where the script (`git_commit.py`) is located first, and then in the current working directory.
- `run_git_cmd(args, strip=True)`: Subprocess wrapper for git commands; returns stdout or `None`.
- `detect_version()`: Priority order (non-interactive) — git tags → git commit messages → `package.json` → `pyproject.toml` → `0.0.0`. Collects versions from all sources and prompts the user interactively if there is any mismatch.
- `validate_commit_message(message)`: Validates commit format (72 char limit, conventional format, blank line). Returns list of issues.
- `check_remote_tag(tag_name)`: Queries remote tags using `git ls-remote --tags origin <tag_name>` to see if the tag already exists on remote.
- `version_already_tagged(version: str) -> bool`: Checks if version tag already exists locally before allowing a version bump.
- `get_branch_version_info()`: Returns sanitized branch name if not on `main`/`master`/`HEAD`.
- `is_valid_semver(version: str) -> bool`: Regex validator for `vX.Y.Z(-pre)(+build)` format.
- `get_recent_commits(n=3)`: Returns last N commit lines as context for the AI prompt.
- `get_last_commit_message()`: Returns the full message of the last commit (used for amend mode context).
- `load_commit_template()`: Loads `.git/COMMIT_TEMPLATE` or `.github/PULL_REQUEST_TEMPLATE.md`.
- `extract_issue_references()`: Finds `#NNN` patterns in branch name and last 5 commits.
- `update_changelog(version, summary, description)`: Inserts a new section into `CHANGELOG.md` if it exists.

**Version management**
- `increment_version(version_str, bump_type)`: Semver bump; also handles `custom:x.y.z` passthrough.
- `update_version_in_files(new_version)`: Edits version field in `package.json` and `pyproject.toml`. Automatically stages these files to ensure the version updates are included in the same commit.

**File & staging**
- `get_git_files()`: Parses `git status --porcelain -z` (null-terminated) into staged/unstaged/untracked lists.
- `prompt_amend_or_new()`: Interactive prompt to select commit mode - new (`n`), amend (`a`), or fresh amend (`f`). Returns the selected mode.
- `prompt_stage_files(staged, unstaged, untracked)`: Interactive picker with stage (`a`, numbers), unstage (`u`), and quit (`q`) options. Uses an iterative while-loop to prevent recursive stack overflows, and correctly handles empty inputs to accept pre-staged files. Already-staged files shown in green.
- `show_commit_stats(staged)`: Prints `git diff --stat` and a per-extension file count.
- `is_binary_file(filepath)`: Reads first 1 KB for null bytes to identify binary files, with checks for file existence to handle deleted files gracefully.

**Analysis**
- `detect_conventional_scope(files)`: Maps file path prefixes (e.g., `ui/`, `db/`) to conventional commit scope labels.
- `detect_conventional_commits_usage()`: Checks last 20 commits — returns `True` if >50% follow `feat:`/`fix:` format. Extended to include `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`, `build`, `revert`.
- `check_spelling(text)`: Pipes text to system `aspell`; returns list of misspelled words.

**Configuration & environment**
- `load_config()`: Reads `.commitgenrc` (repo) or `~/.commitgenrc` (global) JSON for default settings.
- `check_dependencies()`: Validates Python 3.6+, `git` is on PATH; warns if `gh` is missing.
- `is_ci_environment()`: Returns `True` if any CI env var (`CI`, GITHUB_ACTIONS, GITLAB_CI, JENKINS_URL, TRAVIS) is set.

**Session recovery**
- `save_session_state(state)`: Persists current session dict to `.git/COMMITGEN_STATE`. Includes commit mode, summary, description, bump choice, staged files, and version.
- `load_session_state()`: Reads back saved state; returns `None` if not present.
- `clear_session_state()`: Deletes `.git/COMMITGEN_STATE` on clean exit.

**Hooks & CI**
- `check_precommit_hooks()`: Runs `pre-commit run --all-files` if `.pre-commit-config.yaml` exists; prompts to continue on failure.
- `monitor_ci()`: Uses `gh run watch` to stream live CI output after a push. Checks for active runs, shows status, and monitors until completion.

**API**
- `call_gemini_api(api_key, model, prompt_text)`: POST to Gemini REST API with JSON schema enforcement, exponential-backoff retries (max 3), and a 60-second connection timeout to avoid hanging connections. Returns structured JSON with `summary` and `description`.

### Amend Mode Functionality

The tool supports three commit modes for flexible history management:

**1. New Commit Mode (default)**
- Standard commit workflow with version bump and tag creation
- Updates project files (package.json, pyproject.toml) with new version
- Creates git tag and pushes if requested
- Full version bump options available (patch/minor/major/custom/none)
- Version prefix added to commit message (e.g., `v1.2.3 - feat: add feature`)

**2. Amend Mode (`a`)**
- Updates the last commit message and can add new staged changes
- Preserves the original commit message as context for AI generation
- Uses existing commit message as base, allows user to edit
- No version bump by default (version unchanged)
- Warns about force push requirement if already pushed to remote
- Uses `git commit --amend -m <message>`

**3. Fresh Amend Mode (`f`)**
- Completely replaces the last commit message with new AI-generated content
- Shows original commit message for reference only
- AI prompt instructs to generate a COMPLETELY NEW commit message
- No version bump by default (version unchanged)
- Warns about force push requirement if already pushed to remote
- Updates CHANGELOG.md with new message
- Uses `git commit --amend -m <message>`

**Amend-specific behaviors:**
- Session state includes `commit_mode` for crash recovery
- Pre-commit hooks skipped if no new files staged (amend message only)
- Diff against last commit shown for review
- Force push prompted after amend if pushing to remote
- Version bump option hidden in review screen for amend modes
- AI prompt includes amend-specific context with original commit message
- Version prefix added to commit message for all modes (prevents duplication)
- **Tag relocation**: Automatically detects if the commit being amended has any associated Git tags; if found, it prompts the user before relocating them locally (`git tag -f <tag_name>`) and force-pushes them to remote (respects `auto_tag` settings).

### Configuration Details

**Default Configuration:**
```python
{
    "default_bump": "patch",
    "max_diff_length": 20000,
    "auto_push": False,
    "model": "gemini-3.1-flash-lite",
    "auto_tag": False  # Set to True to skip the tagging confirmation prompt
}
```

**Configuration File Search Order:**
1. `.commitgenrc` (current repository)
2. `~/.commitgenrc` (user home directory)
3. Built-in defaults

**Environment Variables:**
- `GEMINI_API_KEY`: Gemini API key (required)
- `GEMINI_MODEL`: Model name (optional, overrides config)
- `NO_COLOR`: Disable colored output (optional)

**Entrypoint**
- `main()`: Full orchestration — dependency check → flag parsing (`--dry-run`, `--non-interactive`) → session recovery → staging → **commit mode selection (new/amend/fresh amend)** → version detection → pre-commit hooks → AI call (with amend-specific context) → interactive review (with validation warnings) → commit/tag/amend → push (with force push option for amended commits) → PR creation → CI monitor → session clear.

**Command-line flags:**
- `--dry-run`: Preview commit without making changes
- `--non-interactive`: Headless/CI mode (no prompts, auto-stages everything)

## 2. register.py

This helper script automates the installation of the Explorer right-click context menu by dynamically generating registry paths.

- **Dynamic Path Resolution**: Reads the current folder path of `git_commit.py` and correctly escapes backslashes for Registry Editor compliance (`\\`).
- **File Generation**: Outputs `register.reg` (to import) and `unregister.reg` (to delete the keys).
- **Registry Structure**:
  - `HKEY_CLASSES_ROOT\Directory\shell\GitCommitAI`: When right-clicking a folder in explorer.
  - `HKEY_CLASSES_ROOT\Directory\Background\shell\GitCommitAI`: When right-clicking inside an open folder background.
