#!/usr/bin/env python3
"""
Sonda SMTP (OPCJONALNA, OFFLINE, poza rdzeniem PoC).

Niezależny skrypt uruchamiany z crona na LIŚCIE adresów (§5, §14 rozsz. #4).
NIE jest częścią serwisu real-time i NIE wolno go wołać z formularza.

Dla każdego adresu:
  1. Ustala MX domeny.
  2. Łączy się z serwerem MX i wykonuje HELO/EHLO -> MAIL FROM -> RCPT TO:<adres>
     BEZ komendy DATA (nie wysyła żadnej treści).
  3. Klasyfikuje wynik:
       250            -> "tak"            (skrzynka prawdopodobnie akceptowana)
       550/551/553    -> "nie"            (odrzucona)
       4xx            -> "niejednoznacznie" (greylisting / spróbuj później)
       błąd/timeout   -> "niejednoznacznie"
  4. Wykrywa CATCH-ALL: sonduje losowy nieistniejący adres w tej samej domenie;
     jeśli i on daje 250 -> domena akceptuje wszystko -> wynik dla realnego
     adresu = "niejednoznacznie" (nie da się potwierdzić).

Wynik dopisywany do CSV jako flaga "skrzynka_zweryfikowana": tak / nie / niejednoznacznie.

RYZYKA (§5) — świadoma decyzja przed użyciem:
  - Duzi dostawcy (Google/Microsoft/Yahoo) często zwracają 250 na wszystko albo
    nie ujawniają istnienia skrzynki -> sonda nic nie wnosi.
  - Regularne sondowanie z naszego IP grozi wpisaniem na czarne listy — tego
    samego IP używamy do realnej korespondencji. Realne ryzyko operacyjne.
  - Port 25 wyjściowo bywa zablokowany (ISP/chmura) — skrypt zwróci wtedy
    "niejednoznacznie" dla wszystkiego (nie crashuje).

Użycie:
  python3 smtp_probe.py --in adresy.csv --out wynik.csv --helo poczta.firma.pl \\
      --mail-from weryfikacja@firma.pl --delay 3 --max-per-domain 20
  python3 smtp_probe.py --in adresy.csv --dry-run    # bez łączenia, tylko MX

Kolumna z adresem w wejściowym CSV: domyślnie "email" (albo pierwsza kolumna).
"""
import argparse
import csv
import random
import smtplib
import socket
import string
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

try:
    import dns.resolver
except ImportError:
    print("Brak dnspython. Zainstaluj: pip install dnspython", file=sys.stderr)
    sys.exit(2)


RESULT_YES = "tak"
RESULT_NO = "nie"
RESULT_UNSURE = "niejednoznacznie"


@dataclass
class ProbeConfig:
    helo: str = "localhost"
    mail_from: str = "verify@localhost"
    connect_timeout: float = 10.0
    delay_s: float = 3.0            # przerwa między adresami (grzeczność, §5)
    max_per_domain: int = 50        # limit sond na domenę na uruchomienie
    catch_all_check: bool = True
    dry_run: bool = False
    verbose: bool = False


@dataclass
class Stats:
    total: int = 0
    yes: int = 0
    no: int = 0
    unsure: int = 0
    by_reason: dict = field(default_factory=lambda: defaultdict(int))


def log(cfg: ProbeConfig, *args):
    if cfg.verbose:
        print(*args, file=sys.stderr)


def resolve_mx(domain: str, timeout: float = 5.0) -> list[str]:
    """Zwraca listę hostów MX posortowaną wg priorytetu; fallback na A (RFC)."""
    r = dns.resolver.Resolver()
    r.timeout = timeout
    r.lifetime = timeout + 1
    try:
        answers = r.resolve(domain, "MX")
        mx = sorted(((a.preference, str(a.exchange).rstrip(".")) for a in answers),
                    key=lambda x: x[0])
        hosts = [h for _, h in mx if h]
        if hosts:
            return hosts
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN,
            dns.resolver.NoNameservers, dns.exception.DNSException):
        pass
    # fallback: brak MX, ale domena może mieć A i przyjmować pocztę
    try:
        r.resolve(domain, "A")
        return [domain]
    except dns.exception.DNSException:
        return []


def _random_localpart(n: int = 16) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _probe_rcpt(server: smtplib.SMTP, mail_from: str, rcpt: str) -> tuple[int, str]:
    """MAIL FROM + RCPT TO bez DATA. Zwraca (kod, komunikat)."""
    server.mail(mail_from)
    code, msg = server.rcpt(rcpt)
    return code, msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg)


def probe_address(email: str, mx_hosts: list[str], cfg: ProbeConfig) -> dict:
    """
    Sonduje pojedynczy adres. Zwraca dict z wynikiem i uzasadnieniem.
    Nigdy nie rzuca wyjątku — błędy mapuje na 'niejednoznacznie'.
    """
    domain = email.split("@", 1)[1].lower()
    out = {"email": email, "skrzynka_zweryfikowana": RESULT_UNSURE,
           "kod_smtp": "", "catch_all": "", "mx": mx_hosts[0] if mx_hosts else "",
           "szczegol": ""}

    if not mx_hosts:
        out["szczegol"] = "brak MX/A — domena nie obsługuje poczty"
        out["skrzynka_zweryfikowana"] = RESULT_NO
        return out

    last_err = ""
    for host in mx_hosts:
        try:
            server = smtplib.SMTP(timeout=cfg.connect_timeout)
            server.connect(host, 25)
            server.ehlo_or_helo_if_needed()
            # jawnie EHLO/HELO naszą nazwą
            try:
                server.ehlo(cfg.helo)
            except smtplib.SMTPException:
                server.helo(cfg.helo)

            code, msg = _probe_rcpt(server, cfg.mail_from, email)
            out["kod_smtp"] = str(code)

            # Catch-all: sonda losowego nieistniejącego adresu
            if cfg.catch_all_check and code == 250:
                rnd = f"{_random_localpart()}@{domain}"
                try:
                    ca_code, _ = _probe_rcpt(server, cfg.mail_from, rnd)
                except smtplib.SMTPException:
                    ca_code = 0
                if ca_code == 250:
                    out["catch_all"] = "tak"
                    out["skrzynka_zweryfikowana"] = RESULT_UNSURE
                    out["szczegol"] = "domena catch-all (akceptuje każdy adres)"
                    server.quit()
                    return out
                out["catch_all"] = "nie"

            try:
                server.quit()
            except smtplib.SMTPException:
                pass

            # Klasyfikacja kodu głównego
            if code == 250:
                out["skrzynka_zweryfikowana"] = RESULT_YES
                out["szczegol"] = "RCPT 250 (akceptacja)"
            elif code in (550, 551, 553, 501):
                out["skrzynka_zweryfikowana"] = RESULT_NO
                out["szczegol"] = f"RCPT {code} (odrzucenie)"
            elif 400 <= code < 500:
                out["skrzynka_zweryfikowana"] = RESULT_UNSURE
                out["szczegol"] = f"RCPT {code} (greylisting/tymczasowe)"
            else:
                out["skrzynka_zweryfikowana"] = RESULT_UNSURE
                out["szczegol"] = f"RCPT {code} (nieokreślone)"
            return out

        except (socket.timeout, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected,
                ConnectionRefusedError, OSError, smtplib.SMTPException) as e:
            last_err = f"{type(e).__name__}: {str(e)[:80]}"
            continue  # spróbuj następny MX

    out["skrzynka_zweryfikowana"] = RESULT_UNSURE
    out["szczegol"] = f"brak połączenia (port 25?) — {last_err}"
    return out


def run(in_path: str, out_path: Optional[str], cfg: ProbeConfig) -> Stats:
    stats = Stats()
    per_domain = defaultdict(int)

    with open(in_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        col = "email" if "email" in fieldnames else (fieldnames[0] if fieldnames else None)
        if not col:
            print("Pusty/niepoprawny CSV wejściowy.", file=sys.stderr)
            return stats
        rows = list(reader)

    out_fields = ["email", "skrzynka_zweryfikowana", "kod_smtp", "catch_all", "mx", "szczegol"]
    writer = None
    out_f = None
    if out_path:
        out_f = open(out_path, "w", newline="", encoding="utf-8")
        writer = csv.DictWriter(out_f, fieldnames=out_fields)
        writer.writeheader()

    mx_cache: dict[str, list[str]] = {}

    for i, row in enumerate(rows):
        email = (row.get(col) or "").strip()
        if not email or "@" not in email:
            continue
        stats.total += 1
        domain = email.split("@", 1)[1].lower()

        if per_domain[domain] >= cfg.max_per_domain:
            res = {"email": email, "skrzynka_zweryfikowana": RESULT_UNSURE,
                   "kod_smtp": "", "catch_all": "", "mx": "",
                   "szczegol": f"pominięto — limit {cfg.max_per_domain}/domena"}
            _tally(stats, res)
            if writer: writer.writerow(res)
            continue
        per_domain[domain] += 1

        if domain not in mx_cache:
            mx_cache[domain] = resolve_mx(domain)
        mx_hosts = mx_cache[domain]

        if cfg.dry_run:
            res = {"email": email,
                   "skrzynka_zweryfikowana": RESULT_UNSURE if mx_hosts else RESULT_NO,
                   "kod_smtp": "", "catch_all": "",
                   "mx": mx_hosts[0] if mx_hosts else "",
                   "szczegol": "dry-run (bez łączenia SMTP)"}
        else:
            res = probe_address(email, mx_hosts, cfg)
            if i < len(rows) - 1 and cfg.delay_s > 0:
                time.sleep(cfg.delay_s)  # grzeczność wobec serwera / reputacja IP

        log(cfg, f"[{i+1}/{len(rows)}] {email} -> {res['skrzynka_zweryfikowana']} "
                 f"({res['szczegol']})")
        _tally(stats, res)
        if writer:
            writer.writerow(res)

    if out_f:
        out_f.close()
    return stats


def _tally(stats: Stats, res: dict):
    r = res["skrzynka_zweryfikowana"]
    if r == RESULT_YES: stats.yes += 1
    elif r == RESULT_NO: stats.no += 1
    else: stats.unsure += 1
    stats.by_reason[res["szczegol"].split(" —")[0].split(" (")[0]] += 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Offline sonda SMTP (poza real-time).")
    ap.add_argument("--in", dest="in_path", required=True, help="CSV wejściowy (kolumna 'email')")
    ap.add_argument("--out", dest="out_path", default=None, help="CSV wynikowy")
    ap.add_argument("--helo", default="localhost", help="nazwa HELO/EHLO (FQDN naszego hosta)")
    ap.add_argument("--mail-from", default="verify@localhost", help="adres MAIL FROM")
    ap.add_argument("--delay", type=float, default=3.0, help="przerwa [s] między adresami")
    ap.add_argument("--max-per-domain", type=int, default=50, help="limit sond na domenę")
    ap.add_argument("--timeout", type=float, default=10.0, help="timeout połączenia [s]")
    ap.add_argument("--no-catch-all", action="store_true", help="wyłącz wykrywanie catch-all")
    ap.add_argument("--dry-run", action="store_true", help="nie łącz się (tylko MX)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    cfg = ProbeConfig(
        helo=args.helo, mail_from=args.mail_from, connect_timeout=args.timeout,
        delay_s=args.delay, max_per_domain=args.max_per_domain,
        catch_all_check=not args.no_catch_all, dry_run=args.dry_run, verbose=args.verbose,
    )

    if not args.dry_run:
        print("UWAGA: sonda SMTP obciąża reputację IP i bywa zawodna (§5). "
              "Wynik traktuj jako 'niepewny'.", file=sys.stderr)

    stats = run(args.in_path, args.out_path, cfg)

    print("\n=== Podsumowanie ===")
    print(f"Sprawdzono adresów: {stats.total}")
    print(f"  tak:              {stats.yes}")
    print(f"  nie:              {stats.no}")
    print(f"  niejednoznacznie: {stats.unsure}")
    if stats.by_reason:
        print("Powody:")
        for reason, n in sorted(stats.by_reason.items(), key=lambda x: -x[1]):
            print(f"  {n:4}  {reason}")
    if args.out_path:
        print(f"\nWynik zapisany do: {args.out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
