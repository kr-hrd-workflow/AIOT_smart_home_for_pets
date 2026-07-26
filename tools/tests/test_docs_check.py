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


def test_live30_activity_docs_are_observation_only_and_hardware_honest() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "demo-runbook.md").read_text(encoding="utf-8")
    privacy = (ROOT / "docs" / "privacy.md").read_text(encoding="utf-8")

    assert "Jetson 단기 실기기 라이브 게이트" in readme
    assert "60분 soak는 `NOT RUN`" in readme
    assert "Sites v5" in readme
    assert "source commit `33ffa873`" in readme
    assert "한 번의 큰 스크롤에 영상이 약 1~2초 전진" in readme
    assert "카메라 관측 공백은 활동 0초로 계산하지 않습니다." in runbook
    assert "`repetitive_motion`" in runbook
    assert "`repetitive_motion`" in privacy
    assert "의료 진단이 아닙니다." in privacy


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
