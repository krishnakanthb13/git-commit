# Design Philosophy

This document outlines the core architectural and design decisions behind the AI Git Commit & Version Bumper Tool.

## 1. Zero External Dependencies
- **Motivation**: Tools like this should run instantly in any repository on any machine without requiring a virtual environment, `pip install`, or node module setups.
- **Implementation**: Written in vanilla Python 3 utilizing `urllib.request` instead of `requests` and a lightweight manual `.env` file parser instead of `python-dotenv`.

## 2. High Efficiency & Cost Minimization
- **Motivation**: Minimize latency, API payload sizes, and Gemini API token consumption.
- **Implementation**:
  - Structured output (`responseMimeType: "application/json"`) is utilized to guarantee the model returns precisely a JSON object containing both `summary` and `description`. This prevents the need for separate API calls for the header and body.
  - Large diffs are safely truncated to fit within standard model context windows without wasting excessive tokens on redundant code lines.

## 3. Keyboard-First Interactive UI
- **Motivation**: Git commit processes should be fast and simple. Keyboard hotkeys allow power-users to speed through staging, reviewing, and committing without mouse interaction.
- **Implementation**: Uses short character keys (`c`, `e`, `v`, `x`) to control the commit loop and simple comma-separated index selection for file staging.

## 4. Automatic Version Alignment
- **Motivation**: Semantic versioning is often neglected or updated out of sync with commits.
- **Implementation**: Binds version tracking directly into the commit pipeline, enabling simultaneous source file version increments and git tags right alongside the commit command.
