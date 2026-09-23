"""Token, cost and wall-time capture per worker session.

Nothing recorded any of this before: the detail control row, the log modal
header and the whole analytics view are all reading numbers that have to be
harvested when a session ends. Two shapes are accepted, because two kinds of
task produce them:

* the agent envelope an agent CLI writes as the last line of its log
  (Claude Code with ``--output-format json``: ``usage.input_tokens`` and
  friends, ``total_cost_usd``, ``model``);
* a ``usage`` block in the result file any adapter may write at
  ``$KRAFT_RESULT_PATH``.

**Cost is only ever the agent's own number.** Kraft used to carry a per-model
rate table and multiply tokens by it when an agent reported no cost. That is
gone: the agent knows what it was billed and Kraft does not, so a computed
figure would be a guess wearing the same font as a fact — and every consumer
of these numbers is someone deciding whether a run was worth it. A session with
tokens and no reported cost stores ``cost_usd = NULL``, and the rollups say so
rather than counting it as zero.

Tokens are different: they are measured, they are reported by everything that
has any, and they are what a rate table would have been applied to anyway. If
per-model pricing is ever wanted, it belongs in one place over the stored
token counts, not smeared across every session row at write time.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Usage:
    #: Uncached input only. Cache writes and reads were billed as input too,
    #: and are kept apart so a run's cache share is visible (Ruling 211);
    #: `total` is what every budget counts (Decision 18).
    tokens_in: int = 0
    tokens_out: int = 0
    #: None means "this agent did not report a cost", never "it was free".
    cost_usd: float | None = None
    model: str | None = None
    tokens_cache_write: int = 0
    tokens_cache_read: int = 0

    @property
    def total(self) -> int:
        return self.tokens_in + self.tokens_cache_write + self.tokens_cache_read + self.tokens_out


#: The token columns a `worker_sessions` row carries, in `Usage`'s names.
KINDS = ("tokens_in", "tokens_cache_write", "tokens_cache_read", "tokens_out")


def spent(row) -> int:
    """Every token a session row (or rollup) records: uncached input, cache
    writes, cache reads and output. What a `token_budget` counts, and what
    "tokens" means wherever one number is shown (Decision 18). A row written
    before the split has NULL cache kinds and its whole input in `tokens_in`,
    so this is the same total either way."""
    return sum(row[k] or 0 for k in KINDS)


def _int(v: object) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _from_usage_block(block: object, model: object) -> Usage | None:
    """One ``usage`` mapping, in either the agent's or Kraft's own field names."""
    if not isinstance(block, dict):
        return None
    u = Usage(
        tokens_in=_int(block.get("input_tokens", block.get("tokens_in"))),
        tokens_out=_int(block.get("output_tokens", block.get("tokens_out"))),
        model=model if isinstance(model, str) else None,
        # Cache writes and reads are input tokens that were billed; leaving
        # them out would under-report a long agent run by most of its input.
        tokens_cache_write=_int(
            block.get("cache_creation_input_tokens", block.get("tokens_cache_write"))
        ),
        tokens_cache_read=_int(
            block.get("cache_read_input_tokens", block.get("tokens_cache_read"))
        ),
    )
    return u if u.total else None


#: The per-model token counts `modelUsage` carries, in the agent CLI's own
#: spelling. Summed to decide which model actually did a session's work.
_MODEL_TOKEN_KEYS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
)


def _dominant(by_model: dict) -> str | None:
    """The `modelUsage` entry with the most tokens against it, or None.

    Strictly greater, so a tie keeps the first key and this stays deterministic
    -- and so a mapping whose values are not the shape expected (every entry
    scoring 0) degrades to the first key rather than to nothing.
    """
    best, best_tokens = None, -1
    for name, stats in by_model.items():
        if not isinstance(name, str) or not name:
            continue
        tokens = (
            sum(_int(stats.get(k)) for k in _MODEL_TOKEN_KEYS) if isinstance(stats, dict) else 0
        )
        if tokens > best_tokens:
            best, best_tokens = name, tokens
    return best


def _from_model_usage(by_model: object, model: str | None) -> Usage | None:
    """Tokens from `modelUsage`: the whole CLI session's, every model summed.

    Sums across models rather than picking one: this is "what did this session
    spend", not "which model did the work" -- that second question is
    `_model_of`/`_dominant`'s and is deliberately left alone (Kraft-s7c04.15).
    None when it reports nothing, so a task that spent nothing is not a
    zero-token run.
    """
    if not isinstance(by_model, dict):
        return None
    stats = [s for s in by_model.values() if isinstance(s, dict)]
    u = Usage(
        tokens_in=sum(_int(s.get("inputTokens")) for s in stats),
        tokens_out=sum(_int(s.get("outputTokens")) for s in stats),
        model=model,
        tokens_cache_write=sum(_int(s.get("cacheCreationInputTokens")) for s in stats),
        tokens_cache_read=sum(_int(s.get("cacheReadInputTokens")) for s in stats),
    )
    return u if u.total else None


def _model_of(envelope: dict) -> str | None:
    """The model an agent reported, in either shape it reports it.

    `--output-format stream-json`'s result envelope has no top-level `model`:
    it carries `modelUsage`, a mapping keyed by model name. Reading only
    `model` is why `worker_sessions.model` was NULL on every row ever written
    (Kraft-2r8s). A stated `model` still wins -- a result file written by a
    non-agent adapter names its model directly.

    *Which* key is the second half of that bug. `modelUsage` is keyed in order
    of first use, and the first use is Claude Code's own haiku warm-up -- so
    taking the first key named haiku on every agent row ever written, and made
    any cost-by-model reading of `worker_sessions` invalid (Kraft-s7c04.15).
    The model that did the work is the one the tokens are against.
    """
    model = envelope.get("model")
    if isinstance(model, str) and model:
        return model
    by_model = envelope.get("modelUsage")
    return _dominant(by_model) if isinstance(by_model, dict) else None


def from_envelope(envelope: object) -> Usage | None:
    """Usage from a decoded agent result envelope, or None if it carries none."""
    if not isinstance(envelope, dict):
        return None
    # `modelUsage` first (Kraft-s7c04.65): it is cumulative over the session,
    # Task-tool sub-agents and any turn killed before its result included, and
    # it is what `total_cost_usd` paid for. The `usage` block is the main
    # agent's last turn only (193475c9 read 1.80M input tokens of the 2.11M
    # billed). An interrupted envelope zeroes `usage` and reports only here
    # (Kraft-s7c04.18). `usage` stays the read for a result file, or any
    # envelope with no `modelUsage` to read.
    u = _from_model_usage(envelope.get("modelUsage"), _model_of(envelope))
    if u is None:
        u = _from_usage_block(envelope.get("usage"), _model_of(envelope))
    if u is None:
        return None
    cost = envelope.get("total_cost_usd", envelope.get("cost_usd"))
    if isinstance(cost, int | float) and not isinstance(cost, bool):
        u = replace(u, cost_usd=float(cost))
    return u


#: Reserved key in the caller's `seen` map for the model the `system/init` line
#: reported. A `request_id` can never look like this, and an entry with no
#: tokens adds nothing to the sums, so one map carries both facts across calls
#: -- which it has to, because the init line arrives in a different call from
#: the `assistant` lines that follow it.
_INIT_KEY = "\x00init"


def from_stream(lines: Iterable[str], seen: dict[str, Usage]) -> Usage | None:
    """Fold new stream-json lines into `seen` and return the running total.

    `seen` maps `request_id` -> that request's usage and is owned by the
    caller, which is what makes repeated calls over a growing log correct: the
    CLI emits several `assistant` lines per API request, with identical usage
    on each (measured: two lines, one request), so summing lines rather than
    requests double-counts. The envelope's own totals are themselves the sum
    over requests, so this converges on the number `session_exited` writes.

    Returns None only when nothing has been seen at all, so a caller can tell
    "no usage yet" from "zero tokens so far, model known".

    No cost: no per-request cost is reported, and cost is only ever the agent's
    own number (module docstring). A live row keeps `cost_usd` NULL until the
    envelope lands.
    """
    for raw in lines:
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        model = obj.get("model")
        if obj.get("type") == "system" and isinstance(model, str) and model:
            seen.setdefault(_INIT_KEY, Usage(model=model))
            continue
        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        u = _from_usage_block(message.get("usage"), message.get("model"))
        if u is None:
            continue
        request_id = obj.get("request_id")
        # A line with no request_id cannot be deduplicated against anything;
        # key it on its own position so it is counted exactly once.
        key = (
            request_id if isinstance(request_id, str) and request_id else f"{_INIT_KEY}{len(seen)}"
        )
        seen[key] = u
    if not seen:
        return None
    return replace(
        _sum(seen.values()),
        # The FIRST model seen, deliberately -- not `_model_of`'s dominant-by-
        # tokens read (Kraft-s7c04.15). `_INIT_KEY` is seeded from the
        # `system`/`init` line above and inserted before any `assistant` line,
        # and that line names the session's real model. This is already right
        # and must not be "fixed" to match the envelope path.
        model=next((u.model for u in seen.values() if u.model), None),
    )


def _sum(usages: Iterable[Usage]) -> Usage:
    """Each kind of token summed; no cost and no model, which do not add."""
    usages = list(usages)
    return Usage(**{k: sum(getattr(u, k) for u in usages) for k in KINDS})


def _combine(envelopes: list[dict]) -> dict:
    """One envelope standing for every agent invocation a log holds.

    A log can carry several result envelopes -- one per invocation, when the
    agent is re-launched into the same log (Kraft-s7c04.60). Measured against
    every such log on the owner's machine: within one CLI `session_id`,
    `total_cost_usd` and `modelUsage` are *cumulative* across invocations
    (5.54 -> 6.03 -> 6.08 -> 6.12 over four) while `usage` is that invocation
    alone. So taking the last envelope kept the cost but dropped every earlier
    invocation's tokens, and summing costs would double-count them.

    Per CLI session, then, the last envelope speaks for the whole: its cost and
    its `modelUsage` tokens. Not "the first envelope plus `modelUsage`
    growth": 6c235b8c's first turn ran 2,380 lines and was killed before it
    wrote a result, so its first result's `usage` held 1.2M input tokens of
    the 123M its $34.72 paid for. Only a group with no `modelUsage` to read
    falls back to summing each invocation's own `usage`. Distinct CLI sessions
    in one log are summed. A group whose last envelope reports no cost makes
    the whole cost unknown, never zero.
    """
    groups: dict[object, list[dict]] = {}
    for envelope in envelopes:
        groups.setdefault(envelope.get("session_id"), []).append(envelope)
    parts: list[Usage] = []
    cost: float | None = 0.0
    for group in groups.values():
        last = group[-1]
        total = _from_model_usage(last.get("modelUsage"), None)
        parts += [total] if total is not None else [from_envelope(e) for e in group]
        last_cost = (from_envelope(last) or Usage()).cost_usd
        cost = None if cost is None or last_cost is None else cost + last_cost
    u = _sum(parts)
    combined: dict = {
        # In Kraft's own names, which `_from_usage_block` reads as well.
        "usage": {k: getattr(u, k) for k in KINDS},
        "model": _model_of(envelopes[-1]),
    }
    if cost is not None:
        combined["total_cost_usd"] = cost
    return combined


def read_envelope(log_path: Path) -> dict | None:
    """The agent's result envelope: every invocation's, combined (`_combine`)
    when there is more than one.

    An invocation is a `type: result` line (or an untyped one, the shape a
    result file has) that `from_envelope` can read. A `usage` block alone is
    not enough: a Task-tool sub-agent's `system/task_progress` line carries
    one of its own, `{total_tokens, tool_uses, duration_ms}`, and read as an
    invocation it over-counted 6c235b8c by 156,662 input tokens
    (Kraft-lp01z).

    Claude Code's `stream-json` output appends a trailing `system/task_summary`
    line *after* the `result` line that carries `usage` and `total_cost_usd`.
    Taking the literal last line picked up that summary instead and silently
    dropped a reported cost (Kraft-xob8). Looking for `usage` finds the
    `result` line regardless of what follows it; a log that never has one
    still gets its last parseable JSON object, unchanged from before.
    """
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    fallback = None
    envelopes = []
    for line in lines:
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(envelope, dict):
            continue
        fallback = envelope
        if envelope.get("type", "result") == "result" and from_envelope(envelope):
            envelopes.append(envelope)
    if len(envelopes) > 1:
        return _combine(envelopes)
    return envelopes[0] if envelopes else fallback


@dataclass(frozen=True)
class RateLimitInfo:
    """A rejected launch's rate-limit details, in Kraft's own field names.

    Same three fields `_rate_limit_claude` always returned as a dict --
    named here so a caller reading `.resets_at_iso` gets a typo caught
    instead of a silent `None` from a misspelled dict key.
    """

    #: Whatever the CLI put at `rateLimitType`, verbatim -- untyped external
    #: JSON, same as `_rate_limit_claude` always passed through unchecked.
    #: Typing this narrower than `object` would mean coercing a value this
    #: function has never validated, which changes behaviour.
    rate_limit_type: object
    #: None when the CLI names no machine-readable reset time (codex's
    #: message is human text); `stops` then retries now and `fallback` does
    #: not treat the pair as known-limited.
    resets_at: int | float | None
    resets_at_iso: str | None
    #: The CLI's own words, for a harness whose limit carries no reset time.
    message: str | None = None


def _rate_limit_claude(log_path: Path) -> RateLimitInfo | None:
    """The rejected `rate_limit_info` from a claude stream-json log, or None.

    The CLI emits a `rate_limit_event` line on most turns, nearly all of them
    `status: "allowed"` -- an `overageStatus` of "rejected" on an otherwise
    allowed turn means only that overage spend was refused, not that the turn
    itself was blocked. Only a top-level `status: "rejected"` means the launch
    was refused. Scanned across every line, not just the last: unlike the
    result envelope, this event is not guaranteed to be the final line.
    Best-effort like `agent._envelope_is_error`: a log Kraft cannot read yet is
    "no rejection seen", not a crash.
    """
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "rate_limit_event":
            continue
        info = obj.get("rate_limit_info")
        if not isinstance(info, dict) or info.get("status") != "rejected":
            continue
        resets_at = info.get("resetsAt")
        if not isinstance(resets_at, int | float):
            continue
        return RateLimitInfo(
            rate_limit_type=info.get("rateLimitType"),
            resets_at=resets_at,
            resets_at_iso=datetime.fromtimestamp(resets_at, UTC).isoformat(),
        )
    return None


def _cumulative(log_path: Path, cli: str) -> Usage | None:
    """What CLI session `cli` had spent by the end of this log: its last
    result envelope's `modelUsage` tokens and `total_cost_usd`, both running
    totals over every invocation of that session (`_combine`'s measurement).
    None when the log has no result envelope for it."""
    last = None
    try:
        with log_path.open() as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("type") == "result":
                    last = obj if obj.get("session_id") in (cli, None) else last
    except OSError:
        return None
    return from_envelope(last)


def net_of_earlier(own: Usage, log_path: Path, earlier_log: Path, cli: str) -> Usage:
    """`own`, less what CLI session `cli` had already spent by the end of
    `earlier_log`, a session this one resumed (Kraft-s7c04.62). Both logs'
    envelopes report the session's running totals, so the difference is this
    session's own spend. Unknown earlier spend makes this session's cost
    unknown too, never the whole running total and never zero."""
    mine, before = _cumulative(log_path, cli), _cumulative(earlier_log, cli)
    if mine is None:
        return own
    if before is None:
        return replace(own, cost_usd=None)
    cost = (
        None
        if mine.cost_usd is None or before.cost_usd is None
        else max(mine.cost_usd - before.cost_usd, 0.0)
    )
    return Usage(
        **{k: max(getattr(mine, k) - getattr(before, k), 0) for k in KINDS},
        cost_usd=cost,
        model=own.model,
    )


def _session_id_claude(log_path: Path) -> str | None:
    """The `claude` CLI's own session id, off the `system`/`init` line every
    `--output-format stream-json` run starts with -- the identity `--resume`
    takes, distinct from Kraft's own `worker_sessions.id`.

    Scanned across every line rather than assumed to be the first, the same
    defensive shape `_rate_limit_claude` uses reading this same log: a line
    Kraft cannot parse yet must not crash a session that otherwise ran fine.
    Read line by line and stopped at the first init: `store.sessions` asks
    this of a work item's earlier logs at every session end (Kraft-s7c04.62).
    """
    try:
        with log_path.open() as fh:
            for line in fh:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(obj, dict)
                    and obj.get("type") == "system"
                    and obj.get("subtype") == "init"
                ):
                    sid = obj.get("session_id")
                    if isinstance(sid, str) and sid:
                        return sid
    except OSError:
        return None
    return None


#: A job's own terminal states, as `task_updated` reports them.
_JOB_ENDED = ("completed", "failed", "killed", "stopped")


def _unfinished_jobs_claude(log_path: Path) -> list[str]:
    """What each background job still running when the agent's last turn
    ended was running, oldest first; `[]` when none was, or no turn ended.

    A job is backgrounded two ways, and the log shows both: the agent asks
    (`run_in_background` on a Bash call), or Claude Code moves a foreground
    command that outlived its timeout (`task_updated` with `is_backgrounded`)
    -- the second is how a full-suite run left the turn in Kraft-nxqft. A
    job ends with its `task_notification`. Read as of the last `result` line,
    the turn's end: the CLI kills what is left on its way out, which the log
    reports after that line, and that kill is not the agent waiting on it.
    """
    try:
        lines = log_path.read_text().splitlines()
    except OSError:
        return []
    running: dict[object, str] = {}  # tool_use_id -> the command it runs
    started: dict[object, tuple[object, str]] = {}  # task_id -> (tool_use_id, command)
    at_turn_end: list[str] = []
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        kind, sub = obj.get("type"), obj.get("subtype")
        if kind == "result":
            at_turn_end = list(running.values())
        elif kind == "assistant" and isinstance(obj.get("message"), dict):
            for block in obj["message"].get("content") or ():
                args = block.get("input") if isinstance(block, dict) else None
                if isinstance(args, dict) and args.get("run_in_background") is True:
                    running[block.get("id")] = str(args.get("command") or block.get("name"))
        elif kind != "system":
            continue
        elif sub == "task_started":
            job = (obj.get("tool_use_id"), str(obj.get("description")))
            started[obj.get("task_id")] = job
            if obj.get("is_backgrounded") is True:
                running.setdefault(*job)
        elif sub == "task_updated" and isinstance(obj.get("patch"), dict):
            job = started.get(obj.get("task_id"))
            if job is not None and obj["patch"].get("is_backgrounded") is True:
                running.setdefault(*job)
            elif job is not None and obj["patch"].get("status") in _JOB_ENDED:
                running.pop(job[0], None)
        elif sub == "task_notification":
            running.pop(obj.get("tool_use_id"), None)
    return at_turn_end


def _json_lines(lines: Iterable[str]) -> Iterable[dict]:
    """Each line that decodes to a JSON object; anything else is skipped."""
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def _log_objects(log_path: Path) -> list[dict]:
    try:
        return list(_json_lines(log_path.read_text().splitlines()))
    except OSError:
        return []


def _usage_codex(obj: dict) -> Usage | None:
    """Tokens off a `codex exec --json` `turn.completed` line.

    Measured against codex-cli 0.155.0 (Kraft-w3kot): this `usage` is the
    thread's running total, not the turn's own -- a resumed thread's second
    turn reported output 28 after a first turn of 23 plus its own 5, matching
    the rollout's `total_token_usage` and not its `last_token_usage`. So the
    latest line wins; summing turns would count every earlier one again.
    `cached_input_tokens` is a share of `input_tokens` (the rollout's
    `total_tokens` is input + output), so it comes off the uncached count.
    No cost: the stream carries none, and cost is only the agent's own number.
    """
    if obj.get("type") != "turn.completed" or not isinstance(obj.get("usage"), dict):
        return None
    block = obj["usage"]
    cache_read = _int(block.get("cached_input_tokens"))
    cache_write = _int(block.get("cache_write_input_tokens"))
    return Usage(
        tokens_in=max(_int(block.get("input_tokens")) - cache_read - cache_write, 0),
        tokens_out=_int(block.get("output_tokens")),
        tokens_cache_read=cache_read,
        tokens_cache_write=cache_write,
    )


def _stream_codex(lines: Iterable[str], seen: dict[str, Usage]) -> Usage | None:
    """Live progress: the newest running total. `seen` is the caller's, kept
    across calls over a growing log like `from_stream`'s.
    ponytail: one key for the whole log, so a log holding two codex threads
    shows only the newer one live; `_envelope_codex` sums them at the end."""
    for obj in _json_lines(lines):
        u = _usage_codex(obj)
        if u is not None:
            seen["codex"] = u
    return seen.get("codex")


def _envelope_codex(log_path: Path) -> dict | None:
    """Every codex thread in the log, each at its last `turn.completed`, summed,
    in Kraft's own names (which `_from_usage_block` reads)."""
    by_thread: dict[object, Usage] = {}
    thread = None
    for obj in _log_objects(log_path):
        if obj.get("type") == "thread.started":
            thread = obj.get("thread_id")
        u = _usage_codex(obj)
        if u is not None:
            by_thread[thread] = u
    if not by_thread:
        return None
    u = _sum(by_thread.values())
    return {"usage": {k: getattr(u, k) for k in KINDS}}


def _session_id_codex(log_path: Path) -> str | None:
    """The thread id `codex exec resume` takes, off the `thread.started` line."""
    for obj in _log_objects(log_path):
        sid = obj.get("thread_id") if obj.get("type") == "thread.started" else None
        if isinstance(sid, str) and sid:
            return sid
    return None


#: What codex-cli 0.155.0 says when it is limited, found in its binary: the
#: ChatGPT-plan message ("You've hit your usage limit. ... try again at ..."),
#: its error codes, and its give-up line after retrying a 429 ("exceeded retry
#: limit, last status: 429 Too Many Requests").
_CODEX_LIMITED = re.compile(
    r"hit your usage limit|usage_limit_(?:reached|exceeded)|rate_limit_exceeded"
    r"|429 Too Many Requests|\"status\":\s*429\b",
    re.IGNORECASE,
)


def _rate_limit_codex(log_path: Path) -> RateLimitInfo | None:
    """A `turn.failed` whose message says the launch was limited, or None.

    Only `turn.failed`, not the bare `error` event: that one also reports
    problems the turn goes on to survive, and in every failure captured
    `turn.failed` repeated the `error` message. The message is human text with
    no machine-readable reset time, so `resets_at` is unknown (None) and the
    rate-limit poller's own retry cap bounds the retries instead.
    """
    for obj in _log_objects(log_path):
        err = obj.get("error") if obj.get("type") == "turn.failed" else None
        message = err.get("message") if isinstance(err, dict) else None
        if isinstance(message, str) and _CODEX_LIMITED.search(message):
            return RateLimitInfo(
                rate_limit_type=None, resets_at=None, resets_at_iso=None, message=message[:500]
            )
    return None


@dataclass(frozen=True)
class Reader:
    """A log schema Kraft knows how to parse.

    In code, never in YAML: an operator must not be able to break a parser by
    editing config. A harness file names one; adding one is a release.
    """

    name: str
    stream: Callable[[Iterable[str], dict], Usage | None]
    #: Deliberately still `dict | None`, not a narrower type: `read_envelope`
    #: hands back either the raw agent envelope (arbitrary external JSON, one
    #: shape per harness) or `_combine`'s own dict, and its only reader is
    #: `from_envelope`, which is already written to treat either as an
    #: untrusted mapping via `.get()`. Typing it narrower would need `Any` for
    #: the raw-envelope branch or reshaping `_combine` (out of scope here) for
    #: no caller that reads a key it shouldn't -- Kraft-5d510.14 stopped here.
    envelope: Callable[[Path], dict | None]
    rate_limit: Callable[[Path], RateLimitInfo | None]
    #: The CLI's own resumable-session id, off this schema's log -- distinct
    #: from Kraft's own `worker_sessions.id` (Kraft-cvnx1). None for a schema
    #: with no such id to extract.
    session_id: Callable[[Path], str | None] = lambda _log_path: None
    #: What each background job still running when the last turn ended was
    #: running (Kraft-xvugd). `[]` for a schema that cannot tell.
    unfinished_jobs: Callable[[Path], list[str]] = lambda _log_path: []


READERS: dict[str, Reader] = {
    "claude-stream-json": Reader(
        name="claude-stream-json",
        stream=from_stream,
        envelope=read_envelope,
        rate_limit=_rate_limit_claude,
        session_id=_session_id_claude,
        unfinished_jobs=_unfinished_jobs_claude,
    ),
    "codex-json": Reader(
        name="codex-json",
        stream=_stream_codex,
        envelope=_envelope_codex,
        rate_limit=_rate_limit_codex,
        session_id=_session_id_codex,
    ),
}


def read(log_path: Path, result_path: Path, reader: str | None = None) -> Usage | None:
    """Usage for a finished session: the result file wins, the log envelope
    backs it up -- but only when `reader` names a schema Kraft can parse.
    `reader=None` means the harness declared no log-based usage reading
    (`source: result_file`), so only the result file is consulted; an unknown
    name is a config error, not a silent skip.
    """
    try:
        result = json.loads(result_path.read_text())
    except OSError, json.JSONDecodeError:
        result = None
    if isinstance(result, dict):
        u = from_envelope(result)
        if u is not None:
            return u
    if reader is None:
        return None
    return from_envelope(READERS[reader].envelope(log_path))
