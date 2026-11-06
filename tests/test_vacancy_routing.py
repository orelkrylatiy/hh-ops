import json

from hh_applicant_tool.utils.vacancy_routing import VacancyRouting


def test_routing_matches_required_and_excluded(tmp_path):
    config = tmp_path / "routing.json"
    config.write_text(
        json.dumps(
            {"profiles": {"rn": {"required_any": [r"react\s*native"], "excluded_any": ["backend"]}}}
        ),
        encoding="utf-8",
    )
    routing = VacancyRouting("rn", config, tmp_path / "ledger.sqlite")
    assert routing.matches({"name": "React Native Developer"})
    assert not routing.matches({"name": "React Native Backend Developer"})


def test_claim_is_cross_profile_exclusive(tmp_path):
    ledger = tmp_path / "ledger.sqlite"
    a = VacancyRouting("a", ledger_path=ledger)
    b = VacancyRouting("b", ledger_path=ledger)
    assert a.claim(12345)
    assert not b.claim(12345)


def test_confirmed_send_never_expires(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.sqlite"
    a = VacancyRouting("a", ledger_path=ledger)
    b = VacancyRouting("b", ledger_path=ledger)
    assert a.claim(12345)
    a.confirm(12345)
    monkeypatch.setenv("HH_VACANCY_CLAIM_TTL_SECONDS", "-1")
    assert not b.claim(12345)
