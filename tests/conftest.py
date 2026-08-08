"""
Wspolne fixture dla testow.

Runtime-przelaczniki (app/runtime.py) sa utrwalane w pliku i wspoldzielone
miedzy modulami (crm/ai/smtp), wiec izolujemy je per-test: czyscimy nadpisania
i kierujemy zapis do pliku tymczasowego, by testy nie dotykaly repo ani siebie.
"""
import pytest

from app import config, runtime


@pytest.fixture(autouse=True)
def _isolate_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNTIME_SETTINGS_PATH", str(tmp_path / "runtime_settings.json"))
    runtime.reset()
    yield
    runtime.reset()
