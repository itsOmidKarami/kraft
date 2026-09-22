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
