# Code Documentation

This document describes the technical implementation details of the AI Git Commit & Version Bumper Tool.

## File Structure

```
c:/Users/ADMIN/OneDrive/Documents/GitHub/git-commit/
├── git_commit.py          # Core CLI tool logic
├── register.py            # Windows Registry integration helper
├── .env.template          # Configuration environment template
├── .gitignore             # Git ignore configuration
└── README.md              # Setup and usage guide
```

## 1. git_commit.py

This script implements the main execution loop. It is designed to be fully self-contained and run on any machine with Python 3.

### Main Functions

**Core helpers**
- `load_dotenv()`: Parses `.env` file without external dependencies.
- `run_git_cmd(args)`: Subprocess wrapper for git commands; returns stdout or `None`.
- `detect_version()`: Priority order — git tags → `package.json` → `pyproject.toml` → `0.0.0`.
- `increment_version(version_str, bump_type)`: Semver bump; also handles `custom:x.y.z` passthrough.
- `update_version_in_files(new_version)`: Edits version field in `package.json` and `pyproject.toml`.
- `update_changelog(version, summary, description)`: Inserts a new section into `CHANGELOG.md` if it exists.
- `get_branch_version_info()`: Returns sanitized branch name if not on `main`/`master`/`HEAD`.
- `is_valid_semver(version)`: Regex validator for `vX.Y.Z(-pre)(+build)` format.
- `get_recent_commits(n)`: Returns last N commit lines as context for the AI prompt.
- `load_commit_template()`: Loads `.git/COMMIT_TEMPLATE` or `.github/PULL_REQUEST_TEMPLATE.md`.
- `extract_issue_references()`: Finds `#NNN` patterns in branch name and last 5 commits.

**File & staging**
- `get_git_files()`: Parses `git status --porcelain -z` (null-terminated) into staged/unstaged/untracked lists.
- `prompt_stage_files(...)`: Interactive picker with stage (`a`, numbers), unstage (`u`), and quit (`q`) options.
- `show_commit_stats(staged)`: Prints `git diff --stat` and a per-extension file count.
- `is_binary_file(filepath)`: Reads first 1 KB for null bytes to identify binary files.

**Analysis**
- `detect_conventional_scope(files)`: Maps file path prefixes (e.g., `ui/`, `db/`) to conventional commit scope labels.
- `detect_conventional_commits_usage()`: Checks last 20 commits — returns `True` if >50% follow `feat:`/`fix:` format.
- `check_spelling(text)`: Pipes text to system `aspell`; returns list of misspelled words.

**Configuration & environment**
- `load_config()`: Reads `.commitgenrc` (repo) or `~/.commitgenrc` (global) JSON for default settings.
- `check_dependencies()`: Validates `git` is on PATH; warns if `gh` is missing.
- `is_ci_environment()`: Returns `True` if any CI env var (`CI`, `GITHUB_ACTIONS`, etc.) is set.

**Session recovery**
- `save_session_state(state)`: Persists current session dict to `.git/COMMITGEN_STATE`.
- `load_session_state()`: Reads back saved state; returns `None` if not present.
- `clear_session_state()`: Deletes `.git/COMMITGEN_STATE` on clean exit.

**Hooks & CI**
- `check_precommit_hooks()`: Runs `pre-commit run --all-files` if `.pre-commit-config.yaml` exists; prompts to continue on failure.
- `monitor_ci()`: Uses `gh run watch` to stream live CI output after a push.

**API**
- `call_gemini_api(api_key, model, prompt_text)`: POST to Gemini REST API with JSON schema enforcement and exponential-backoff retries (max 3).

**Entrypoint**
- `main()`: Full orchestration — dependency check → flag parsing (`--dry-run`, `--non-interactive`) → session recovery → staging → version detection → pre-commit hooks → AI call → interactive review → commit/tag → push → PR → CI monitor → session clear.

## 2. register.py

This helper script automates the installation of the Explorer right-click context menu by dynamically generating registry paths.

- **Dynamic Path Resolution**: Reads the current folder path of `git_commit.py` and correctly escapes backslashes for Registry Editor compliance (`\\`).
- **File Generation**: Outputs `register.reg` (to import) and `unregister.reg` (to delete the keys).
- **Registry Structure**:
  - `HKEY_CLASSES_ROOT\Directory\shell\GitCommitAI`: When right-clicking a folder in explorer.
  - `HKEY_CLASSES_ROOT\Directory\Background\shell\GitCommitAI`: When right-clicking inside an open folder background.
