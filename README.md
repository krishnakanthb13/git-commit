# AI-Powered Git Commit & Version Bumper Tool

A professional, zero-dependency Python CLI tool that uses Google's Gemini API (specifically optimized for `gemini-3.1-flash-lite`) to analyze your git diffs, interactively stage files, generate structured commit summaries/descriptions in a single API call, and automatically manage semantic version updates. Includes powerful amend capabilities to update or replace previous commit messages.

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
12. 🚀 **Pushes to remote** with tags and force push support for amended commits
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

**A comprehensive Git commit tool** that handles the entire workflow from staging to CI monitoring with excellent error handling and user experience.

## Features

- 🔋 **Zero Dependencies**: Requires only standard Python 3 libraries. No `pip install` required.
- ⚡ **Highly Efficient**: Single structured JSON API call. Smartly optimizes and truncates large diffs while preserving file headers.
- 🔄 **Powerful Amend Workflow**: Three commit modes for flexible history management:
  - **New**: Create a fresh commit with version bump and tag
  - **Amend**: Update the last commit message and add new staged changes (preserves original as context)
  - **Fresh Amend**: Completely replace the last commit message with AI-generated content
  - Smart force-push detection for amended commits
  - Session recovery preserves commit mode across crashes
- 📦 **Semantic Versioning**: Auto-detects version from git tags, recent commit messages, `package.json`, or `pyproject.toml` and updates those files on commit. Resolves version mismatches interactively across all sources. Skips version bump for amend mode by default.
- 📜 **Changelog & PR Management**: Automatically updates `CHANGELOG.md` and can create GitHub Pull Requests using `gh` CLI.
- 🤖 **Smart Context**: Detects architectural scope from file paths, extracts issue numbers from branch names, respects `.git/COMMIT_TEMPLATE` and `.github/PULL_REQUEST_TEMPLATE.md`, and learns from your repo's commit history. For amend mode, includes the original commit message as context.
- 🔒 **Robustness**: Binary file detection, pre-commit hook integration, session recovery (crash-safe with commit mode preservation), and startup dependency checks.
- ⚙️ **Configurable**: Per-repo `.commitgenrc` JSON config for default bump type, diff size, and model. Global config via `~/.commitgenrc`.
- 🚀 **CI/CD Ready**: `--dry-run` and `--non-interactive` flags for headless/automated environments. Auto-detects CI environments (GitHub Actions, GitLab CI, Jenkins, Travis).
- 🔍 **Commit Validation**: Validates commit messages against conventional commit format (72 char limit, proper format, blank line after title). Shows warnings in review screen.
- 📊 **Commit Statistics**: Shows detailed stats with per-extension file counts before committing.
- 🔤 **Spell Checking**: Optional spell-check via system `aspell` command (press `s` in review screen).
- 🛠️ **Interactive UI**:
  - **Commit Mode Selection**: Choose between new commit, amend, or fresh amend at startup
  - Stage, unstage (`u`), and review files with detailed commit statistics
  - Review, edit (`e`), spell-check (`s`), or preview diffs (`d`) before committing
  - Version bump options (`v`) only shown for new commits (amend mode skips version bump by default)
  - Monitor CI pipelines live directly after pushing
  - Validation warnings displayed in review screen

## Code Base

```
git-commit/
├── git_commit.py              ← main tool (1,265 lines)
├── register.py                ← install/uninstall context menu (winreg)
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
   GEMINI_MODEL=gemini-3.1-flash-lite  # (optional, this is the default)
   ```

*Note: The script automatically resolves and loads the `.env` file from the directory where `git_commit.py` is installed/located, so you can execute the command from any folder. If a `.env` file is also present in the current working directory, it will load that as well to allow project-specific overrides.*

## Usage

```bash
python git_commit.py              # normal interactive mode
python git_commit.py --dry-run    # preview commit without making changes
python git_commit.py --non-interactive  # headless/CI mode (no prompts)
```

**Command-line Options:**
- `--dry-run`: Preview the commit message and actions without making any changes
- `--non-interactive`: Run in headless mode (auto-stages all files, no prompts, uses defaults)

**Auto-detection:** The tool automatically detects CI environments (GitHub Actions, GitLab CI, Jenkins, Travis) and enables non-interactive mode.

### Options inside the tool:

**Commit Mode Selection** (appears at startup if previous commits exist):
- `n`: Create a NEW commit (default) - includes version bump and tag
- `a`: AMEND the last commit - updates message and adds staged changes (no version bump)
- `f`: FRESH amend - replaces last commit message completely with new AI suggestion (no version bump)

**Staging**: Choose which files to stage (`a` = all, numbers, or `u` to unstage). Already-staged files shown in green.

**Context**: Provide optional notes to steer the AI. You can also specify version in context (e.g., "v1.2.3") for auto-detection.

**Review Screen**:
- `c`: Execute the commit (or amend, depending on mode)
- `e`: Manually edit the generated summary/description
- `v`: Change version bump (`patch`, `minor`, `major`, `custom:X.Y.Z`, `none`) - **only shown for new commits**
- `d`: View the full git diff in `less` (with `-R` for color support)
- `s`: Run a spell-check via `aspell`
- `x`: Cancel and exit

**Post-Commit Actions**:
- Push to remote (with automatic force push prompt for amended commits)
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

**Note:** Non-interactive mode automatically clears saved sessions to prevent stale state in CI environments.

### Config File (`.commitgenrc`)
Create a `.commitgenrc` JSON file in your repo (or `~/.commitgenrc` globally) to set project defaults:
```json
{
  "default_bump": "minor",
  "max_diff_length": 20000,
  "auto_push": false,
  "model": "gemini-3.1-flash-lite"
}
```

**Configuration priority** (highest to lowest):
1. Environment variables (e.g., `GEMINI_MODEL`)
2. `.commitgenrc` (repo-local)
3. `~/.commitgenrc` (global user config)
4. Built-in defaults

**Available options**:
- `default_bump`: Default version bump type (`patch`, `minor`, `major`, `none`)
- `max_diff_length`: Maximum diff size in characters (default: 20000)
- `auto_push`: Automatically push after commit (default: false)
- `model`: Gemini model to use (default: `gemini-3.1-flash-lite`)

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
