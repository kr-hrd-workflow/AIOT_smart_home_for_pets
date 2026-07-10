# Pico Proposal Implementation Slice Code Review

Status: BLOCK
Recommendation: REQUEST_CHANGES

Scope reviewed: updated `tools/pico_contract_check.py` and demo/test changes only, per re-review request.

Skill-perspective check: `remove-ai-slops` and `programming` were loaded and applied. The previous string-concatenation and exact-assert-source blockers are addressed. The current blocker is runtime verification reliability.

## CRITICAL

None.

## HIGH

- `tools/pico_contract_check.py:23-30` runs `subprocess.run(..., text=True, stdout=PIPE, stderr=STDOUT)` without an explicit encoding. In this workspace, `python tools\pico_contract_check.py` fails before validation with `UnicodeDecodeError: 'cp949' codec can't decode byte 0xec...` while reading build output from a path containing Korean characters. The contract checker is therefore not a reliable verification gate on the target Windows workspace.

## MEDIUM

None.

## LOW

None.

## Evidence

- Line-numbered source inspection: `nl -ba tools/pico_contract_check.py | sed -n '1,260p'`
- Runtime check: `python tools\pico_contract_check.py` exited 1 with `UnicodeDecodeError` before contract validation.

## Blockers

- Decode build output explicitly, or capture bytes and decode with a deterministic encoding/error policy, so the checker can run in this Windows workspace and validate the runtime contract.
