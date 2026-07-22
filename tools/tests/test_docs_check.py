from __future__ import annotations

from pathlib import Path

import pytest

from tools.docs_check import DocsCheckError, parse_structured_blocks, validate_repository_docs


ROOT = Path(__file__).resolve().parents[2]


def test_repository_structured_docs_match_authoritative_sources() -> None:
    result = validate_repository_docs(ROOT)

    assert result.hardware_status == "NOT RUN"
    assert result.workbook_sha256 == "bb58fecc63a50f4cdc0795d7937855e7b24d9bd4ba4c1377a798e1473e1458dc"
    assert result.checked_blocks == 8


def test_parser_rejects_duplicate_named_blocks(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.md"
    path.write_text(
        """<!-- petcare-docs:sample -->
```json
{"value": 1}
```
<!-- petcare-docs:sample -->
```json
{"value": 2}
```
""",
        encoding="utf-8",
    )

    with pytest.raises(DocsCheckError, match="duplicate structured block"):
        parse_structured_blocks(path)


def test_parser_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "invalid.md"
    path.write_text(
        """<!-- petcare-docs:sample -->
```json
{"value": }
```
""",
        encoding="utf-8",
    )

    with pytest.raises(DocsCheckError, match="invalid JSON"):
        parse_structured_blocks(path)
