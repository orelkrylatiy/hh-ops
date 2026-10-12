"""Tests for hh_applicant_tool.utils.misc module."""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from hh_applicant_tool.utils.misc import load_prompt, calc_hash


class TestCalcHash:
    """Tests for calc_hash function."""

    def test_calc_hash_returns_hex_string(self):
        """Hash should be a 64-character hex string (SHA-256)."""
        result = calc_hash("test")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_calc_hash_deterministic(self):
        """Same input should produce same hash."""
        assert calc_hash("test") == calc_hash("test")

    def test_calc_hash_different_inputs(self):
        """Different inputs should produce different hashes."""
        assert calc_hash("test1") != calc_hash("test2")


class TestLoadPrompt:
    """Tests for load_prompt function."""

    def test_load_prompt_none_returns_none(self):
        """None input should return None."""
        assert load_prompt(None) is None

    def test_load_prompt_empty_returns_empty(self):
        """Empty string should return empty string."""
        assert load_prompt("") == ""

    def test_load_prompt_inline_text(self):
        """Inline text (no file path) should be returned as-is."""
        prompt = "This is an inline prompt"
        assert load_prompt(prompt) == prompt

    def test_load_prompt_at_syntax_reads_file(self, tmp_path: Path):
        """@path syntax should read file contents."""
        test_file = tmp_path / "prompt.txt"
        test_file.write_text("file content", encoding="utf-8")
        result = load_prompt(f"@{test_file}")
        assert result == "file content"

    def test_load_prompt_at_syntax_missing_file_raises(self):
        """@path with missing file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Prompt file not found"):
            load_prompt("@/nonexistent/path/prompt.txt")

    def test_load_prompt_at_syntax_directory_raises(self, tmp_path: Path):
        """@path pointing to directory should raise ValueError."""
        test_dir = tmp_path / "subdir"
        test_dir.mkdir()
        with pytest.raises(ValueError, match="Prompt path is not a file"):
            load_prompt(f"@{test_dir}")

    def test_load_prompt_existing_file_reads_content(self, tmp_path: Path):
        """Path to existing file should read its contents."""
        test_file = tmp_path / "prompt.txt"
        test_file.write_text("file content", encoding="utf-8")
        result = load_prompt(str(test_file))
        assert result == "file content"

    def test_load_prompt_existing_file_strips_whitespace(self, tmp_path: Path):
        """File content should be stripped of leading/trailing whitespace."""
        test_file = tmp_path / "prompt.txt"
        test_file.write_text("  \n  file content  \n  ", encoding="utf-8")
        result = load_prompt(str(test_file))
        assert result == "file content"

    def test_load_prompt_directory_raises(self, tmp_path: Path):
        """Path to directory should raise ValueError."""
        test_dir = tmp_path / "subdir"
        test_dir.mkdir()
        with pytest.raises(ValueError, match="Prompt path is not a file"):
            load_prompt(str(test_dir))

    def test_load_prompt_nonexistent_path_returns_as_is(self):
        """Non-existent path (not starting with @) should be returned as-is."""
        prompt = "prompts/nonexistent.txt"
        result = load_prompt(prompt)
        assert result == prompt

    def test_load_prompt_expands_user_path(self, tmp_path: Path, monkeypatch):
        """Path with ~ should be expanded to home directory."""
        # Mock home directory
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        test_file = tmp_path / "prompt.txt"
        test_file.write_text("home file content", encoding="utf-8")
        result = load_prompt("~/prompt.txt")
        assert result == "home file content"

    def test_load_prompt_at_syntax_expands_user_path(self, tmp_path: Path, monkeypatch):
        """@path with ~ should be expanded to home directory."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        test_file = tmp_path / "prompt.txt"
        test_file.write_text("home file content", encoding="utf-8")
        result = load_prompt("@~/prompt.txt")
        assert result == "home file content"
