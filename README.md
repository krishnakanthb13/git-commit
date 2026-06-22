# AI-Powered Git Commit & Version Bumper Tool

A professional, zero-dependency Python CLI tool that uses Google's Gemini API (specifically optimized for `gemini-2.0-flash-lite`) to analyze your git diffs, interactively stage files, generate structured commit summaries/descriptions in a single API call, and automatically manage semantic version updates.

## Overview

**An AI-powered Git commit workflow assistant** that:

1. 🔍 **Analyzes changes** with Git diff
2. 🤖 **Generates commit messages** using Google's Gemini AI
3. 📝 **Follows conventional commits** format (auto-detected from repo history)
4. 🏷️ **Manages version bumping** (patch/minor/major/custom/none)
5. 📦 **Updates project files** (package.json, pyproject.toml)
6. 📋 **Maintains changelogs** (CHANGELOG.md)
7. ✅ **Runs pre-commit hooks** before committing
8. 🔍 **Detects binary files** and scopes from file paths
9. 🔗 **Links issues** from branches/commits
10. 💾 **Saves session state** for crash recovery
11. 🚀 **Pushes to remote** with tags
12. 🔧 **Creates Pull Requests** via GitHub CLI
13. 👀 **Monitors CI pipelines** after push
14. 🎨 **Beautiful CLI interface** with colors
15. ⚙️ **Configuration file** support (.commitgenrc)
16. 🏃 **CI/CD ready** with non-interactive mode
17. 🧪 **Dry run mode** for testing
18. 🔍 **Validates commit messages** against conventional commit format
19. 📊 **Shows commit statistics** with per-extension file counts
20. 🔤 **Spell checking** integration (via aspell)

**A comprehensive Git commit tool** that handles the entire workflow from staging to CI monitoring with excellent error handling and user experience.

## Features

- 🔋 **Zero Dependencies**: Requires only standard Python 3 libraries. No `pip install` required.
- ⚡ **Highly Efficient**: Single structured JSON API call. Smartly optimizes and truncates large diffs while preserving file headers.
- 📦 **Semantic Versioning**: Auto-detects version from git tags, `package.json`, or `pyproject.toml` and updates those files on commit.
- 📜 **Changelog & PR Management**: Automatically updates `CHANGELOG.md` and can create GitHub Pull Requests using `gh` CLI.
- 🤖 **Smart Context**: Detects architectural scope from file paths, extracts issue numbers from branch names, respects `.git/COMMIT_TEMPLATE`, and learns from your repo's commit history.
- 🔒 **Robustness**: Binary file detection, pre-commit hook integration, session recovery (crash-safe), and startup dependency checks.
- ⚙️ **Configurable**: Per-repo `.commitgenrc` JSON config for default bump type, diff size, and model. Global config via `~/.commitgenrc`.
- 🚀 **CI/CD Ready**: `--dry-run` and `--non-interactive` flags for headless/automated environments. Auto-detects CI environments.
- 🔍 **Commit Validation**: Validates commit messages against conventional commit format (72 char limit, proper format, blank line after title).
- 📊 **Commit Statistics**: Shows detailed stats with per-extension file counts before committing.
- 🔤 **Spell Checking**: Optional spell-check via system `aspell` command.
- 🛠️ **Interactive UI**:
  - Stage, unstage (`u`), and review files with detailed commit statistics.
  - Review, edit, spell-check (`s`), or preview diffs (`d`) before committing.
  - Monitor CI pipelines live directly after pushing.

## Code Base

```
git-commit/
├── git_commit.py              ← main tool (1,109 lines)
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

1. Copy `.env.template` to `.env`:
   ```bash
   copy .env.template .env
   ```
2. Open `.env` and configure your `GEMINI_API_KEY`:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   GEMINI_MODEL=gemini-3.1-flash-lite
   ```

## Usage

```bash
python git_commit.py              # normal interactive mode
python git_commit.py --dry-run    # preview commit without making changes
python git_commit.py --non-interactive  # headless/CI mode (no prompts)
```

### Options inside the tool:
- **Staging**: Choose which files to stage (`a` = all, numbers, or `u` to unstage).
- **Context**: Provide optional notes to steer the AI.
- **Review Screen**:
  - `c`: Execute the commit and tags.
  - `e`: Manually edit the generated summary/description.
  - `v`: Change version bump (`patch`, `minor`, `major`, `custom:X.Y.Z`, `none`).
  - `d`: View the full git diff in `less`.
  - `s`: Run a spell-check via `aspell`.
  - `x`: Cancel and exit.

### Config File (`.commitgenrc`)
Create a `.commitgenrc` JSON file in your repo (or `~/.commitgenrc` globally) to set project defaults:
```json
{
  "default_bump": "minor",
  "max_diff_length": 20000,
  "auto_push": false,
  "model": "gemini-2.0-flash-lite"
}
```

**Configuration priority** (highest to lowest):
1. Environment variables (e.g., `GEMINI_MODEL`)
2. `.commitgenrc` (repo-local)
3. `~/.commitgenrc` (global user config)
4. Built-in defaults

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
