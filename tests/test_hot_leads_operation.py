from __future__ import annotations

import argparse
from types import SimpleNamespace

from hh_applicant_tool.operations.hot_leads import Operation


def test_hot_leads_operation_prints_recent_events(tmp_path, capsys) -> None:
    db_path = tmp_path / "data"
    tool = SimpleNamespace(db_path=db_path)

    from hh_applicant_tool.automation.hot_lead_state import HotLeadStore

    store = HotLeadStore(db_path)
    store.record(
        chat_id="chat-1",
        message_id="msg-1",
        is_hot=True,
        confidence=0.96,
        human_likelihood="high",
        reason="Рекрутер предлагает созвон.",
        next_step="Согласовать время.",
        message_text="Давайте созвонимся завтра.",
        vacancy_name="AI Engineer",
        employer_name="Acme",
    )

    Operation().run(tool, argparse.Namespace(limit=10, json_output=False))

    output = capsys.readouterr().out
    assert "AI Engineer" in output
    assert "Acme" in output
    assert "96%" in output


def test_hot_leads_operation_supports_json(tmp_path, capsys) -> None:
    tool = SimpleNamespace(db_path=tmp_path / "data")

    Operation().run(tool, argparse.Namespace(limit=10, json_output=True))

    assert capsys.readouterr().out.strip() == "[]"
