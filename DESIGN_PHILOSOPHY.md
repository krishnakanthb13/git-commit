# Design Philosophy

This document outlines the core architectural and design decisions behind the AI Git Commit & Version Bumper Tool.

## 1. Zero External Dependencies
- **Motivation**: Tools like this should run instantly in any repository on any machine without requiring a virtual environment, `pip install`, or node module setups.
- **Implementation**: Written in vanilla Python 3 utilizing `urllib.request` instead of `requests` and a lightweight manual `.env` file parser instead of `python-dotenv`.

## 2. High Efficiency & Cost Minimization
- **Motivation**: Minimize latency, API payload sizes, and Gemini API token consumption.
- **Implementation**:
  - Uses cost-effective model (`gemini-3.1-flash-lite`) by default for optimal price-performance ratio.
  - Structured output (`responseMimeType: "application/json"`) guarantees the model returns precisely a JSON object containing both `summary` and `description`.
  - Smart Diff Optimization: Large diffs are parsed and truncated thoughtfully. The script prioritizes preserving `diff --git` headers so the model always knows exactly which files were modified, rather than blindly truncating the middle of the alphabet.
  - Binary file detection prevents sending unreadable content to the AI, reducing wasted tokens.
  - Single API call per commit minimizes latency and cost.

## 3. Keyboard-First Interactive UI
- **Motivation**: Git commit processes should be fast and simple. Keyboard hotkeys allow power-users to speed through staging, reviewing, and committing without mouse interaction.
- **Implementation**: Uses short character keys (`c`, `e`, `v`, `d`, `s`, `x`) to control the commit loop. File staging uses comma-separated index selection. The unstage option (`u`) allows in-flight corrections without restarting.

## 4. Automatic Version Alignment
- **Motivation**: Semantic versioning is often neglected or updated out of sync with commits.
- **Implementation**: Binds version tracking directly into the commit pipeline, enabling simultaneous source file version increments (`package.json`, `pyproject.toml`), `CHANGELOG.md` generation, and git tags right alongside the commit command.

## 5. Automated Workflow Lifecycle
- **Motivation**: Committing is only step one of modern development. A professional tool should support the full lifecycle up to deployment.
- **Implementation**: After pushing, the tool attempts to use native CLIs like GitHub's `gh` to handle immediate Pull Request creation and interactive live-streaming of CI/CD pipelines directly in the terminal.

## 6. Resilience & Crash Safety
- **Motivation**: A commit tool that loses your AI-generated message on a crash forces a costly re-run of the API.
- **Implementation**: Immediately after a successful Gemini response, the tool writes a session snapshot to `.git/COMMITGEN_STATE`. On the next launch, it offers to restore the session, skipping the API call entirely. State is always cleared on a clean exit.

## 7. User Configurability Without Complexity
- **Motivation**: Different repos have different conventions. A hardcoded tool becomes an obstacle for teams.
- **Implementation**: An optional `.commitgenrc` JSON file (local or global) lets users override defaults like `default_bump`, `max_diff_length`, and `model`. Environment variables always take precedence over config file values, allowing CI pipelines to override without editing files. Configuration priority: environment variables → repo config → global config → built-in defaults.

## 8. Headless & CI/CD Compatibility
- **Motivation**: The tool should be usable as a step in automated pipelines, not just interactively.
- **Implementation**: `--non-interactive` mode (and auto-detection of `CI`/`GITHUB_ACTIONS` env vars) skips all prompts, auto-stages everything, and uses config defaults throughout. `--dry-run` mode allows full pipeline testing without writing any git objects.

## 9. Commit Message Validation & Quality Assurance
- **Motivation**: AI-generated messages should still meet repository standards and conventional commit specifications.
- **Implementation**: Built-in validation checks commit messages for 72-character line limit, proper conventional commit format (`feat:`, `fix:`, etc.), and required blank line after title. Optional spell-checking via system `aspell` command. Commit statistics with per-extension file counts provide context before finalizing.
