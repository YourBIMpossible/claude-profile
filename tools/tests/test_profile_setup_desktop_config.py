"""plan_desktop_config on a Claude Desktop config whose mcpServers is not an object (2026-11-02 slop audit, SA02-12).

With `"mcpServers": null` (or a list) there is no object to replace, so the plan used to insert a second `mcpServers`
key ahead of the old one. A JSON reader keeps the last duplicate, so the old value stayed in effect: no server was
configured while bootstrap reported the file updated. The plan is now refused, as every other unusable config is.

Plans only, against a throwaway APPDATA: nothing is written and no real profile or Desktop config is read.

Run: python -m pytest tools/tests -q   (or directly: python tools/tests/test_profile_setup_desktop_config.py)
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import profile_setup as ps  # noqa: E402

NOT_AN_OBJECT = {"null": "null", "list": "[]", "string": '"off"', "number": "0", "false": "false"}


def plan(existing: str) -> "ps.DesktopPlan":
    with tempfile.TemporaryDirectory(prefix="desktop-config-test-") as tmp:
        appdata = Path(tmp)
        (appdata / "Claude").mkdir()
        (appdata / "Claude" / "claude_desktop_config.json").write_text(existing, encoding="utf-8")
        return ps.plan_desktop_config(appdata, REPO)


@pytest.mark.parametrize("label", sorted(NOT_AN_OBJECT))
def test_sa02_12_mcp_servers_that_is_not_an_object_is_refused_with_the_reason(label):
    existing = '{\n  "mcpServers": ' + NOT_AN_OBJECT[label] + ',\n  "preferences": {"a": 1}\n}\n'
    with pytest.raises(ps.Refuse) as refused:
        plan(existing)
    assert "mcpServers" in str(refused.value) and "not a JSON object" in str(refused.value), refused.value


def test_sa02_12_an_object_valued_mcp_servers_is_still_replaced_exactly_once():
    existing = '{\n  "mcpServers": {"old": {}},\n  "preferences": {"a": 1}\n}\n'
    planned = plan(existing)
    assert planned.changed and planned.text.count('"mcpServers"') == 1, planned.text
    merged = json.loads(planned.text)
    assert isinstance(merged["mcpServers"], dict) and "old" not in merged["mcpServers"], merged
    assert merged["preferences"] == {"a": 1}, merged


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
