from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "path, needle",
    [
        pytest.param("README.md", "kraft-sdlc.dev/demo/", id="readme"),
        pytest.param("docsite/content/index.md", "to: /demo/", id="docs-home"),
        pytest.param("docsite/content/1.get-started/index.md", "](/demo/)", id="get-started"),
        pytest.param(
            "docsite/content/5.reference/00.web-ui/index.md", "](/demo/)", id="web-ui-reference"
        ),
        pytest.param("docsite/content/6.project/3.whats-new.md", "](/demo/)", id="whats-new"),
    ],
)
def test_the_demo_is_linked_from(path, needle):
    assert needle in (ROOT / path).read_text()
