# Design Philosophy

This document outlines the core architectural and design decisions behind the AI Git Commit & Version Bumper Tool.

## 1. Zero External Dependencies
- **Motivation**: Tools like this should run instantly in any repository on any machine without requiring a virtual environment, `pip install`, or node module setups.
- **Implementation**: Written in vanilla Python 3 utilizing `urllib.request` instead of `requests` and a lightweight manual `.env` file parser instead of `python-dotenv`.

## 2. High Efficiency & Cost Minimization
- **Motivation**: Minimize latency, API payload sizes, and Gemini API token consumption.
- **Implementation**:
  - Uses cost-effective model (`gemini-3.1-flash-lite`, updated from `gemini-2.0-flash-lite`) by default for optimal price-performance ratio.
  - Structured output (`responseMimeType: "application/json"`) guarantees the model returns precisely a JSON object containing both `summary` and `description`.
  - Smart Diff Optimization: Large diffs are parsed and truncated thoughtfully. The script prioritizes preserving `diff --git` headers so the model always knows exactly which files were modified, rather than blindly truncating the middle of the alphabet.
  - Binary file detection prevents sending unreadable content to the AI, reducing wasted tokens.
  - Single API call per commit minimizes latency and cost.
  - Amend mode reuses the original commit message as context, reducing the need for extensive re-analysis.

## 3. Keyboard-First Interactive UI
- **Motivation**: Git commit processes should be fast and simple. Keyboard hotkeys allow power-users to speed through staging, reviewing, and committing without mouse interaction.
- **Implementation**: Uses short character keys (`c`, `e`, `v`, `d`, `s`, `x`) to control the commit loop. File staging uses comma-separated index selection. The unstage option (`u`) allows in-flight corrections without restarting. Commit mode selection (`n`/`a`/`f`) at startup provides quick access to amend workflows. Version bump options (`v`) only shown for new commits. Validation warnings displayed in review screen.

## 4. Automatic Version Alignment
- **Motivation**: Semantic versioning is often neglected or updated out of sync with commits.
- **Implementation**: Binds version tracking directly into the commit pipeline, enabling simultaneous source file version increments (`package.json`, `pyproject.toml`), `CHANGELOG.md` generation, and git tags right alongside the commit command. Additionally, it checks git commit messages for version strings, resolves version conflicts across all sources interactively, detects if the proposed version is already tagged locally to prevent collision, prompts the user for approval before creating or relocating git tags (with an optional `auto_tag: true` config setting to bypass the prompts), and preserves the original formatting of the version string (preserving the `v` prefix or lack thereof).

## 5. Automated Workflow Lifecycle
- **Motivation**: Committing is only step one of modern development. A professional tool should support the full lifecycle up to deployment.
- **Implementation**: After pushing, the tool attempts to use native CLIs like GitHub's `gh` to handle immediate Pull Request creation and interactive live-streaming of CI/CD pipelines directly in the terminal.

## 6. Resilience & Crash Safety
- **Motivation**: A commit tool that loses your AI-generated message on a crash forces a costly re-run of the API.
- **Implementation**: Immediately after a successful Gemini response, the tool writes a session snapshot to `.git/COMMITGEN_STATE`. The saved state includes commit mode, summary, description, bump choice, staged files, and version. On the next launch, it offers to restore the session, skipping the API call entirely. State is always cleared on a clean exit. Non-interactive mode automatically clears saved sessions to prevent stale state in CI environments.

## 7. User Configurability Without Complexity
- **Motivation**: Different repos have different conventions. A hardcoded tool becomes an obstacle for teams.
- **Implementation**: An optional `.commitgenrc` JSON file (local or global) lets users override defaults like `default_bump`, `max_diff_length`, and `model`. Environment variables always take precedence over config file values, allowing CI pipelines to override without editing files. Configuration priority: environment variables → repo config → global config → built-in defaults.

## 8. Headless & CI/CD Compatibility
- **Motivation**: The tool should be usable as a step in automated pipelines, not just interactively.
- **Implementation**: `--non-interactive` mode (and auto-detection of `CI`, `GITHUB_ACTIONS`, `GITLAB_CI`, `JENKINS_URL`, `TRAVIS` env vars) skips all prompts, auto-stages everything, and uses config defaults throughout. `--dry-run` mode allows full pipeline testing without writing any git objects. Non-interactive mode automatically clears saved sessions to prevent stale state.

## 9. Commit Message Validation & Quality Assurance
- **Motivation**: AI-generated messages should still meet repository standards and conventional commit specifications.
- **Implementation**: Built-in validation checks commit messages for 72-character line limit, proper conventional commit format (`feat:`, `fix:`, `docs:`, `style:`, `refactor:`, `test:`, `chore:`, `perf:`, `ci:`, `build:`, `revert:`, `update:`), and required blank line after title. Validation warnings (including version tag collision warning) displayed in review screen. Optional spell-checking via system `aspell` command (press `s` in review). Commit statistics with per-extension file counts provide context before finalizing.

## 10. Flexible Commit History Management
- **Motivation**: Real development workflows often require updating or refining previous commits—whether to fix a typo, add forgotten changes, or rephrase for clarity. A commit tool should support these workflows natively rather than forcing users to use complex git commands.
- **Implementation**: Three distinct commit modes:
  - **New**: Standard commit with version bump and tag creation
  - **Amend**: Update the last commit message while preserving its structure, adding new staged changes
  - **Fresh Amend**: Completely replace the last commit message with AI-generated content that encompasses all changes
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

## 12. Post-Commit Workflow Automation
- **Motivation**: Committing is only step one of modern development. A professional tool should support the full lifecycle up to deployment.
- **Implementation**: 
  - Automatic version updates in project files (package.json, pyproject.toml)
  - CHANGELOG.md generation with version, date, and changes
  - Git tag creation preserving original version formatting
  - Pull Request creation via GitHub CLI (`gh pr create`)
  - CI pipeline monitoring with live streaming (`gh run watch`)
  - Automatic GitHub Repository Creation (public or private) when no remote is configured
- Force-push detection and warnings for amended commits
  - Version prefix added to commit messages for all modes (e.g., `v1.2.3 - feat: add feature` or `1.2.3 - feat: add feature`)

## 13. Terminal Resilience & Interface Stability
- **Motivation**: Command-line interfaces should never crash or hang indefinitely due to recursive call stack limits, network timeouts, or stdout redirection errors.
- **Implementation**:
  - Replaces recursive interaction models in the file staging picker with iterative loops to avoid stack overflows.
  - Adds connection timeouts (60 seconds) on API client boundaries to fail gracefully instead of hanging forever.
  - Introduces defensive file-existence guards before opening files for binary checking, preventing errors on deleted files.
  - Implements a centralized conditional ANSI color wrapper (`c()`) to prevent color escape codes from leaking in non-TTY or redirected output environments.
  - Employs robust printing fallbacks to quietly suppress/bypass terminal errors if stdout streams are closed or redirected.
