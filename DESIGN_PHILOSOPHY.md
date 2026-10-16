# Design Philosophy

This document outlines the core architectural and design decisions behind the AI Git Commit & Version Bumper Tool.

## 1. Zero External Dependencies
- **Motivation**: Tools like this should run instantly in any repository on any machine without requiring a virtual environment, `pip install`, or node module setups.
- **Implementation**: Written in vanilla Python 3 utilizing `urllib.request` instead of `requests` and a lightweight manual `.env` file parser instead of `python-dotenv`.

## 2. High Efficiency & Cost Minimization
- **Motivation**: Minimize latency, API payload sizes, and Gemini API token consumption.
- **Implementation**:
  - Uses cost-effective model (`gemini-3.5-flash-lite`, updated from `gemini-3.1-flash-lite`) by default for optimal price-performance ratio, with on-demand switching to `gemini-3.1-flash-lite` or custom models.
  - Structured output (`responseMimeType: "application/json"`) guarantees the model returns precisely a JSON object containing both `summary` and `description`.
  - Smart Diff Optimization: Large diffs are parsed and truncated thoughtfully. The script prioritizes preserving `diff --git` headers so the model always knows exactly which files were modified, rather than blindly truncating the middle of the alphabet. Truncation reports the original token count (not the post-truncation count) and always retains some content even when a single line exceeds the whole budget, so the AI never receives an empty diff.
  - Binary file detection prevents sending unreadable content to the AI, reducing wasted tokens.
  - Single API call per commit minimizes latency and cost.
  - Amend mode reuses the original commit message as context, reducing the need for extensive re-analysis.

## 3. Keyboard-First Interactive UI
- **Motivation**: Git commit processes should be fast, transparent, and simple. Keyboard hotkeys allow power-users to speed through staging, reviewing, and committing without mouse interaction, while granular status indicators and flexible range syntax provide full control and visibility into index state without needing a separate git status call.
- **Implementation**: Uses short character keys (`c`, `e`, `v`, `d`, `s`, `x`) to control the commit loop. File staging supports comma-separated numbers (`1, 2, 4`), numeric ranges (`1-3, 5`), and keywords (`all`, `*`) with granular staged/unstaged status badges (`[staged: modified]`, `[staged: untracked]`, `[staged: renamed]`, `[staged: deleted]`, `[modified]`, `[deleted]`, `[untracked]`) and rename origin tracking (`old -> new`). The unstage option (`u`) allows in-flight corrections using the same flexible range syntax without restarting. Commit mode selection (`n`/`a`/`f`) at startup provides quick access to amend workflows. Version bump options (`v`) feature semantic color-coding (patch: green, minor: cyan, major: magenta, none: yellow) and are only shown for new commits. Proposed commit review screen maintains granular status badges under `Files to commit:`. Validation warnings displayed in review screen. Selecting an untracked file (or untracked directory) that pairs with an unstaged deletion by filename automatically stages the deletion as well, so a rename is never split into a duplicate add.

## 4. Automatic Version Alignment
- **Motivation**: Semantic versioning is often neglected or updated out of sync with commits.
- **Implementation**: Binds version tracking directly into the commit pipeline, enabling simultaneous source file version increments (`package.json`, `pyproject.toml`), `CHANGELOG.md` generation, and git tags right alongside the commit command. Parses detected version strings into integer tuples (supporting standard SemVer as well as multi-part version numbers like `v1.1.1.11`) and orders choices descending so option `1` always defaults to the highest version available. If a user selects a lower base version (or enters a custom lower version), the tool requires explicit confirmation before proceeding and allows returning to selection, preventing accidental backward version bumps. Additionally, it checks git commit messages for version strings, detects if the proposed version is already tagged locally to prevent collision, prompts the user for approval before creating or relocating git tags (with an optional `auto_tag: true` config setting to bypass prompts), and preserves the original formatting of the version string (preserving the `v` prefix or lack thereof). A shared `is_version_regression()` guard also catches custom or context-detected versions that would move the version backwards, and existing multi-part prefixes are stripped correctly when the commit message is assembled. Remote tag checks match the exact `refs/tags/<name>` ref — so `v1.2.3` can never collide with `v1.2.34` — and run *before* the local tag is created; relocating tags on amend preserves annotated tags and their messages.

## 5. Automated Workflow Lifecycle
- **Motivation**: Committing is only step one of modern development. A professional tool should support the full lifecycle up to deployment.
- **Implementation**: After pushing, the tool attempts to use native CLIs like GitHub's `gh` to handle immediate Pull Request creation and interactive live-streaming of CI/CD pipelines directly in the terminal.

## 6. Resilience & Crash Safety
- **Motivation**: A commit tool that loses your AI-generated message on a crash forces a costly re-run of the API.
- **Implementation**: Immediately after a successful Gemini response, the tool writes a session snapshot to the repository's Git directory. The saved state includes commit mode, summary, description, bump choice, staged files, and version. On the next launch, it offers to restore the session, skipping the API call entirely. State is always cleared on a clean exit. Non-interactive mode automatically clears saved sessions to prevent stale state in CI environments. The Git directory is resolved to an absolute path at startup (`init_git_dir()`), so state and templates keep working from any subdirectory. A resumed session re-runs the `.env` guard over its restored staged list. Version files and the changelog are snapshotted before mutation and restored if the commit fails, so a retry never accumulates version drift. A dedicated `prompt_restart()` helper and an `(KeyboardInterrupt, EOFError)` guard make a closed stdin exit cleanly instead of crash-looping.

## 7. User Configurability Without Complexity
- **Motivation**: Different repos have different conventions. A hardcoded tool becomes an obstacle for teams.
- **Implementation**: An optional `.commitgenrc` JSON file (local or global) lets users override defaults like `default_bump`, `max_diff_length`, and `model`. Environment variables always take precedence over config file values, allowing CI pipelines to override without editing files. Configuration priority: environment variables → repo config → global config → built-in defaults.

## 8. Headless & CI/CD Compatibility
- **Motivation**: The tool should be usable as a step in automated pipelines, not just interactively.
- **Implementation**: `--non-interactive` mode (and auto-detection of `CI`, `GITHUB_ACTIONS`, `GITLAB_CI`, `JENKINS_URL`, `TRAVIS` env vars) skips all prompts, auto-stages everything, and uses config defaults throughout. `--dry-run` mode allows full pipeline testing without writing any git objects. Non-interactive mode automatically clears saved sessions to prevent stale state. Because safety-critical prompts have no human to answer them, it applies conservative defaults: it auto-selects the commit action, never force-pushes amended commits, creates tags only when `auto_tag: true`, pushes only when `auto_push: true`, honors `auto_pull` silently, skips PR creation and CI monitoring, and aborts the commit if pre-commit hooks fail. It also never blocks on `input()`, so a closed stdin cannot hang a pipeline.

## 9. Commit Message Validation & Quality Assurance
- **Motivation**: AI-generated messages should still meet repository standards and conventional commit specifications.
- **Implementation**: Built-in validation checks commit messages for 72-character line limit, proper conventional commit format (`feat:`, `fix:`, `docs:`, `style:`, `refactor:`, `test:`, `chore:`, `perf:`, `ci:`, `build:`, `revert:`, `update:`), and required blank line after title. Validation warnings (including version tag collision warning) displayed in review screen. Optional spell-checking via system `aspell` command (press `s` in review). Commit statistics with per-extension file counts provide context before finalizing.

## 10. Flexible Commit History Management
- **Motivation**: Real development workflows often require updating or refining previous commits—whether to fix a typo, add forgotten changes, or rephrase for clarity. A commit tool should support these workflows natively rather than forcing users to use complex git commands.
- **Implementation**: Three distinct commit modes:
  - **New**: Standard commit with version bump and tag creation
  - **Amend**: Update the last commit message while preserving its structure, adding new staged changes
  - **Fresh Amend**: Completely replace the last commit message with AI-generated content that encompasses all changes (analyzing the combined diff of the original commit and staged changes against `HEAD~1` to describe the entire change set as a single unit)
  - Amend mode includes the original commit message as AI context for coherent updates
  - Smart force-push detection for amended commits that were already pushed
  - Version bump automatically disabled for amend mode (no accidental version changes)
  - Session recovery preserves commit mode for crash safety
  - **Tag relocation**: Automatically moves all associated tags to the new amended commit locally and force-pushes them to remote to prevent tags from pointing to old hidden commits.

## 11. Rich Context & Template Support
- **Motivation**: AI-generated commit messages are most useful when they understand project conventions and the full scope of changes.
- **Implementation**: 
  - Detects architectural scope from file paths (e.g., `src/` → `core`, `tests/` → `test`, `ui/` → `ui`)
  - Extracts issue references from branch names and recent commits
  - Loads commit templates from `.git/COMMIT_TEMPLATE` or `.github/PULL_REQUEST_TEMPLATE.md`
  - Learns from repo's commit history (auto-detects conventional commit usage)
  - Includes recent commit history as context for AI
  - Binary file detection prevents sending unreadable content to AI

## 12. Security-First .env Handling
- **Motivation**: Accidentally committing `.env` files containing API keys, database credentials, or other secrets is one of the most common security mistakes in development. A commit tool should actively prevent this.
- **Implementation**:
  - Strict `.env` detection via `is_env_file()` — matches secret `.env`, `.env.*`, and `.envrc` files while excluding safe template/example files (`.env.template`, `.env.example`, `.env.sample`, `.env.dist`) so templates can be committed freely.
  - Automatically detects and unstages `.env` files with a security warning
  - Recursive re-scan after unstaging to ensure clean file lists
  - AI prompt exclusion list (`AI_PROMPT_EXCLUDED_EXTENSIONS`) prevents binary/media files from being sent to Gemini, saving tokens and avoiding parsing issues while still allowing normal git commits
  - Resumed sessions re-apply the `.env` guard (`drop_env_files()`) over their restored staged list, so a file that was staged before a crash is filtered again before commit
  - `redact_secrets()` scrubs high-confidence secret patterns (AWS access keys, GitHub/Gemini tokens, bearer tokens, credentials embedded in URLs) from the AI prompt before it is sent, while leaving the committed diff untouched

## 13. Post-Commit Workflow Automation
- **Motivation**: Committing is only step one of modern development. A professional tool should support the full lifecycle up to deployment.
- **Implementation**: 
  - Automatic version updates in project files (package.json, pyproject.toml)
  - CHANGELOG.md generation with version, date, and changes; re-committing or fresh-amending the same version replaces that version's entry instead of duplicating it
  - Git tag creation preserving original version formatting
  - Pull Request creation via GitHub CLI (`gh pr create`)
  - CI pipeline monitoring with live streaming (`gh run watch`)
  - Automatic GitHub Repository Creation (public or private) when no remote is configured
  - Pre-push pull safety check (`git pull --rebase`) with user permission confirmation (respects `auto_pull` configuration), featuring interactive loop recovery that switches default to `[n]` if git pull fails due to unstaged changes (at startup or pre-push)
  - Push failures offer an automatic retry prompt (interactive) or a clean skip (non-interactive); choosing not to push prints an explicit "skipped push" hint instead of silently exiting
  - Force-push detection and warnings for amended commits (refused in non-interactive mode)
  - Version prefix added to commit messages for all modes (e.g., `v1.2.3 - feat: add feature` or `1.2.3 - feat: add feature`)

## 14. Terminal Resilience & Interface Stability
- **Motivation**: Command-line interfaces should never crash, hang indefinitely, or abruptly drop the user back to the shell when a working tree is clean or an action is aborted.
- **Implementation**:
  - Replaces recursive interaction models in the file staging picker with iterative loops to avoid stack overflows.
  - Adds connection timeouts (60 seconds) on API client boundaries to fail gracefully instead of hanging forever.
  - Introduces defensive file-existence guards before opening files for binary checking, preventing errors on deleted files.
  - Implements a centralized conditional ANSI color wrapper (`c()`) to prevent color escape codes from leaking in non-TTY or redirected output environments.
  - Employs robust printing fallbacks to quietly suppress/bypass terminal errors if stdout streams are closed or redirected.
  - Substitutes abrupt `sys.exit()` calls with a unified restart prompt (`prompt_exit_or_restart`), wrapped in a top-level execution loop. When the working tree is clean, staging is aborted, a commit is cancelled, or a commit finishes, the user is offered an immediate restart hotkey (`r`) or exit (`Enter`). This enables continuous sessions, allowing users to make changes and commit again without dropping back to the shell, while bounding retries (max 10) to prevent infinite loops.
  - **Console Input Buffer Hygiene**: Eliminates unintended prompt auto-advancement by actively draining the terminal's input buffer before displaying interactive prompts. In interactive terminals, users frequently double-tap Enter or enter keystrokes while a previous step (such as diff calculation or Gemini API invocation) is completing. Standard OS consoles queue these unconsumed keystrokes, which Python's default `input()` would immediately consume, bypassing subsequent menus with default values before the user can read the prompt. Using `flush_input_buffer()` (via `FlushConsoleInputBuffer` and `msvcrt` on Windows, and `termios.tcflush` on POSIX) paired with `isatty()` guards, the tool guarantees that inputs are captured strictly after the prompt is rendered while keeping piped and CI/CD executions seamless.

## 15. Transparent AI Attribution & Context Awareness
- **Motivation**: When AI tools assist with git history, developers and teams require transparency, control, and immediate context. Developers should instantly see which repository and path they are executing against upon startup, and have an effortless, opt-in mechanism to credit AI generation without imposing unwanted trailers by default.
- **Implementation**:
  - **Immediate Startup Context**: `show_startup_banner()` displays the project folder name and absolute directory path before model selection, removing ambiguity when launched from context menus or deeply nested directories.
  - **Opt-in Git Trailers**: Generates standardized Git trailer format (`Co-authored-by: Custom Tool By BKK ({model}) <{model}>`). Defaults to `n` on interactive prompt to respect team privacy preferences and clean git history by default.
  - **Flexible In-Flight Control**: Allows users to toggle attribution dynamically on the review screen (`t`), configure project-wide defaults in `.commitgenrc` (`co_author: false`), override via environment variables (`CO_AUTHOR`), or pass CLI flags (`--co-author` / `--no-co-author`).
