"""The REST API agents and scripts depend on is recorded in docs/openapi.json.

Any change to routes, request or response models shows up as a diff of that file
in the PR. After an intended change run `make openapi` and commit the result.
"""

import json
import os
from pathlib import Path

CONTRACT = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def current_schema() -> str:
    import main

    return json.dumps(main.app.openapi(), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def test_openapi_matches_contract():
    if os.environ.get("UPDATE_OPENAPI"):  # `make openapi`
        CONTRACT.write_text(current_schema(), "utf-8")
    assert CONTRACT.exists(), "docs/openapi.json is missing: run `make openapi`"
    assert current_schema() == CONTRACT.read_text("utf-8"), (
        "The API changed. If intended, run `make openapi` and commit docs/openapi.json"
    )
