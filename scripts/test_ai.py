#!/usr/bin/env python3
"""
Szybki test polaczenia z wlasnym modelem AI (warstwa 'ai').

Uzywa DOKLADNIE tego samego kontraktu, co aplikacja (app/ai_client.py:_call),
czyli POST {AI_BASE_URL}/chat/completions w standardzie OpenAI-compatible.
Czyta konfiguracje ze zmiennych srodowiskowych (AI_BASE_URL / AI_MODEL /
AI_API_KEY / AI_TIMEOUT_S) - te same, ktore w dockerze wstrzykuje env_file: .env.

Uruchomienie:
  # w kontenerze (zalecane - testuje to, co realnie widzi aplikacja):
  docker compose exec validator python scripts/test_ai.py

  # albo na hoscie (najpierw zaladuj .env do srodowiska):
  set -a; . ./.env; set +a
  python scripts/test_ai.py

Kod wyjscia: 0 = polaczenie OK, 1 = blad (powod wypisany na stderr).
"""
import sys
from pathlib import Path

# Pozwol uruchamiac skrypt bezposrednio (python scripts/test_ai.py) - dorzuc
# katalog glowny projektu do sciezki importu, aby pakiet 'app' byl widoczny.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, ai_client  # noqa: E402 - po modyfikacji sys.path


def main() -> int:
    print("=== Test polaczenia z modelem AI ===")
    print(f"AI_BASE_URL : {config.AI_BASE_URL or '(puste!)'}")
    print(f"AI_MODEL    : {config.AI_MODEL or '(puste!)'}")
    print(f"AI_API_KEY  : {'ustawiony (' + str(len(config.AI_API_KEY)) + ' zn.)' if config.AI_API_KEY else '(brak)'}")
    print(f"AI_TIMEOUT_S: {config.AI_TIMEOUT_S}")
    print("-" * 36)

    if not config.AI_BASE_URL or not config.AI_MODEL:
        print(
            "BLAD: brak AI_BASE_URL lub AI_MODEL w srodowisku.\n"
            "  - w dockerze: uzupelnij .env i odkomentowany env_file, potem `docker compose up -d`\n"
            "  - na hoscie:  `set -a; . ./.env; set +a` przed uruchomieniem",
            file=sys.stderr,
        )
        return 1

    # Wywolanie surowego klienta (omija flage AI_ENABLED - testujemy samo laczenie).
    try:
        content = ai_client._call("Jan Kowalski", "jan.kowalski@firma.pl")
    except Exception as exc:  # noqa: BLE001 - chcemy pokazac dowolny powod
        print(f"BLAD: polaczenie/wywolanie nieudane -> {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "Najczestsze przyczyny: zly host/port w AI_BASE_URL, brak sieci do LLM,\n"
            "zly AI_MODEL (404), zly AI_API_KEY (401/403), timeout (zwieksz AI_TIMEOUT_S).",
            file=sys.stderr,
        )
        return 1

    print("OK: model odpowiedzial. Surowa tresc odpowiedzi:")
    print(content)

    parsed = ai_client._extract_json(content or "")
    if parsed:
        print("\nSparsowany JSON (tak widzi to aplikacja):")
        print(parsed)
    else:
        print(
            "\nUWAGA: polaczenie dziala, ale nie udalo sie wyluskac JSON-a z odpowiedzi.\n"
            "Model laczy sie poprawnie; warto sprawdzic, czy zwraca zwarty JSON wg promptu."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
