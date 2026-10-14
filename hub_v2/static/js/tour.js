/* Tour de boas-vindas (N-116).
 *
 * A tela escurece, a parte apresentada fica acesa e um cartão explica o que ela faz.
 * Começa sozinho uma vez para cada usuário (data-autostart no <script>) e pode ser revisto em
 * Configurações (window.ikTour.start()). Passos cuja peça não existe para o usuário são pulados;
 * peça escondida (menu fechado no celular) vira cartão no centro.
 */
(function () {
  "use strict";

  var STEPS = [
    { icon: "bi-stars", title: "Bem-vindo ao Ikabot",
      text: "Um giro rápido pelas partes que você vai usar no dia a dia. Leva menos de um minuto." },
    { target: 'a.sidebar-item[href="/accounts/"]', icon: "bi-person-badge", title: "Contas do Jogo",
      text: "Comece por aqui: cadastre o e-mail e a senha do GameForge. Cada mundo em que a conta joga vira uma subconta." },
    { target: '[data-tour="novo-job"]', icon: "bi-plus-lg", title: "Novo Job",
      text: "Tudo o que o Ikabot faz no jogo é um job: escolha a subconta, a ação e envie. O primeiro costuma ser Verificar Status." },
    { target: 'a.sidebar-item[href="/game/"]', icon: "bi-joystick", title: "Painel do Jogo",
      text: "Cidades, recursos, edifícios e tropas de cada subconta, atualizados a cada leitura." },
    { target: 'a.sidebar-item[href="/game/actions/"]', icon: "bi-controller", title: "Ações do Jogo",
      text: "O catálogo completo: construir, transportar, pesquisar, vender e muito mais." },
    { target: 'a.sidebar-item[href="/jobs/"]', icon: "bi-list-task", title: "Fila de Jobs",
      text: "Cada job com o seu estado (na fila, executando, concluído ou com erro) e o log de cada passo." },
    { target: 'a.sidebar-item[href="/telegram/"]', icon: "bi-telegram", title: "Telegram",
      text: "Avisos, captchas e confirmações direto no celular." },
    { target: 'a.sidebar-item[href="/settings/"]', icon: "bi-gear", title: "Configurações",
      text: "Saúde do sistema, integrações e este tour, se quiser vê-lo de novo." },
    { icon: "bi-flag", title: "Tudo pronto", final: true,
      text: "Cadastre a primeira conta e o resto acompanha.",
      cta: { label: "Cadastrar conta", href: "/accounts/create/", needs: 'a.sidebar-item[href="/accounts/"]' } },
  ];

  var GAP = 14;          // between the lit part and the card
  var PAD = 6;           // light around the lit part
  var EDGE = 12;         // card never closer than this to the window edge

  var CSS = [
    ".iktour{position:fixed;inset:0;z-index:10000;font-family:inherit}",
    ".iktour-shield{position:fixed;inset:0;background:transparent}",
    ".iktour-spot{position:fixed;border-radius:12px;pointer-events:none;",
    "  box-shadow:0 0 0 3px rgba(201,156,78,.95),0 0 26px 6px rgba(201,156,78,.35),0 0 0 9999px rgba(22,17,10,.72);",
    "  transition:top .45s cubic-bezier(.2,.8,.2,1),left .45s cubic-bezier(.2,.8,.2,1),width .45s cubic-bezier(.2,.8,.2,1),height .45s cubic-bezier(.2,.8,.2,1),box-shadow .3s ease}",
    ".iktour-spot.is-center{box-shadow:0 0 0 0 rgba(201,156,78,0),0 0 0 0 rgba(201,156,78,0),0 0 0 9999px rgba(22,17,10,.76)}",
    ".iktour.is-entering .iktour-spot{box-shadow:0 0 0 0 rgba(201,156,78,0),0 0 0 0 rgba(201,156,78,0),0 0 0 9999px rgba(22,17,10,0)}",
    ".iktour-card{position:fixed;width:min(360px,calc(100vw - 32px));padding:18px 20px 16px;border-radius:16px;",
    "  background:var(--ik-paper,#fffdf7);color:var(--ik-ink,#2d2a24);border:1px solid rgba(201,156,78,.35);",
    "  box-shadow:0 22px 60px rgba(0,0,0,.38),0 2px 6px rgba(0,0,0,.12);",
    "  transition:opacity .22s ease,transform .22s ease}",
    ".iktour-card.is-hidden{opacity:0;transform:translateY(6px) scale(.98)}",
    ".iktour-arrow{position:absolute;width:14px;height:14px;background:var(--ik-paper,#fffdf7);transform:rotate(45deg);",
    "  border:1px solid rgba(201,156,78,.35)}",
    ".iktour-arrow.left{left:-8px;border-top:0;border-right:0}.iktour-arrow.right{right:-8px;border-bottom:0;border-left:0}",
    ".iktour-arrow.top{top:-8px;border-right:0;border-bottom:0}.iktour-arrow.bottom{bottom:-8px;border-top:0;border-left:0}",
    ".iktour-arrow.none{display:none}",
    ".iktour-head{display:flex;align-items:center;gap:10px;margin-bottom:10px}",
    ".iktour-icon{display:grid;place-items:center;width:38px;height:38px;border-radius:11px;font-size:1.1rem;color:#fff;",
    "  background:linear-gradient(135deg,#c99c4e,#a67c2e);box-shadow:0 4px 10px rgba(166,124,46,.35)}",
    ".iktour-count{margin-right:auto;font-size:.68rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--ik-muted,#6f6b63)}",
    ".iktour-close{display:grid;place-items:center;width:28px;height:28px;border:0;border-radius:8px;background:transparent;",
    "  color:var(--ik-muted,#6f6b63);font-size:1rem;cursor:pointer}",
    ".iktour-close:hover{background:rgba(0,0,0,.06);color:var(--ik-ink,#2d2a24)}",
    ".iktour-title{margin:0 0 6px;font-family:Rajdhani,inherit;font-size:1.45rem;font-weight:700;line-height:1.1;color:var(--ik-ink,#2d2a24)}",
    ".iktour-text{margin:0;font-size:.9rem;line-height:1.5;color:#4a463e}",
    ".iktour-foot{display:flex;align-items:center;justify-content:flex-end;gap:8px;margin-top:16px}",
    ".iktour-dots{display:flex;gap:5px;margin-right:auto}",
    ".iktour-dots span{width:7px;height:7px;border-radius:99px;background:rgba(138,129,114,.3);transition:width .25s ease,background .25s ease}",
    ".iktour-dots span.on{width:20px;background:#c99c4e}.iktour-dots span.past{background:rgba(201,156,78,.55)}",
    ".iktour-btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:9px;font-size:.84rem;font-weight:600;",
    "  border:1px solid transparent;cursor:pointer;text-decoration:none;line-height:1.2;white-space:nowrap}",
    ".iktour-btn.ghost{background:transparent;color:var(--ik-muted,#6f6b63)}.iktour-btn.ghost:hover{color:var(--ik-ink,#2d2a24);background:rgba(0,0,0,.05)}",
    ".iktour-btn.primary{background:var(--ik-sea,#4f86b9);color:#fff}.iktour-btn.primary:hover{background:var(--ik-sea-dark,#3e719f)}",
    ".iktour-btn.gold{background:linear-gradient(135deg,#c99c4e,#a67c2e);color:#fff}",
    ".iktour-btn:focus{outline:none}.iktour-btn:focus-visible,.iktour-close:focus-visible{box-shadow:0 0 0 3px rgba(79,134,185,.35)}",
    "@media (prefers-reduced-motion:reduce){.iktour-spot,.iktour-card,.iktour-dots span{transition:none!important}}",
  ].join("\n");

  var root, spot, card, steps, index, lastFocus, rafPending;

  function el(tag, cls, html) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  }

  function injectStyle() {
    if (document.getElementById("iktour-style")) return;
    var style = el("style");
    style.id = "iktour-style";
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  function visibleRect(node) {
    if (!node) return null;
    var rect = node.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return null;
    if (rect.right <= 0 || rect.left >= window.innerWidth || rect.bottom <= 0 || rect.top >= window.innerHeight) return null;
    var style = window.getComputedStyle(node);
    if (style.visibility === "hidden" || style.display === "none") return null;
    return rect;
  }

  function availableSteps() {
    return STEPS.filter(function (step) {
      return !step.target || document.querySelector(step.target);
    });
  }

  function build() {
    injectStyle();
    root = el("div", "iktour is-entering");
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "iktour-title");
    spot = el("div", "iktour-spot is-center");
    card = el("div", "iktour-card is-hidden");
    card.innerHTML =
      '<div class="iktour-arrow none"></div>' +
      '<div class="iktour-head"><span class="iktour-icon"><i class="bi"></i></span><span class="iktour-count"></span>' +
      '<button type="button" class="iktour-close" aria-label="Fechar o tour"><i class="bi bi-x-lg"></i></button></div>' +
      '<h3 class="iktour-title" id="iktour-title"></h3><p class="iktour-text"></p>' +
      '<div class="iktour-foot"><div class="iktour-dots"></div>' +
      '<button type="button" class="iktour-btn ghost" data-act="back"><i class="bi bi-arrow-left"></i> Voltar</button>' +
      '<a class="iktour-btn gold" data-act="cta" hx-boost="false"></a>' +
      '<button type="button" class="iktour-btn primary" data-act="next"></button></div>';
    root.appendChild(el("div", "iktour-shield"));
    root.appendChild(spot);
    root.appendChild(card);
    document.body.appendChild(root);

    card.querySelector(".iktour-close").addEventListener("click", finish);
    card.querySelector('[data-act="back"]').addEventListener("click", function () { go(index - 1); });
    card.querySelector('[data-act="next"]').addEventListener("click", function () {
      if (index >= steps.length - 1) finish(); else go(index + 1);
    });
    card.querySelector('[data-act="cta"]').addEventListener("click", function () { finish(); });
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", schedule);
    window.addEventListener("scroll", schedule, true);
  }

  function onKey(event) {
    if (!root) return;
    if (event.key === "Escape") { event.preventDefault(); finish(); }
    else if (event.key === "ArrowRight") { event.preventDefault(); if (index < steps.length - 1) go(index + 1); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); if (index > 0) go(index - 1); }
    else if (event.key === "Tab") {                       // keep the focus inside the card
      var focusable = Array.prototype.filter.call(card.querySelectorAll("button, a[href]"), function (node) { return node.offsetParent !== null; });
      if (!focusable.length) return;
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  }

  function schedule() {
    if (rafPending || !root) return;
    rafPending = true;
    window.requestAnimationFrame(function () { rafPending = false; place(false); });
  }

  function fill(step) {
    card.querySelector(".iktour-icon i").className = "bi " + (step.icon || "bi-signpost-split");
    card.querySelector(".iktour-count").textContent = (index + 1) + " de " + steps.length;
    card.querySelector(".iktour-title").textContent = step.title;
    card.querySelector(".iktour-text").textContent = step.text;
    var dots = card.querySelector(".iktour-dots");
    dots.innerHTML = "";
    dots.style.display = step.final ? "none" : "";
    steps.forEach(function (_, i) { dots.appendChild(el("span", i === index ? "on" : (i < index ? "past" : ""))); });
    var back = card.querySelector('[data-act="back"]');
    back.style.display = index > 0 && !step.final ? "" : "none";
    var next = card.querySelector('[data-act="next"]');
    var last = index >= steps.length - 1;
    next.innerHTML = index === 0 ? 'Começar <i class="bi bi-arrow-right"></i>' : (last ? "Concluir" : 'Próximo <i class="bi bi-arrow-right"></i>');
    next.className = "iktour-btn " + (last && step.cta && document.querySelector(step.cta.needs) ? "ghost" : "primary");
    var cta = card.querySelector('[data-act="cta"]');
    if (step.cta && document.querySelector(step.cta.needs)) {
      cta.style.display = "";
      cta.setAttribute("href", step.cta.href);
      cta.innerHTML = step.cta.label + ' <i class="bi bi-arrow-right"></i>';
    } else {
      cta.style.display = "none";
    }
  }

  function place(scroll) {
    var step = steps[index];
    var target = step.target ? document.querySelector(step.target) : null;
    if (target && scroll) target.scrollIntoView({ block: "nearest", inline: "nearest" });
    var rect = visibleRect(target);
    var vw = window.innerWidth, vh = window.innerHeight;
    var cw = card.offsetWidth, ch = card.offsetHeight;
    var arrow = card.querySelector(".iktour-arrow");

    if (!rect) {
      spot.classList.add("is-center");
      setBox(spot, vh / 2, vw / 2, 0, 0);
      setPos(card, Math.max(EDGE, (vh - ch) / 2), Math.max(EDGE, (vw - cw) / 2));
      arrow.className = "iktour-arrow none";
      return;
    }
    spot.classList.remove("is-center");
    // the light stays inside the window (menu items touch its left edge)
    var top = Math.max(rect.top - PAD, 3), left = Math.max(rect.left - PAD, 3);
    var width = Math.min(rect.right + PAD, vw - 3) - left, height = Math.min(rect.bottom + PAD, vh - 3) - top;
    setBox(spot, top, left, width, height);

    var cx = left + width / 2, cy = top + height / 2;
    var clampTop = function (value) { return Math.min(Math.max(value, EDGE), vh - ch - EDGE); };
    var clampLeft = function (value) { return Math.min(Math.max(value, EDGE), vw - cw - EDGE); };
    var choice;
    if (left + width + GAP + cw <= vw - EDGE) choice = { side: "left", top: clampTop(cy - ch / 2), left: left + width + GAP };
    else if (top + height + GAP + ch <= vh - EDGE) choice = { side: "top", top: top + height + GAP, left: clampLeft(cx - cw / 2) };
    else if (left - GAP - cw >= EDGE) choice = { side: "right", top: clampTop(cy - ch / 2), left: left - GAP - cw };
    else if (top - GAP - ch >= EDGE) choice = { side: "bottom", top: top - GAP - ch, left: clampLeft(cx - cw / 2) };
    else choice = { side: "none", top: clampTop(vh - ch - EDGE), left: clampLeft(cx - cw / 2) };
    setPos(card, choice.top, choice.left);

    // the arrow sits on the side that faces the lit part
    arrow.className = "iktour-arrow " + choice.side;
    arrow.style.top = arrow.style.left = "";
    if (choice.side === "left" || choice.side === "right") arrow.style.top = Math.min(Math.max(cy - choice.top - 7, 18), ch - 32) + "px";
    if (choice.side === "top" || choice.side === "bottom") arrow.style.left = Math.min(Math.max(cx - choice.left - 7, 18), cw - 32) + "px";
  }

  function setBox(node, top, left, width, height) {
    node.style.top = top + "px"; node.style.left = left + "px";
    node.style.width = width + "px"; node.style.height = height + "px";
  }

  function setPos(node, top, left) { node.style.top = top + "px"; node.style.left = left + "px"; }

  function go(next) {
    if (next < 0 || next >= steps.length) return;
    card.classList.add("is-hidden");
    window.setTimeout(function () {
      if (!root) return;
      index = next;
      fill(steps[index]);
      place(true);
      card.classList.remove("is-hidden");
      card.querySelector('[data-act="next"]').focus({ preventScroll: true });
    }, root.classList.contains("is-entering") ? 0 : 180);
  }

  function start() {
    if (root) return;
    steps = availableSteps();
    lastFocus = document.activeElement;
    build();
    index = 0;
    go(0);
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () { if (root) root.classList.remove("is-entering"); });
    });
  }

  function finish() {
    if (!root) return;
    var node = root;
    root = null;
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("resize", schedule);
    window.removeEventListener("scroll", schedule, true);
    node.classList.add("is-entering");
    node.querySelector(".iktour-card").classList.add("is-hidden");
    window.setTimeout(function () { node.remove(); }, 320);
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }

  function markShown(url) {
    var headers = { "X-Requested-With": "XMLHttpRequest" };
    try { Object.assign(headers, JSON.parse(document.body.getAttribute("hx-headers") || "{}")); } catch (e) { /* no token: the server says 403 */ }
    window.fetch(url, { method: "POST", headers: headers, credentials: "same-origin", keepalive: true }).catch(function () {});
  }

  window.ikTour = { start: start, finish: finish };

  var script = document.querySelector("script[data-tour-script]");
  if (script && script.hasAttribute("data-autostart")) {
    var begin = function () {
      // shown once: marked as soon as it opens, so closing the tab does not bring it back
      markShown(script.getAttribute("data-done-url"));
      window.setTimeout(start, 500);
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", begin);
    else begin();
  }
})();
