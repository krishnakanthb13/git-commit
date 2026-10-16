"""Regression tests for git_commit.py.

These tests cover the helper functions that were hardened during the audit:
version handling, changelog updates, .env handling, secret redaction, diff
truncation, commit rollback, tag handling and rename staging.

They use temporary directories/repositories and never touch the network.
Run with:  python -m pytest
"""
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import git_commit as gc  # noqa: E402


def _git(*args, cwd=None):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True, cwd=cwd)


def _init_repo(path):
    _git("init", "-q", cwd=path)
    _git("config", "user.email", "test@example.com", cwd=path)
    _git("config", "user.name", "tester", cwd=path)


# --------------------------------------------------------------------- version
def test_version_prefix_regex_handles_multipart_versions():
    pattern = re.compile(r'^(v?\d+(?:\.\d+)+)\s*-\s*')
    assert pattern.match("v1.1.1.11 - fix: x").group(1) == "v1.1.1.11"
    assert pattern.match("1.2.3 - feat: y").group(1) == "1.2.3"
    assert pattern.match("feat: y") is None


def test_semver_key_and_regression_detection():
    assert gc.semver_key("v1.2.3.4") == (1, 2, 3, 4)
    assert gc.is_version_regression("v0.9.0", "v1.0.0")
    assert not gc.is_version_regression("v1.1.0", "v1.0.0")
    assert not gc.is_version_regression("v1.0.0", "v1.0.0")
    assert gc.is_version_regression("v1.1.1.9", "v1.1.1.11")


# ------------------------------------------------------------------- .env / secrets
def test_drop_env_files_filters_secrets_and_keeps_templates():
    files = [".env", "src/a.py", ".env.local", ".env.template", ".envrc"]
    assert gc.drop_env_files(files) == ["src/a.py", ".env.template"]


def test_redact_secrets_only_touches_matches():
    # Built at runtime so this file never contains a literal secret-shaped
    # string (which would itself be redacted from the AI prompt).
    aws_key_id = "AKIA" + "ABCDEFGHIJKLMNOP"
    credential_url = "postgres://" + "user:pass" + "@host/db"

    assert "[REDACTED]" in gc.redact_secrets(aws_key_id)
    assert "[REDACTED]" in gc.redact_secrets(credential_url)
    assert gc.redact_secrets("x = compute(1, 2)") == "x = compute(1, 2)"


def test_redact_secrets_reports_masked_category(capsys):
    aws_key_id = "AKIA" + "ABCDEFGHIJKLMNOP"
    result = gc.redact_secrets(aws_key_id)
    assert result == "[REDACTED]"

    out = capsys.readouterr().out
    assert "AWS access key id" in out
    # The secret itself is never echoed back, only a short masked prefix.
    assert aws_key_id not in out
    assert "AKIA" + "*" * 16 in out


def test_load_dotenv_does_not_override_real_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "GC_TEST_FROM_FILE=fromfile\nGC_TEST_BOTH=filevalue\n", encoding="utf-8"
    )
    monkeypatch.setenv("GC_TEST_BOTH", "envvalue")
    monkeypatch.delenv("GC_TEST_FROM_FILE", raising=False)

    before = set(os.environ)
    gc.load_dotenv()
    try:
        assert os.environ["GC_TEST_FROM_FILE"] == "fromfile"
        assert os.environ["GC_TEST_BOTH"] == "envvalue"
    finally:
        for key in set(os.environ) - before:
            os.environ.pop(key, None)


# -------------------------------------------------------------------- changelog
def test_update_changelog_insert_replace_and_append(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    # No changelog -> no-op.
    gc.update_changelog("1.0.0", "s", "d")
    assert not (tmp_path / "CHANGELOG.md").exists()

    # Append into a file with only a title.
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    gc.update_changelog("1.0.0", "first", "- a")
    gc.update_changelog("1.1.0", "second", "- b")
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.startswith("# Changelog")
    assert text.index("## [v1.1.0]") < text.index("## [v1.0.0]")

    # Re-writing the same version replaces instead of duplicating.
    gc.update_changelog("1.0.0", "first-updated", "- a2")
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.count("## [v1.0.0]") == 1
    assert "first-updated" in text
    assert text.index("## [v1.1.0]") < text.index("## [v1.0.0]")


# ----------------------------------------------------------------- truncation
def test_truncate_keeps_content_for_single_overlong_line():
    out, truncated = gc.truncate_diff_for_context("x" * 5000, 1000, 10_000_000)
    assert truncated is True
    assert out.startswith("x")

    same, not_truncated = gc.truncate_diff_for_context("short", 1000, 10_000_000)
    assert not_truncated is False and same == "short"


# ----------------------------------------------------------- rollback / restart
def test_snapshot_restore_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text('{"version": "1.0.0"}\n', encoding="utf-8")
    snapshot = gc.snapshot_files(["package.json", "missing.json"])
    (tmp_path / "package.json").write_text('{"version": "1.0.1"}\n', encoding="utf-8")

    gc.restore_files_snapshot(snapshot)
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == '{"version": "1.0.0"}\n'
    assert "missing.json" not in snapshot

    # Files that were never rewritten are left completely untouched.
    (tmp_path / "CHANGELOG.md").write_text("keep me\n", encoding="utf-8")
    unchanged_snapshot = gc.snapshot_files(["CHANGELOG.md"])
    gc.restore_files_snapshot(unchanged_snapshot)
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == "keep me\n"


def test_rollback_does_not_unstage_untouched_files(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("v1\n", encoding="utf-8")
    _git("add", "CHANGELOG.md")
    _git("commit", "-qm", "init")

    # The user stages an edit manually; the tool never rewrites the file.
    (tmp_path / "CHANGELOG.md").write_text("v1\nuser edit\n", encoding="utf-8")
    _git("add", "CHANGELOG.md")

    snapshot = gc.snapshot_files(["CHANGELOG.md"])
    gc.restore_files_snapshot(snapshot)

    assert "CHANGELOG.md" in _git("diff", "--cached", "--name-only").stdout
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == "v1\nuser edit\n"


def test_rollback_restores_and_unstages_modified_files(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "package.json").write_text('{"version": "1.0.0"}\n', encoding="utf-8")
    _git("add", "package.json")
    _git("commit", "-qm", "init")

    snapshot = gc.snapshot_files(["package.json"])
    # The tool rewrites and stages the version file before a failing commit.
    (tmp_path / "package.json").write_text('{"version": "1.0.1"}\n', encoding="utf-8")
    _git("add", "package.json")

    gc.restore_files_snapshot(snapshot)

    assert (tmp_path / "package.json").read_text(encoding="utf-8") == '{"version": "1.0.0"}\n'
    assert "package.json" not in _git("diff", "--cached", "--name-only").stdout


def test_prompt_restart_survives_closed_stdin(monkeypatch):
    class _EOFStdin:
        def readline(self):
            raise EOFError()

        def isatty(self):
            return False

    monkeypatch.setattr(sys, "stdin", _EOFStdin())
    assert gc.prompt_restart() is False


# --------------------------------------------------------- git-dir / tags / renames
def test_git_dir_and_session_state_from_subdirectory(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    subdir = tmp_path / "sub" / "deep"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)

    assert gc.init_git_dir() is True
    assert os.path.isabs(gc.GIT_DIR)
    state_path = gc.session_state_path()
    assert os.path.normpath(state_path).startswith(os.path.normpath(gc.GIT_DIR))

    gc.save_session_state({"summary": "x"})
    assert gc.load_session_state() == {"summary": "x"}
    gc.clear_session_state()
    assert not os.path.exists(state_path)


def test_move_tag_to_head_preserves_annotated_tag(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("a", encoding="utf-8")
    _git("add", "f.txt")
    _git("commit", "-qm", "c1")
    _git("tag", "-a", "v1.0.0", "-m", "Annotated message")
    (tmp_path / "f.txt").write_text("b", encoding="utf-8")
    _git("commit", "-qam", "c2")

    gc.move_tag_to_head("v1.0.0")

    assert _git("cat-file", "-t", "refs/tags/v1.0.0").stdout.strip() == "tag"
    assert _git("for-each-ref", "refs/tags/v1.0.0", "--format=%(contents)").stdout.strip() == "Annotated message"
    head = _git("rev-parse", "HEAD").stdout.strip()
    assert _git("rev-parse", "v1.0.0^{}").stdout.strip() == head


def test_prompt_stage_files_pairs_unstaged_rename(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "util.py").write_text("def old():\n    return 1\n", encoding="utf-8")
    _git("add", ".")
    _git("commit", "-qm", "initial")

    # Rename lib/util.py -> src/util.py without staging anything.
    os.remove(tmp_path / "lib" / "util.py")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "util.py").write_text("def new():\n    return 2\n", encoding="utf-8")

    staged, unstaged, untracked = gc.get_git_files()
    assert "lib/util.py" in unstaged
    assert "src/" in untracked

    # Picker lists the deletion first, the untracked directory second.
    monkeypatch.setattr(gc, "input", lambda prompt="": "2")
    assert gc.prompt_stage_files(staged, unstaged, untracked) is True

    cached = _git("diff", "--cached", "--name-status").stdout
    assert "src/util.py" in cached
    assert any(line.startswith("D") and "lib/util.py" in line for line in cached.splitlines())


def test_check_remote_tag_requires_exact_match(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    _init_repo(work)
    (work / "f.txt").write_text("a", encoding="utf-8")
    _git("add", "f.txt", cwd=work)
    _git("commit", "-qm", "c1", cwd=work)
    _git("tag", "v1.2.34", cwd=work)  # would be glob-matched by a "v1.2.3" pattern
    _git("push", "-q", "origin", "v1.2.34", cwd=work)

    monkeypatch.chdir(work)
    assert gc.check_remote_tag("v1.2.34") is True
    assert gc.check_remote_tag("v1.2.3") is False
    assert gc.check_remote_tag("v9.9.9") is False
