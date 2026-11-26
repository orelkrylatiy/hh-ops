"""Tests for config utility module."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hh_applicant_tool.utils.config import Config, get_config_path


class TestGetConfigPath:
    """Test config path resolution for different OSs."""

    def test_windows_config_path(self):
        """Windows should use APPDATA environment variable."""
        with patch("platform.system", return_value="Windows"):
            with patch("os.getenv") as mock_getenv:
                mock_getenv.return_value = "C:\\Users\\TestUser\\AppData\\Roaming"
                # Clear cache to force recomputation
                get_config_path.cache_clear()
                path = get_config_path()

                assert "AppData" in str(path) or "Roaming" in str(path)

    def test_darwin_config_path(self):
        """macOS should use Library/Application Support."""
        with patch("platform.system", return_value="Darwin"):
            with patch("pathlib.Path.home", return_value=Path("/Users/testuser")):
                get_config_path.cache_clear()
                path = get_config_path()
                assert "Library" in str(path)

    def test_linux_config_path(self):
        """Linux should use XDG_CONFIG_HOME or ~/.config."""
        with patch("platform.system", return_value="Linux"):
            with patch("os.getenv", return_value=None):
                get_config_path.cache_clear()
                path = get_config_path()
                assert ".config" in str(path)

    def test_config_path_caching(self):
        """Config path should be cached (called only once)."""
        get_config_path.cache_clear()
        path1 = get_config_path()
        path2 = get_config_path()
        assert path1 == path2


class TestConfigBasics:
    """Test basic Config functionality."""

    def test_config_initialization_empty(self):
        """Config should initialize as empty dict if file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config = Config(config_file)

            assert len(config) == 0
            assert isinstance(config, dict)

    def test_config_load_existing_file(self):
        """Config should load from existing JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            test_data = {"token": "test123", "user_id": 42}

            # Write test config
            with config_file.open("w") as f:
                json.dump(test_data, f)

            # Load config
            config = Config(config_file)
            assert config["token"] == "test123"
            assert config["user_id"] == 42

    def test_config_getitem_returns_none_for_missing(self):
        """dict.get() is used, so missing keys should return None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config = Config(config_file)

            # Should return None, not raise KeyError
            assert config["nonexistent"] is None

    def test_config_save_creates_parent_dirs(self):
        """Config.save() should create parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "deep" / "nested" / "config.json"
            config = Config(config_file)

            config.save({"token": "test123"})

            # Parent directories should be created
            assert config_file.parent.exists()
            assert config_file.exists()

    def test_config_save_persists_data(self):
        """Data saved to config should exist in file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config = Config(config_file)

            config.save({"key": "value", "number": 123})

            # Read raw JSON to verify
            with config_file.open("r") as f:
                data = json.load(f)

            assert data["key"] == "value"
            assert data["number"] == 123

    def test_config_save_updates_dict(self):
        """save() should also update the dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config = Config(config_file)

            config.save({"key": "value"})

            assert config["key"] == "value"

    def test_config_save_merges_data(self):
        """Multiple save() calls should merge data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config = Config(config_file)

            config.save({"key1": "value1"})
            config.save({"key2": "value2"})

            assert config["key1"] == "value1"
            assert config["key2"] == "value2"

    def test_config_handles_invalid_json(self):
        """Config should raise JSONDecodeError for invalid JSON."""
        import json as stdlib_json
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            # Write invalid JSON
            config_file.write_text("not valid json")

            # Should raise JSONDecodeError
            with pytest.raises(stdlib_json.JSONDecodeError):
                config = Config(config_file)

    def test_config_repr(self):
        """Config repr should show config file path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config = Config(config_file)

            # repr should show the path
            repr_str = repr(config)
            assert str(config_file) in repr_str

    def test_config_thread_safety(self):
        """Config uses lock for thread-safe operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config = Config(config_file)

            # Lock should exist
            assert config._lock is not None

            # Should be able to acquire lock
            with config._lock:
                config["test"] = "value"

            assert config["test"] == "value"

    def test_config_save_sorting(self):
        """Config should save with sorted keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config = Config(config_file)

            config.save({"zebra": 1, "apple": 2, "banana": 3})

            with config_file.open("r") as f:
                content = f.read()

            # Check that keys appear in sorted order in JSON
            apple_pos = content.find("apple")
            banana_pos = content.find("banana")
            zebra_pos = content.find("zebra")

            assert apple_pos < banana_pos < zebra_pos

    def test_config_with_unicode(self):
        """Config should handle Unicode data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config = Config(config_file)

            test_data = {"name": "Привет", "emoji": "🚀"}
            config.save(test_data)

            config2 = Config(config_file)
            assert config2["name"] == "Привет"
            assert config2["emoji"] == "🚀"


def test_save_updates_memory_under_same_lock_as_file_write(tmp_path):
    config = Config(tmp_path / "config.json")
    original_update = config.update

    def checked_update(*args, **kwargs):
        assert config._lock.locked()
        original_update(*args, **kwargs)

    with patch.object(config, "update", side_effect=checked_update):
        config.save({"one": 1})
    assert Config(tmp_path / "config.json")["one"] == 1
