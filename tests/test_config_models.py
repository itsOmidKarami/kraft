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
