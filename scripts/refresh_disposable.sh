#!/usr/bin/env bash
# Cykliczny refresh listy domen jednorazowych (§11).
# Snapshot open-source disposable-email-domains do lokalnego pliku w repo.
# Uruchamiac np. z crona raz na tydzien. W runtime serwis czyta wylacznie
# lokalny plik - zero zapytan na zewnatrz podczas walidacji.
set -euo pipefail

DEST="$(cd "$(dirname "$0")/.." && pwd)/app/data/disposable_domains.txt"
URL="https://raw.githubusercontent.com/disposable-email-domains/disposable-email-domains/master/disposable_email_blocklist.conf"
TMP="$(mktemp)"

echo "Pobieram: $URL"
curl -sSL --max-time 30 "$URL" -o "$TMP"

LINES=$(wc -l < "$TMP")
if [ "$LINES" -lt 1000 ]; then
  echo "Blad: pobrano tylko $LINES linii - podejrzanie malo, przerywam." >&2
  rm -f "$TMP"
  exit 1
fi

mv "$TMP" "$DEST"
echo "OK. Zapisano $LINES domen do: $DEST"
echo "Uwaga: restart serwisu wczytuje nowa liste (ladowana na starcie)."
