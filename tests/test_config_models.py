import pytest


def test_first_error_names_the_file_and_the_field():
    """Callers show one message, not a pydantic error list. The file name and
    the offending field both have to survive, because that is what today's
    hand-rolled strings say: "repos.yaml: 'managed' must be a boolean"."""
    from pydantic import BaseModel, ValidationError

    from kraft import config

    class M(BaseModel):
        managed: bool

    try:
        M.model_validate({"managed": "yes please"})
    except ValidationError as exc:
        msg = config.first_error(exc, "repos.yaml")
    assert msg.startswith("repos.yaml: ")
    assert "managed" in msg


def test_theme_read_path_rejects_a_palette_the_write_path_would_reject(tmp_path):
    """PUT /theme checks PALETTE_IDS; GET /theme merged the file into the
    defaults with no check at all, so a hand-edited theme.yaml naming a palette
    that does not exist reached the SPA. One model, both directions."""
    from kraft import config

    p = tmp_path / "theme.yaml"
    p.write_text("palette: chartreuse\nmode: dark\n")
    with pytest.raises(config.ConfigError) as exc:
        config.Theme.load(p)
    assert "chartreuse" in str(exc.value)


def test_intake_defaults_every_missing_key(tmp_path):
    """`Intake.load`'s docstring already promises this; it becomes field
    defaults rather than a dict merge."""
    from kraft import config

    assert config.Intake.load(tmp_path / "does-not-exist.yaml") == config.Intake()


@pytest.mark.parametrize("name", ["Access", "Notify", "Intake", "Theme"])
def test_each_settings_file_loads_and_saves_through_its_own_model(tmp_path, name):
    """Kraft-5d510.7: a settings file's read and write are its model's
    `load`/`save`, the way `Policy.from_yaml` is policy.yaml's, so no caller
    writes a dict the loader would then refuse. A missing file is the
    defaults; what one saves, the next loads; a bad value names the file."""
    from kraft import config

    model = getattr(config, name)
    path = tmp_path / f"{name.lower()}.yaml"
    assert model.load(path) == model()
    model().save(path)
    assert model.load(path) == model()
    path.write_text("nonsense: 1\n")
    with pytest.raises(config.ConfigError, match=f"{name.lower()}.yaml: .*nonsense"):
        model.load(path)


# ── theme.yaml V2 keys (UX V2, B30) ──


def test_theme_v2_keys_are_optional_and_diff_and_code_scheme_default():
    from kraft import config

    t = config.Theme()
    assert (t.surface, t.accent, t.colour_amount) == (None, None, None)
    assert t.code_scheme.model_dump() == {"light": "auto", "dark": "auto"}
    assert t.diff.model_dump() == {
        "layout": "unified",
        "colours": "theme",
        "show_whitespace": True,
        "word_highlight": True,
        "wrap_lines": False,
        "one_file_at_a_time": True,
    }


@pytest.mark.parametrize(
    "scheme", [{"light": "solarized-dark"}, {"light": "monokai"}, {"dark": "solarized-light"}]
)
def test_theme_refuses_a_code_scheme_from_the_other_mode(scheme):
    from pydantic import ValidationError

    from kraft import config

    with pytest.raises(ValidationError):
        config.Theme.model_validate({"code_scheme": scheme})


def test_theme_refuses_an_accent_at_mono_in_plain_words():
    from pydantic import ValidationError

    from kraft import config

    with pytest.raises(ValidationError, match="mono has no accent"):
        config.Theme.model_validate({"colour_amount": "mono", "accent": "blue"})
    assert config.Theme.model_validate({"colour_amount": "mono", "accent": "none"}).accent == "none"


@pytest.mark.parametrize(
    ("palette", "surface", "accent"),
    [
        ("nocturne", "ink", "violet"),
        ("rose", "ink", "violet"),
        ("forest", "moss", "green"),
        ("amber", "sand", "amber"),
        ("slate", "slate", "blue"),
    ],
)
def test_theme_derives_v2_values_from_an_old_palette(palette, surface, accent):
    from kraft import config

    eff = config.Theme(palette=palette, mode="light").effective()
    assert (eff["surface"], eff["accent"], eff["colour_amount"]) == (surface, accent, "full")
    assert eff["derived"] is True
    assert eff["mode"] == "light"


def test_theme_with_its_own_surface_is_not_derived():
    from kraft import config

    theme = config.Theme(surface="graphite", accent="rose", colour_amount="subtle")
    eff = theme.effective()
    assert (eff["surface"], eff["accent"], eff["colour_amount"], eff["derived"]) == (
        "graphite",
        "rose",
        "subtle",
        False,
    )


def test_theme_with_a_surface_alone_defaults_to_no_accent_at_subtle():
    from kraft import config

    eff = config.Theme(palette="forest", surface="moss").effective()
    assert (eff["accent"], eff["colour_amount"], eff["derived"]) == ("none", "subtle", False)


def _look(theme_file):
    from kraft import config

    # What the new UI paints from: every key `GET /theme` answers but `derived`.
    eff = config.Theme.load(theme_file).effective()
    del eff["derived"]
    return eff


@pytest.mark.parametrize(
    "before",
    [
        "palette: nocturne\n",
        "palette: rose\n",
        "palette: forest\n",
        "palette: amber\n",
        "palette: slate\n",
        "palette: forest\nmode: light\ndensity: comfortable\n"
        "board:\n  group_by: repo\n  show_done: 9\n  open_in: full\n",
        "palette: amber\ncolour_amount: mono\n",
        "palette: slate\naccent: rose\n",
        "palette: forest\nsurface: graphite\naccent: blue\n",
        "palette: rose\ncode_scheme:\n  dark: monokai\n"
        "diff:\n  layout: split\n  wrap_lines: true\n",
    ],
    ids=[
        "nocturne-alone",
        "rose-alone",
        "forest-alone",
        "amber-alone",
        "slate-alone",
        "with-mode-density-board",
        "at-mono",
        "with-its-own-accent",
        "with-its-own-surface",
        "with-code-scheme-and-diff",
    ],
)
def test_theme_migration_keeps_the_look(tmp_path, before):
    """The cutover rewrites a user's theme.yaml without `palette` (spec §11.3)
    and must not change how the UI looks (kickoff §4.5). A file with only
    `palette` is the common case: every V2 key there was derived."""
    import yaml

    from kraft import config

    p = tmp_path / "theme.yaml"
    p.write_text(before)
    look = _look(p)

    assert config.migrate_theme(p) is True

    after = yaml.safe_load(p.read_text())
    assert "palette" not in after
    assert _look(p) == look
    kept = {k: v for k, v in yaml.safe_load(before).items() if k != "palette"}
    assert {k: after[k] for k in kept} == kept
    assert (tmp_path / "theme.yaml.pre-1.5").read_text() == before


@pytest.mark.parametrize(
    "before",
    [None, "mode: light\nsurface: moss\n", "palette: [unclosed\n", "palette: cerulean\n"],
    ids=["missing", "no-palette", "unreadable", "unknown-palette"],
)
def test_theme_migration_leaves_files_without_palette_alone(tmp_path, before):
    from kraft import config

    p = tmp_path / "theme.yaml"
    if before is not None:
        p.write_text(before)

    assert config.migrate_theme(p) is False

    assert p.exists() is (before is not None)
    if before is not None:
        assert p.read_text() == before
    assert not (tmp_path / "theme.yaml.pre-1.5").exists()


def test_theme_migration_runs_once(tmp_path):
    from kraft import config

    p = tmp_path / "theme.yaml"
    backup = tmp_path / "theme.yaml.pre-1.5"
    p.write_text("palette: forest\n")
    assert config.migrate_theme(p) is True
    migrated = p.read_bytes()

    assert config.migrate_theme(p) is False
    assert p.read_bytes() == migrated

    # A palette written back later (an old tab) migrates again; the first
    # backup, the one that holds the user's original file, is kept.
    p.write_text("palette: amber\nsurface: sand\n")
    assert config.migrate_theme(p) is True
    assert backup.read_text() == "palette: forest\n"


def test_a_theme_with_no_palette_or_surface_keeps_the_nocturne_look():
    """What a user with no theme.yaml sees, before and after the cutover."""
    from kraft import config

    for theme in (config.Theme(), config.Theme.model_validate({"mode": "light"})):
        eff = theme.effective()
        assert (eff["surface"], eff["accent"], eff["colour_amount"]) == ("ink", "violet", "full")
