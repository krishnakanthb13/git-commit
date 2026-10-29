"""Regression tests for git_commit.py.

These tests cover the helper functions that were hardened during the audit:
version handling, changelog updates, .env handling, secret redaction, diff
truncation, commit rollback, tag handling and rename staging.

They use temporary directories/repositories and never touch the network.
Run with:  python -m pytest
"""
import os
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
    match = gc.COMMIT_VERSION_PREFIX_RE.match("v1.1.1.11 - fix: x")
    assert match and match.group(1) == "v1.1.1.11"
    match = gc.COMMIT_VERSION_PREFIX_RE.match("1.2.3 - feat: y")
    assert match and match.group(1) == "1.2.3"
    assert gc.COMMIT_VERSION_PREFIX_RE.match("feat: y") is None
    # Prerelease/build suffixes are part of the version token.
    match = gc.COMMIT_VERSION_PREFIX_RE.match("v1.2.3-rc1 - fix: z")
    assert match and match.group(1) == "v1.2.3-rc1"
    # Tags accept the same multi-part/prerelease shapes.
    assert gc.TAG_VERSION_RE.match("v1.1.1.11")
    assert gc.TAG_VERSION_RE.match("1.2.3-rc1+build.7")
    assert gc.TAG_VERSION_RE.match("release-1.2.3") is None


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

    gc.restore_files_snapshot(snapshot, {"package.json": '{"version": "1.0.1"}\n'})
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == '{"version": "1.0.0"}\n'
    assert "missing.json" not in snapshot

    # Files that were never rewritten are left completely untouched.
    (tmp_path / "CHANGELOG.md").write_text("keep me\n", encoding="utf-8")
    unchanged_snapshot = gc.snapshot_files(["CHANGELOG.md"])
    gc.restore_files_snapshot(unchanged_snapshot, {})
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == "keep me\n"


def test_rollback_leaves_user_edit_the_tool_never_wrote(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("original\n", encoding="utf-8")
    _git("add", "CHANGELOG.md")
    _git("commit", "-qm", "init")

    snapshot = gc.snapshot_files(["CHANGELOG.md"])
    # The user edits the file while the tool is running; the tool never wrote
    # this path, so rollback must not resurrect the snapshot content.
    (tmp_path / "CHANGELOG.md").write_text("user edit\n", encoding="utf-8")

    gc.restore_files_snapshot(snapshot, {})
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == "user edit\n"


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

    gc.restore_files_snapshot(snapshot, {"package.json": '{"version": "1.0.1"}\n'})

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
    gc.restore_files_snapshot(snapshot, {"CHANGELOG.md": "v1\ntool edit\n"})

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


# ------------------------------------------------- audit regressions (P0/P1)
def test_update_version_in_files_pyproject_replacement(tmp_path, monkeypatch, capsys):
    # The old backreference replacement raised 'invalid group reference 12'
    # for every normal version ('\\12.0.0\\1') and never wrote pyproject.toml.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1.0.0"\n\n'
        '[tool.other]\nversion = "9.9.9"\n',
        encoding="utf-8",
    )

    gc.update_version_in_files("v2.0.0")

    text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "invalid group reference" not in out
    assert "Failed to update pyproject.toml" not in out
    assert 'name = "demo"\nversion = "2.0.0"' in text
    # Only [project] / [tool.poetry] versions are rewritten.
    assert 'version = "9.9.9"' in text


def test_init_git_dir_moves_to_repo_root_and_stages_from_subdirectory(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "root.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.txt").write_text("b\n", encoding="utf-8")
    _git("add", ".", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)
    (tmp_path / "root.txt").write_text("a2\n", encoding="utf-8")
    (tmp_path / "sub" / "inner.txt").write_text("b2\n", encoding="utf-8")

    # Launched from a subdirectory: git status paths are repo-root-relative
    # while git add resolves against the cwd, so the tool must move to root.
    monkeypatch.chdir(tmp_path / "sub")
    assert gc.init_git_dir() is True
    assert os.path.normcase(os.path.normpath(os.getcwd())) == \
        os.path.normcase(os.path.normpath(str(tmp_path)))

    staged, unstaged, untracked = gc.get_git_files()
    assert sorted(unstaged) == ["root.txt", "sub/inner.txt"]

    monkeypatch.setattr(gc, "input", lambda prompt="": "a")
    assert gc.prompt_stage_files(staged, unstaged, untracked) is True
    cached = sorted(_git("diff", "--cached", "--name-only", cwd=tmp_path).stdout.split())
    assert cached == ["root.txt", "sub/inner.txt"]


def test_get_git_files_parses_unmerged_and_typechange(monkeypatch):
    # UU = unresolved conflict, 'T ' = staged typechange, ' T' = unstaged typechange.
    raw = "UU keep.txt\0T  f\0 T g\0?? n.txt\0"
    monkeypatch.setattr(gc, "run_git_cmd", lambda args, strip=True: raw if args[0] == "status" else None)

    staged, unstaged, untracked, details = gc.get_git_files(include_details=True)

    assert staged == ["f"]
    assert unstaged == ["g"]
    assert untracked == ["n.txt"]
    # Conflicts are recorded with a badge instead of silently vanishing or
    # being presented as committable.
    assert "keep.txt" not in staged + unstaged + untracked
    assert details["keep.txt"]["unmerged"] == "conflict: needs resolution"
    assert details["f"]["staged"] == "staged: typechange"
    assert details["g"]["unstaged"] == "typechange"


def test_merge_conflicts_block_staging(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "keep.txt").write_text("base\n", encoding="utf-8")
    _git("add", "keep.txt")
    _git("commit", "-qm", "base")
    base_branch = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    _git("checkout", "-qb", "feature")
    (tmp_path / "keep.txt").write_text("feature\n", encoding="utf-8")
    _git("commit", "-qam", "feature")
    _git("checkout", "-q", base_branch)
    (tmp_path / "keep.txt").write_text("main\n", encoding="utf-8")
    _git("commit", "-qam", "main change")
    assert _git("merge", "feature").returncode != 0

    assert gc.get_unmerged_files() == ["keep.txt"]

    staged, unstaged, untracked, details = gc.get_git_files(include_details=True)
    assert "keep.txt" not in staged
    entries = gc.build_file_entries(staged, unstaged, untracked, details)
    assert entries and entries[0][0] == "keep.txt"

    assert gc.prompt_stage_files(staged, unstaged, untracked) == "quit"
    assert "Unresolved merge conflicts" in capsys.readouterr().out


def test_staged_env_rename_resets_both_sides(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _git("add", ".env")
    _git("commit", "-qm", "init")
    _git("mv", ".env", "foo")  # staged rename .env -> foo

    staged, unstaged, untracked = gc.get_git_files()

    assert "foo" not in staged + unstaged + untracked
    assert ".env" not in staged + unstaged + untracked
    # Both sides of the rename are reset: previously only 'foo' was reset and
    # the staged deletion of '.env' stayed committable.
    assert _git("diff", "--cached", "--name-only").stdout.strip() == ""


def test_env_reset_failure_fails_closed(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _git("add", ".env")

    real_run = gc.run_git_cmd

    def fake_run(args, strip=True):
        if args[:2] == ["reset", "-q"]:
            return None  # simulate an index lock / permission failure
        return real_run(args, strip=strip)

    monkeypatch.setattr(gc, "run_git_cmd", fake_run)

    with pytest.raises(RuntimeError, match="could not unstage"):
        gc.get_git_files()
    # The tool must never claim success while a secret stays staged.
    assert ".env" in _git("diff", "--cached", "--name-only").stdout


def test_get_git_files_status_failure_is_not_clean(monkeypatch):
    monkeypatch.setattr(gc, "run_git_cmd", lambda args, strip=True: None)
    with pytest.raises(RuntimeError, match="git status failed"):
        gc.get_git_files()


def test_viewer_lists_env_but_never_mutates_index(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _git("add", ".env")

    _feed_inputs(monkeypatch, [""])  # Enter -> back
    gc.show_changed_files_viewer()

    assert ".env" in capsys.readouterr().out
    # Read-only screen: the index is completely untouched.
    assert ".env" in _git("diff", "--cached", "--name-only").stdout


def test_dry_run_makes_session_state_inert(tmp_path, monkeypatch):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    monkeypatch.setattr(gc, "GIT_DIR", str(git_dir))
    monkeypatch.setattr(gc, "DRY_RUN_ACTIVE", True)

    gc.save_session_state({"summary": "x"})
    assert not (git_dir / "COMMITGEN_STATE").exists()
    assert gc.load_session_state() is None

    # Even clearing an existing file is a no-op in dry-run mode.
    state_file = git_dir / "COMMITGEN_STATE"
    state_file.write_text('{"summary": "old"}', encoding="utf-8")
    gc.clear_session_state()
    assert state_file.exists()


def test_unstage_works_before_first_commit(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)  # deliberately no commit: no HEAD exists yet
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    _git("add", "a.txt")

    # 'u' -> unstage menu, '1' -> file 1, 'q' -> leave the picker.
    _feed_inputs(monkeypatch, ["u", "1", "q"])
    assert gc.prompt_stage_files([], [], []) == "quit"

    out = capsys.readouterr().out
    assert "Unstaged: a.txt" in out
    assert "Could not unstage" not in out
    # 'git restore --staged' failed here ("could not resolve HEAD") and the
    # result was ignored; 'git reset' must actually unstage.
    assert _git("diff", "--cached", "--name-only").stdout.strip() == ""


def test_load_config_validates_bad_values(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".commitgenrc").write_text(
        '{"max_diff_length": "1000", "max_input_tokens": null, '
        '"default_bump": "sideways", "auto_push": "false", "model": 42}',
        encoding="utf-8",
    )

    cfg = gc.load_config()

    assert cfg["max_diff_length"] == 1000  # numeric string coerced
    assert cfg["max_input_tokens"] == gc.CONFIG_DEFAULTS["max_input_tokens"]  # null -> default
    assert cfg["default_bump"] == "patch"  # invalid enum -> default
    assert cfg["auto_push"] is False  # 'false' string -> boolean
    assert cfg["model"] == gc.CONFIG_DEFAULTS["model"]  # non-string -> default
    out = capsys.readouterr().out
    assert "default_bump" in out and "max_input_tokens" in out


def test_truncate_clamps_bad_token_budget():
    # max_tokens smaller than the prompt overhead used to produce an empty
    # diff (negative budget) while claiming it was 'truncated'.
    out, truncated = gc.truncate_diff_for_context("abc", 1000, 1000)
    assert out == "abc"
    assert truncated is False


def test_prompt_select_model_enter_keeps_current(monkeypatch):
    _feed_inputs(monkeypatch, [""])
    assert gc.prompt_select_model("gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"
    _feed_inputs(monkeypatch, ["3", ""])  # custom with empty name also keeps current
    assert gc.prompt_select_model("gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"
    _feed_inputs(monkeypatch, ["2"])
    assert gc.prompt_select_model("gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"


def test_print_honors_explicit_sep(capsys):
    gc.print("a", "b", sep="")
    assert capsys.readouterr().out == "ab\n"
    gc.print("a", "b")
    assert capsys.readouterr().out == "a b\n"


def test_is_ci_environment_treats_falsy_values_as_false(monkeypatch):
    for var in ('CI', 'GITHUB_ACTIONS', 'GITLAB_CI', 'JENKINS_URL', 'TRAVIS'):
        monkeypatch.delenv(var, raising=False)
    assert gc.is_ci_environment() is False
    monkeypatch.setenv("CI", "false")
    assert gc.is_ci_environment() is False
    monkeypatch.setenv("CI", "0")
    assert gc.is_ci_environment() is False
    monkeypatch.setenv("CI", "true")
    assert gc.is_ci_environment() is True


def test_update_changelog_unreleased_placement_and_bracket_forms(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n- wip\n\n## [1.0.0] - 2026-01-01\n\n- old\n",
        encoding="utf-8",
    )

    gc.update_changelog("1.1.0", "new stuff", "")
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    # The entry lands below [Unreleased], not above it.
    assert text.index("[Unreleased]") < text.index("[v1.1.0]") < text.index("[1.0.0]")

    # Replacement recognises the '## [X]' heading form without the 'v'.
    gc.update_changelog("1.0.0", "replaced", "")
    text = (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "replaced" in text
    assert "## [1.0.0]" not in text  # replaced in place, not duplicated
    assert "## [v1.0.0]" in text


def test_check_precommit_hooks_commit_anyway_returns_skip_verify(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")

    real_run = subprocess.run

    def fake_run(args, *o, **kw):
        if args and args[0] == "pre-commit":
            raise subprocess.CalledProcessError(1, args)
        return real_run(args, *o, **kw)

    monkeypatch.setattr(gc.subprocess, "run", fake_run)

    # 'Commit anyway' must translate into --no-verify for the real commit,
    # otherwise the installed hooks run again and fail the same way.
    _feed_inputs(monkeypatch, ["y"])
    assert gc.check_precommit_hooks(staged_files=[]) == "skip_verify"
    assert "no-verify" in capsys.readouterr().out

    # Non-interactive runs abort instead of silently overriding hooks.
    assert gc.check_precommit_hooks(non_interactive=True, staged_files=[]) is False


def test_load_dotenv_handles_export_comments_quotes_and_bom(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\ufeffexport GC_T1=one # trailing comment\n"
        'GC_T2="has # hash"\n'
        "GC_T3='quoted # value'\n"
        "GC_T4=plain\n"
        'GC_T5="quoted value" # trailing comment\n'
        "GC_T6='single value' # trailing comment\n"
        'GC_T7="escaped \\"quote\\" and \\n newline" # note\n',
        encoding="utf-8",
    )
    for key in ("GC_T1", "GC_T2", "GC_T3", "GC_T4", "GC_T5", "GC_T6", "GC_T7"):
        monkeypatch.delenv(key, raising=False)

    before = set(os.environ)
    gc.load_dotenv()
    try:
        assert os.environ["GC_T1"] == "one"
        assert os.environ["GC_T2"] == "has # hash"
        assert os.environ["GC_T3"] == "quoted # value"
        assert os.environ["GC_T4"] == "plain"
        # A trailing comment after the closing quote must not become part of
        # the value (and must not leave the quotes in it).
        assert os.environ["GC_T5"] == "quoted value"
        assert os.environ["GC_T6"] == "single value"
        assert os.environ["GC_T7"] == 'escaped "quote" and \n newline'
    finally:
        for key in set(os.environ) - before:
            os.environ.pop(key, None)


def test_print_stat_block_keeps_pipe_in_commit_subject(capsys):
    gc.print_stat_block("commit abc123\n    fix: handle a | b\n a.txt | 2 ++\n")
    out = capsys.readouterr().out
    # A subject containing '|' is not a stat line and must not be mangled.
    assert "fix: handle a | b" in out
    assert "a.txt" in out


def test_semver_helpers_accept_numeric_versions():
    assert gc.semver_key(1.0) == (1, 0)
    assert gc.is_valid_semver(1.0) is False  # coerced to str, no crash


def test_detect_version_merges_agreeing_sources(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    _git("add", "f.txt")
    _git("commit", "-qm", "v1.0.0 - initial")
    _git("tag", "v1.0.0")

    version, source = gc.detect_version(prompt=False)

    # The tag and the commit message agree on one version: it is listed once,
    # but the label names every source instead of hiding the others.
    assert version == "v1.0.0"
    assert source == "git tag, git commit"


def test_restore_keeps_user_edits_made_after_tool_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original = '{"version": "1.0.0"}\n'
    (tmp_path / "package.json").write_text(original, encoding="utf-8")
    snapshot = gc.snapshot_files(["package.json"])

    # The tool rewrites the file, then the user edits it further (e.g. while
    # answering the tag prompt) before the commit fails.
    tool_content = '{"version": "1.0.1"}\n'
    (tmp_path / "package.json").write_text(tool_content, encoding="utf-8")
    user_content = '{"version": "1.0.1"}\n// user edit\n'
    (tmp_path / "package.json").write_text(user_content, encoding="utf-8")

    gc.restore_files_snapshot(snapshot, {"package.json": tool_content})

    # A path is only rolled back when it still holds exactly what the tool
    # wrote; the user's later edit is kept.
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == user_content


def test_restore_rolls_back_pure_tool_write(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original = '{"version": "1.0.0"}\n'
    (tmp_path / "package.json").write_text(original, encoding="utf-8")
    snapshot = gc.snapshot_files(["package.json"])

    tool_content = '{"version": "1.0.1"}\n'
    (tmp_path / "package.json").write_text(tool_content, encoding="utf-8")

    gc.restore_files_snapshot(snapshot, {"package.json": tool_content})

    assert (tmp_path / "package.json").read_text(encoding="utf-8") == original


def test_check_remote_tag_returns_none_when_check_fails(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("a", encoding="utf-8")
    _git("add", "f.txt")
    _git("commit", "-qm", "c1")

    # An unreachable remote must read as 'unknown' (None), never as
    # 'tag absent' (False) — the collision check may not silently pass.
    assert gc.check_remote_tag("v1.0.0", "no-such-remote") is None


# ------------------------------------------------- remaining audit regressions
def test_dry_run_restores_index_after_preview_staging(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git("add", "tracked.txt")
    _git("commit", "-qm", "init")
    (tmp_path / "tracked.txt").write_text("two\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")

    index = tmp_path / ".git" / "index"
    before_status = _git("status", "--porcelain").stdout
    before_index = index.read_bytes()

    def fake_impl():
        # Simulate non-interactive auto-staging inside the dry run.
        gc.run_git_cmd(["add", "--", "new.txt"])
        assert _git("diff", "--cached", "--name-only").stdout.strip() == "new.txt"
        return 0

    monkeypatch.setattr(gc, "_main_impl", fake_impl)
    monkeypatch.setattr(sys, "argv", ["git_commit.py", "--dry-run"])

    assert gc.main() == 0
    assert index.read_bytes() == before_index
    assert _git("status", "--porcelain").stdout == before_status


def test_env_rename_destination_reset_failure_fails_closed(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _git("add", ".env")
    _git("commit", "-qm", "init")
    _git("mv", ".env", "foo")  # staged rename .env -> foo

    real_run = gc.run_git_cmd

    def fake_run(args, strip=True):
        # Simulate the destination reset failing while the source succeeds.
        if args[:3] == ["reset", "-q", "--"] and args[3:] == ["foo"]:
            return None
        return real_run(args, strip=strip)

    monkeypatch.setattr(gc, "run_git_cmd", fake_run)

    with pytest.raises(RuntimeError, match="could not unstage"):
        gc.get_git_files()
    # The harmless-looking destination is still staged; the tool must abort
    # instead of reporting a clean index and committing the secret content.
    assert "foo" in _git("diff", "--cached", "--name-only").stdout


def test_verify_no_secrets_staged_blocks_env_and_env_rename(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "normal.txt").write_text("ok\n", encoding="utf-8")
    _git("add", ".env", "normal.txt")
    _git("commit", "-qm", "init")

    # A staged .env edit is rejected.
    (tmp_path / ".env").write_text("SECRET=2\n", encoding="utf-8")
    _git("add", ".env")
    with pytest.raises(RuntimeError, match="staged .env"):
        gc.verify_no_secrets_staged()
    _git("reset", "-q", "--", ".env")

    # A staged rename of .env is rejected even though the new name is benign.
    _git("mv", ".env", "foo")
    with pytest.raises(RuntimeError, match="staged .env"):
        gc.verify_no_secrets_staged()
    _git("reset", "-q", "--", ".env", "foo")

    # Ordinary staged files pass.
    (tmp_path / "normal.txt").write_text("changed\n", encoding="utf-8")
    _git("add", "normal.txt")
    gc.verify_no_secrets_staged()


def test_detect_version_accepts_multipart_tag_and_commit(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    _git("add", "f.txt")
    _git("commit", "-qm", "v2.3.4.5 - feat: multipart")
    _git("tag", "v1.1.1.11")

    version, source = gc.detect_version(prompt=False)

    # Both the four-part commit prefix and the four-part tag used to be
    # ignored, falling back to 0.0.0 / default.
    assert version == "v2.3.4.5"
    assert "git commit" in source


def test_detect_version_accepts_prerelease_tag(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    _git("add", "f.txt")
    _git("commit", "-qm", "init")
    _git("tag", "-a", "v1.2.3-rc1", "-m", "release candidate")

    version, source = gc.detect_version(prompt=False)

    assert version == "v1.2.3-rc1"
    assert "git tag" in source


def test_detect_version_prefers_release_over_same_core_prerelease(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("a\n", encoding="utf-8")
    _git("add", "f.txt")
    _git("commit", "-qm", "init")
    _git("tag", "v1.2.3-rc1")
    _git("tag", "v1.2.3")

    version, _source = gc.detect_version(prompt=False)

    assert version == "v1.2.3"


def test_pyproject_detection_and_update_use_the_same_section(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.commitizen]\nversion = "9.9.9"\n\n'
        '[project]\nname = "demo"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )

    assert gc.find_pyproject_version() == "1.0.0"
    version, source = gc.detect_version(prompt=False)
    assert version == "1.0.0"
    assert "pyproject.toml" in source

    gc.update_version_in_files("v2.5.0")

    text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    # Detection and update agree: the tool config keeps its version, while the
    # package version is the one that is read and rewritten.
    assert '[tool.commitizen]\nversion = "9.9.9"' in text
    assert 'name = "demo"\nversion = "2.5.0"' in text
    assert gc.find_pyproject_version() == "2.5.0"


def test_move_tag_to_head_keeps_empty_annotated_tag_type(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("a", encoding="utf-8")
    _git("add", "f.txt")
    _git("commit", "-qm", "c1")
    _git("tag", "-a", "empty", "-m", "")
    (tmp_path / "f.txt").write_text("b", encoding="utf-8")
    _git("commit", "-qam", "c2")

    assert gc.move_tag_to_head("empty") is True

    assert _git("cat-file", "-t", "refs/tags/empty").stdout.strip() == "tag"
    assert _git("for-each-ref", "refs/tags/empty", "--format=%(contents)").stdout.strip() == ""
    assert _git("rev-parse", "empty^{}").stdout.strip() == _git("rev-parse", "HEAD").stdout.strip()


def test_precommit_hook_rename_restages_destination(tmp_path, monkeypatch, capsys):
    _init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    (tmp_path / "old.txt").write_text("content\n", encoding="utf-8")
    _git("add", "old.txt")
    _git("commit", "-qm", "init")

    (tmp_path / "old.txt").write_text("changed\n", encoding="utf-8")
    _git("add", "old.txt")

    real_run = subprocess.run

    def fake_run(args, *o, **kw):
        if args and args[0] == "pre-commit":
            os.rename(tmp_path / "old.txt", tmp_path / "new.txt")
            raise subprocess.CalledProcessError(1, args)
        return real_run(args, *o, **kw)

    monkeypatch.setattr(gc.subprocess, "run", fake_run)
    _feed_inputs(monkeypatch, ["y"])

    assert gc.check_precommit_hooks(staged_files=["old.txt"]) == "skip_verify"

    cached = _git("diff", "--cached", "--name-status").stdout
    assert "new.txt" in cached
    assert "old.txt" in cached
    assert not (tmp_path / "old.txt").exists()
    assert "Staged hook-created file: new.txt" in capsys.readouterr().out
