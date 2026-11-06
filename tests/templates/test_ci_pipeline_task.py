"""The `ci.pipeline` forge target in the chain schema: the one task that waits
for a CI pipeline by id (`ci-pipeline-takes-a-pipeline-id`)."""

import pytest
from pydantic import ValidationError

from kraft.templates import models as tm

from .test_step_outputs import node, sub

CI = tm.ForgeAction.CI_PIPELINE.value


def forge(target: str = CI, **kw) -> dict:
    return {"id": "wait", "kind": "forge", "target": target, **kw}


@pytest.mark.parametrize(
    "task",
    [
        pytest.param(forge(pipeline="12345"), id="digits"),
        pytest.param(forge(pipeline="0"), id="zero"),
        pytest.param(forge(pipeline=12345), id="unquoted-yaml-integer"),
    ],
)
def test_ci_pipeline_accepts_a_literal_id(task):
    assert tm.ForgeTask.model_validate(task).pipeline == str(task["pipeline"])


@pytest.mark.parametrize(
    ("task", "match"),
    [
        pytest.param(forge(), "needs its pipeline id", id="missing"),
        pytest.param(
            forge("mr.ci", pipeline="12345"), "only a ci.pipeline task", id="other-target"
        ),
        pytest.param(forge(pipeline="12a"), "digits only", id="letters"),
        pytest.param(forge(pipeline="12\n"), "digits only", id="trailing-newline"),
        pytest.param(forge(pipeline=""), "digits only", id="empty"),
        pytest.param(forge(pipeline=-5), "digits only", id="negative-integer"),
        pytest.param(forge(pipeline="12345", scope="each_repository"), "scope", id="fan-out"),
    ],
)
def test_ci_pipeline_refusals(task, match):
    with pytest.raises(ValidationError, match=match):
        tm.ForgeTask.model_validate(task)


def test_a_bad_value_is_never_echoed():
    with pytest.raises(ValidationError) as caught:
        tm.ForgeTask.model_validate(forge(pipeline="s3cret-id"))
    assert all("s3cret-id" not in e["msg"] for e in caught.value.errors())


@pytest.mark.parametrize(
    ("target", "kw", "hidden"),
    [
        pytest.param("mr.ci", {}, True, id="mr.ci"),
        pytest.param(CI, {"pipeline": "1"}, False, id="ci.pipeline"),
    ],
)
def test_pipeline_row_is_shown_only_on_ci_pipeline(target, kw, hidden):
    task = tm.ForgeTask.model_validate(forge(target, **kw))
    assert ("pipeline" in task.unread()) is hidden


def test_ci_pipeline_waits():
    assert tm.ForgeAction.CI_PIPELINE.waits


def _chain_with(pipeline: str, *, writes=("PIPELINE_ID",), reads=("find.t",)) -> dict:
    """A writer step then a `ci.pipeline` step reading it."""
    return {
        "id": "c",
        "nodes": [
            node(
                steps=[
                    {"id": "find", "tasks": [sub(outputs={"variables": list(writes)})]},
                    {
                        "id": "wait",
                        "tasks": [forge(pipeline=pipeline, reads=list(reads))],
                    },
                ]
            )
        ],
    }


@pytest.mark.parametrize("pipeline", ["$PIPELINE_ID", "${PIPELINE_ID}"])
def test_ci_pipeline_reads_its_id_from_a_writers_variable(pipeline):
    assert tm.Chain.model_validate(_chain_with(pipeline))


@pytest.mark.parametrize(
    ("pipeline", "kw", "match"),
    [
        pytest.param("$NOPE", {}, "no task in its reads declares", id="no-writer-declares-it"),
        pytest.param("$PIPELINE_ID", {"reads": ()}, "no task in its reads", id="no-reads"),
        pytest.param("${X", {}, "digits only", id="unclosed-brace"),
        pytest.param("$X}", {}, "digits only", id="stray-brace"),
        pytest.param("x$A", {}, "digits only", id="text-around-it"),
        pytest.param("$lower", {}, "digits only", id="lowercase"),
    ],
)
def test_ci_pipeline_variable_refusals(pipeline, kw, match):
    with pytest.raises(ValidationError, match=match):
        tm.Chain.model_validate(_chain_with(pipeline, **kw))
