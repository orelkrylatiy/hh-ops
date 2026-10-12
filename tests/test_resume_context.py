from pathlib import Path

from hh_applicant_tool.utils.resume_context import load_resume_context


def test_resume_context_prefers_profile_override(tmp_path: Path) -> None:
    default = tmp_path / "resumes" / "context" / "default"
    account = tmp_path / "resumes" / "context" / "account2"
    default.mkdir(parents=True)
    account.mkdir(parents=True)
    (default / "primary.md").write_text("default", encoding="utf-8")
    (account / "primary.md").write_text("account-specific", encoding="utf-8")

    assert load_resume_context(alias="primary", profile_id="account2", root=tmp_path) == (
        "account-specific"
    )


def test_resume_context_resolves_alias_from_resume_id(tmp_path: Path) -> None:
    context_dir = tmp_path / "resumes" / "context" / "default"
    context_dir.mkdir(parents=True)
    (context_dir / "ai-engineer.md").write_text("AI profile", encoding="utf-8")

    assert (
        load_resume_context(
            config={"resume_aliases": {"ai-engineer": "resume-2"}},
            resume_id="resume-2",
            root=tmp_path,
        )
        == "AI profile"
    )
