"""
Szablony double opt-in: tresc maila (HTML + tekst) oraz strony potwierdzenia w przegladarce.
Neutralne, brandowane przez konfiguracje (VERIFY_COMPANY_NAME, VERIFY_LOGO_URL).
Bez zaleznosci od silnika szablonow - zwykle f-stringi (bezpieczne, bo dane
wstawiane sa kontrolowane: nazwa firmy z env, link generowany po naszej stronie).
"""
from html import escape

from . import config


def _logo_html() -> str:
    if config.VERIFY_LOGO_URL:
        return (f'<img src="{escape(config.VERIFY_LOGO_URL)}" alt="'
                f'{escape(config.VERIFY_COMPANY_NAME)}" '
                f'style="max-height:40px;margin-bottom:16px">')
    return (f'<div style="font-size:18px;font-weight:700;color:#16202b;'
            f'margin-bottom:16px">{escape(config.VERIFY_COMPANY_NAME)}</div>')


# --- MAIL --------------------------------------------------------------------
def email_plain(link: str) -> str:
    return (
        "Dzien dobry,\n\n"
        f"prosimy o potwierdzenie adresu e-mail. Kliknij ponizszy link:\n{link}\n\n"
        f"Link jest wazny {config.VERIFY_TTL_S // 3600} godzin. "
        "Jesli to nie Ty, zignoruj te wiadomosc.\n\n"
        f"{config.VERIFY_COMPANY_NAME}\n"
    )


def email_html(link: str) -> str:
    hours = config.VERIFY_TTL_S // 3600
    return f"""\
<!DOCTYPE html>
<html lang="pl"><head><meta charset="utf-8"></head>
<body style="margin:0;background:#eef2f6;font-family:Arial,Helvetica,sans-serif;color:#16202b">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0">
    <tr><td align="center">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0"
             style="background:#fff;border:1px solid #dbe3ea;border-radius:12px;padding:32px">
        <tr><td>
          {_logo_html()}
          <h1 style="font-size:20px;margin:0 0 12px">Potwierd&#378; sw&oacute;j adres e-mail</h1>
          <p style="font-size:14px;line-height:1.6;color:#5b6b7a;margin:0 0 24px">
            Aby doko&#324;czy&#263;, kliknij przycisk poni&#380;ej. Link jest wa&#380;ny {hours} godzin.
          </p>
          <a href="{escape(link)}"
             style="display:inline-block;background:#2f6f8f;color:#fff;text-decoration:none;
                    padding:12px 24px;border-radius:8px;font-size:14px;font-weight:600">
            Potwierd&#378; adres
          </a>
          <p style="font-size:12px;color:#9aa7b2;margin:24px 0 0;word-break:break-all">
            Je&#347;li przycisk nie dzia&#322;a, skopiuj link:<br>
            <a href="{escape(link)}" style="color:#2f6f8f">{escape(link)}</a>
          </p>
          <p style="font-size:12px;color:#9aa7b2;margin:16px 0 0">
            Je&#347;li to nie Ty prosi&#322;e&#347;/a&#347; o potwierdzenie, zignoruj t&#281; wiadomo&#347;&#263;.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


# --- STRONY W PRZEGLADARCE (po kliknieciu linku) -----------------------------
def _page(title: str, heading: str, body_html: str, color: str) -> str:
    return f"""\
<!DOCTYPE html>
<html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin:0;background:#eef2f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#16202b">
  <div style="max-width:440px;margin:64px auto;padding:0 20px">
    <div style="background:#fff;border:1px solid #dbe3ea;border-radius:12px;padding:36px;text-align:center">
      {_logo_html()}
      <div style="font-size:40px;line-height:1;margin:8px 0 16px;color:{color}">
        {'&#10003;' if color == '#1c7a45' else '&#9888;'}
      </div>
      <h1 style="font-size:20px;margin:0 0 10px">{escape(heading)}</h1>
      <div style="font-size:14px;line-height:1.6;color:#5b6b7a">{body_html}</div>
    </div>
    <p style="text-align:center;font-size:12px;color:#9aa7b2;margin-top:16px">
      {escape(config.VERIFY_COMPANY_NAME)}
    </p>
  </div>
</body></html>"""


def page_confirmed(email: str) -> str:
    return _page(
        "Adres potwierdzony", "Adres potwierdzony",
        f"Dzi&#281;kujemy. Adres <strong>{escape(email)}</strong> zosta&#322; potwierdzony.",
        "#1c7a45",
    )


def page_already(email: str) -> str:
    return _page(
        "Adres ju&#380; potwierdzony", "Adres by&#322; ju&#380; potwierdzony",
        f"Adres <strong>{escape(email)}</strong> zosta&#322; potwierdzony wcze&#347;niej. "
        "Nie musisz nic robi&#263;.",
        "#1c7a45",
    )


def page_invalid() -> str:
    return _page(
        "Link niewa&#380;ny", "Link niewa&#380;ny lub wygas&#322;",
        "Ten link potwierdzaj&#261;cy jest nieprawid&#322;owy albo up&#322;yn&#261;&#322; jego termin wa&#380;no&#347;ci. "
        "Popro&#347; o nowy link potwierdzaj&#261;cy.",
        "#b03636",
    )
