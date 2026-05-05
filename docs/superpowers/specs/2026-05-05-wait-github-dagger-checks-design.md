# wait-github-dagger-checks — design

Date: 2026-05-05

## Problem

CI needs to block until Dagger Cloud has reported success for every check defined by the modules in this repo. The previous approach used `lewagon/wait-on-check-action` with a regex on check names. It fails for two reasons:

1. Dagger Cloud reports results via the **GitHub commit Status API**, not the **Check Runs API**. `lewagon/wait-on-check-action` only inspects check-runs, so its regex never matches.
2. A regex is duplicated state — it drifts from the actual set of checks declared by the modules.

## Goal

Replace the action step with a tiny Dagger module that natively enumerates the repo's Dagger checks and waits for each one to land as a successful commit status.

## Non-goals

- Generic, publishable-on-Daggerverse reuse across other repos. The module is wired as a dependency of this repo's root module; enumerating checks via `dag.checks()` relies on that wiring. (Generic reuse is option A in the brainstorm; we chose option B.)
- Replacing or fronting Dagger Cloud's reporting. We only consume what Dagger Cloud already publishes to GitHub.
- Posting statuses ourselves.

## Architecture

### Placement

`wait-github-dagger-checks/` is a sibling Dagger module next to `uv-workspace/`. The root `dagger.json` adds it as a dependency. Inside the wait function `dag.checks()` then sees the root module's installed deps — i.e. `uv-workspace`'s checks — and enumerates them.

### API

One async function on the module object:

```python
@function
async def wait(
    self,
    repo: str,                    # "typesafe-ai/daggerverse"
    ref: str,                     # commit SHA
    token: dagger.Secret,         # GITHUB_TOKEN
    poll_interval: int = 10,      # seconds between polls
    timeout: int = 1800,          # total wall-clock budget
    discovery_timeout: int = 300, # how long to allow each expected status to first appear
    status_prefix: str | None = None,  # override for the GitHub status context prefix
) -> str:
    """Wait until every Dagger check enumerated via `dag.checks()` has a
    matching successful GitHub commit status on `ref`. Returns a summary."""
```

No regex, no caller-supplied check list — the expected set is derived from `dag.checks().list_()`.

### Status context name derivation

`Check.name()` returns a fully-qualified Dagger name (e.g. `uv-workspace.build-self`). Dagger Cloud posts GitHub statuses with names like `typesafe-daggerverse:uv-workspace-build-self`. Observed shape:

```
<root-module-name>:<dagger-name-with-dots-replaced-by-dashes>
```

A small private helper `_to_status_context(check_name: str, root: str) -> str` performs the mapping. The function logs both the enumerated set and the GitHub-side context set on the first poll so any mismatch is immediately visible. `status_prefix` overrides the root prefix when the heuristic doesn't fit.

### Polling loop

```
expected = {to_ctx(c.name(), prefix) for c in await dag.checks().list_()}
deadline = now + timeout
discovery_deadline = now + discovery_timeout

while now < deadline:
    statuses = GET /repos/{repo}/commits/{ref}/statuses  (paginated, all pages)
    # API returns newest-first; keep the first occurrence per context.
    latest = {s.context: s.state for s in dedup_keep_first(statuses)}

    failed  = {c for c in expected if latest.get(c) in {"failure", "error"}}
    if failed: raise CheckFailed(failed)

    pending = {c for c in expected if latest.get(c) == "pending"}
    success = {c for c in expected if latest.get(c) == "success"}
    missing = expected - latest.keys()

    if not missing and not pending and success == expected:
        return summary(success)

    if missing and now > discovery_deadline:
        raise DiscoveryTimeout(missing)

    sleep(poll_interval)

raise TimeoutError(pending | missing)
```

GitHub Statuses API returns results in reverse chronological order; deduping keeps the first occurrence per context (i.e. the newest). Token is supplied as `dagger.Secret` and read via `await token.plaintext()` at the boundary; it's never logged.

HTTP via `httpx` (already a transitive dep of the Dagger Python SDK).

## CI wiring

Root `dagger.json` adds `wait-github-dagger-checks` as a dependency.

`.github/workflows/CI.yml` replaces the `lewagon/wait-on-check-action` step:

```yaml
- name: Wait for Dagger Cloud checks
  run: |
    dagger call -m wait-github-dagger-checks wait \
      --repo "${{ github.repository }}" \
      --ref  "${{ github.event.pull_request.head.sha || github.sha }}" \
      --token env:GITHUB_TOKEN
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

(The existing Dagger install step is unchanged.)

## Testing

- Manual end-to-end: open a PR, observe the workflow, confirm it waits for and surfaces the `typesafe-daggerverse:uv-workspace-*` statuses.
- Unit test for `_to_status_context` (pure function, deterministic).
- Unit test for the dedup-keep-first helper.
- A small fake-statuses test for the loop's terminal conditions (success, fail, timeout, discovery-timeout). The loop function takes the GitHub-fetcher as a parameter so the test can inject a fake.

## Risks / open questions

- The exact format of `Check.name()` for nested module deps is observed but not contractual. The first-poll log makes any mismatch obvious; `status_prefix` is the escape hatch.
- The Dagger checks API is documented as experimental and may change.
- The GitHub Statuses API is paginated; the implementation must traverse all pages (especially after many re-runs) before deduping.

## README

A short example is added to `wait-github-dagger-checks/README.md` showing both `dagger call` usage and the CI snippet above.
