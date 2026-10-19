# Code Documentation

This document describes the technical implementation details of the AI Git Commit & Version Bumper Tool.

## File Structure

```
git-commit/
├── git_commit.py              # Core CLI tool logic (~2,500 lines)
├── register.py                # Windows Registry integration helper
├── tests/test_git_commit.py   # Pytest regression suite (offline, temp repos)
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

### Module-Level Constants

- `AI_PROMPT_EXCLUDED_EXTENSIONS`: Set of file extensions excluded from the Gemini AI prompt (images, media, binary formats). These files can still be committed — they are only excluded from AI analysis to save tokens and avoid binary parsing issues.
- `GIT_DIR`: Absolute path of the repository's Git directory. Initialized to `.git` and replaced by `init_git_dir()` at startup so session state and commit templates resolve correctly even when the tool is launched from a subdirectory.
- `SECRET_PATTERNS`: List of `(category, compiled_regex)` pairs for high-confidence secret formats (AWS access keys, GitHub/Gemini tokens, bearer tokens, credentials embedded in URLs) used by `redact_secrets()` to scrub the AI prompt.

### Main Functions

**Core helpers**
- `c(color_code)`: Conditional color helper that returns the color code if `USE_COLORS` is enabled, otherwise returns an empty string. Prevents ANSI color code leaking on non-TTY environments.
- `print_success(msg)`, `print_info(msg)`, `print_warn(msg)`, `print_error(msg)`: Colored output helpers with NO_COLOR/c() support.
- `flush_input_buffer()`: Flushes unconsumed user keystrokes from the console input buffer. On Windows, it clears both the C runtime keyboard buffer (`msvcrt.kbhit()` / `msvcrt.getwch()`) and the Win32 console input queue (`kernel32.FlushConsoleInputBuffer` on `STD_INPUT_HANDLE`). On POSIX systems, it issues `termios.tcflush(sys.stdin, termios.TCIFLUSH)`. Safely bypassed if `sys.stdin.isatty()` is `False` to maintain compatibility with piped inputs, automated tests, and CI/CD pipelines.
- `input(prompt="")`: Module-level wrapper around Python's built-in `input()` that invokes `flush_input_buffer()` immediately before rendering the prompt. Ensures the user only provides input after seeing the display prompt, preventing accidental double-Enter keypresses or keystrokes entered during background operations (e.g. AI calls, git diff calculations) from prematurely auto-advancing the next prompt.
- `prompt_restart(message=...)`: Wraps the restart confirmation prompt and returns `False` safely on `EOFError`/`KeyboardInterrupt`, so a closed stdin (piped input or CI) never crashes or loops the top-level runner.
- `show_startup_banner()`: Displays the CommitGen header banner alongside the active project folder name and absolute directory path.
- `format_co_author_trailer(model)`: Generates the standardized git commit trailer `Co-authored-by: Custom Tool By BKK ({model}) <{model}>`.
- `prompt_exit_or_restart(non_interactive=False)`: Prompts the user with `Press Enter to exit, or 'r' to restart:` and returns `True` if `r` is entered (clearing the console, displaying completed runs and max 10 restart limit info, and signaling the runner loop to re-execute `main()`), or `False` to exit cleanly. Bypassed in non-interactive/CI mode.
- `load_dotenv()`: Parses `.env` files without external dependencies. Precedence (lowest → highest) is tool-directory `.env`, then project `.env` (current working directory); real environment variables always win because a key already present in `os.environ` is never overwritten. `.env` files themselves use first-wins semantics.
- `run_git_cmd(args, strip=True)`: Subprocess wrapper for git commands; returns stdout or `None`.
- `init_git_dir()`: Resolves the repository's Git directory via `git rev-parse --absolute-git-dir` and stores it in the `GIT_DIR` global. Called at the start of `main()`; returns `False` (and the tool exits) when not inside a Git repository.
- `redact_secrets(text)`: Replaces high-confidence secret patterns with `[REDACTED]` before text is sent to the Gemini API. It prints the match count plus a per-category line with a masked preview (e.g. `AWS access key id: AKIA****************`), so genuine detections can be told apart from fixtures. Only the prompt is affected — the committed diff is never modified.
- `_mask_secret(value)`: Returns a short, non-reversible preview of a matched secret (a 4-character prefix followed by asterisks), used by `redact_secrets()` for diagnostics.
- `detect_version()`: Priority order — checks git tags, git commit messages, `package.json`, and `pyproject.toml`. Collects all detected versions, parses them into integer tuples (supporting standard SemVer as well as multi-part versions like `v1.1.1.11`), and sorts the choices descending so the largest version is listed first (Option 1). In interactive mode, if a lower version (or a custom lower version) is selected, it asks for user confirmation before proceeding, allowing the user to confirm or return to the selection list. In non-interactive mode, it auto-selects the highest available version. For custom base versions, it automatically prepends `v` if missing to maintain consistency.
- `validate_commit_message(message)`: Validates commit format (72 char limit, conventional format, blank line). Returns list of issues.
- `check_remote_tag(tag_name)`: Queries the remote with `git ls-remote --tags --refs origin refs/tags/<tag_name>` and matches the exact ref, so a short name such as `v1.2.3` cannot glob-match `v1.2.34`. Checked before the local tag is created.
- `version_already_tagged(version: str) -> bool`: Checks if version tag already exists locally before allowing a version bump.
- `move_tag_to_head(tag)`: Relocates a tag to `HEAD` while preserving annotated tags — if `git cat-file -t refs/tags/<tag>` reports `tag`, it re-creates it with `git tag -f -a` and the original message; otherwise it creates a lightweight tag.
- `get_branch_version_info()`: Returns sanitized branch name if not on `main`/`master`/`HEAD`.
- `is_valid_semver(version: str) -> bool`: Regex validator for `vX.Y.Z(-pre)(+build)` format.
- `semver_key(version_str)`: Extracts a numeric tuple from a version string (e.g. `v1.2.3.4` → `(1, 2, 3, 4)`), supporting multi-part versions.
- `is_version_regression(proposed, current)`: Returns `True` when the proposed version is numerically lower than the current one (zero-padded tuple comparison). Used to guard custom and context-detected versions.
- `get_recent_commits(n=3)`: Returns last N commit lines as context for the AI prompt.
- `get_last_commit_message()`: Returns the full message of the last commit (used for amend mode context).
- `load_commit_template()`: Loads `<GIT_DIR>/COMMIT_TEMPLATE` or `.github/PULL_REQUEST_TEMPLATE.md`.
- `extract_issue_references()`: Finds `#NNN` patterns in branch name and last 5 commits.
- `update_changelog(version, summary, description)`: Updates `CHANGELOG.md` if it exists. Inserts a new section before the first existing version heading (or appends when none exist), and replaces the entry for an already-present version in place (heading → next `## ` heading, or end of file) to avoid duplicates on re-commit/fresh-amend.

**Version management**
- `increment_version(version_str, bump_type)`: Semver bump; also handles `custom:x.y.z` passthrough. If the version is not a standard SemVer or can't be parsed, it preserves the original string (including any `v` prefix) instead of stripping it.
- `update_version_in_files(new_version)`: Edits version field in `package.json` and `pyproject.toml`. Automatically stages these files to ensure the version updates are included in the same commit.
- `snapshot_files(paths)`: Reads file contents into an in-memory rollback snapshot (`None` for files that do not exist).
- `restore_files_snapshot(snapshot)`: Restores snapshotted file contents and unstages them (`git restore --staged`). Files whose content is unchanged since the snapshot are skipped entirely, so the user's own staging is never discarded. Called on commit/amend failure so version files and the changelog never drift when a commit fails and is retried.

**File & staging**
- `get_git_files(include_details=False)`: Parses `git status --porcelain -z` (null-terminated) into staged/unstaged/untracked lists, optionally returning file metadata (`details` dict with granular staged/unstaged status and rename origin paths). Automatically detects and unstages `.env` files with a security warning to prevent accidental credential leaks.
- `prompt_amend_or_new()`: Interactive prompt to select commit mode - new (`n` [default]), amend (`a`), or fresh amend (`f`). Displays options in distinct ANSI colors and defaults to `n` on empty input.
- `parse_index_selection(input_str, max_len)`: Parses comma-separated numbers, range expressions (e.g. `1-3, 5`), and keywords (`all`, `*`) into validated 0-based indices for staging and unstaging operations.
- `prompt_stage_files(staged, unstaged, untracked)`: Interactive file picker featuring a top status color legend (`staged`: green, `modified`: yellow, `untracked`: red), section divider lines, and color-coded action hotkeys (`a`: stage all [default], `u`: unstage, `r`: refresh status, `p`: proceed, `q`: abort). Displays detailed staging status tags (`staged: modified`, `staged: untracked`, `staged: renamed` with origin mappings, `staged: deleted`), supports range/keyword selections (`1-3, 5`, `all`), and defaults to `a` (Stage all files) on empty input. When a selected untracked file (or untracked directory, expanded via `git ls-files --others --exclude-standard`) shares a filename with an unstaged deletion, the deletion is staged automatically and reported, so a rename is never committed as a duplicated file.
- `show_commit_stats(staged)`: Prints colorized `git diff --cached --stat` with green `+` insertions, red `-` deletions, highlighted summary line, and per-extension file counts.
- `is_binary_file(filepath)`: Reads first 1 KB for null bytes to identify binary files, with checks for file existence to handle deleted files gracefully.
- `is_env_file(filepath)`: Strict matching for security-sensitive files — returns `True` for secret `.env`, `.env.*`, and `.envrc` files, but returns `False` for safe template/example files (`.env.template`, `.env.example`, `.env.sample`, `.env.dist`).
- `drop_env_files(files)`: Filters `.env` files out of a file list with a security warning. Applied when a resumed session restores its staged list from saved state, so pre-crash `.env` entries are re-validated before commit.
- `perform_git_pull()`: Executes `git pull --rebase` with intelligent fallback resolution for `fatal: Cannot rebase onto multiple branches.` The fallback target is resolved in order: current branch → the remote's default branch (`refs/remotes/<remote>/HEAD`) → `init.defaultBranch` → `main`.
- `should_exclude_from_ai(filepath)`: Checks if a file should be excluded from AI diff analysis. Returns `True` if the file extension is in `AI_PROMPT_EXCLUDED_EXTENSIONS` or if the file is detected as binary.

**Viewer (changes & commit history)**
- `prompt_startup_mode()`: Top-level startup menu shown before model selection (`1` commit [default], `2` open the viewer, `0` quit; `q`/`quit`/`exit` also accepted). Returns `'commit'`, `'viewer'` or `'quit'`; defaults to `'commit'` on Enter. Skipped entirely for non-interactive/CI runs and when `--model` is given, so automation goes straight to committing.
- `print_stat_block(stats_text)`: Colourised printer shared by the viewer. Renders `git --stat` output, colouring file names and the `+`/`-` bars and highlighting the `N files changed` summary line; commit/author/date/merge header lines are styled separately.
- `show_changed_files_viewer()`: Lists staged, unstaged/modified and untracked files grouped by state (reusing `get_git_files(include_details=True)`), then prints the staged `git diff --cached --stat` summary. Shows file names and change counts only — never file contents.
- `show_commit_detail(commit_hash)`: Prints a single commit's metadata and per-file `+/-` change bars (`git show --stat --no-color --date=local`), without any line-by-line diff.
- `browse_commit_history(per_page=20)`: Pages through `git log` (short hash, short date, author, subject) using real `--skip`/`-n` pagination with `n`/`p` page navigation and numeric selection to open a commit via `show_commit_detail()`.
- `browse_git_viewer()`: Read-only viewer menu (`1` changed files, `2` commit history, `3` back to start). Returns `None` on Back. Reachable from the startup mode menu and from the review loop via `w`; it never modifies the index or working tree.
- `prompt_select_model()`: Unchanged 3.5 / 3.1 / custom selection (the viewer now lives in the startup mode menu).

**Analysis**
- `truncate_diff_for_context(diff, max_chars_limit, max_tokens, prompt_overhead=2000)`: Truncates an oversized diff to the stricter of the character and token budgets. Reports the *original* token count in the truncation notice, and keeps a slice of the first line when that single line already exceeds the budget so the AI never receives an empty diff.
- `get_consolidated_diff_stats(staged, commit_mode)`: Computes consolidated git diff breakdown using `git diff --cached --numstat` (insertions, deletions, text files, binary files).
- `print_diff_breakdown(staged, commit_mode)`: Prints an `[INFO]` line showing consolidated diff statistics with colors and emojis (`📄 7 files │ ➕ 142 added │ ➖ 38 deleted │ (+104 net)`).
- `detect_conventional_scope(files)`: Maps file path prefixes (e.g., `ui/`, `db/`) to conventional commit scope labels.
- `detect_conventional_commits_usage()`: Checks last 20 commits — returns `True` if >50% follow `feat:`/`fix:` format. Extended to include `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`, `build`, `revert`.
- `check_spelling(text)`: Pipes text to system `aspell`; returns list of misspelled words.

**Configuration & environment**
- `load_config()`: Reads `.commitgenrc` (repo) or `~/.commitgenrc` (global) JSON for default settings.
- `check_dependencies()`: Validates Python 3.6+, `git` is on PATH; warns if `gh` is missing.
- `is_ci_environment()`: Returns `True` if any CI env var (`CI`, GITHUB_ACTIONS, GITLAB_CI, JENKINS_URL, TRAVIS) is set.

**Session recovery**
- `session_state_path()`: Returns the absolute path of the state file under the resolved `GIT_DIR` (`<GIT_DIR>/COMMITGEN_STATE`).
- `save_session_state(state)`: Persists current session dict to the state file. Includes commit mode, summary, description, bump choice, staged files, and version.
- `load_session_state()`: Reads back saved state; returns `None` if not present.
- `clear_session_state()`: Deletes the state file on clean exit.

**Hooks & CI**
- `check_precommit_hooks(non_interactive=False)`: Runs `pre-commit run --all-files` if `.pre-commit-config.yaml` exists; prompts to continue on failure. In non-interactive mode it aborts the commit (`False`) instead of prompting.
- `monitor_ci()`: Uses `gh run watch` to stream live CI output after a push. Checks for active runs, shows status, and monitors until completion.

**API**
- `call_gemini_api(api_key, model, prompt_text)`: POST to Gemini REST API with JSON schema enforcement, exponential-backoff retries (max 3), and a 60-second connection timeout to avoid hanging connections. Returns structured JSON with `summary`, `description`, and `_tokens` metadata (sent prompt tokens and received response tokens).
- `print_token_usage(ai_res)`: Prints an `[INFO]` line indicating the number of tokens sent to the AI and received in the response.

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
- Completely replaces the last commit message with new AI-generated content based on the combined diff of the original commit's changes and any newly staged changes (diffed against `HEAD~1`)
- Bypasses the original commit message to avoid anchoring bias
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
- AI prompt includes amend-specific context with original commit message for standard amend mode (skipped in fresh amend mode to prevent anchoring, which instead compares against `HEAD~1` to capture the entire combined change set)
- Version prefix added to commit message for all modes (prevents duplication)
- Description bullet points are guaranteed to be structured on new lines starting with `- ` via API response schema and prompt constraints.
- **Tag relocation**: Automatically detects if the commit being amended has any associated Git tags; if found, it prompts the user before relocating them locally and force-pushes them to remote (respects `auto_tag` settings; in non-interactive mode tags are moved automatically). Annotated tags are re-created with `git tag -f -a` and their original message preserved via `move_tag_to_head()`.
- **Rollback on failure**: If the amend (or a new commit) fails, version files and `CHANGELOG.md` are restored from the pre-mutation snapshot and unstaged, so a retry does not leave version drift.
- **Tag Approval**: Prompts user for confirmation before tagging new commits or moving tags on amend (configurable/bypassable via `auto_tag` settings). Before creating a tag, the remote is checked for an exact `refs/tags/<name>` match; in non-interactive mode tag creation is skipped unless `auto_tag: true`.
- **Format Preservation**: Version prefix (e.g. `1.2.3 - ...` vs `v1.2.3 - ...`) matches the original format of the base version instead of always forcing a leading `v`.

### Configuration Details

**Default Configuration:**
```python
{
    "default_bump": "patch",
    "max_diff_length": 500000,
    "auto_push": False,
    "auto_pull": True,
    "model": "gemini-3.5-flash-lite",
    "auto_tag": False,  # Set to True to skip the tagging confirmation prompt
    "co_author": False  # Set to True to enable Co-Authored-By attribution by default
}
```

**Configuration File Search Order:**
1. `.commitgenrc` (current repository)
2. `~/.commitgenrc` (user home directory)
3. Built-in defaults

**Environment Variables:**
- `GEMINI_API_KEY`: Gemini API key (required)
- `GEMINI_MODEL`: Model name (optional, overrides config)
- `CO_AUTHOR` / `CO_AUTHORED_BY`: Enable or disable Co-Authored-By attribution trailer (optional)
- `NO_COLOR`: Disable colored output (optional)

**Entrypoint**
- `main()`: Full orchestration — dependency check → Git directory resolution (`init_git_dir()`; exits with a clear error when not inside a repository) → flag parsing (`--dry-run`, `--non-interactive`, `--co-author`, `--no-co-author`) → **startup banner (project folder name & absolute directory path)** → **startup mode menu (`prompt_startup_mode()`: commit / browse viewer / quit; skipped for non-interactive and `--model` runs, and the viewer never touches the index or working tree)** → **startup `git pull --rebase` check (with `git stash` protection and interactive prompt loop default `[n]` recovery on unstaged changes failure)** → session recovery (saved staged lists are re-filtered through `drop_env_files()`) → staging (with granular status tags `[staged: modified]`, `[staged: untracked]`, `[staged: renamed]`, `[staged: deleted]` and rename mappings in both picker and confirmation summary) → **commit mode selection (new/amend/fresh amend)** → version detection (with a regression guard for custom/context versions) → pre-commit hooks → AI call (with interactive error recovery: 30s retry, manual entry, or default summary) → **Co-Authored-By attribution prompt (default `[n]`)** → interactive review (with validation warnings, `t` toggle hotkey, `d` diff pager and `w` viewer shortcut) → commit/tag/amend (with rollback of version files/changelog on failure) → push (prompts default to yes; retries on failure; runs optional interactive pre-push `git pull --rebase` safety check with prompt loop default `[n]` recovery on unstaged changes failure; force-push option for amended commits, refused in non-interactive mode; or triggers interactive GitHub repository creation via `gh repo create` if no remote is configured) → PR creation → CI monitor → session clear. Returns boolean indicating whether to restart the session via `prompt_exit_or_restart(NON_INTERACTIVE)` (triggered on clean working tree, aborted staging, cancelled commit, dry-run completion, or post-commit workflow).
- Script runner (`if __name__ == "__main__":`): Wraps `main()` in a bounded while-loop (max 10 restarts). It handles `KeyboardInterrupt` and `EOFError` (closed stdin) by exiting cleanly, and uses `prompt_restart()` for the unexpected-exception restart prompt so EOF can never crash-loop the runner.

**Command-line flags:**
- `--dry-run`: Preview commit without making changes
- `--non-interactive`: Headless/CI mode. No prompts: auto-stages everything, auto-selects the commit action, uses the highest detected version, skips tag creation unless `auto_tag: true`, skips pushing unless `auto_push: true`, refuses force push for amended commits, honors `auto_pull` silently, skips PR creation/CI monitoring, and aborts on pre-commit hook failure.
- `--co-author` / `--no-co-author`: Force enable or disable Co-Authored-By attribution trailer

## 2. register.py

This helper script automates the installation of the Explorer right-click context menu by dynamically generating registry paths.

- **Dynamic Path Resolution**: Reads the current folder path of `git_commit.py` and correctly escapes backslashes for Registry Editor compliance (`\\`).
- **File Generation**: Outputs `register.reg` (to import) and `unregister.reg` (to delete the keys).
- **Registry Structure**:
  - `HKEY_CLASSES_ROOT\Directory\shell\GitCommitAI`: When right-clicking a folder in explorer.
  - `HKEY_CLASSES_ROOT\Directory\Background\shell\GitCommitAI`: When right-clicking inside an open folder background.

> **Note:** The generated command nests quotes (`cmd.exe /k "cd /d "%V%" && python "<script>""`). This is the conventional Explorer context-menu pattern and works in practice, but it is fragile for paths containing both quotes and special characters. Registry editing is Windows-only and is not covered by the automated test suite.

## 3. tests/

`tests/test_git_commit.py` is an offline pytest regression suite for the hardened helpers. It uses temporary directories/repositories, never touches the network, and covers:

- Multi-part version-prefix stripping and `semver_key()` / `is_version_regression()`
- `drop_env_files()` filtering and `redact_secrets()` behaviour
- `.env` precedence (real environment variables are never overridden)
- `update_changelog()` insert / replace / append behaviour
- `truncate_diff_for_context()` single-overlong-line handling
- `snapshot_files()` / `restore_files_snapshot()` rollback round-trip
- `prompt_restart()` surviving a closed stdin
- `init_git_dir()` + session state resolution from a subdirectory
- `move_tag_to_head()` preserving annotated tags
- Rename pairing in `prompt_stage_files()`
- `check_remote_tag()` requiring an exact ref match (against a local bare remote)
- Viewer: `prompt_startup_mode()` commit/viewer/quit selection, grouped output from `show_changed_files_viewer()`, and commit history browsing / detail stats in `browse_commit_history()`

Run with:
```bash
python -m pytest
```
