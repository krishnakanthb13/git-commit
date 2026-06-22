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

- `load_dotenv()`: Manually parses the `.env` file in the current directory.
- `run_git_cmd(args)`: A wrapper around `subprocess.run` to execute git commands safely.
- `detect_version()`: Checks `git tag -l`, `package.json`, and `pyproject.toml`.
- `increment_version(version_str, bump_type)`: Performs semantic version bumping.
- `update_version_in_files(new_version)`: Overwrites the version fields inside `package.json` and `pyproject.toml` automatically on successful commits.
- `update_changelog(version, summary, description)`: Automatically appends the new commit to `CHANGELOG.md` under a new release block if the file exists.
- `get_git_files()`: Parses `git status --porcelain -z` to extract staged, unstaged, and untracked files without truncation issues.
- `prompt_stage_files(...)`: Interactive command-line UI to stage/unstage files before committing.
- `show_commit_stats(staged)`: Displays `git diff --stat` and a language/extension breakdown for the staging area.
- `detect_conventional_scope(files)`: Maps file paths (like `ui/` or `tests/`) to conventional commit scopes (`ui`, `test`) to feed into the prompt context.
- `extract_issue_references()`: Scrapes the current branch name and recent Git logs for issue numbers (e.g., `#123` or `feat/123-`) to auto-link in the commit.
- `check_spelling(text)`: Pipes the generated commit message to the system's `aspell` utility to detect typos.
- `monitor_ci()`: Hooks into the GitHub CLI (`gh`) to search for and live-stream the output of matching GitHub Actions CI pipelines immediately after a push.
- `call_gemini_api(api_key, model, prompt_text)`: Sends a POST request directly to the Gemini REST API via `urllib.request`. Enforces JSON schemas and includes exponential backoff retry logic.
- `main()`: Orchestrates the staging, context building, API call, interactive review loop, committing, PR creation (`gh pr create`), and CI monitoring.

## 2. register.py

This helper script automates the installation of the Explorer right-click context menu by dynamically generating registry paths.

- **Dynamic Path Resolution**: Reads the current folder path of `git_commit.py` and correctly escapes backslashes for Registry Editor compliance (`\\`).
- **File Generation**: Outputs `register.reg` (to import) and `unregister.reg` (to delete the keys).
- **Registry Structure**:
  - `HKEY_CLASSES_ROOT\Directory\shell\GitCommitAI`: When right-clicking a folder in explorer.
  - `HKEY_CLASSES_ROOT\Directory\Background\shell\GitCommitAI`: When right-clicking inside an open folder background.
