"""requirements.lock (what the image installs, with hashes) agrees with requirements.txt (what people edit)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;\\]+)", re.M)


def pins(path: Path) -> dict[str, str]:
    return {name.lower().replace("_", "-"): version for name, version in PIN.findall(path.read_text("utf-8"))}


def test_lock_has_every_pin_of_requirements_txt():
    locked = pins(ROOT / "requirements.lock")
    stale = {n: v for n, v in pins(ROOT / "requirements.txt").items() if locked.get(n) != v}
    assert not stale, f"requirements.lock отстал от requirements.txt ({stale}): make lock и закоммитьте его"


def test_every_locked_package_has_a_hash():
    text = (ROOT / "requirements.lock").read_text("utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text.split("\n", 2)[2])
    assert all("--hash=sha256:" in b for b in blocks if b.strip())
