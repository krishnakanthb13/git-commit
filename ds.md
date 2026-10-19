# CommitGen Code Audit — bugs, edge cases & integrity issues

Audit of `git_commit.py` (~3,100 lines), `register.py`, and `tests/test_git_commit.py`.
Line numbers refer to the pre-fix working-tree version. Several findings were verified
empirically against throwaway repos.

Priority legend: **C**ritical · **H**igh · **M**edium · **L**ow (polish)
Status legend: **✅ fixed** · **◐ partial** · **— left as-is (deliberate)**

> **Status update:** every finding below has been implemented where it makes sense.
> `python -m pytest -q` → **34 passed**; `python -m py_compile git_commit.py register.py` clean.
> See [Status summary](#status-summary) for details on what was deliberately left as-is.

---

## Critical

### C1. Infinite recursion / hang when a `.env` file is staged and unstaging fails — ✅ fixed
`get_git_files()` unstaged staged `.env` files and then called **itself** recursively to
rebuild the lists. `git restore --staged <path>` fails when the repo has no HEAD
(`fatal: could not resolve 'HEAD'`), `run_git_cmd` swallowed the error, the file stayed
staged, and the function recursed forever (reproduced: fresh repo, endless warning spam).

Fix (both parts, as recommended):
- Unstage with `git reset -q -- <path>` instead of `git restore --staged` (works with no
  HEAD).
- Never recurse: the parser already excludes every `.env` file, so the lists are built in
  one pass; re-calling the function is gone.
- The same `restore --staged` weakness in `restore_files_snapshot()` was fixed the same
  way (rollback of version/changelog files after a failed first commit).

Tests: `test_staged_env_without_head_is_unstaged_without_hanging`,
`test_restore_files_snapshot_unstages_on_headless_repo`.

### C2. Rollback ran after a *successful* commit when tagging failed — ✅ fixed
The commit and the tag were wrapped in one try/except: `version_already_tagged()` only
warns, so a pre-existing local tag made `git tag` fail *after* the commit succeeded, the
except block rewrote `package.json`/`CHANGELOG.md` back and unstaged them, and it printed
"Commit failed" for a commit that actually landed.

Fix: commit and tag are separate failure domains everywhere.
- New-commit path: only the commit is in the rollback try/except; tag creation happens
  afterwards via the new `create_annotated_tag()` helper, which reports failure, prints
  the manual `git tag` command, and **never raises** (so it can never trigger a rollback).
- Amend path: `git commit --amend` is in the try/except; tag moves run afterwards and
  report per-tag failures (`Could not move tag '…'`) without rolling back the amend.

Test: `test_create_annotated_tag_duplicate_does_not_raise`.

### C3. Secrets were only redacted in the diff, not the rest of the prompt — ✅ fixed
`redact_secrets(diff)` left secrets in recent commit messages, the project commit
template, issue references, user context and filenames sent to Gemini.

Fix: `prompt_text` is assembled first and then passed through `redact_secrets()`; the raw
`diff` stays untouched so the `d` review view still shows the real diff. Side benefit: the
saved session state now persists the redacted prompt (partial mitigation of M11).

---

## High

### H1. `pre-commit run --all-files` operated on the whole repo — ✅ fixed
`check_precommit_hooks()` now runs `pre-commit run` (staged files only) and re-stages hook
auto-fixes for the files that are part of this commit before the "commit anyway" prompt.

### H2. Version-selection prompt appeared even when amending — ✅ fixed
`detect_version(prompt=False)` skips the interactive selection loop; amend/fresh-amend
call it that way, since no bump happens there.

### H3. "Files to commit" preview omitted files that will be committed — ✅ fixed
The review screen now lists auto-added rows: `package.json`/`pyproject.toml`
("auto-added: version bump") when a bump is active and `CHANGELOG.md`
("auto-added: changelog entry") for new commits with a bump and for fresh amend.

---

## Medium

| # | Finding | Status |
|---|---------|--------|
| M1 | Custom `print` ignores `sep` (`" ".join`) | ✅ `sep` is honoured (incl. `sep=None`) |
| M2 | `update_version_in_files` rewrote `package.json` via `json.dump(indent=2)`, destroying formatting | ✅ targeted regex + `json.loads` sanity check preserves indentation/key order; pyproject regex already line-scoped. Test: `test_update_version_in_files_preserves_package_json_formatting` |
| M3 | An `MM` file appeared twice (staged + unstaged rows) | ✅ `build_file_entries` merges into one row `[staged: modified + modified]`. Test: `test_build_file_entries_merges_staged_and_unstaged_edit` |
| M4 | Non-interactive/CI detection duplicated in 3 places | ✅ `detect_version` now reuses `is_ci_environment()` |
| M5 | Minimum-Python check said 3.6 but code needs 3.7+ | ✅ raised to 3.7 |
| M6 | Hardcoded `origin` remote in tag checks/pushes | ✅ new `get_default_remote()` (branch remote → first remote → origin); used by `check_remote_tag(tag_name, remote)` and tag pushes. Repo-create pushes keep `--remote=origin` (correct by construction) |
| M7 | No timeouts on network ops — can hang | ◐ `ls-remote` 30 s, `git pull` 300 s (TimeoutExpired returned as a failed CompletedProcess so callers keep their handling), `gh run list` 30 s. `gh run watch` intentionally untimed (streams long-running builds) |
| M8 | `validate_commit_message` split on the first `" - "` → false positives | ✅ version-prefix regex (`^v?\d+(\.\d+)*\s*-\s+`) replaces the split. Test: `test_validate_commit_message_keeps_body_dashes` |
| M9 | `semver_key("1.2.3-rc1")` → `(1,2,3,1)`; `increment_version` returned prereleases unchanged | ✅ core-only keys and bumping (prerelease/build metadata stripped). Test: `test_prerelease_versions_use_core_for_compare_and_bump` |
| M10 | Comment claimed version dedupe; there was none | ✅ dedupe by version string (first source wins) before sorting in `detect_version` |
| M11 | Session file stores the full prompt; rewritten on every toggle | ◐ unchanged storage, but the stored prompt is now redacted (via C3); capping/encrypting left as-is |
| M12 | `.envrc` (direnv) auto-unstaged as a secret | ✅ no longer auto-unstaged (often tracked config); a warning appears at commit time ("double-check it contains no secrets"). Tests updated/added |

---

## Low / polish

- **L1.** ✅ `input(prompt, flush=True)` gained a `flush` parameter; the multi-line
  description editor passes `flush=False` so pasted blocks are not truncated.
- **L2.** ✅ ASCII fallback now covers `→` `—` `•` `👋`.
- **L3.** ✅ `parse_index_selection` raises on out-of-range numbers and fully-out-of-range
  ranges; partially valid ranges still clip. Test added.
- **L4.** ✅ trailer email is now `<model@users.noreply.github.com>` — syntactically valid.
- **L5.** ✅ `browse_commit_history` guards `per_page`. Test added.
- **L6.** ✅ `register.py` command is now `cmd.exe /k cd /d "%V" && python "…"` (single
  quotes per path); `--help` prints usage; unknown options exit 2.
- **L7.** — left as-is: `numstat` parse failures stay silent (display-only stats; a warning
  per unparsable line would be noise).
- **L8.** ✅ centralized via `_mark_restart()` + `request_restart()` (counter, screen clear,
  message); all four restart paths use them, so they can no longer drift.
- **L9.** ✅ repo tree is gated by config `show_tree` (default `true`).
- **L10.** ✅ dead commented `# print()` removed.
- **L11.** — left as-is: picker default remains "stage all". It is the tool's documented
  one-key quick path, `.env*` files are auto-excluded, and the picker is shown before
  anything is committed, so the risk is user-visible.

---

## What's already solid

- `git status --porcelain -z` with correct rename mapping (`R  new\0old\0` verified).
- Honors `NO_COLOR`; API key sent via header, not URL.
- Rollback snapshots distinguish "tool rewrote it" vs "user staged it" (well-tested).
- `--force-with-lease` pushes; `check_remote_tag` anchors `refs/tags/<name>` against glob
  false-positives.
- Session recovery, `--dry-run`, and non-interactive fallbacks are well thought out.

## Regression tests added

All in `tests/test_git_commit.py` (34 passing):

1. `test_staged_env_without_head_is_unstaged_without_hanging` (C1)
2. `test_restore_files_snapshot_unstages_on_headless_repo` (C1)
3. `test_create_annotated_tag_duplicate_does_not_raise` (C2)
4. `test_build_file_entries_merges_staged_and_unstaged_edit` (M3)
5. `test_validate_commit_message_keeps_body_dashes` (M8)
6. `test_prerelease_versions_use_core_for_compare_and_bump` (M9)
7. `test_parse_index_selection_rejects_out_of_range_and_clips_ranges` (L3)
8. `test_browse_commit_history_guards_page_size` (L5)
9. `test_staged_envrc_is_kept_but_flagged` + updated `test_drop_env_files_…` (M12)
10. `test_update_version_in_files_preserves_package_json_formatting` (M2)
11. `test_co_author_trailer_is_address_like` (L4)

## Status summary

| Item | Status | Notes |
|------|--------|-------|
| C1 | ✅ | `reset -q --` unstage, no recursion (both call sites) |
| C2 | ✅ | commit/tag failure domains split; `create_annotated_tag()` never raises |
| C3 | ✅ | whole prompt redacted; raw diff kept for the `d` view |
| H1 | ✅ | staged-only hooks + re-stage of auto-fixes |
| H2 | ✅ | `detect_version(prompt=False)` for amend modes |
| H3 | ✅ | preview lists auto-added version/changelog rows |
| M1–M6, M8–M10, M12 | ✅ | see table above |
| M7 | ◐ | timeouts except `gh run watch` (long-running by design) |
| M11 | ◐ | prompt now stored redacted; size/encryption unchanged |
| L1–L6, L8–L10 | ✅ | see list above |
| L7, L11 | — | deliberate (rationale above) |

**Superseded priority plan:** the original tiers ("do now / do next / when convenient /
only if bitten") no longer apply — all actionable items are done; remaining entries are
intentional non-goals.
