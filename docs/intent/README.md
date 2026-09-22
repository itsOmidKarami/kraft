# The intent tree

What this repository is meant to do, one file per capability, each requirement
pinned to the tests that enforce it. A capability is a chosen name for a
coherent slice of behaviour (`review-surface`, not `gate_review.py`). This file
states the format; it is not a capability file, and the check does not read it.

## A requirement

Each requirement has four parts, in this order:

```markdown
  ## REQ reject-records-note-and-reopens
  WHEN a gate is rejected, the system SHALL record the reviewer's note on the
  gate row and reopen the row.
  enforced-by: tests/test_gates.py::test_reject, tests/test_gates.py::test_reopen
  origin: src/kraft/gate_review.py
```

(Indented here so the check and the origins test pass over it. In a capability
file each part starts at the beginning of its line.)

| Part | Rule |
|---|---|
| `## REQ <id>` | Lowercase kebab-case, unique within its file. Never renamed: a rename is a new requirement and the removal of the old one. |
| requirement | One sentence in EARS (below). It may wrap over several lines. |
| `enforced-by:` | Zero or more pytest node ids, comma-separated. Zero is legal, and the check lists it. |
| `origin:` | Where the intent was decided: a committed file, optionally followed by a section (`path §3`). Specs and plans are not committed, so they can't be origins. |

A non-`REQ` `## ` heading ends the requirement before it.

## EARS

One of five patterns per requirement:

| Pattern | Shape |
|---|---|
| Ubiquitous | `The <system> SHALL <response>` |
| Event-driven | `WHEN <trigger>, the <system> SHALL <response>` |
| State-driven | `WHILE <state>, the <system> SHALL <response>` |
| Unwanted | `IF <condition>, THEN the <system> SHALL <response>` |
| Optional | `WHERE <feature is enabled>, the <system> SHALL <response>` |

`SHALL` and `SHALL NOT` only. `should`, `may`, `appropriate`, `properly` and
`as needed` each hide the decision a test would have to make. A sentence that
fits no pattern bundles several behaviours: split it.

## What the check enforces

`just intent` (`python -m kraft.intent`) fails when:

- a pin names no test that `pytest --collect-only` finds (`BROKEN`);
- an id is not lowercase kebab-case (`MALFORMED`);
- an id appears twice in one file (`DUPLICATE`).

It lists, and does not fail on, a requirement with no pin (`UNPINNED`) and a
pin into `frontend/` (`FRONTEND`), which pytest can't resolve.

What it can't enforce: that a pin is useful. A requirement pinned to
`assert True` passes. Usefulness is bought when the requirement is written: its
test is seen to fail for the stated reason, then the code makes it pass.

## The process

- A spec states the requirement changes its work makes: each requirement it
  adds, changes or removes, one behaviour each, no broader than a test will
  enforce.
- A plan applies those changes to the tree and writes the tests they are
  pinned to, each seen to fail first.
- Review flags a diff that changes described behaviour without changing the
  requirement, and a requirement stated more broadly than its test.
- The work brief lists the requirements a diff adds, changes or removes.
