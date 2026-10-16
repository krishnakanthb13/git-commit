# AI-Powered Git Commit & Version Bumper Tool

A professional, zero-dependency Python CLI tool that uses Google's Gemini API (specifically optimized for `gemini-3.5-flash-lite`, with support for switching to `gemini-3.1-flash-lite`) to analyze your git diffs, interactively stage files, generate structured commit summaries/descriptions in a single API call, and automatically manage semantic version updates. Includes powerful amend capabilities to update or replace previous commit messages.

## Workflow

```
1. Stage files → 2. Choose mode (n/a/f) → 3. Provide context → 
4. AI generates/loads message → 5. Review & confirm (with validation) → 
6. Commit/Amend → 7. Push (with force-push warning for amends) → 
8. Create PR & Monitor CI (optional)
```

## Overview

**An AI-powered Git commit workflow assistant** that:

1. 🔍 **Analyzes changes** with Git diff
2. 🤖 **Generates commit messages** using Google's Gemini AI
3. 📝 **Follows conventional commits** format (auto-detected from repo history)
4. 🔄 **Three commit modes** for flexible history management:
   - **New commit**: Create a fresh commit with version bump and tag
   - **Amend**: Update last commit message and add staged changes (no version bump)
   - **Fresh amend**: Replace last commit message completely with new AI suggestion (no version bump)
5. 🏷️ **Manages version bumping** (patch/minor/major/custom/none)
6. 📦 **Updates project files** (package.json, pyproject.toml)
7. 📋 **Maintains changelogs** (CHANGELOG.md)
8. ✅ **Runs pre-commit hooks** before committing
9. 🔍 **Detects binary files** and scopes from file paths
10. 🔗 **Links issues** from branches/commits
11. 💾 **Saves session state** for crash recovery (includes commit mode and amend state)
12. 🚀 **Pushes to remote** with multiple tags support, force push warnings for amended commits, remote tag conflict prompts, an automatic **retry prompt** if the push itself fails, and interactive retry fallback (loops back with default `[n]` if `git pull --rebase` fails due to unstaged changes at startup or pre-push).
13. 🔧 **Creates Pull Requests** via GitHub CLI
14. 👀 **Monitors CI pipelines** after push
15. 🎨 **Beautiful CLI interface** with colors
16. ⚙️ **Configuration file** support (.commitgenrc)
17. 🏃 **CI/CD ready** with non-interactive mode
18. 🧪 **Dry run mode** for testing
19. 🔍 **Validates commit messages** against conventional commit format (72 char limit, proper format, blank line)
20. 📊 **Shows commit statistics** with per-extension file counts
21. 🔤 **Spell checking** integration (via aspell)
22. 🔄 **Unstage files** during staging with interactive picker
23. 📝 **Loads commit templates** from `.git/COMMIT_TEMPLATE` or `.github/PULL_REQUEST_TEMPLATE.md`
24. 📦 **GitHub Repository Creation**: Auto-prompts to create a public/private repository on GitHub and push code/tags when no git remote is configured.
25. 🔁 **Continuous Sessions**: Prompts to optionally restart the tool (`Press Enter to exit, or 'r' to restart`) when the working tree is clean, staging is aborted, commits are cancelled, or upon completion/errors, allowing seamless multi-commit workflows without dropping back to the shell.
26. 🔒 **Security: .env Protection**: Automatically detects and unstages `.env`, `.env.*`, and `.envrc` files to prevent accidental credential leaks, while allowing safe template and example files (`.env.template`, `.env.example`, `.env.sample`, `.env.dist`) to be committed normally.
27. 🤖 **AI Prompt Optimization**: Excludes images, videos, audio, PDFs, and binary files from the Gemini prompt to save tokens and improve analysis quality. Files are still committed normally — only AI analysis is affected.
28. 👥 **Co-Authored-By Attribution**: Optional git commit trailer (`Co-authored-by: Custom Tool By BKK ({model}) <{model}>`) to credit AI generation with interactive prompting (defaults to `n`), dynamic review menu toggle (`t`), CLI flags (`--co-author`/`--no-co-author`), and configuration support.
29. 📁 **Startup Directory Context**: Immediately displays the active project folder name and absolute directory path at launch before model selection.
30. 🛡️ **Input Buffer Protection**: Automatically purges unconsumed console input buffers before rendering prompts, preventing accidental double-Enter keypresses or premature keystrokes from auto-advancing subsequent menus.
31. 🩹 **Rollback on Failed Commit**: If a commit fails after version files or the changelog were rewritten, the original file contents are restored and unstaged, so retrying never leaves version drift behind.
32. 🏷️ **Exact Remote Tag Matching**: Remote tag checks match the precise `refs/tags/<name>` ref (`v1.2.3` never collides with `v1.2.34`), and the remote is checked *before* the local tag is created.
33. 🔢 **Version Regression Guard**: Custom or context-detected versions that are lower than the current version require explicit confirmation before they are used.
34. 📋 **Changelog De-duplication**: Re-committing or fresh-amending the same version replaces that version's `CHANGELOG.md` entry instead of adding a duplicate.
35. 🔀 **Atomic Rename Staging**: When a selected untracked file (or untracked directory) matches an unstaged deletion by filename, both sides are staged together so renames are never committed as a duplicated file.
36. 🧯 **Closed-Stdin Resilience**: EOF/closed stdin (piped input, CI) exits gracefully instead of crashing or looping, and non-interactive mode never blocks on a prompt.
37. 🔐 **Secret Redaction**: High-confidence secrets (AWS keys, GitHub/Gemini tokens, credentials embedded in URLs) are redacted from the AI prompt only — the committed content is never modified.
38. 🏷️ **Annotated Tag Preservation**: Relocating tags on amend keeps annotated tags annotated and preserves their messages.

**A comprehensive Git commit tool** that handles the entire workflow from staging to CI monitoring with excellent error handling and user experience.

## Features

- 🔋 **Zero Dependencies**: Requires only standard Python 3 libraries. No `pip install` required.
- ⚡ **Highly Efficient**: Single structured JSON API call with 60-second timeouts to prevent hanging. Smartly optimizes and truncates large diffs while preserving file headers.
- 🔒 **Secure by Default**: Automatically blocks `.env` files from being committed, preventing accidental credential leaks. Strict `.env` detection avoids false positives.
- 🤖 **AI-Optimized**: Excludes binary/media files from the AI prompt to save tokens and focus analysis on meaningful code changes.
- 🔄 **Powerful Amend Workflow**: Three commit modes for flexible history management:
  - **New**: Create a fresh commit with version bump and tag
  - **Amend**: Update the last commit message and add new staged changes (preserves original as context)
  - **Fresh Amend**: Completely replace the last commit message with AI-generated content
  - Smart force-push detection for amended commits
  - Session recovery preserves commit mode across crashes
- 📦 **Semantic Versioning**: Auto-detects versions from git tags, recent commit messages, `package.json`, or `pyproject.toml`. Parses versions into integer tuples (supporting standard SemVer and multi-part versions like `v1.1.1.11`) and sorts choices in descending order so the highest version is the default option (`1`). Existing multi-part version prefixes (e.g. `1.1.1.11 - ...`) are detected and stripped correctly. Prompts for confirmation if a lower version is selected — including custom or context-detected versions that would regress the version — before proceeding or returning to selection. Automatically stages version files after a bump. Skips version bump for amend mode by default. Includes local version collision detection (warnings when the proposed bump already exists as a local tag) and an **exact** remote tag check (`refs/tags/<name>`, so `v1.2.3` never matches `v1.2.34`) performed *before* the tag is created.
- 📜 **Changelog & PR Management**: Automatically updates `CHANGELOG.md` — re-writing the entry for an already-existing version instead of duplicating it — and can create GitHub Pull Requests using `gh` CLI.
- 🩹 **Failure-Safe Versioning**: Version files and the changelog are snapshotted before they are rewritten. If the commit fails, the originals are restored and unstaged, keeping the working tree consistent for a retry.
- 🤖 **Smart Context**: Detects architectural scope from file paths, extracts issue numbers from branch names, respects `.git/COMMIT_TEMPLATE` and `.github/PULL_REQUEST_TEMPLATE.md`, and learns from your repo's commit history. For amend mode, includes the original commit message as context.
- 🔒 **Robustness & Input Hygiene**: Proactive console input buffer flushing (`FlushConsoleInputBuffer` and `msvcrt` on Windows, `tcflush` on POSIX) eliminates skipped prompts caused by accidental double-Enters or premature keystrokes while preserving piped input; binary file existence checks, pre-commit hook integration, session recovery (crash-safe with commit mode preservation), startup dependency checks, safe ANSI color fallback via `c()` helper, and remote/local tag conflict checks.
- ⚙️ **Configurable**: Per-repo `.commitgenrc` JSON config for default bump type, diff size, and model. Global config via `~/.commitgenrc`.
- 🚀 **CI/CD Ready**: `--dry-run` and `--non-interactive` flags for headless/automated environments. Auto-detects CI environments (GitHub Actions, GitLab CI, Jenkins, Travis). Non-interactive mode is fully prompt-free: it auto-stages everything, selects the commit action, uses the highest detected version, skips tag creation unless `auto_tag: true`, skips pushing unless `auto_push: true`, never force-pushes amended commits, honors `auto_pull` silently, skips PR creation and CI monitoring, and aborts the commit if pre-commit hooks fail.
- 🔐 **Secret Redaction**: The AI prompt is scrubbed of high-confidence secrets (AWS access keys, GitHub/Gemini tokens, bearer tokens, credentials embedded in URLs) before it leaves your machine. Only the prompt is affected — the diff that is committed is never modified.
- 🧯 **Resilient Input Handling**: A dedicated restart helper (`prompt_restart()`) and an `(KeyboardInterrupt, EOFError)` guard in the top-level loop mean a closed stdin (piped input or CI) exits cleanly instead of crashing or looping.
- 🔍 **Commit Validation**: Validates commit messages against conventional commit format (72 char limit, proper format, blank line after title). Shows warnings in review screen for format violations and version tag collisions.
- 📊 **Commit Statistics**: Shows detailed colorized stats (green `+` insertions, red `-` deletions) with per-extension file counts before committing.
- 🔤 **Spell Checking**: Optional spell-check via system `aspell` command (press `s` in review screen).
- 🛠️ **Interactive UI**:
  - **Header Status Badge**: Shows active project folder, AI model, and branch status with upstream sync indicators (`🌿 Branch: main [up to date]`)
  - **Commit Mode Selection**: Choose between new commit, amend, or fresh amend at startup
  - **Interactive Staging & Range Selection**: Stage, unstage (`u`), and proceed (`p`) with granular status tags (`staged: modified`, `staged: untracked`, `staged: renamed`, `staged: deleted`) and rename mappings (`old -> new`). Supports comma-separated numbers, ranges (e.g. `1,3-5`), and keywords (`all`, `*`).
  - Review, edit (`e`), spell-check (`s`), or preview diffs (`d`) before committing, with granular staged badges displayed under `Files to commit:`
  - Color-coded version bump options (`v`) with semantic highlights (patch: green, minor: cyan, major: magenta, none: yellow) — only shown for new commits
  - Monitor CI pipelines live directly after pushing
  - Validation warnings (including version tag collision warning) displayed in review screen
  - **GitHub Repository Creation**: Interactively prompts to create a public/private GitHub repository if no remote is configured (requires `gh` CLI).
  - **Continuous Session**: Prompts to restart upon success, clean tree, or error, providing a seamless multi-commit experience without abrupt termination.

## Code Base

```
git-commit/
├── git_commit.py              ← main tool (~2,500 lines)
├── register.py                ← install/uninstall context menu (winreg)
├── tests/                     ← pytest regression suite (no network required)
│   └── test_git_commit.py
├── .env.template              ← copy to .env and add your API key
├── .env                       ← local configuration (contains API key, gitignored)
├── .gitignore                 ← ignores .env and Python cache
├── git.ico                    ← Windows icon for context menu
├── README.md                  ← setup and usage guide
├── CODE_DOCUMENTATION.md      ← technical implementation details
└── DESIGN_PHILOSOPHY.md       ← architecture decisions
```

## Setup

1. Copy `.env.template` to `.env` in the same directory as `git_commit.py`:
   ```bash
   copy .env.template .env
   ```
2. Open `.env` and configure your `GEMINI_API_KEY`:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   GEMINI_MODEL=gemini-3.5-flash-lite  # (optional, this is the default)
   ```

*Note: The script automatically resolves and loads the `.env` file from the directory where `git_commit.py` is installed/located, so you can execute the command from any folder. If a `.env` file is also present in the current working directory, it is loaded as well to allow project-specific overrides. Precedence is: real environment variables (never overridden) → project `.env` (current directory) → tool-directory `.env`. This means CI-provided environment variables always win over any `.env` file.*

## Usage

```bash
python git_commit.py              # normal interactive mode
python git_commit.py --dry-run    # preview commit without making changes
python git_commit.py --non-interactive  # headless/CI mode (no prompts)
python git_commit.py --3.5        # force gemini-3.5-flash-lite
python git_commit.py --3.1        # force gemini-3.1-flash-lite
```

**Command-line Options:**
- `--dry-run`: Preview the commit message and actions without making any changes
- `--non-interactive`: Run in headless mode (auto-stages all files, no prompts, uses defaults). Tag creation is skipped unless `auto_tag: true`, pushing is skipped unless `auto_push: true`, amended commits are never force-pushed, `auto_pull` is honored silently, and PR/CI monitoring is skipped.
- `--model <name>`, `--3.5`, `--3.1`: Specify Gemini model to use
- `--co-author` / `--no-co-author`: Enable or disable Co-Authored-By attribution trailer

**Auto-detection:** The tool automatically detects CI environments (GitHub Actions, GitLab CI, Jenkins, Travis) and enables non-interactive mode.

### Options inside the tool:

**Model Selection Prompt** (appears at interactive startup):
- `1`: `gemini-3.5-flash-lite` (default)
- `2`: `gemini-3.1-flash-lite`
- `3`: Custom model entry

**Startup Remote Pull**:
- Prompts whether to pull latest changes from remote (defaults to `n` on Enter so uncommitted work is not unexpectedly rebased unless requested).

**Co-Authored-By Attribution**:
- Prompts whether to include Co-Authored-By attribution (`Custom Tool By BKK ({model})`) in the commit message trailer (defaults to `n` on Enter).

**Commit Mode Selection** (appears at startup if previous commits exist):
- `n`: Create a NEW commit (default) - includes version bump and tag
- `a`: AMEND the last commit - updates message and adds staged changes (no version bump)
- `f`: FRESH amend - replaces last commit message completely with new AI suggestion (no version bump)

**Staging**: Enhanced interactive file picker featuring:
- Granular staging status tags: `[staged: modified]`, `[staged: untracked]`, `[staged: renamed]`, `[staged: deleted]` alongside unstaged `[modified]`, `[deleted]`, and `[untracked]`
- Origin path tracking for renames (e.g., `old_path -> new_path`)
- Flexible input parsing: supports comma-separated numbers (`1, 2, 4`), ranges (`1-3, 5`), and keywords (`all`, `*`)
- Top status color legend: `(staged: green, modified: yellow, untracked: red)`
- Section divider lines for visual clarity
- Color-coded options: `a` (Stage all [default]), `u` (Unstage with range support), `r` (Refresh status), `p` (Proceed), `q` (Abort)
- `r` key stroke to dynamically refresh file status without leaving the menu
- Pressing Enter on empty input defaults to `a` (Stage all files)

**Context**: Provide optional notes to steer the AI. You can also specify version in context (e.g., "v1.2.3") for auto-detection.

**Review Screen**:
- `c`: Execute the commit or amend (default on Enter)
- `e`: Manually edit the generated summary/description
- `m`: Switch Gemini model (`gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, or custom) at will and optionally regenerate
- `v`: Change version bump with color-coded choices (`patch`: green, `minor`: cyan, `major`: magenta, `none`: yellow, `custom`) - **only shown for new commits**
- `t`: Toggle Co-Authored-By attribution trailer on/off dynamically before committing
- `d`: View the full git diff in `less` (with `-R` for color support)
- `s`: Run a spell-check via `aspell`
- `x`: Cancel and exit

**Post-Commit Actions**:
- Push to remote (defaults to yes; includes automatic force push prompt for amended commits, or repository creation options if no remote exists)
- Create Pull Request (if on a branch)
- Monitor CI pipeline (if `gh` CLI is available)

### Session Recovery
If the tool crashes or is interrupted after generating a commit message, it will automatically save the session state. On the next launch, you'll be prompted to resume the session, skipping the API call and saving time.

**Session state includes:**
- Commit mode (new/amend/fresh amend)
- Generated summary and description
- Version bump choice
- Staged files list
- Current version
- Selected model

**Note:** Non-interactive mode automatically clears saved sessions to prevent stale state in CI environments. A resumed session re-applies the `.env` guard, so sensitive files that were staged before a crash are filtered out again before anything is committed.

### Config File (`.commitgenrc`)
Create a `.commitgenrc` JSON file in your repo (or `~/.commitgenrc` globally) to set project defaults:
```json
{
  "default_bump": "minor",
  "max_diff_length": 500000,
  "auto_push": false,
  "auto_pull": true,
  "model": "gemini-3.5-flash-lite",
  "auto_tag": false,
  "co_author": false
}
```

**Configuration priority** (highest to lowest):
1. Environment variables (e.g., `GEMINI_MODEL`, `CO_AUTHOR`)
2. `.commitgenrc` (repo-local)
3. `~/.commitgenrc` (global user config)
4. Built-in defaults

**Available options**:
- `default_bump`: Default version bump type (`patch`, `minor`, `major`, `none`)
- `max_diff_length`: Maximum diff size in characters (default: 500000)
- `auto_push`: Automatically push after commit (default: false)
- `auto_pull`: Automatically pull before push (default: true)
- `model`: Gemini model to use (default: `gemini-3.5-flash-lite`)
- `auto_tag`: Automatically create/move git tags without confirmation prompt (default: `false` for safety)
- `co_author`: Include Co-Authored-By attribution trailer by default (default: `false`)

## Windows Right-Click Context Menu Integration

Add a shortcut to Windows Explorer so you can right-click any folder and launch this tool directly — no `.reg` files needed.

**Install** (run PowerShell/CMD as Administrator):
```powershell
python register.py --install
```

**Remove**:
```powershell
python register.py --uninstall
```

## Running from a Subdirectory

Session state and commit templates are stored under the repository's Git directory, which is resolved to an absolute path at startup (`git rev-parse --absolute-git-dir`). You can therefore launch the tool from any subdirectory of a repository and crash-recovery/session features keep working. If the tool is launched outside a Git repository it exits immediately with a clear error.

## Testing

A pytest regression suite lives in `tests/` and covers version handling, changelog de-duplication, `.env` precedence, secret redaction, diff truncation, commit rollback, tag relocation, rename staging, and exact remote tag matching. It uses temporary repositories and never touches the network:

```bash
python -m pytest
```

