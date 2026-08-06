/*
 * Widget walidacji e-mail (PoC v2) - wstrzykiwany do formularza CRM.
 *
 * Robi w przegladarce (0 ms sieci):
 *   skladnia (RFC-ish regex zgodny z HTML5 + domkniecia)
 *   literowki (Damerau-Levenshtein + slownik domen PL) => "Czy chodzilo o...?"
 * Po debounce ~300 ms / onblur wola POST /validate.
 * Renderuje 5 stanow UI (§7) i egzekwuje reguly blokowania (§8) na przycisku zapisu.
 *
 * Uzycie:
 *   EmailValidator.attach({
 *     input: document.querySelector('#email'),
 *     serviceUrl: 'http://localhost:8000',
 *     saveButton: document.querySelector('#save'),   // opcjonalnie
 *     statusEl: document.querySelector('#email-status') // opcjonalnie (auto-tworzone)
 *   });
 */
(function (global) {
  "use strict";

  // --- Slownik popularnych domen (strona klienta, dostosowany do PL) ----
  var POPULAR_DOMAINS = [
    "gmail.com", "googlemail.com", "wp.pl", "o2.pl", "interia.pl", "interia.eu",
    "onet.pl", "poczta.onet.pl", "op.pl", "vp.pl", "gazeta.pl", "outlook.com",
    "outlook.pl", "hotmail.com", "hotmail.pl", "live.com", "icloud.com", "me.com",
    "yahoo.com", "yahoo.pl", "proton.me", "protonmail.com", "pm.me", "tlen.pl",
    "neostrada.pl", "poczta.fm", "go2.pl"
  ];
  var TYPO_MAX_DISTANCE = 2;

  // --- skladnia -----------------------------------------------------------
  // Pragmatyczny wzorzec (zgodny z duchem HTML5), serwer i tak waliduje ponownie.
  var SYNTAX_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

  function checkSyntax(email) {
    if (!email) return false;
    if ((email.match(/@/g) || []).length !== 1) return false;
    if (/\.\./.test(email)) return false;
    if (/^\.|\.$/.test(email.split("@")[0])) return false;
    return SYNTAX_RE.test(email);
  }

  // --- Damerau-Levenshtein ------------------------------------------------
  function damerauLevenshtein(a, b) {
    var la = a.length, lb = b.length;
    var d = [];
    for (var i = 0; i <= la; i++) { d[i] = [i]; }
    for (var j = 0; j <= lb; j++) { d[0][j] = j; }
    for (i = 1; i <= la; i++) {
      for (j = 1; j <= lb; j++) {
        var cost = a.charAt(i - 1) === b.charAt(j - 1) ? 0 : 1;
        d[i][j] = Math.min(
          d[i - 1][j] + 1,
          d[i][j - 1] + 1,
          d[i - 1][j - 1] + cost
        );
        if (i > 1 && j > 1 &&
            a.charAt(i - 1) === b.charAt(j - 2) &&
            a.charAt(i - 2) === b.charAt(j - 1)) {
          d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
        }
      }
    }
    return d[la][lb];
  }

  function suggestDomain(domain) {
    if (!domain) return null;
    if (POPULAR_DOMAINS.indexOf(domain) !== -1) return null;
    var best = null, bestDist = TYPO_MAX_DISTANCE + 1;
    for (var k = 0; k < POPULAR_DOMAINS.length; k++) {
      var dist = damerauLevenshtein(domain, POPULAR_DOMAINS[k]);
      if (dist < bestDist) { bestDist = dist; best = POPULAR_DOMAINS[k]; }
    }
    if (best && bestDist > 0 && bestDist <= TYPO_MAX_DISTANCE) return best;
    return null;
  }

  // --- Renderowanie stanow (§7) ----------------------------------------------
  var STATE_STYLE = {
    valid:              { cls: "ev-ok",      icon: "\u2713" },
    typo_suspected:     { cls: "ev-warn",    icon: "\u270E" },
    domain_not_found:   { cls: "ev-error",   icon: "\u2715" },
    no_mail_capability: { cls: "ev-error",   icon: "\u2715" },
    disposable:         { cls: "ev-warn",    icon: "\u26A0" },
    mailbox_not_found:  { cls: "ev-warn",    icon: "\u26A0" },
    syntax_invalid:     { cls: "ev-error",   icon: "\u2715" },
    unknown:            { cls: "ev-info",    icon: "\u2139" },
    checking:           { cls: "ev-info",    icon: "\u2026" },
    idle:               { cls: "",           icon: "" }
  };

  function EmailValidatorWidget(opts) {
    this.input = opts.input;
    this.serviceUrl = (opts.serviceUrl || "").replace(/\/$/, "");
    this.saveButton = opts.saveButton || null;
    this.debounceMs = opts.debounceMs || 300;
    this.checks = opts.checks || ["syntax", "typo", "dns", "mx", "lists"];
    this.onResult = opts.onResult || null;

    this.statusEl = opts.statusEl || this._createStatusEl();
    this._timer = null;
    this._lastResult = null;
    this._overrideChecked = false;
    this._bind();
  }

  EmailValidatorWidget.prototype._createStatusEl = function () {
    var el = document.createElement("div");
    el.className = "ev-status";
    if (this.input && this.input.parentNode) {
      this.input.parentNode.insertBefore(el, this.input.nextSibling);
    }
    return el;
  };

  EmailValidatorWidget.prototype._bind = function () {
    var self = this;
    this.input.addEventListener("input", function () {
      self._overrideChecked = false;
      self._onInput();
    });
    this.input.addEventListener("blur", function () {
      clearTimeout(self._timer);
      self._runFull();
    });
  };

  EmailValidatorWidget.prototype._onInput = function () {
    var self = this;
    var email = this.input.value.trim();

    if (!email) { this._render("idle", {}); this._setBlocked(false); return; }

    // skladnia natychmiast
    if (!checkSyntax(email)) {
      // Dopoki uzytkownik pisze, nie krzyczymy od razu bledem skladni -
      // pokazujemy neutralnie i czekamy; twardy komunikat na blur.
      this._render("checking", { message_pl: "Sprawdzam\u2026" });
    } else {
      // literowki lokalnie - natychmiastowa sugestia bez sieci
      var domain = email.split("@")[1];
      var sugg = suggestDomain(domain);
      if (sugg) {
        var local = email.split("@")[0];
        this._render("typo_suspected", {
          result: "typo_suspected",
          message_pl: "Czy chodzi\u0142o o " + sugg + "?",
          suggestion: local + "@" + sugg
        });
      } else {
        this._render("checking", { message_pl: "Sprawdzam\u2026" });
      }
    }

    clearTimeout(this._timer);
    this._timer = setTimeout(function () { self._runFull(); }, this.debounceMs);
  };

  EmailValidatorWidget.prototype._runFull = function () {
    var self = this;
    var email = this.input.value.trim();
    if (!email) { this._render("idle", {}); this._setBlocked(false); return; }

    // skladnia twardo na blur/po debounce
    if (!checkSyntax(email)) {
      this._render("syntax_invalid", {
        result: "syntax_invalid",
        message_pl: "Adres jest niepoprawny (sprawd\u017a format)"
      });
      this._setBlocked(true, false);
      return;
    }

    if (!this.serviceUrl) { return; } // tylko skladnia/literowki lokalnie

    this._render("checking", { message_pl: "Sprawdzam domen\u0119\u2026" });

    fetch(this.serviceUrl + "/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email, checks: this.checks })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        self._lastResult = data;
        if (self.onResult) self.onResult(data);
        self._render(data.result, data);
        self._setBlocked(data.block_save, data.block_override_allowed);
      })
      .catch(function () {
        // Blad sieci = nie wiemy -> nie blokujemy (§6 unknown).
        self._render("unknown", { message_pl: "Nie uda\u0142o si\u0119 zweryfikowa\u0107 (brak po\u0142\u0105czenia)" });
        self._setBlocked(false);
      });
  };

  EmailValidatorWidget.prototype._render = function (state, data) {
    var style = STATE_STYLE[state] || STATE_STYLE.idle;
    var el = this.statusEl;
    el.className = "ev-status " + style.cls;
    el.innerHTML = "";

    if (state === "idle") return;

    var icon = document.createElement("span");
    icon.className = "ev-icon";
    icon.textContent = style.icon;
    el.appendChild(icon);

    var msg = document.createElement("span");
    msg.className = "ev-msg";
    msg.textContent = data.message_pl || "";
    el.appendChild(msg);

    // Sugestia literowki jako JEDEN KLIK (§7)
    if (state === "typo_suspected" && data.suggestion) {
      var self = this;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ev-fix";
      btn.textContent = "Popraw";
      btn.addEventListener("click", function () {
        self.input.value = data.suggestion;
        self.input.dispatchEvent(new Event("input"));
        self._runFull();
      });
      el.appendChild(btn);
    }
  };

  // Egzekwowanie regul §8 na przycisku zapisu.
  EmailValidatorWidget.prototype._setBlocked = function (block, overrideAllowed) {
    if (!this.saveButton) return;

    // usun poprzedni checkbox override
    var old = this.statusEl.querySelector(".ev-override");
    if (old) old.remove();

    if (block && overrideAllowed) {
      var self = this;
      var wrap = document.createElement("label");
      wrap.className = "ev-override";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = this._overrideChecked;
      cb.addEventListener("change", function () {
        self._overrideChecked = cb.checked;
        self.saveButton.disabled = !cb.checked;
      });
      wrap.appendChild(cb);
      wrap.appendChild(document.createTextNode(" Potwierdzam adres r\u0119cznie"));
      this.statusEl.appendChild(wrap);
      this.saveButton.disabled = !this._overrideChecked;
    } else {
      this.saveButton.disabled = !!block;
    }
  };

  var EmailValidator = {
    attach: function (opts) { return new EmailValidatorWidget(opts); },
    checkSyntax: checkSyntax,
    suggestDomain: suggestDomain
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = EmailValidator;
  }
  global.EmailValidator = EmailValidator;
})(typeof window !== "undefined" ? window : this);
