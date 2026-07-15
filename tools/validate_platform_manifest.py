"""Validate the sealed PetCare platform authority without accepting aliases."""

import argparse
import hashlib
import json
import sys
from pathlib import Path


EXPECTED_CANONICAL_SHA256 = "5589CDF6BDC6F08F1224C11D04153889FE117BA4056BC8FAECB8C9CAA9F52AD0"


def validate(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return ["manifest must be a JSON object"]
    canonical = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    actual = hashlib.sha256(canonical).hexdigest().upper()
    if actual != EXPECTED_CANONICAL_SHA256:
        return ["manifest does not match the sealed platform authority"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    errors = validate(args.manifest)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"valid platform manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
