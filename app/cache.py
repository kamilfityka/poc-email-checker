"""
Cache wynikow DNS/MX per-domena.

Domyslnie: cachetools.TTLCache w pamieci procesu (§3, §11) - zero zaleznosci
sieciowych, wystarcza do PoC. Interfejs jest celowo prosty (get/set), zeby
w razie potrzeby podmienic backend na Redis bez zmian w logice walidacji.
"""
from typing import Any, Optional
from cachetools import TTLCache

from . import config

_dns_cache: TTLCache = TTLCache(maxsize=config.CACHE_MAXSIZE, ttl=config.CACHE_TTL_S)


def get(domain: str) -> Optional[dict]:
    """Zwraca zbuforowany wynik DNS/MX dla domeny albo None."""
    return _dns_cache.get(domain)


def set(domain: str, value: dict) -> None:
    _dns_cache[domain] = value


def clear() -> None:
    _dns_cache.clear()


def stats() -> dict[str, Any]:
    return {
        "backend": "memory_ttlcache",
        "size": len(_dns_cache),
        "maxsize": _dns_cache.maxsize,
        "ttl_s": _dns_cache.ttl,
    }
