"""Testy runtime-przelacznikow warstw (app/runtime.py)."""
from pathlib import Path

from app import runtime, config


def test_defaults_follow_env(monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    monkeypatch.setattr(config, "CRM_CHECK_ENABLED", True)
    runtime.reset()
    assert runtime.enabled("ai") is False
    assert runtime.enabled("crm") is True


def test_override_wins_over_env(monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    runtime.set_enabled("ai", True)
    assert runtime.enabled("ai") is True


def test_apply_subset_only_changes_given():
    runtime.set_enabled("crm", True)
    state = runtime.apply({"ai": True})   # nie ruszamy crm
    assert state["ai"] is True
    assert state["crm"] is True


def test_unknown_key_rejected():
    import pytest
    with pytest.raises(ValueError):
        runtime.set_enabled("foo", True)


def test_persisted_to_file_and_reloaded(monkeypatch):
    # Zapis tworzy plik; ponowny _load() go wczytuje (przetrwanie restartu).
    runtime.set_enabled("crm", True)
    assert Path(config.RUNTIME_SETTINGS_PATH).exists()
    runtime.reset()
    assert runtime.enabled("crm") is config.CRM_CHECK_ENABLED  # po reset bez load
    runtime._load()
    assert runtime.enabled("crm") is True


def test_states_has_all_keys():
    s = runtime.states()
    assert set(s.keys()) == {"ai", "crm"}
