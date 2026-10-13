import json
from pathlib import Path

import pytest

from scripts.build_private_els_check import build_local_page, public_quotes


def test_private_tool_uses_only_public_quotes_and_blocks_network(tmp_path):
    source = tmp_path / "els.json"
    source.write_text(json.dumps({"indices": [{"id": "spx", "label": "</script>", "sixMonthPriceSeries": [{"date": "2026-10-12", "close": 100}], "private": "not-exported"}]}))
    path = build_local_page(source, tmp_path / "private.html")
    text = path.read_text()
    assert "not-exported" not in text
    assert "connect-src 'none'" in text and "form-action 'none'" in text
    assert "localStorage" not in text and "fetch(" not in text
    assert "\\u003c/script>" in text
    assert "localOnly" in text
    assert "reports/private/" in (Path(__file__).resolve().parents[1] / ".gitignore").read_text()


def test_private_tool_refuses_missing_prices():
    with pytest.raises(ValueError, match="유효"):
        public_quotes({"indices": []})
