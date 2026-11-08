"""New item (spec §7.6): `n` from any view files a work item through a form
holding the fields `kraft item create` is most used with, filed as the CLI
files it. It lands paused unless Start now is on; the Item then opens on it,
and what filing said, the server's warnings among it, is a toast."""

from __future__ import annotations

from functools import partial

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.widgets import Button, Checkbox, Input, Static, TextArea

from kraft import answers, client, render
from kraft.console.dialogs import BACK, Dialog, Toggle

#: The answer's fields that warn: said once it is filed, since the server
#: knows them only then (spec §7.6).
WARNINGS = ("duplicate_warning", "bead_warning", "repo_warning")
#: How long a toast that warns stays: long enough to read the ids in it.
WARNED_S = 15.0
#: The form's fields, by the id after `new-`, and their labels.
FIELDS = (
    ("title", "Title"),
    ("description", "Description"),
    ("repo", "Repo"),
    ("chain", "Chain"),
    ("spec", "Spec"),
    ("plan", "Plan"),
    ("after", "After"),
)
#: What each field takes, said while it is empty.
HINTS = {
    "title": "one line, as the board shows it",
    "description": "the brief the spec is written from",
    "repo": "empty: the repo this directory is in",
    "chain": "empty: the repo's default chain",
    "spec": "a file inside the repo; skips the spec node",
    "plan": "a file inside the repo; skips the plan node",
    "after": "item ids it comes after, space-separated",
}
#: Not offered in a remote console: the API takes a path the server resolves,
#: not a file's contents, and this machine's files are not the server's.
REMOTE_HIDDEN = ("spec", "plan")
REMOTE_REPO_HINT = "a repo on the server, as the Repos screen lists it"


def said(result: dict) -> tuple[str, str]:
    """What filing says, and its toast's severity: the item and where it
    stands, the CLI's sentence for a start with no free slot, then each
    warning in the server's words."""
    lines = [f"filed {result['id']}; the item is now {answers.item_now(result)}"]
    if slots := answers.filed(result):
        lines.append(slots)
    warned = [result[key] for key in WARNINGS if result.get(key)]
    return "\n".join([*lines, *warned]), "warning" if warned else "information"


class NewItem(Dialog[dict | None]):
    """The form. Enter in a one-line field, or File, sends it; a refusal
    shows under the fields in the server's words and the form stays. Answers
    the create's answer, or None for Esc."""

    def __init__(self, repo: str | None) -> None:
        super().__init__()
        self.repo = repo or ""
        #: A create is on its way: a second Enter must not file it twice.
        self.filing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="new-item"):
            yield Static("New item", id="new-item-heading")
            for name, label in FIELDS:
                if client.is_remote() and name in REMOTE_HIDDEN:
                    continue
                with Horizontal(classes="new-field", id=f"new-{name}-row"):
                    yield Static(label)
                    hint = (
                        REMOTE_REPO_HINT if name == "repo" and client.is_remote() else HINTS[name]
                    )
                    if name == "description":
                        yield TextArea(compact=True, placeholder=hint, id="new-description")
                    else:
                        value = self.repo if name == "repo" else ""
                        yield Input(value, placeholder=hint, compact=True, id=f"new-{name}")
            yield Toggle(
                "Auto-gate: an agent reviews gates first",
                True,
                compact=True,
                id="new-auto-gate",
            )
            yield Toggle("Start now (off: it is filed paused)", compact=True, id="new-start")
            yield Static(id="new-said")
            with Horizontal(id="new-buttons"):
                yield Button("File", id="new-go")
                yield Button(BACK, id="new-no")

    def on_mount(self) -> None:
        self.query_one("#new-title", Input).focus()

    def _value(self, name: str) -> str:
        return self.query_one(f"#new-{name}", Input).value.strip()

    def _say(self, text: str, style: str = "$kraft-muted") -> None:
        # Content: a refusal echoes what was typed, `[/]` and all.
        self.query_one("#new-said", Static).update(Content.styled(text, style))

    @on(Input.Submitted)
    @on(Button.Pressed, "#new-go")
    def _go(self) -> None:
        if self.filing:
            return
        title = self._value("title")
        if not title:
            self._say("A title is required.", "$error")
            self.query_one("#new-title", Input).focus()
            return
        remote = client.is_remote()
        repo = self._value("repo")
        spec, plan = ("", "") if remote else (self._value("spec"), self._value("plan"))
        # Read now, before the create is awaited: what is sent is what was there.
        fields = {
            # Remotely a path is the server's, resolved against its repo list in `_file`.
            "repo": repo if remote else (client.absolute_path(repo) if repo else None),
            "chain": self._value("chain") or None,
            "description": self.query_one(TextArea).text.strip() or None,
            "attachments": client.attachment_paths(spec or None, plan or None),
            "auto_gate": self.query_one("#new-auto-gate", Checkbox).value,
            "depends_on": self._value("after").replace(",", " ").split() or None,
            "autostart": self.query_one("#new-start", Checkbox).value,
        }
        self.filing = True
        self._say("Filing…")
        self.run_worker(partial(self._file, title, fields), group="file")

    async def _file(self, title: str, fields: dict) -> None:
        if client.is_remote():
            try:
                fields["repo"] = await self._server_repo(fields["repo"])
            except client.ClientError as exc:
                self.filing = False
                self._say(render.printable(client.reason_of(str(exc))), "$error")
                return
            except ValueError as exc:
                self.filing = False
                self._say(str(exc), "$error")
                return
        try:
            created = await client.create_work_item(title, **fields)
        except client.ClientError as exc:
            self.filing = False
            # Written out, never obeyed: the server echoes the typed path.
            self._say(render.printable(client.reason_of(str(exc))), "$error")
            return
        self._answer(created)

    @staticmethod
    async def _server_repo(typed: str) -> str:
        """The server's own spelling of the repo `typed` names: its path, or the
        last part of one. Raises ValueError with the sentence to show."""
        if not typed:
            raise ValueError("A remote item needs a repo: name one the Repos screen lists.")
        known = [r["path"] for r in await client.repos()]
        by_name = [p for p in known if p == typed or p.rstrip("/").rsplit("/", 1)[-1] == typed]
        if len(by_name) == 1:
            return by_name[0]
        if not by_name:
            raise ValueError(f"The server has no repo called {typed}. The Repos screen lists them.")
        raise ValueError(f"{typed} names {len(by_name)} repos on the server; type a whole path.")

    def action_cancel(self) -> None:
        self._answer(None)

    @on(Button.Pressed, "#new-no")
    def _no(self) -> None:
        self._answer(None)
