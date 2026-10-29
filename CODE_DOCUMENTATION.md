# Code Documentation

This document describes the technical implementation details of the AI Git Commit & Version Bumper Tool.

## File Structure

```
git-commit/
├── git_commit.py              # Core CLI tool logic (~3,600 lines)
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
- `GIT_DIR`: Absolute path of the repository's Git directory. Initialized to `.git` and replaced by `init_git_dir()` at startup so session state and commit templates resolve correctly from any subdirectory. `ORIGINAL_CWD` preserves the launch directory for the banner, while the process itself runs from the repository root.
- `SECRET_PATTERNS`: List of `(category, compiled_regex)` pairs for high-confidence secret formats (AWS access keys, GitHub/Gemini tokens, bearer tokens, credentials embedded in URLs) used by `redact_secrets()` to scrub the AI prompt.

### Main Functions

**Core helpers**
- `c(color_code)`: Conditional color helper that returns the color code if `USE_COLORS` is enabled, otherwise returns an empty string. Prevents ANSI color code leaking on non-TTY environments.
- `print_success(msg)`, `print_info(msg)`, `print_warn(msg)`, `print_error(msg)`: Colored output helpers with NO_COLOR/c() support.
- `print(*args, **kwargs)`: Module-level wrapper around Python's built-in `print()` that honours `sep`, applies Unicode→ASCII fallbacks on encode errors, and tracks the last physical line written in `_last_output_blank`. When the previous line was already blank, it strips leading newlines from the new text, so a banner written as `print(f"\n...")` can never stack on top of an existing separator row. Tracking is `end=`-aware (a custom terminator is accounted for, and `end=""` inline output is not mistaken for a blank row) and only applies to stdout — a `file=` write leaves the screen state untouched.
- `print_blank()`: Writes at most one blank separator line. It is a no-op when the previous output already ended on a blank row, giving the whole UI a single, predictable vertical rhythm instead of occasional double empty lines. `_mark_restart()` resets the tracker after clearing the screen, so stale state cannot add a stray blank line at the top of a restarted session.
- `flush_input_buffer()`: Flushes unconsumed user keystrokes from the console input buffer. On Windows, it clears both the C runtime keyboard buffer (`msvcrt.kbhit()` / `msvcrt.getwch()`) and the Win32 console input queue (`kernel32.FlushConsoleInputBuffer` on `STD_INPUT_HANDLE`). On POSIX systems, it issues `termios.tcflush(sys.stdin, termios.TCIFLUSH)`. Safely bypassed if `sys.stdin.isatty()` is `False` to maintain compatibility with piped inputs, automated tests, and CI/CD pipelines.
- `input(prompt="", flush=True)`: Module-level wrapper around Python's built-in `input()` that invokes `flush_input_buffer()` immediately before rendering the prompt (pass `flush=False` to skip the purge — used by the multi-line description editor so pasted blocks are not truncated). Ensures the user only provides input after seeing the display prompt, preventing accidental double-Enter keypresses or keystrokes entered during background operations (e.g. AI calls, git diff calculations) from prematurely auto-advancing the next prompt. Prompts are commonly written as `input("\n...")` for spacing; when the previous output already ended on a blank row, the wrapper strips those leading newlines so the prompt never renders after two empty lines. After the answer is read, the blank-row tracker is reset because the prompt and typed answer are visible output.
- `prompt_restart(message=...)`: Wraps the restart confirmation prompt and returns `False` safely on `EOFError`/`KeyboardInterrupt`, so a closed stdin (piped input or CI) never crashes or loops the top-level runner.
- `_mark_restart()`: Increments the shared restart counter, clears the console and reports the run count — the single place where restart bookkeeping happens.
- `request_restart(message=...)`: Combines the restart cap check, `prompt_restart()` and `_mark_restart()`; every restart path (main's error handler, the exit prompt, `__main__`'s exception handler) goes through it so the counter and messaging can never drift apart.
- `show_startup_banner()`: Displays the CommitGen header banner alongside the active project folder name and absolute directory path.
- `format_co_author_trailer(model)`: Generates the standardized git commit trailer `Co-authored-by: Custom Tool By BKK ({model}) <{model}@users.noreply.github.com>`.
- `prompt_exit_or_restart(non_interactive=False)`: Prompts the user with `Press Enter to exit, or 'r' to restart:` and returns `True` if `r` is entered (clearing the console, displaying completed runs and max 10 restart limit info, and signaling the runner loop to re-execute `main()`), or `False` to exit cleanly. Bypassed in non-interactive/CI mode.
- `load_dotenv()`: Parses `.env` files without external dependencies, handling optional `export` prefixes, inline `#` comments on unquoted values, single/double-quoted values (with escapes), a trailing comment after the closing quote, and a UTF-8 BOM. Precedence (lowest → highest) is tool-directory `.env`, then project `.env` (repository root, since the tool runs from there); real environment variables always win because a key already present in `os.environ` is never overwritten. `.env` files themselves use first-wins semantics.
- `run_git_cmd(args, strip=True)`: Subprocess wrapper for git commands; returns stdout or `None`. Runs with a 300-second timeout and `GIT_TERMINAL_PROMPT=0` so a credential prompt can never hang the tool.
- `run_git_checked(args, timeout=300)`: Same timeout/non-interactive environment as `run_git_cmd()`, but raises `CalledProcessError`/`TimeoutExpired` on failure. Used for mutating commands (commit, amend, tag, push, pull, stash pop) so existing `try`/`except` flows keep working.
- `DryRunIndexGuard`: Context manager that snapshots the raw `.git/index` before a dry run and restores it on exit (atomically, removing an index that did not exist before). Dry-run previews can therefore stage files and still leave the repository untouched.
- `init_git_dir()`: Resolves the repository's Git directory via `git rev-parse --absolute-git-dir` and stores it in the `GIT_DIR` global, then changes the process working directory to `git rev-parse --show-toplevel` (the repository root), because `git status` reports root-relative paths while `git add`/`git diff -- <path>` resolve against the cwd — running from a subdirectory would silently break staging and diffs. The launch directory is preserved in `ORIGINAL_CWD` for display. Called at the start of `main()`; returns `False` (and the tool exits) when not inside a Git repository.
- `redact_secrets(text)`: Replaces high-confidence secret patterns with `[REDACTED]` before text is sent to the Gemini API. It prints the match count plus a per-category line with a masked preview (e.g. `AWS access key id: AKIA****************`), so genuine detections can be told apart from fixtures. Only the prompt is affected — the committed diff is never modified.
- `_mask_secret(value)`: Returns a short, non-reversible preview of a matched secret (a 4-character prefix followed by asterisks), used by `redact_secrets()` for diagnostics.
- `detect_version(prompt=True)`: Priority order — checks git tags, git commit messages, `package.json`, and `pyproject.toml`. Tags and commit-message prefixes may be standard SemVer, multi-part (`v1.1.1.11`), or carry pre-release/build suffixes (`v1.2.3-rc1`). Numeric version values are coerced to strings, and duplicates are compared with the `v` prefix stripped (`1.2.3` and `v1.2.3` are one choice); sources that agree are merged into a single label naming every source (e.g. `v1.0.0 (from git tag, git commit)`). Collects all detected versions, parses their release cores into integer tuples (pre-release/build metadata is ignored), and sorts the choices descending so the largest version is listed first (Option 1). `pyproject.toml` is read through `find_pyproject_version()`, so detection uses the same `[project]`/`[tool.poetry]` sections that `update_version_in_files()` rewrites. In interactive mode, if a lower version (or a custom lower version) is selected, it asks for user confirmation before proceeding, allowing the user to confirm or return to the selection list. In non-interactive mode (or when called with `prompt=False`, as amend modes do), it auto-selects the highest available version. For custom base versions, it automatically prepends `v` if missing and warns when the value is not strict SemVer.
- `find_pyproject_version(path="pyproject.toml")`: Returns the first `version = "..."` value found under `[project]` or `[tool.poetry]`, ignoring version keys in other tables such as `[tool.commitizen]`. Shared by detection and update so the two can never disagree about which line is authoritative.
- `validate_commit_message(message)`: Validates commit format (72 char limit, conventional format, blank line). Returns list of issues.
- `check_remote_tag(tag_name, remote=None)`: Queries the remote with `git ls-remote --tags --refs <remote> refs/tags/<tag_name>` (30-second timeout) and matches the exact ref, so a short name such as `v1.2.3` cannot glob-match `v1.2.34`. Checked before the local tag is created. Returns `True`/`False` on a successful check, or `None` when the remote is unreachable — an unknown answer is reported to the user instead of silently passing as "tag absent".
- `get_default_remote()`: Resolves the remote used for tag checks/pushes: the current branch's configured remote, else the first configured remote, else `origin`.
- `version_already_tagged(version: str) -> bool`: Checks if version tag already exists locally before allowing a version bump.
- `move_tag_to_head(tag)`: Relocates a tag to `HEAD` while preserving annotated tags. If `git cat-file -t refs/tags/<tag>` reports `tag`, it is re-created as an annotated tag with `git tag -f -a` and its original message (including an empty message) instead of being silently downgraded to lightweight. A GPG signature signs the old target, so it is dropped with a warning when the tag moves. Returns `True` on success and `False` when the tag could not be inspected or moved.
- `get_branch_version_info()`: Returns sanitized branch name if not on `main`/`master`/`HEAD`.
- `is_valid_semver(version: str) -> bool`: Regex validator for `vX.Y.Z(-pre)(+build)` format.
- `semver_key(version_str)`: Extracts the numeric tuple of a version's release core (e.g. `v1.2.3.4` → `(1, 2, 3, 4)`), supporting multi-part versions. Pre-release and build metadata (`-rc1`, `+build.7`) are ignored, so a prerelease keys the same as the release it precedes.
- `is_version_regression(proposed, current)`: Returns `True` when the proposed version is numerically lower than the current one (zero-padded tuple comparison). Used to guard custom and context-detected versions.
- `get_recent_commits(n=3)`: Returns last N commit lines as context for the AI prompt.
- `get_last_commit_message()`: Returns the full message of the last commit (used for amend mode context).
- `load_commit_template()`: Loads `<GIT_DIR>/COMMIT_TEMPLATE` or `.github/PULL_REQUEST_TEMPLATE.md`; unreadable templates are skipped with a warning instead of crashing the run.
- `extract_issue_references()`: Finds `#NNN` patterns in branch name and last 5 commits.
- `update_changelog(version, summary, description)`: Updates `CHANGELOG.md` if it exists. Inserts a new section below an `[Unreleased]` section (or before the first existing version heading, or appends when none exist), and replaces the entry for an already-present version in place (heading → next `## ` heading, or end of file) to avoid duplicates on re-commit/fresh-amend; both `## [vX]` and `## [X]` heading forms are recognised. Staging is checked and reported. Returns `{path: written_content}` for rollback safety.

**Version management**
- `increment_version(version_str, bump_type)`: Semver bump; also handles `custom:x.y.z` passthrough. Bumps the release core (pre-release/build metadata is dropped) and preserves the original `v` prefix.
- `update_version_in_files(new_version)`: Edits the version field in `package.json` and in the `[project]` / `[tool.poetry]` section of `pyproject.toml` (function-based regex replacement — a backreference string would raise `invalid group reference` for any version starting with a digit). The caller stages these files so the version updates are included in the same commit. Returns `{path: written_content}` for rollback safety.
- `snapshot_files(paths)`: Reads file contents into an in-memory rollback snapshot (`None` for files that do not exist).
- `restore_files_snapshot(snapshot, tool_writes=None)`: Restores snapshotted file contents and unstages them (`git reset`, which also works before the first commit where `git restore --staged` fails). Only paths recorded in `tool_writes` (the tool's own post-write contents) are eligible: a file the tool never wrote is left completely alone, and a file the user edited after the tool wrote it is kept as well — so manual edits and staging are never discarded. Called on commit/amend failure so version files and the changelog never drift when a commit fails and is retried.

**File & staging**
- `get_git_files(include_details=False, enforce_env=True)`: Parses `git status --porcelain -z` (null-terminated) into staged/unstaged/untracked lists, optionally returning file metadata (`details` dict with granular staged/unstaged status, rename origin paths, and an `unmerged` badge for merge conflicts; `T` type changes are classified too). With `enforce_env=True` it detects staged `.env` files, resets **both** sides of any rename (so a `.env → foo` rename cannot leave the deletion of `.env` staged), verifies with `git diff --cached` that **no path involved in the rename** remains staged (a failed reset of the benign-looking `foo` destination aborts too), and raises `RuntimeError` when the unstage could not be verified (fail closed). Read-only screens pass `enforce_env=False`, which lists `.env` files without touching the index. A failed `git status` raises instead of masquerading as a clean tree. Use `get_unmerged_files()` for the explicit list of unresolved merge conflicts.
- `verify_no_secrets_staged()`: Final fail-closed check run immediately before every commit/amend. Scans the index for staged `.env` paths and for staged renames where either side is an `.env` file, raising `RuntimeError` instead of letting the secret reach `git commit`.
- `get_unmerged_files()`: Returns the explicit list of paths with unresolved merge conflicts (`git diff --name-only --diff-filter=U`). Used to block committing, and by the picker to refuse staging, while conflicts exist.
- `prompt_amend_or_new()`: Interactive prompt to select commit mode - new (`n` [default]), amend (`a`), or fresh amend (`f`). Displays options in distinct ANSI colors and defaults to `n` on empty input.
- `print_review_options(model, include_co_author, commit_mode)`: Renders the review-screen options as a compact left-gutter table. Group names sit in a left gutter (**Commit**, **AI**, **Version**, **Inspect**, **Cancel**) with short option labels (`c`/`e`, `g` new-only, `m`, `t`, `v`/`h` new-only, `d`/`w`/`s`, `x`); each option occupies exactly one line and there are no blank lines between groups, keeping the menu short in a small console window. The AI-regeneration and Version groups are omitted in amend modes (`m` stays grouped under **AI**), and `t` shows the current toggle state as `Co-Authored-By: yes/no`.
- `parse_index_selection(input_str, max_len)`: Parses comma-separated numbers, range expressions (e.g. `1-3, 5`), and keywords (`all`, `*`) into validated 0-based indices for staging and unstaging operations. Out-of-range numbers — and ranges lying entirely outside the list — raise `ValueError`; partially valid ranges (e.g. `1-999`) are clipped.
- `prompt_stage_files(staged, unstaged, untracked)`: Interactive file picker featuring a top status color legend (`staged`: green, `modified`: yellow, `untracked`: red), section divider lines, and color-coded action hotkeys (`a`: stage all [default], `u`: unstage, `r`: refresh status, `p`: proceed, `q`: abort). Refuses to run (`"quit"`) while unresolved merge conflicts exist. Displays detailed staging status tags (`staged: modified`, `staged: untracked`, `staged: renamed` with origin mappings, `staged: deleted`, `staged: typechange`, conflict badges), supports range/keyword selections (`1-3, 5`, `all`), and defaults to `a` (Stage all files) on empty input. Every `git add`/`git reset` uses `-- <path>` (so a filename beginning with `-` is never treated as an option) and its return code is checked before printing success. Unstaging uses `git reset`, which also works before the first commit. When a selected untracked file (or untracked directory, expanded via `git ls-files --others --exclude-standard`) shares a filename with an unstaged deletion, the deletion is staged automatically and reported, so a rename is never committed as a duplicated file. The list layout is shared with the read-only viewer through `build_file_entries()`, `status_color()`, `print_status_legend()`, `print_file_rows()` and `print_file_picker()`.
- `show_commit_stats(staged)`: Prints colorized `git diff --cached --stat` with green `+` insertions, red `-` deletions, highlighted summary line, and per-extension file counts.
- `is_binary_file(filepath)`: Reads first 1 KB for null bytes to identify binary files, with checks for file existence to handle deleted files gracefully.
- `is_env_file(filepath)`: Matching for security-sensitive files — returns `True` for secret `.env` and `.env.*` files, but returns `False` for safe template/example files (`.env.template`, `.env.example`, `.env.sample`, `.env.dist`). `.envrc` (direnv config) is deliberately **not** treated as a secret (it is commonly tracked project config); a warning is printed instead when a staged `.envrc` is detected at commit time.
- `drop_env_files(files)`: Filters `.env` files out of a file list with a security warning. Applied when a resumed session restores its staged list from saved state, so pre-crash `.env` entries are re-validated before commit.
- `perform_git_pull()`: Executes `git pull --rebase` with intelligent fallback resolution for `fatal: Cannot rebase onto multiple branches.` The fallback target is resolved in order: current branch → the remote's default branch (`refs/remotes/<remote>/HEAD`) → `init.defaultBranch` → `main`.
- `should_exclude_from_ai(filepath)`: Checks if a file should be excluded from AI diff analysis. Returns `True` if the file extension is in `AI_PROMPT_EXCLUDED_EXTENSIONS` or if the file is detected as binary.

**Viewer (changes & commit history)**
- `prompt_startup_mode()`: Top-level startup menu shown before model selection (`1` commit [default], `2` open the viewer, `0` quit; `q`/`quit`/`exit` also accepted). Returns `'commit'`, `'viewer'` or `'quit'`; defaults to `'commit'` on Enter. Skipped entirely for non-interactive/CI runs and when `--model` is given, so automation goes straight to committing.
- Shared file-list formatting (used by both the staging picker and the viewer): `build_file_entries(staged, unstaged, untracked, details)` builds the ordered `(path, status, display_name)` rows; `status_color(status)` maps a status to its badge colour; `print_status_legend()` prints the `staged/modified/untracked` colour legend; `print_file_rows(entries)` prints the numbered `[status] name` rows; and `print_file_picker(title, entries)` prints the whole block (title, legend, dividers, rows). `prompt_stage_files()` and the viewer both use these, so the two screens look identical.
- `print_stat_block(stats_text)`: Colourised printer shared by the viewer. Renders `git --stat` output, colouring file names and the `+`/`-` bars and highlighting the `N files changed` summary line; commit/author/date/merge header lines are styled separately. Only lines whose part after `|` matches the stat shape (counts, `Bin ...`, or a size) are parsed, so a commit subject containing `|` is printed verbatim instead of being mangled.
- `show_file_change_stats(path)`: Read-only per-file `git diff --stat` view (staged and/or unstaged) opened by selecting a file number in the viewer. For untracked entries it reports line count / child-file count instead, since no diff exists. Never shows file contents.
- `show_changed_files_viewer()`: Read-only screen that reuses the staging picker's exact layout (title, legend, dividers, numbered `[status] name` rows for staged / unstaged / untracked files, plus conflict badges) and adds file selection to view per-file `+/-` stats via `show_file_change_stats()`, plus `r` refresh and `b` back. Calls `get_git_files(enforce_env=False)`, so it can never modify the index. File names and change counts only — never file contents.
- `show_commit_detail(commit_hash)`: Prints a single commit's metadata and per-file `+/-` change bars (`git show --stat --no-color --date=local`), without any line-by-line diff.
- `browse_commit_history(per_page=20)`: Pages through `git log` (short hash, local date and time, author, subject) using real `--skip`/`-n` pagination with `n`/`p` page navigation and numeric selection to open a commit via `show_commit_detail()`.
- `browse_git_viewer()`: Read-only viewer menu (`1` changed files, `2` commit history, `3` back to start). Returns `None` on Back. Reachable from the startup mode menu and from the review loop via `w`; it never modifies the index or working tree.
- `prompt_select_model(current_model=...)`: 3.5 / 3.1 / custom selection (the viewer now lives in the startup mode menu). The current model is displayed and kept when Enter is pressed, so opening the switcher and confirming with Enter no longer resets the model.

**Analysis**
- `truncate_diff_for_context(diff, max_chars_limit, max_tokens, prompt_overhead=2000)`: Truncates an oversized diff to the stricter of the character and token budgets. The token budget and character budget are clamped to a floor, so a bad `max_input_tokens` (smaller than the prompt overhead) cannot turn the diff into an empty string. Reports the *original* token count in the truncation notice, and keeps a slice of the first line when that single line already exceeds the budget so the AI never receives an empty diff.
- `get_consolidated_diff_stats(staged, commit_mode)`: Computes consolidated git diff breakdown using `git diff --cached --numstat` (insertions, deletions, text files, binary files). For `fresh_amend` it diffs against `HEAD~1` only when that parent exists, so a root commit still reports stats.
- `print_diff_breakdown(staged, commit_mode)`: Prints an `[INFO]` line showing consolidated diff statistics with colors and emojis (`📄 7 files │ ➕ 142 added │ ➖ 38 deleted │ (+104 net)`).
- `detect_conventional_scope(files)`: Maps file path prefixes (e.g., `ui/`, `db/`) to conventional commit scope labels.
- `detect_conventional_commits_usage()`: Checks last 20 commits — returns `True` if >50% follow `feat:`/`fix:` format. Extended to include `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `ci`, `build`, `revert`.
- `check_spelling(text)`: Pipes text to system `aspell`; returns list of misspelled words.

**Configuration & environment**
- `load_config()`: Reads `.commitgenrc` (repo) or `~/.commitgenrc` (global) JSON for default settings. Values are validated/coerced by `_validate_config()` (strings for `default_bump`/`model` with an allowed-bump check, positive integers for the size limits, booleans for the toggles); invalid entries fall back to the defaults with a warning instead of crashing mid-commit.
- `check_dependencies()`: Validates Python 3.7+, `git` is on PATH; warns if `gh` is missing.
- `is_ci_environment()`: Returns `True` if any CI env var (`CI`, GITHUB_ACTIONS, GITLAB_CI, JENKINS_URL, TRAVIS) is set to a truthy value — explicit falsy values (`false`, `0`, `no`, `off`) do not force non-interactive mode.

**Session recovery**
- `session_state_path()`: Returns the absolute path of the state file under the resolved `GIT_DIR` (`<GIT_DIR>/COMMITGEN_STATE`).
- `save_session_state(state)`: Persists current session dict to the state file (temp file + `os.replace`, so a crash mid-write cannot corrupt the previous state). Includes commit mode, summary, description, bump choice, staged files, and version. No-op in dry-run mode.
- `load_session_state()`: Reads back saved state; returns `None` if not present or structurally invalid (non-dict JSON).
- `clear_session_state()`: Deletes the state file on clean exit. No-op in dry-run mode.

**Hooks & CI**
- `check_precommit_hooks(non_interactive=False, staged_files=None)`: Runs `pre-commit run` (staged files only, 30-minute timeout) if `.pre-commit-config.yaml` exists; hook auto-fixes are re-staged with `git add -A -- <path>` for `staged_files` (result checked) before prompting to continue on failure. Files created by the hooks (for example the destination of a rename) are detected by diffing the untracked set from before/after the run and staged too, so "commit anyway" never ships half a rename. Returns `True` on success, `'skip_verify'` when the user chooses "Commit anyway" (the commit then runs with `--no-verify`, since installed hooks would otherwise run again and fail identically), or `False` to abort — which is what non-interactive mode does instead of prompting.
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
- Version prefix added to commit message when a bump is selected (e.g., `v1.2.3 - feat: add feature`); a `none` bump ships without a prefix, and the review preview shows exactly the message that will be committed

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
- Version prefix added to amend-mode commit messages (new commits get one only when a bump is selected; a `none` bump ships without a prefix, and the review preview matches the final message)
- Description bullet points are guaranteed to be structured on new lines starting with `- ` via API response schema and prompt constraints.
- **Tag relocation**: Automatically detects if the commit being amended has any associated Git tags; if found, it prompts the user before relocating them locally and force-pushes them to remote (respects `auto_tag` settings; in non-interactive mode tags are moved automatically). Annotated tags are re-created with `git tag -f -a` and their original message preserved via `move_tag_to_head()`.
- **Rollback on failure**: If the amend (or a new commit) fails, version files and `CHANGELOG.md` are restored from the pre-mutation snapshot and unstaged, so a retry does not leave version drift. The tool records exactly what it wrote, so edits made by the user after that point are preserved rather than clobbered.
- **Tag Approval**: Prompts user for confirmation before tagging new commits or moving tags on amend (configurable/bypassable via `auto_tag` settings). Before creating a tag, the remote is checked for an exact `refs/tags/<name>` match; in non-interactive mode tag creation is skipped unless `auto_tag: true`.
- **Format Preservation**: Version prefix (e.g. `1.2.3 - ...` vs `v1.2.3 - ...`) matches the original format of the base version instead of always forcing a leading `v`.

### Configuration Details

**Default Configuration:**
```python
{
    "default_bump": "patch",
    "max_diff_length": 500000,
    "max_input_tokens": 900000,
    "auto_push": False,
    "auto_pull": True,
    "model": "gemini-3.5-flash-lite",
    "auto_tag": False,  # Set to True to skip the tagging confirmation prompt
    "co_author": False,  # Set to True to enable Co-Authored-By attribution by default
    "show_tree": True  # Set to False to skip the repository structure listing
}
```

**Configuration File Search Order:**
1. `.commitgenrc` (current repository)
2. `~/.commitgenrc` (user home directory)
3. Built-in defaults

Values are validated on load: `default_bump` must be `patch`/`minor`/`major`/`none` (or `custom:<ver>`), `model` must be a non-empty string, `max_diff_length`/`max_input_tokens` must be positive integers, and the toggles must be booleans (a string like `"false"` is coerced). Invalid entries fall back to the defaults with a warning instead of crashing later in the commit flow.

**Environment Variables:**
- `GEMINI_API_KEY`: Gemini API key (required to generate commits; the read-only viewer works without it)
- `GEMINI_MODEL`: Model name (optional, overrides config)
- `CO_AUTHOR` / `CO_AUTHORED_BY`: Enable or disable Co-Authored-By attribution trailer (optional)
- `NO_COLOR`: Disable colored output (optional)

**Entrypoint**
- `main()`: Thin wrapper around `_main_impl()`. When `--dry-run` is present it runs the whole flow inside `DryRunIndexGuard`, so the index snapshot is restored on every exit (including exceptions).
- `_main_impl()`: Full orchestration — Git directory resolution (`init_git_dir()`; exits with a clear error when not inside a repository, and moves the process to the repository root) → `.env`/config loading and dependency check → flag parsing (`--dry-run`, `--non-interactive`, `--co-author`, `--no-co-author`) → **startup banner (project folder name & absolute directory path)** → **startup mode menu (`prompt_startup_mode()`: commit / browse viewer / quit; skipped for non-interactive and `--model` runs, and the viewer never touches the index or working tree; the `GEMINI_API_KEY` check happens only after this menu, so viewer-only use needs no API key)** → **startup `git pull --rebase` check (with `git stash` protection and interactive prompt loop default `[n]` recovery on unstaged changes failure)** → session recovery (saved staged lists are re-filtered through `drop_env_files()`) → staging (with granular status tags `[staged: modified]`, `[staged: untracked]`, `[staged: renamed]`, `[staged: deleted]` and rename mappings in both picker and confirmation summary; blocked while unresolved merge conflicts exist) → **commit mode selection (new/amend/fresh amend)** → version detection (with a regression guard for custom/context versions; agreeing sources merged into one labeled choice) → pre-commit hooks (a "Commit anyway" override sets `--no-verify` for the later commit) → AI call (with interactive error recovery: 30s retry, manual entry, or default summary) → **Co-Authored-By attribution prompt (default `[n]`)** → interactive review (with validation warnings, grouped options via `print_review_options()`, `t` toggle hotkey, `d` diff pager and `w` viewer shortcut) → commit/tag/amend (dirty aux files require confirmation before auto-staging; `verify_no_secrets_staged()` runs immediately before every commit/amend; rollback of version files/changelog on failure; tag creation is tracked separately from the tag request so only a tag this run actually created is pushed) → push (prompts default to yes; retries on failure; runs optional interactive pre-push `git pull --rebase` safety check with prompt loop default `[n]` recovery on unstaged changes failure; force-push option for amended commits, refused in non-interactive mode; or triggers interactive GitHub repository creation via `gh repo create` if no remote is configured) → PR creation → CI monitor → session clear. Returns boolean indicating whether to restart the session via `prompt_exit_or_restart(NON_INTERACTIVE)` (triggered on clean working tree, aborted staging, cancelled commit, dry-run completion, or post-commit workflow).
- Script runner (`if __name__ == "__main__":`): Wraps `main()` in a bounded while-loop (max 10 restarts). It handles `KeyboardInterrupt` and `EOFError` (closed stdin) by exiting cleanly, and uses `prompt_restart()` for the unexpected-exception restart prompt so EOF can never crash-loop the runner.

**Command-line flags:**
- `--dry-run`: Preview the commit message without committing. The startup pull/stash, pre-commit hooks, session-state writes, version/changelog writes, commit, tag and push are skipped, and `main()` restores the raw index snapshot on exit, so preview staging is rolled back and the tree, index and refs are left as they were found.
- `--non-interactive`: Headless/CI mode. No prompts: auto-stages everything, auto-selects the commit action, uses the highest detected version, skips tag creation unless `auto_tag: true`, skips pushing unless `auto_push: true`, refuses force push for amended commits, honors `auto_pull` silently before pushes (the startup pull is skipped), skips PR creation/CI monitoring, and aborts on pre-commit hook failure.
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

`tests/test_git_commit.py` is an offline pytest regression suite (68 tests) for the hardened helpers. It uses temporary directories/repositories, never touches the network, and covers:

- Multi-part and pre-release tag/commit-prefix detection, version-prefix stripping, and `semver_key()` / `is_version_regression()`
- `drop_env_files()` filtering and `redact_secrets()` behaviour
- `.env` precedence (real environment variables are never overridden) and the parser handling `export`, inline comments, quoted values with trailing comments, and BOM
- `update_changelog()` insert / replace / append behaviour, `[Unreleased]` placement and `## [X]` heading forms
- `update_version_in_files()` and `find_pyproject_version()` for `package.json` / `pyproject.toml`, including the section-aware `[project]` vs `[tool.commitizen]` consistency
- `truncate_diff_for_context()` single-overlong-line handling and the bad-token-budget clamp
- `snapshot_files()` / `restore_files_snapshot()` rollback round-trip (including keeping user edits the tool never wrote, and edits made after the tool wrote)
- `prompt_restart()` surviving a closed stdin
- `init_git_dir()` + session state resolution from a subdirectory, and staging root/nested files when launched from one
- `move_tag_to_head()` preserving annotated tags, including empty-message annotated tags
- Rename pairing in `prompt_stage_files()`, pre-commit hook rename re-staging, and unstaging before the first commit (no HEAD)
- `.env` safety: staged rename `.env → foo` resets both sides; a failed destination reset and the final `verify_no_secrets_staged()` check fail closed; the viewer never mutates the index
- Merge conflicts and type changes: parsing (`UU`/`T` codes), `get_unmerged_files()`, and the picker refusing to stage while conflicts exist
- `check_remote_tag()` requiring an exact ref match (against a local bare remote)
- `load_config()` validation of invalid `.commitgenrc` values
- `check_precommit_hooks()` "commit anyway" → `--no-verify` contract
- Dry-run index restoration and session-state inertness, and `is_ci_environment()` falsy values
- Viewer: `prompt_startup_mode()` commit/viewer/quit selection, picker-style output from `show_changed_files_viewer()` (shared legend/badges), per-file stats via `show_file_change_stats()`, and commit history browsing / detail stats in `browse_commit_history()`
- Review options grouping: `print_review_options()` for new vs amend modes (Version group hidden and `g` omitted when amending)

Run with:
```bash
python -m pytest
```
