# AI-Powered Git Commit & Version Bumper Tool

A professional, zero-dependency Python CLI tool that uses Google's Gemini API (specifically optimized for `gemini-2.0-flash-lite`) to analyze your git diffs, interactively stage files, generate structured commit summaries/descriptions in a single API call, and automatically manage semantic version updates.

## Features

- 🔋 **Zero Dependencies**: Requires only standard Python libraries. No `pip install` required for dependencies.
- ⚡ **Highly Efficient**: Fetches both summary and description in a single structured JSON API call. Smartly optimizes large diffs.
- 📦 **Semantic Versioning**: Automatically checks git tags, `package.json`, or `pyproject.toml` for the current version and auto-updates those files with the bumped version.
- 📜 **Changelog & PR Management**: Automatically updates `CHANGELOG.md` and can create GitHub Pull Requests directly using `gh` CLI.
- 🤖 **Smart Context**: Auto-detects architectural scope from folders (e.g., `ui/`, `db/`), extracts issue numbers from branch names, and respects your local `.git/COMMIT_TEMPLATE`.
- 🛠️ **Interactive UI**:
  - Stage, unstage (`u`), and review files with detailed commit statistics.
  - Review, edit, spell-check (`s`), or preview diffs (`d`) before committing.
  - Monitor CI pipelines live directly after pushing.

## Code Base

```
git-commit/
├── git_commit.py          ← main tool
├── register.py            ← install/uninstall context menu (winreg)
├── .env.template          ← copy to .env and add your API key
├── .gitignore             ← ignores .env and Python cache
├── README.md
├── CODE_DOCUMENTATION.md
└── DESIGN_PHILOSOPHY.md
```

## Setup

1. Copy `.env.template` to `.env`:
   ```bash
   copy .env.template .env
   ```
2. Open `.env` and configure your `GEMINI_API_KEY`:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   GEMINI_MODEL=gemini-2.0-flash-lite
   ```

## Usage

Simply run the script within your Git repository:

```bash
python git_commit.py
```

### Options inside the tool:
- **Staging**: Choose which modified or untracked files to stage (`a`), or unstage specific files (`u`).
- **Context**: Provide optional notes or hints to steer the AI on what changes to highlight.
- **Review Screen**:
  - `c`: Execute the commit and tags.
  - `e`: Manually edit the generated commit summary/description.
  - `v`: Change the version bump category (toggle between `patch`, `minor`, `major`, `custom`, and `none`).
  - `d`: View the full detailed git diff in your terminal using `less`.
  - `s`: Run a spelling check on the generated commit message using `aspell`.
  - `x`: Cancel and exit.

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
