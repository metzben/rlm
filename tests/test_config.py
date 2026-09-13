"""Tests for the simple .env configuration loader."""

import os
from pathlib import Path

import pytest

from config import Config


def _env_file(tmp_path: Path, contents: str) -> Path:
    envfile = tmp_path / ".env"
    envfile.write_text(contents, encoding="utf-8")
    return envfile


def test_loads_values_and_exposes_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("REGISTRY", raising=False)
    monkeypatch.delenv("CONFIG_TEST_PORT", raising=False)
    envfile = _env_file(
        tmp_path,
        "REGISTRY=example.io\nCONFIG_TEST_PORT=8000\n",
    )

    config = Config(envfile)

    assert config.registry == "example.io"
    assert os.environ["CONFIG_TEST_PORT"] == "8000"


def test_ignores_blank_lines_comments_and_malformed_lines(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("CONFIG_TEST_HOST", raising=False)
    envfile = _env_file(
        tmp_path,
        "\n# comment\nNOT_AN_ASSIGNMENT\nCONFIG_TEST_HOST=localhost\n",
    )

    Config(envfile)

    assert os.environ["CONFIG_TEST_HOST"] == "localhost"
    assert "NOT_AN_ASSIGNMENT" not in os.environ


def test_strips_whitespace_from_keys_and_values(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFIG_TEST_NAME", raising=False)
    envfile = _env_file(tmp_path, "  CONFIG_TEST_NAME = Benjamin  \n")

    Config(envfile)

    assert os.environ["CONFIG_TEST_NAME"] == "Benjamin"


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        ("CONFIG_TEST_MESSAGE='Hello world'\n", "Hello world"),
        ('CONFIG_TEST_MESSAGE="Hello world"\n', "Hello world"),
    ],
)
def test_removes_matching_surrounding_quotes(
    tmp_path,
    monkeypatch,
    contents,
    expected,
):
    monkeypatch.delenv("CONFIG_TEST_MESSAGE", raising=False)
    envfile = _env_file(tmp_path, contents)

    Config(envfile)

    assert os.environ["CONFIG_TEST_MESSAGE"] == expected


def test_preserves_equals_signs_inside_values(tmp_path, monkeypatch):
    monkeypatch.delenv("CONFIG_TEST_URL", raising=False)
    envfile = _env_file(
        tmp_path,
        "CONFIG_TEST_URL=https://example.com?a=1&b=2\n",
    )

    Config(envfile)

    assert os.environ["CONFIG_TEST_URL"] == (
        "https://example.com?a=1&b=2"
    )


def test_does_not_overwrite_existing_environment_value(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CONFIG_TEST_SOURCE", "environment")
    envfile = _env_file(tmp_path, "CONFIG_TEST_SOURCE=env-file\n")

    Config(envfile)

    assert os.environ["CONFIG_TEST_SOURCE"] == "environment"


@pytest.mark.parametrize(
    "line",
    [
        "CONFIG_TEST_EMPTY=\n",
        'CONFIG_TEST_EMPTY=""\n',
        "CONFIG_TEST_EMPTY=''\n",
    ],
)
def test_ignores_empty_values(tmp_path, monkeypatch, line):
    monkeypatch.delenv("CONFIG_TEST_EMPTY", raising=False)
    envfile = _env_file(tmp_path, line)

    Config(envfile)

    assert "CONFIG_TEST_EMPTY" not in os.environ


@pytest.mark.parametrize(
    "line",
    [
        'CONFIG_TEST_BROKEN="Hello\n',
        "CONFIG_TEST_BROKEN='Hello\n",
        'CONFIG_TEST_BROKEN="Hello\'\n',
    ],
)
def test_ignores_values_with_unmatched_quotes(tmp_path, monkeypatch, line):
    monkeypatch.delenv("CONFIG_TEST_BROKEN", raising=False)
    envfile = _env_file(tmp_path, line)

    Config(envfile)

    assert "CONFIG_TEST_BROKEN" not in os.environ


def test_missing_env_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        Config(tmp_path / "missing.env")
