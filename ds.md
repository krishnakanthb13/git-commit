# CommitGen Code Audit — bugs, edge cases & integrity issues

Audit of `git_commit.py` (~3,875 lines), `register.py`, and `tests/test_git_commit.py`.
Line numbers below refer to the audit-baseline working tree; the code has since moved.
Several findings were verified empirically against throwaway repos.

Audit date: 2026-10-29

Priority legend: **C**ritical · **H**igh · **M**edium · **L**ow (polish)

> **Test/lint status (audit baseline):** `python -m pytest -q` → **34 passed**;
> `ruff` / `pyflakes` → 8 minor findings (unused variables, needless f-strings,
> one lambda assignment, one ambiguous variable name).
>
> **Status after fixes (2026-10-29, second pass):** `python -m pytest -q` → **68 passed**;
> `ruff` / `pyflakes` → **clean**. All Critical/High findings, the P2 quick wins, and the
> remaining integrity gaps found while validating this report are resolved — see
> [Fix status](#fix-status-2026-10-29--resolved).
>
> **Bottom line (historical):** the helper-level tests were good, but there were integrity
> bugs in the main commit path: `pyproject.toml` version bumps broken, running from a
> subdirectory broke staging/diffs, `--dry-run` was not side-effect free, and the
> “read-only” viewer could mutate the index. All four are fixed and tested.

---

## Fix status (2026-10-29) — resolved

| # | Finding | Fix | Regression test |
|---|---|---|---|
| 1 | `pyproject.toml` version updates always fail | Section-aware (`[project]` / `[tool.poetry]`) function replacer; warns when no key exists | `test_update_version_in_files_pyproject_replacement` |
| 2 | Subdirectory breaks staging/diffs | `init_git_dir()` chdirs to `git rev-parse --show-toplevel`; launch dir kept in `ORIGINAL_CWD` for the banner; `.env`/config load from the root | `test_init_git_dir_moves_to_repo_root_and_stages_from_subdirectory` |
| 3 | `.env` fail-open and rename handling | Both sides of a rename reset; `git diff --cached` verification checks **every** path in the rename (including a benign-looking destination); a final `verify_no_secrets_staged()` runs before every commit/amend; `RuntimeError` (fail closed) when anything remains staged | `test_staged_env_rename_resets_both_sides`, `test_env_reset_failure_fails_closed`, `test_env_rename_destination_reset_failure_fails_closed`, `test_verify_no_secrets_staged_blocks_env_and_env_rename` |
| 4 | `--dry-run` is not dry | Startup pull/stash, pre-commit hooks, session writes, commit/tag/push skipped; `main()` runs inside `DryRunIndexGuard`, which snapshots and restores the raw `.git/index` on every exit | `test_dry_run_makes_session_state_inert`, `test_dry_run_restores_index_after_preview_staging` |
| 5 | Read-only viewer mutates the index | Viewer calls `get_git_files(enforce_env=False)`; enforcement is an explicit commit-path parameter | `test_viewer_lists_env_but_never_mutates_index` |
| 6 | Conflicts/type changes invisible | `UU`/`AA`/`DD`/`AU`/`UA`/`DU`/`UD` and `T` parsed with badges; `get_unmerged_files()`; commit and picker blocked while conflicts exist; `git status` failure now raises | `test_get_git_files_parses_unmerged_and_typechange`, `test_merge_conflicts_block_staging`, `test_get_git_files_status_failure_is_not_clean` |
| 7 | `none` preview disagrees with commit | `proposed_version = None` for a `none` bump, so the preview adds no prefix; final assembly uses the same rule; typed-prefix overrides/regressions are reported | Covered by the review-loop logic in `main()` (no end-to-end test — interactive) |
| 8 | Aux-file edits silently committed | Dirty `package.json`/`pyproject.toml`/`CHANGELOG.md` are shown as a stat block and require confirmation before auto-staging | — |
| 9 | “Commit anyway” cannot work | Hook failure override returns `"skip_verify"`; the later commits pass `--no-verify`; fixes re-staged with `git add -A -- <path>` (checked); hook-created rename destinations are detected and staged too; 30-minute hook timeout | `test_check_precommit_hooks_commit_anyway_returns_skip_verify`, `test_precommit_hook_rename_restages_destination` |
| 10 | Failed tag creation still pushed stale tag | `tag_created` tracked separately from `should_tag`; tag pushes are gated on it | — |
| 11 | Unstage before first commit fails silently | `git reset -q -- <path>` (works without HEAD) and success is printed only when the command succeeds | `test_unstage_works_before_first_commit` |
| 12 | Viewer required an API key | Key check moved after the startup menu (viewer works without it); `prompt_select_model()` keeps the current model on Enter | `test_prompt_select_model_enter_keeps_current` |
| 13 | Multi-part/prerelease versions ignored | Tag and commit-prefix detection use `semver_key()` and accept `v1.1.1.11` / `v1.2.3-rc1` | `test_detect_version_accepts_multipart_tag_and_commit`, `test_detect_version_accepts_prerelease_tag` |
| 14 | `pyproject.toml` detection/update disagree | Both use `find_pyproject_version()` and only the `[project]` / `[tool.poetry]` sections; `[tool.commitizen]` is ignored by both | `test_pyproject_detection_and_update_use_the_same_section` |
| 15 | Quoted `.env` value with trailing comment kept quotes | The parser reads to the closing quote and ignores the comment after it | `test_load_dotenv_handles_export_comments_quotes_and_bom` |
| 16 | Rollback restored a file the tool never wrote | `restore_files_snapshot()` only restores paths present in `tool_writes` | `test_rollback_leaves_user_edit_the_tool_never_wrote` |
| 17 | Empty-message annotated tag became lightweight | `move_tag_to_head()` re-creates annotated tags even with an empty message; a GPG signature is dropped with a warning | `test_move_tag_to_head_keeps_empty_annotated_tag_type` |
| 18 | Raw commit/push/tag/pull bypassed timeout/env hardening | Shared `run_git_checked()` used for mutating commands; pulls and stash-pop set the timeout and `GIT_TERMINAL_PROMPT=0` | — (behavioural hardening; covered indirectly by existing flows) |
| 19 | Non-interactive/amend `git add` results unchecked | Failures are reported; an amend aborts rather than silently amending without requested changes | — |

**P2 fixes:** `.commitgenrc` validation/coercion (`_validate_config()`), truncation budget clamp,
`print(sep="")`, output blank-row de-duplication (`print_blank()` plus leading-newline collapse in the
`print()`/`input()` wrappers, so separators and `\n`-prefixed prompts cannot stack into two empty rows),
changelog `[Unreleased]` placement + `## [X]` headings + checked staging, `.env`
parser (`export`, inline comments, quotes with trailing comments, BOM), CI falsy values (`CI=false`),
atomic and validated session state, `--` path separators everywhere, deleted files included in the AI
diff (`--diff-filter=ACDMRT`), template-load `OSError` tolerance, `print_stat_block` stat-shape gating,
`check_remote_tag()` returning “unknown”, root-commit amend stats, rollback preserving user edits and
never restoring files the tool did not write (`tool_writes`), `run_git_cmd()` timeout +
`GIT_TERMINAL_PROMPT=0` (plus `run_git_checked()` for mutating commands), numeric/multi-part/prerelease
version detection, one section-aware `pyproject.toml` source for detection and update, merged source
labels in `detect_version()`, checked `git add` results in auto-stage/amend paths, and
version/changelog staging return-code checks.

**Tests added (34 total across both passes):** pyproject fix, subdirectory staging, conflict/type-change
parsing and blocking, `.env` rename/fail-closed/read-only viewer, dry-run state and index restoration,
unstage-before-HEAD, config validation, truncation clamp, model default, `print` separator, CI falsy
values, changelog placement/headings, `--no-verify` contract, `.env` parser (including quoted values
with trailing comments), stat-block subject, numeric semver, rollback edit preservation, rollback of
never-written files, unknown remote-tag state, version-source merging, multi-part/prerelease
detection, pyproject section consistency, empty-message annotated tag relocation, and pre-commit hook
rename re-staging.

**Still open (structural / P3, intentionally not part of this pass):** splitting the monolith into
modules, replacing `locals()` checks with a `CommitContext` dataclass, a `GitResult`-returning git
runner (a checked runner now exists), terminal-output sanitization, `register.py` command fixes, and
CI/packaging (ruff config + workflow). GPG signatures are not preserved across tag moves (a signature
signs the old commit); the tool drops it with a warning.

---

## Critical / High findings

### 1. `pyproject.toml` version updates always fail — Critical

`update_version_in_files()` at `git_commit.py:883-884` builds a regex replacement string like:

```python
re.sub(r'(["\'])([^"\']+)["\']', f'\\1{clean_ver}\\1', line)
```

For `clean_ver = "2.0.0"`, the replacement becomes `\12.0.0\1`. Python interprets `\12` as
group 12 → `re.error: invalid group reference 12`. Every normal version starts with a digit,
so the `pyproject.toml` branch **always raises**, is swallowed by `except Exception`, prints
a warning, and the version is never written.

Verified:

```text
[WARN] Failed to update pyproject.toml: invalid group reference 12 at position 1
```

Impact: the commit/tag says `v2.0.0` while `pyproject.toml` still says `1.0.0`. The test
suite only covers `package.json`.

**Fix:** use a function replacer, not a backreference string:

```python
line = re.sub(
    r'^(\s*version\s*=\s*)(["\'])[^"\']*\2',
    lambda m: f"{m.group(1)}{m.group(2)}{clean_ver}{m.group(2)}",
    line,
)
```

Also target the correct section (`[project]` / `[tool.poetry]`) rather than the first
`version =` anywhere, and add a pyproject regression test.

---

### 2. Running from a subdirectory breaks staging and diffs — Critical

`init_git_dir()` resolves an absolute `.git`, but the process never changes to the repository
root. `git status --porcelain` returns paths **relative to the repo root**, while `git add`
and `git diff -- <path>` interpret paths **relative to the current directory**.

Verified: from `repo/sub`, a modified root file appears as `root.txt`, but:

```text
git add root.txt
fatal: pathspec 'root.txt' did not match any files
```

So from a subdirectory:

- `git add` in the picker silently fails while printing “Staged”
- `git diff --cached -- <files>` misses files
- `show_file_change_stats()` reports wrong/no stats
- `package.json`, `pyproject.toml`, `CHANGELOG.md`, `.commitgenrc`, `.env`, and
  `.pre-commit-config.yaml` are looked up relative to the subdirectory
- `README.md:265`’s claim that any subdirectory works is false

**Fix (simplest):** after resolving the repo, `os.chdir(git rev-parse --show-toplevel)`.
Keep the original cwd only for display. Alternatively, run all Git commands with
`git -C <root>` and convert every path to a repo-root path.

Add tests that stage root-level and nested files from a subdirectory.

---

### 3. `.env` protection can fail open, and rename handling is incomplete — Critical (security)

`get_git_files()` detects staged `.env` files and calls:

```python
run_git_cmd(["reset", "-q", "--", env_f])
```

The return value is ignored (`git_commit.py:1094-1105`). The parser then removes `.env` from
the returned lists, so the caller proceeds. But the commit uses:

```python
subprocess.run(["git", "commit", "-m", full_commit_msg], check=True)
```

which commits **the entire index**, not just the displayed file list. If the `reset` failed
(index lock, permission problem, race with another process), the `.env` remains staged and
is committed while the UI says it was excluded.

Also, for a staged rename `.env → foo`, the code resets only `foo`; verified result is:

```text
D  .env
?? foo
```

The deletion of `.env` remains staged and can be committed.

**Fix:**

- Reset both sides of a rename (`git reset -- .env foo`).
- Verify the result (`git diff --cached --name-only -z`) and abort if any secret path remains
  staged.
- Commit explicit paths (`git commit -m msg -- <staged files>`) or use `--only`, instead of
  relying on the whole index.
- Move `.env` enforcement out of a “getter”; a read-only function should not mutate the
  index.

---

### 4. `--dry-run` is not dry — High

`DRY_RUN` is parsed at `git_commit.py:2136`, but before the dry-run exit at `2923` the tool
can still:

- stash changes and `git pull --rebase` at startup (`2235-2287`)
- run `pre-commit` hooks (`2381-2385`), which commonly rewrite files and re-stage them
- save/clear session state

So `--dry-run` can modify the working tree, index, stash list, and possibly leave rebase
state.

**Fix:** guard every mutating step with `if not DRY_RUN`. In dry-run mode skip
pull/stash/hooks/`git add`/commit/tag. Prefer a single `mutations_allowed` flag checked
centrally.

---

### 5. The “read-only” viewer mutates the index — High

`show_changed_files_viewer()` calls `get_git_files()`, which auto-unstages staged `.env`
files. That is an index mutation from a screen documented as read-only (`README.md:163`,
`CODE_DOCUMENTATION.md:92`: “never modifies the index”).

**Fix:** add `get_git_files(enforce_env=False)` for viewer/history paths, or split enforcement
into an explicit `enforce_env_safety()` called only before committing.

---

### 6. Merge conflicts and type changes are invisible or misclassified — High

`get_git_files()` only handles `M`, `A`, `D`, `R`, `C`, and `??` (`1040-1092`). It ignores
unmerged statuses (`UU`, `AA`, `DD`, `AU`, `UA`, `DU`, `UD`) and type changes (`T`).

Verified:

```text
RAW: 'UU keep.txt\0...'
PARSED: (['newdir/f.txt'], [], [])       # conflict omitted entirely

RAW: 'TM f\0'
PARSED: ([], ['f'], ...)                 # staged typechange ignored
```

Impact: the tool can say “working tree clean”, or omit a staged typechange from the preview
while `git commit` includes it from the index.

**Fix:** parse all XY codes, collect an explicit `unmerged` list, block commit while conflicts
exist, and treat `T` as staged/unstaged changes. Also distinguish “`git status` failed” from
“clean”.

---

### 7. Version bump `none` preview disagrees with the actual commit — High

In the review loop (`2637-2664`), `proposed_version` is set to `curr_version` even when
`bump_choice == "none"`, and the preview always adds a version prefix. But final assembly
(`2883-2912`) sets `final_version = None` for `none`, so the committed subject has **no**
version prefix.

The user sees:

```text
v1.2.3 - feat: ...
```

but the commit contains:

```text
feat: ...
```

**Fix:** compute one `final_version` before rendering, use it for both preview and commit,
and test that the preview matches `git log -1 --format=%s`.

Related: an AI/user summary containing a version prefix can silently override the chosen
bump (`2647-2652`, `2892-2897`); add the same regression guard used for custom context
versions.

---

### 8. Auto-staging version/changelog files can commit unrelated edits — High

`update_version_in_files()` edits the working-tree `package.json`/`pyproject.toml`, then
`git add`s the whole file (`2938-2943`). `update_changelog()` reads and writes the whole
working-tree `CHANGELOG.md`, then stages the whole file (`763-809`).

If the user has unstaged edits in those files, those edits are silently included in the
commit. If the user had a partially staged version of the file, `git add` replaces that
partial staging with the full working tree.

**Fix:** preflight with `git diff --quiet -- package.json pyproject.toml CHANGELOG.md` and
warn/abort when dirty; or apply the version edit to the index blob through a temporary index.
At minimum, show the aux-file diff in the preview and require confirmation.

---

### 9. “Commit anyway” after pre-commit failure often cannot work — High

`check_precommit_hooks()` runs `pre-commit run` (`1970`), then the commit runs `git commit`
(`3033`/`3051`). When hooks are installed in `.git/hooks`, Git runs them **again** during the
commit. A hook that failed on real lint errors will fail again, so choosing “Commit anyway”
does not actually override it.

Also, hook auto-fixes are re-staged only for existing `staged_files`; renamed/deleted paths
are skipped, and the staging result is not checked.

**Fix:** pick one integration model:

- rely on `git commit`’s hooks and remove the manual `pre-commit run`; or
- keep the manual run and pass `--no-verify` only when the user explicitly chooses
  “commit anyway”.

Re-stage with `git add -A -- <paths>` and check the result. Add a timeout for hook execution.

---

### 10. A failed tag creation can still push a stale local tag — High

`create_annotated_tag()` returns `False` when the tag already exists, but `should_tag` remains
`True`. Later, the push block checks only `should_tag` (`3143-3155`) and runs:

```python
git push <remote> <tag_name>
```

If `v1.2.3` already exists locally on an **older** commit, tag creation fails, but the push
can still send that old tag to the remote. `version_already_tagged()` only warns; it does not
block the commit.

**Fix:** track `tag_created` separately and push only when it is true. If the tag already
exists locally, either refuse the version or ask explicitly whether to move it, then move it
before pushing. Consider blocking commit when the proposed version is already tagged by
default.

---

### 11. Unstaging before the first commit fails silently — High

The picker’s unstage path uses:

```python
run_git_cmd(["restore", "--staged", staged[idx]])
```

Verified in a repo with no `HEAD`:

```text
fatal: could not resolve 'HEAD'   (return code 128)
```

The result is ignored, and the code prints “Unstaged: …”. `git reset -q -- <path>` already
works in this situation and is used elsewhere.

**Fix:** use `git reset -q --` and only print success on return code 0.

---

### 12. Viewer requires an API key, despite not using the API — High (usability)

The `GEMINI_API_KEY` check (`2161-2165`) happens **before** the startup banner and viewer menu
(`2174-2188`). A user who only wants to browse changes/history is forced to configure an API
key.

**Fix:** move the key check into the commit path, after the viewer choice.

Related: `prompt_select_model(current_model=...)` (`1915`) ignores its argument, so pressing
Enter in the “Switch model” screen always resets to `gemini-3.5-flash-lite`. Use
`current_model` as the default and display it.

---

## Medium findings

| Area | Issue | Suggested fix |
|---|---|---|
| Config | `.commitgenrc` values are merged without type/range validation. `"max_diff_length": "1000"` or `null` causes `TypeError` in `truncate_diff_for_context`; invalid `default_bump` silently does not bump. | Validate a schema: string model, positive ints, bools, allowed bump values; fall back with a warning. |
| Diff truncation | `truncate_diff_for_context("abc", 1000, 1000)` returns `("", True)` because `available_tokens` goes negative and the budget becomes 0. A bad `max_input_tokens` sends an empty diff. | Clamp `available_tokens`/budget to a minimum and emit a clear notice. Also account for template/recent-commit size, not a fixed 2000-token overhead. |
| `print()` wrapper | `sep = kwargs.pop("sep", " ") or " "` turns an explicit `sep=""` into a space. | Use `sep = kwargs.pop("sep", " ")`; only substitute `" "` for `None`. |
| Changelog | New entries are inserted before the first `## ` heading, which can put them above `## [Unreleased]`. Replacement only recognizes `## [vX]`, not `## [X]`. Staging result is ignored. | Insert after `Unreleased`/before the first released version; support both heading forms; check `git add`. |
| `detect_version` | A numeric `"version"` in `package.json` reaches `semver_key()` and crashes on `.lstrip`. Custom base versions are not validated as semver. `1.2.3` and `v1.2.3` are listed as separate choices. | Coerce/validate versions to strings; run `is_valid_semver()` for custom input; normalize/deduplicate by `v`-stripped value. |
| `.env` parser | Does not handle `export KEY=value`, inline comments (`KEY=value # note`), BOM, or quoting with escapes. An API key followed by a comment becomes invalid. | Strip optional `export`, parse quoted values, handle BOM, or use a tiny dedicated parser. |
| CI detection | `CI=false` is still truthy, forcing non-interactive mode. | Treat `false`, `0`, `no`, `off` as false. |
| Session state | `json.dump` is non-atomic; a crash can corrupt it. `load_session_state()` accepts any JSON value, so a list/string causes `.get()` crashes. Every toggle rewrites the full prompt. | Write temp + `os.replace`, validate `isinstance(dict)` and field types, cap prompt size. |
| Git execution | `run_git_cmd()` has no timeout or stdin policy. `git commit/push/tag` can hang on credential, GPG, or hook prompts. Several error checks match English stderr strings, which breaks under other locales. | Central runner with `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS`, `--no-pager`, configurable timeouts, and exit-code/status-based logic. |
| Filenames | `git add f` / `git restore --staged f` lack `--`. A file named `-p` can make `git add -p` enter interactive patch mode. | Use `git add -- <path>` and `git restore --staged -- <path>` everywhere. |
| Deleted files in AI diff | `--diff-filter=ACMRT` excludes `D`, so text deletions are invisible to the model even though the file is in the prompt’s file list. | Include `D` for non-binary deletions, or classify via `--numstat`. |
| Auxiliary staging | `git add` calls for version/changelog files and picker selections print success without checking the return code. | Check and report failures; never claim a file was staged when Git returned non-zero. |
| Rollback | `restore_files_snapshot()` overwrites the file if contents differ from the snapshot, which can clobber edits made after the snapshot (for example while waiting at the tag prompt). | Record the tool’s exact post-write hash, and only restore when the current hash matches that value. |
| Template loading | `load_commit_template()` has no exception handling; an unreadable template crashes the run. | Catch `OSError` and warn/skip. |
| Terminal output | Commit messages, author names, filenames, and diffs are printed raw. ANSI escape sequences in a malicious repo can manipulate the terminal. | Sanitize control characters before display (except deliberately generated color codes). |
| `print_stat_block` | Any line containing `|` is treated as a stat line, so a commit subject containing `|` is mangled in commit detail. | Only parse lines matching the stat shape (`name | count +/-`). |
| Remote errors | `check_remote_tag()` returns `False` on any network/auth/timeout failure, so the collision check silently passes. | Distinguish “tag absent” from “could not check”; warn or require confirmation on failure. |
| `--force-with-lease` tag moves | `move_tag_to_head()` re-creates annotated tags from `%(contents)`, dropping GPG signatures and changing empty-message annotated tags to lightweight tags. | Preserve the object/message explicitly or warn; add `--no-sign`/signing policy. |
| Fresh amend on root commit | `get_consolidated_diff_stats()` uses `HEAD~1` for `fresh_amend`; a root commit has no parent, so stats are silently missing. | Guard with `rev-parse --verify HEAD~1` as the diff code already does. |
| Documentation | `README.md:265` claims any subdirectory works (false), `README.md:163` says the viewer never modifies files (false for `.env`), `CODE_DOCUMENTATION.md:69` says rollback uses `git restore --staged` (code uses `reset`), and non-interactive “honors `auto_pull` silently” is false at startup. | Update docs after fixing, and add doc checks where feasible. |
| `register.py` | The same command using `%V` is installed under both `Directory\shell` and `Directory\Background\shell`. `%V` is the background-folder variable; `Directory\shell` normally needs `%1`. Right-clicking a folder can therefore run `cd /d "%V"` and fail. Also uses `python` from PATH rather than `sys.executable`, and prints ANSI unconditionally. | Generate separate commands per parent (`%1` vs `%V`), use `sys.executable`, `pushd` for UNC paths, and detect color support. |

---

## Low / hygiene

- `ruff`/`pyflakes`: unused `commit_source`, `file_source`, `pyproject_source`; unused
  `global restart_count`; `lambda` assignment; `l` variable; unnecessary f-strings at lines
  1209, 2533, 2823.
- `COLOR_WHITE` is unused.
- Line `2107` prints raw `COLOR_BOLD`/`COLOR_RESET` instead of using `c()`, so escape codes
  appear when colors are disabled.
- `is_ci_environment()` is a weak switch; `check_dependencies()` calls `sys.exit`, making
  `main()` hard to test.
- No `pyproject.toml`, ruff config, or CI workflow; tests only run locally.
- `test_browse_commit_history` does not assert the new date/time/name format, so the recent
  viewer change is untested.
- `show_folder_structure()` walks the filesystem up to depth 3 on every run; on large repos
  this is slow and ignores `.gitignore` semantics. Consider `git ls-files` or a
  configurable/capped tree.
- `estimate_tokens()` uses `len // 4`; this significantly underestimates many non-English
  diffs, so truncation can still exceed the context window.
- `main()` catches `Exception` at the top level and prints only `str(e)`, which makes
  diagnosing real failures difficult. Add `--debug`/logging with traceback.
- `prompt_exit_or_restart()` duplicates restart-cap logic that `request_restart()` already
  centralizes.

---

## Quality and structural improvements

1. **Break up the monolith.** Split into `git_ops.py`, `versioning.py`, `changelog.py`,
   `ai.py`, `config.py`, `session.py`, `viewer.py`, and a thin `main.py`. The current file
   mixes UI, Git, versioning, networking, and state.

2. **Replace `locals()` checks with explicit state.** `main()` uses `'curr_version' in
   locals()`, `'tag_name' in locals()`, and similar guards (`2588-2596`, `3143`, `3217`).
   Initialize a `CommitContext` dataclass up front so every branch has defined fields and the
   type checker can help.

3. **Centralize Git execution.** Return a `GitResult(returncode, stdout, stderr)` instead of
   `str | None`. Separate `run_git()` (read-only) from `run_git_checked()` (mutating), and
   make “success” messages depend on the result. This directly fixes the env, add, unstage,
   and changelog truthfulness bugs.

4. **Make paths repo-root-relative.** Resolve the top-level once, use `pathlib.Path`, and never
   mix cwd-relative and root-relative paths. This is the root cause of several findings.

5. **Add a config schema and validation layer.** Validate `.commitgenrc` types/ranges at load
   time and surface a clear warning instead of crashing later.

6. **Make dry-run a first-class mode.** Pass a `dry_run` flag through the mutation helpers and
   unit-test that no file/index/ref changes occur.

7. **Harden the AI boundary.** Expand redaction (private keys, GitLab/Slack/Stripe/npm tokens,
   `PASSWORD=` patterns), sanitize terminal output, and consider showing a “sent to Gemini”
   confirmation for especially large/secret-looking diffs.

8. **Improve tests.** Add regression tests for:
   - pyproject version update
   - subdirectory staging/diff
   - merge conflicts and type changes
   - `bump_choice="none"` preview vs actual commit
   - `.env` reset failure and `.env` rename
   - dry-run with a mutating hook
   - pre-commit “commit anyway”
   - tag-created-false push
   - unstage before first commit
   - invalid config values

9. **Add CI and packaging.** A `pyproject.toml` with `pytest`, `ruff`, and a Windows + Linux
   GitHub Actions matrix would have caught several of these (especially the pyproject regex
   and subdir behavior).

10. **Separate read paths from write paths.** The viewer should be incapable of changing the
    index by construction, not merely by convention.

---

## Suggested fix order

1. **P0:** pyproject replacement bug, subdirectory root handling, `.env` fail-open/rename,
   dry-run mutations.
2. **P1:** viewer side effects, conflict/typechange parsing, bump-none preview, aux-file dirty
   checks, pre-commit override, tag-created tracking, unstage-before-HEAD.
3. **P2:** config validation, command runner/timeouts/locale, changelog placement, `.env`
   parser, session-state hardening, docs.
4. **P3:** style/lint cleanup, `register.py` fixes, CI and broader tests.

All P0/P1 findings, the listed P2 quick wins, and the second-pass integrity gaps were fixed and
covered by regression tests where practical; see
[Fix status](#fix-status-2026-10-29--resolved). The P3 and structural items above remain open.
