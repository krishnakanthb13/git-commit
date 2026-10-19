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


def test_prerelease_versions_use_core_for_compare_and_bump():
    # Pre-release/build metadata is ignored for ordering...
    assert gc.semver_key("1.2.3-rc1") == (1, 2, 3)
    assert gc.semver_key("1.2.3+build.7") == (1, 2, 3)
    assert not gc.is_version_regression("1.2.3-rc1", "1.2.3")
    # ...and bumping a prerelease works instead of returning it unchanged.
    assert gc.increment_version("1.2.3-rc1", "patch") == "1.2.4"
    assert gc.increment_version("v1.2.3-rc1+build", "major") == "v2.0.0"


def test_validate_commit_message_keeps_body_dashes():
    # A ' - ' in the body is not a version prefix.
    assert gc.validate_commit_message("fix: handle a - b") == []
    # A real version prefix is still stripped before the format check.
    assert gc.validate_commit_message("v1.2.3 - fix: handle it") == []
    # Non-conventional messages are still flagged.
    assert any("conventional" in issue for issue in gc.validate_commit_message("random text - here"))


def test_co_author_trailer_is_address_like():
    trailer = gc.format_co_author_trailer("gemini-3.5-flash-lite")
    assert trailer.endswith("<gemini-3.5-flash-lite@users.noreply.github.com>")


# ------------------------------------------------------------------- .env / secrets
def test_drop_env_files_filters_secrets_and_keeps_templates():
    # '.envrc' (direnv config) is often intentionally tracked and is no
    # longer filtered; only real secret .env variants are dropped.
    files = [".env", "src/a.py", ".env.local", ".env.template", ".envrc"]
    assert gc.drop_env_files(files) == ["src/a.py", ".env.template", ".envrc"]


def test_staged_envrc_is_kept_but_flagged(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".envrc").write_text("layout python\n", encoding="utf-8")
    _git("add", ".envrc")

    staged, unstaged, untracked = gc.get_git_files()
    assert ".envrc" in staged  # user-committable config, not silently stripped
    assert ".envrc" in _git("diff", "--cached", "--name-only").stdout  # not auto-unstaged
    assert "no secrets" in capsys.readouterr().out


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


def test_staged_env_without_head_is_unstaged_without_hanging(tmp_path, monkeypatch):
    _init_repo(tmp_path)  # deliberately no commit: the repo has no HEAD yet
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _git("add", ".env")

    # This used to loop forever: unstaging failed (no HEAD) and the filter
    # re-ran itself, spamming warnings. It must return promptly instead.
    staged, unstaged, untracked = gc.get_git_files()
    assert ".env" not in staged + unstaged + untracked
    # The file is genuinely unstaged in git, not merely hidden from the lists.
    assert ".env" not in _git("diff", "--cached", "--name-only").stdout


def test_restore_files_snapshot_unstages_on_headless_repo(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("v1\n", encoding="utf-8")
    snapshot = gc.snapshot_files(["CHANGELOG.md"])
    # The tool rewrites and stages the file before a commit that never lands.
    (tmp_path / "CHANGELOG.md").write_text("v1\ntool edit\n", encoding="utf-8")
    _git("add", "CHANGELOG.md")

    # 'git restore --staged' failed here ("could not resolve HEAD"); 'reset'
    # works on a repo without commits.
    gc.restore_files_snapshot(snapshot)

    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == "v1\n"
    assert "CHANGELOG.md" not in _git("diff", "--cached", "--name-only").stdout


def test_create_annotated_tag_duplicate_does_not_raise(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("a", encoding="utf-8")
    _git("add", "f.txt")
    _git("commit", "-qm", "c1")
    _git("tag", "-a", "v1.0.0", "-m", "existing")

    # A pre-existing tag is reported but must not raise, so the caller can
    # never mistake it for a failed commit and roll back.
    assert gc.create_annotated_tag("v1.0.0") is False
    out = capsys.readouterr().out
    assert "manually" in out
    # The commit itself is untouched.
    assert _git("log", "-1", "--format=%s").stdout.strip() == "c1"
    # A fresh tag name still succeeds.
    assert gc.create_annotated_tag("v1.0.1") is True
    assert _git("tag", "-l", "v1.0.1").stdout.strip() == "v1.0.1"


def test_update_version_in_files_preserves_package_json_formatting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original = '{\n    "name": "demo",\n    "version": "1.2.3",\n    "private": true\n}\n'
    (tmp_path / "package.json").write_text(original, encoding="utf-8")

    gc.update_version_in_files("v1.2.4")

    updated = (tmp_path / "package.json").read_text(encoding="utf-8")
    # Only the version value changes; indentation/order/trailing newline stay.
    assert updated == original.replace('"1.2.3"', '"1.2.4"')


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


def test_build_file_entries_merges_staged_and_unstaged_edit():
    details = {
        "f.txt": {"staged": "staged: modified", "unstaged": "modified"},
        "g.txt": {"unstaged": "modified"},
    }
    entries = gc.build_file_entries(["f.txt"], ["f.txt", "g.txt"], [], details)

    # f.txt is staged and edited again; it appears once with a merged badge.
    assert [e[0] for e in entries] == ["f.txt", "g.txt"]
    assert entries[0][1] == "staged: modified + modified"
    # Merged rows still count as staged for colouring purposes.
    assert entries[0][1].startswith("staged")


def test_parse_index_selection_rejects_out_of_range_and_clips_ranges():
    with pytest.raises(ValueError):
        gc.parse_index_selection("5", 3)
    with pytest.raises(ValueError):
        gc.parse_index_selection("4-9", 3)  # whole range outside the list
    assert gc.parse_index_selection("1-99", 3) == [0, 1, 2]  # clipped
    assert gc.parse_index_selection("2,2,1", 3) == [1, 0]  # de-duplicated


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


# --------------------------------------------------------------------- viewer
def _feed_inputs(monkeypatch, answers):
    """Patch gc.input to return the supplied answers in order."""
    it = iter(answers)
    monkeypatch.setattr(gc, "input", lambda prompt="": next(it, ""))


def test_prompt_startup_mode_variants(monkeypatch):
    _feed_inputs(monkeypatch, ["2"])
    assert gc.prompt_startup_mode() == "viewer"
    _feed_inputs(monkeypatch, ["0"])
    assert gc.prompt_startup_mode() == "quit"
    _feed_inputs(monkeypatch, [""])
    assert gc.prompt_startup_mode() == "commit"  # default on Enter
    _feed_inputs(monkeypatch, ["1"])
    assert gc.prompt_startup_mode() == "commit"


def test_prompt_select_model_reverted_to_three_options(monkeypatch):
    # Option 4 no longer lives in the model prompt; it is now the startup menu.
    _feed_inputs(monkeypatch, ["2"])
    assert gc.prompt_select_model() == "gemini-3.1-flash-lite"


def test_print_review_options_compact_gutter_for_new_commit(capsys):
    gc.print_review_options("gemini-3.5-flash-lite", False, "new")
    out = capsys.readouterr().out

    for header in ("Commit", "AI", "Version", "Inspect", "Cancel"):
        assert header in out
    for key in ("c)", "e)", "g)", "m)", "t)", "v)", "h)", "d)", "w)", "s)", "x)"):
        assert key in out
    # No blank lines between groups — keeps the menu short in a narrow window.
    assert "\n\n" not in out.lstrip("\n")


def test_print_review_options_hides_version_group_when_amending(capsys):
    gc.print_review_options("gemini-3.5-flash-lite", True, "amend")
    out = capsys.readouterr().out

    assert "Inspect" in out
    assert "Version" not in out
    # AI regeneration is only offered for new commits.
    assert "g)" not in out
    # Attribution reflects the current toggle state.
    assert "Co-Authored-By: yes" in out


def test_show_changed_files_viewer_picker_style(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "modified.txt").write_text("a\n", encoding="utf-8")
    _git("add", ".")
    _git("commit", "-qm", "initial")

    # One staged change, one unstaged change, one untracked file.
    (tmp_path / "tracked.txt").write_text("two\n", encoding="utf-8")
    _git("add", "tracked.txt")
    (tmp_path / "modified.txt").write_text("b\n", encoding="utf-8")
    (tmp_path / "brand_new.txt").write_text("new\n", encoding="utf-8")

    _feed_inputs(monkeypatch, [""])  # Enter -> back
    gc.show_changed_files_viewer()
    out = capsys.readouterr().out

    # Same picker-style header, legend and badges as the staging screen.
    assert "Changed files" in out
    assert "staged: green" in out and "untracked: red" in out
    assert "[staged: modified] tracked.txt" in out
    assert "[modified] modified.txt" in out
    assert "[untracked] brand_new.txt" in out


def test_show_changed_files_viewer_opens_file_stats(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    _git("add", "a.txt")
    _git("commit", "-qm", "initial")
    (tmp_path / "a.txt").write_text("two\nthree\n", encoding="utf-8")

    # 1 -> open file 1 stats, "" -> return, "" -> back to viewer menu.
    _feed_inputs(monkeypatch, ["1", "", ""])
    gc.show_changed_files_viewer()
    out = capsys.readouterr().out

    assert "--- a.txt ---" in out
    assert "Unstaged:" in out
    assert "insertion" in out
    # Change counts only — never the file contents.
    assert "three" not in out


def test_browse_commit_history_opens_commit_details(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample.txt").write_text("hello\nworld\n", encoding="utf-8")
    _git("add", "sample.txt")
    _git("commit", "-qm", "add sample file")

    # 1 -> open first commit, "" -> return from detail, b -> back to menu.
    _feed_inputs(monkeypatch, ["1", "", "b"])
    gc.browse_commit_history(per_page=5)
    out = capsys.readouterr().out

    assert "Commit History" in out
    assert "add sample file" in out
    # Per-file +/- stats are shown, but not the file contents.
    assert "sample.txt" in out
    assert "file changed" in out
    assert "hello" not in out


def test_browse_commit_history_guards_page_size(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    _git("add", "a.txt")
    _git("commit", "-qm", "c1")

    _feed_inputs(monkeypatch, ["b"])
    gc.browse_commit_history(per_page=0)  # used to divide by zero
