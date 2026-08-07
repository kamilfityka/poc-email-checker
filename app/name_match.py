"""
Zgodnosc imienia i nazwiska z adresem e-mail (warstwa deterministyczna).

Cel: wykryc rozjazd miedzy wpisanym "Imie Nazwisko" a czescia lokalna adresu
(np. name="Kamil Ftyka" vs email="kamil.fityka@..." -> nazwisko ma literowke).

Zasady (spojne z §6): to sygnal POMOCNICZY, nie blokujacy. Zwracamy status i,
gdy sie da, sugestie poprawionego imienia/nazwiska. Brak danych -> "unknown".

Bez zaleznosci zewnetrznych. Opcjonalne dopracowanie modelem AI robi ai_client.py.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

from . import config

# Wartosci statusu: match | partial | mismatch | unknown

_PL_MAP = str.maketrans({"ł": "l", "Ł": "l"})


def _strip_diacritics(s: str) -> str:
    s = (s or "").translate(_PL_MAP)
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm(s: str) -> str:
    """Do porownan: bez znakow diakrytycznych, tylko [a-z0-9]."""
    return re.sub(r"[^a-z0-9]", "", _strip_diacritics(s).lower())


def _tokens(s: str) -> list[str]:
    """Rozbicie na tokeny wg separatorow i granicy litera/cyfra."""
    base = _strip_diacritics(s).lower()
    return [t for t in re.split(r"[^a-z0-9]+", base) if t]


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _best_local_match(name_tok: str, local_norm: str, local_tokens: list[str]) -> tuple[float, Optional[str]]:
    """
    Zwraca (najlepsze_podobienstwo, dopasowany_token_lokalny_lub_None).
    Uwzglednia: exact-substring w sklejonej czesci lokalnej oraz fuzzy po tokenach.
    """
    # Exact: token imienia zawiera sie w sklejonej czesci lokalnej (np. inicjaly/sklejenia).
    if len(name_tok) >= 3 and name_tok in local_norm:
        return 1.0, name_tok

    best_ratio, best_tok = 0.0, None
    for lt in local_tokens:
        r = _ratio(name_tok, lt)
        if r > best_ratio:
            best_ratio, best_tok = r, lt
    return best_ratio, best_tok


def evaluate(name: str, email: str) -> dict:
    """
    Zwraca dict:
      {
        "status": "match"|"partial"|"mismatch"|"unknown",
        "suggestion": <poprawione 'Imie Nazwisko' albo None>,
        "source": "heuristic",
      }
    """
    result = {"status": "unknown", "suggestion": None, "source": "heuristic"}

    local = (email or "").split("@")[0]
    name_tokens = _tokens(name)
    local_norm = _norm(local)
    local_tokens = _tokens(local)

    # Za malo danych, zeby cokolwiek orzec.
    if not name_tokens or not local_norm:
        return result
    # Bardzo krotka czesc lokalna (np. "jk") - nie oceniamy zgodnosci.
    if len(local_norm) < 3:
        return result

    min_ratio = config.NAME_MATCH_MIN_RATIO
    matched = 0            # tokeny trafione (dokladnie lub bardzo blisko)
    typos = 0              # trafione, ale z literowka -> daja sugestie
    corrected_tokens: list[str] = []  # zbudowanie sugestii pelnego imienia/nazwiska

    for nt in name_tokens:
        if len(nt) < 2:
            corrected_tokens.append(nt)
            continue
        ratio, local_tok = _best_local_match(nt, local_norm, local_tokens)

        if ratio >= 0.999:
            matched += 1
            corrected_tokens.append(nt)
        elif ratio >= min_ratio and local_tok:
            # "Blisko" -> prawdopodobna literowka we wpisanym imieniu/nazwisku.
            matched += 1
            typos += 1
            corrected_tokens.append(local_tok)
        else:
            corrected_tokens.append(nt)

    significant = [t for t in name_tokens if len(t) >= 2] or name_tokens
    coverage = matched / len(significant)

    if coverage >= 0.999:
        result["status"] = "partial" if typos else "match"
    elif coverage > 0:
        result["status"] = "partial"
    else:
        result["status"] = "mismatch"

    # Sugestia tylko wtedy, gdy poprawa faktycznie zmienia tekst.
    if typos:
        suggestion = " ".join(t.capitalize() for t in corrected_tokens)
        if _norm(suggestion) != _norm(name):
            result["suggestion"] = suggestion

    return result
