# Design Philosophy

This document outlines the core architectural and design decisions behind the AI Git Commit & Version Bumper Tool.

## 1. Zero External Dependencies
- **Motivation**: Tools like this should run instantly in any repository on any machine without requiring a virtual environment, `pip install`, or node module setups.
- **Implementation**: Written in vanilla Python 3 utilizing `urllib.request` instead of `requests` and a lightweight manual `.env` file parser instead of `python-dotenv`.

## 2. High Efficiency & Cost Minimization
- **Motivation**: Minimize latency, API payload sizes, and Gemini API token consumption.
- **Implementation**:
  - Structured output (`responseMimeType: "application/json"`) guarantees the model returns precisely a JSON object containing both `summary` and `description`.
  - Smart Diff Optimization: Large diffs are parsed and truncated thoughtfully. The script prioritizes preserving `diff --git` headers so the model always knows exactly which files were modified, rather than blindly truncating the middle of the alphabet.

## 3. Keyboard-First Interactive UI
- **Motivation**: Git commit processes should be fast and simple. Keyboard hotkeys allow power-users to speed through staging, reviewing, and committing without mouse interaction.
- **Implementation**: Uses short character keys (`c`, `e`, `v`, `x`) to control the commit loop and simple comma-separated index selection for file staging.

## 4. Automatic Version Alignment
- **Motivation**: Semantic versioning is often neglected or updated out of sync with commits.
- **Implementation**: Binds version tracking directly into the commit pipeline, enabling simultaneous source file version increments (`package.json`, `pyproject.toml`), `CHANGELOG.md` generation, and git tags right alongside the commit command.

## 5. Automated Workflow Lifecycle
- **Motivation**: Committing is only step one of modern development. A professional tool should support the full lifecycle up to deployment.
- **Implementation**: After pushing, the tool attempts to use native CLIs like GitHub's `gh` to handle immediate Pull Request creation and interactive live-streaming of CI/CD pipelines directly in the terminal.
