# The GitHub MCP form of each step

For a session where `gh auth status` fails. Load the tools with ToolSearch, and
reload them if they vanish mid-session. MCP tools can't be called from a shell
loop: wait for a run with a background `sleep` (60s for a dry run) and check
again when it ends.

| Step | `gh` | MCP |
|---|---|---|
| Newest `release.yml` run | `gh run list --workflow release.yml -L 1` | `list_workflow_runs`, `resource_id: release.yml`, `perPage: 1` |
| Dispatch | `gh workflow run release.yml …` | `actions_run_trigger`, `method: run_workflow`, `workflow_id: release.yml`, `ref: main`, `inputs: {dry_run: <true\|false>, pre: "<rc\|none>", sha: "<sha>"}` |
| Wait for a run | `gh run watch <id>` | `list_workflow_jobs` on the run id; its job `status` and `conclusion` say when it's done |
| Read the log | `gh run view <id> --log` | `get_job_logs` with the job's `job_id`, `return_content: true`, `tail_lines: 300` |
| Open PRs | `gh pr list` | `list_pull_requests`, state `open` |
| Main's `test` run | `gh run list --workflow test --branch main --event push --commit <sha>` | `list_workflow_runs`, `resource_id: test.yml`, `workflow_runs_filter: {branch: main, event: push}`, `perPage: 3`; it can't filter by commit, so pick the run whose `head_sha` is the tip |
| A PR's labels | `gh pr view <n> --json labels` | `search_pull_requests`, field `labels` (`issue_read get_labels` fails on a PR number) |
| Add a label | `gh pr edit <n> --add-label notes::highlight` | `issue_write`, `method: update`. It replaces the whole label set, so send the existing `release::` label too: `labels: ["release::minor", "notes::highlight"]` |
| The GitHub Release | `gh release view <tag>` | `list_releases` |
| The stamp PR | `gh pr list --state all --search "stamp plugin manifests vX.Y.Z"` | `search_pull_requests` |

## Gotchas

| Gotcha | What to do |
|---|---|
| `get_job_logs` answers 404 | The job is still running. Wait for `get_workflow_job` to report `completed`, then fetch the log. |
| `get_job_logs` needs a `job_id` | Get it from `list_workflow_jobs` on the run id; `run_id` works only with `failed_only: true`. |
| A large tool result lands in a file | Parse it with Python instead of reading it whole. |
