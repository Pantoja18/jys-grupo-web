/* Aviso de cookies de J&S (sin librerías externas).
   - Los recursos de terceros (por ahora, el mapa de Google) se marcan con
     data-consent-src y NO se cargan hasta que la persona pulse "Aceptar".
   - La elección se guarda en localStorage ("jys-cookies": "aceptado" | "rechazado").
   - Cualquier enlace con la clase .js-cookie-prefs vuelve a abrir el aviso. */
(function () {
  var KEY = "jys-cookies";
  var script = document.currentScript;
  var base = (script && script.getAttribute("data-base")) || "";

  function leer() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function guardar(v) { try { localStorage.setItem(KEY, v); } catch (e) {} }

  function activarTerceros() {
    document.querySelectorAll("[data-consent-src]").forEach(function (el) {
      if (!el.getAttribute("src")) el.setAttribute("src", el.getAttribute("data-consent-src"));
      el.hidden = false;
    });
    document.querySelectorAll("[data-consent-placeholder]").forEach(function (el) { el.hidden = true; });
  }

  var css =
    ".ck-banner{position:fixed;left:16px;right:16px;bottom:16px;max-width:720px;margin:0 auto;z-index:400;" +
    "background:rgba(14,26,43,0.97);border:1px solid rgba(201,162,39,0.4);border-radius:12px;padding:14px 16px;" +
    "box-shadow:0 6px 24px rgba(0,0,0,0.45);display:flex;gap:14px;align-items:center;flex-wrap:wrap;" +
    "font-family:'Segoe UI',Arial,sans-serif;font-size:13px;line-height:1.5;color:#a9b2c0;}" +
    ".ck-banner p{flex:1 1 300px;margin:0;}" +
    ".ck-banner a{color:#e6c65c;text-decoration:underline;}" +
    ".ck-actions{display:flex;gap:10px;flex:0 0 auto;}" +
    ".ck-btn{min-width:96px;background:transparent;color:#e6c65c;border:1px solid #c9a227;border-radius:6px;" +
    "padding:9px 16px;font:600 13px 'Segoe UI',Arial,sans-serif;cursor:pointer;}" +
    ".ck-btn:hover,.ck-btn:focus-visible{background:rgba(201,162,39,0.15);outline:none;}" +
    ".ck-banner[hidden]{display:none !important;}" +
    "@media(max-width:520px){.ck-banner{left:10px;right:10px;bottom:10px;}.ck-actions{width:100%;}.ck-btn{flex:1;}}";

  var banner = null;
  function ajustarWhatsApp() {
    var wa = document.querySelector(".wa-float");
    if (wa) wa.style.bottom = banner && !banner.hidden ? (banner.offsetHeight + 30) + "px" : "";
  }
  function cerrar() { if (banner) banner.hidden = true; ajustarWhatsApp(); }

  function abrir() {
    if (!banner) {
      var st = document.createElement("style");
      st.textContent = css;
      document.head.appendChild(st);
      banner = document.createElement("div");
      banner.className = "ck-banner";
      banner.setAttribute("role", "dialog");
      banner.setAttribute("aria-label", "Aviso de cookies");
      banner.innerHTML =
        "<p>Usamos un mapa de Google que puede usar cookies de terceros. Solo se carga si lo aceptas. " +
        "Más información en nuestra <a href=\"" + base + "terminos.html#privacidad\">Política de privacidad</a>.</p>" +
        "<div class=\"ck-actions\"><button type=\"button\" class=\"ck-btn\" data-ck=\"rechazado\">Rechazar</button>" +
        "<button type=\"button\" class=\"ck-btn\" data-ck=\"aceptado\">Aceptar</button></div>";
      banner.addEventListener("click", function (e) {
        var v = e.target.getAttribute && e.target.getAttribute("data-ck");
        if (!v) return;
        guardar(v);
        if (v === "aceptado") activarTerceros();
        cerrar();
      });
      document.body.appendChild(banner);
    }
    banner.hidden = false;
    ajustarWhatsApp();
  }

  function iniciar() {
    var eleccion = leer();
    if (eleccion === "aceptado") activarTerceros();
    else if (eleccion !== "rechazado") abrir();

    document.addEventListener("click", function (e) {
      var t = e.target.closest && e.target.closest(".js-cookie-prefs, [data-consent-accept]");
      if (!t) return;
      e.preventDefault();
      if (t.hasAttribute("data-consent-accept")) { guardar("aceptado"); activarTerceros(); cerrar(); }
      else abrir();
    });
    window.addEventListener("resize", ajustarWhatsApp);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
