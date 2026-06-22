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

- `load_dotenv()`: Manually parses the `.env` file in the current directory, extracting environment variables without requiring the `python-dotenv` package.
- `run_git_cmd(args)`: A wrapper around `subprocess.run` to execute git commands and return their output safely.
- `detect_version()`: Checks:
  1. Git tags using `git tag -l` (using regex `v?(\d+)\.(\d+)\.(\d+)` to match semantic versions).
  2. `package.json`'s `"version"` field.
  3. `pyproject.toml`'s `version = "X.Y.Z"` property.
- `increment_version(version_str, bump_type)`: Performs version bumping based on `major`, `minor`, or `patch` increments.
- `update_version_in_files(new_version)`: Overwrites the version fields inside `package.json` and `pyproject.toml` automatically on successful commits.
- `get_git_files()`: Parses `git status --porcelain` to extract staged, unstaged, and untracked files.
- `prompt_stage_files(...)`: Interactive command-line UI using `input()` to allow users to select which unstaged/untracked files to stage for commit.
- `call_gemini_api(api_key, model, prompt_text)`: Sends a POST request via Python's standard `urllib.request` library directly to the Gemini REST API. Requests structured JSON outputs with schema matching `{"summary": "...", "description": "..."}`.
- `main()`: Orchestrates the staging, version bump category selection, prompt assembly, API call execution, commit message review, and git execution.

## 2. register.py

This helper script automates the installation of the Explorer right-click context menu by dynamically generating registry paths.

- **Dynamic Path Resolution**: Reads the current folder path of `git_commit.py` and correctly escapes backslashes for Registry Editor compliance (`\\`).
- **File Generation**: Outputs `register.reg` (to import) and `unregister.reg` (to delete the keys).
- **Registry Structure**:
  - `HKEY_CLASSES_ROOT\Directory\shell\GitCommitAI`: When right-clicking a folder in explorer.
  - `HKEY_CLASSES_ROOT\Directory\Background\shell\GitCommitAI`: When right-clicking inside an open folder background.
