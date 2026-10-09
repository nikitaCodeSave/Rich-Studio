"""Bring app/bundled/api.json up to the published Bot API spec.

Downloads the spec (SPEC_URL, by default telegram-bot-api-spec on GitHub), checks its shape,
and replaces the bundled copy byte for byte when the version or the tracked types changed.
Prints what changed in the rich types and methods, then the gaps tests/test_spec_coverage.py
reports. Exit code: 0 nothing new, 10 the bundled spec was updated, 1 download or check failed.
Run: make spec-update
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from settings import Settings  # noqa: E402
from spec.diff import diff_specs  # noqa: E402
from spec.parse import check_spec_shape, version_tuple  # noqa: E402

BUNDLED = ROOT / "app" / "bundled" / "api.json"
UPDATED = 10


def main() -> int:
    url = os.environ.get("SPEC_URL") or Settings().spec_url
    try:
        r = httpx.get(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        new = r.json()
    except (httpx.HTTPError, ValueError) as e:
        print(f"не удалось скачать спецификацию: {e}")
        return 1
    problem = check_spec_shape(new)
    if problem:
        print(f"спецификация отклонена: {problem}")
        return 1
    old = json.loads(BUNDLED.read_text("utf-8"))
    if version_tuple(new.get("version")) < version_tuple(old.get("version")):
        print(f"скачана более старая версия {new.get('version')}, в репозитории {old.get('version')}")
        return 1
    diff = diff_specs(old, new)
    if new.get("version") == old.get("version") and not diff:
        print(f"{old.get('version')}: нового нет")
        return 0
    BUNDLED.write_bytes(r.content)
    print(f"{old.get('version')} → {new.get('version')} ({new.get('release_date')}), {new.get('changelog')}")
    for item in diff:
        print(f"- {item['kind']}: {item['name']}")
    print("\nЧего не хватает редактору (tests/test_spec_coverage.py):")
    coverage_test = ["-q", "--no-header", "--tb=line", "-p", "no:cacheprovider", "tests/test_spec_coverage.py"]
    cov = subprocess.run(  # noqa: S603 - fixed arguments, no user input
        [sys.executable, "-m", "pytest", *coverage_test],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    print(cov.stdout[-4000:])
    return UPDATED


if __name__ == "__main__":
    sys.exit(main())
