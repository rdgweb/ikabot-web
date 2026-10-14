/* Tour de boas-vindas (N-116).
 *
 * Leva o usuário pelo caminho real até o primeiro job: o agente (nó), a conta, os mundos e o job.
 * A tela escurece, a parte apresentada fica acesa e um cartão explica o que fazer ali. O tour troca
 * de página sozinho quando o passo seguinte mora em outra, e espera quando o passo é do usuário
 * (salvar a conta). O texto de cada passo segue o que a instalação já tem (/settings/tour/state/).
 *
 * Começa sozinho uma vez para cada usuário (data-autostart no <script>) e pode ser revisto em
 * Configurações (window.ikTour.start()).
 */
(function () {
  "use strict";

  var ACCOUNT_PAGE = /^\/accounts\/[0-9a-f-]{36}\/$/;

  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  var STEPS = [
    { icon: "bi-stars", title: "Bem-vindo ao Ikabot",
      text: "Vamos deixar a primeira conta jogando, na ordem certa: o agente, a conta, os mundos e o primeiro job. Esc sai a qualquer momento." },

    { page: /^\/accounts\/nodes\/$/, go: "/accounts/nodes/", target: '[data-tour="nodes"]', icon: "bi-hdd-network",
      chapter: "1 · O agente", title: "Nós & Agents",
      text: function (s) {
        if (s.online) return "O agente é o programa que entra no jogo e executa os jobs; cada agente conectado aparece aqui como um nó. "
          + plural(s.online, "nó online", "nós online") + ": pronto para receber contas.";
        if (s.nodes) return "O nó existe, mas o agente não fala com o hub há mais de 3 minutos. Sem agente online os jobs ficam parados na fila: confira se o container agent está rodando.";
        return "Ainda não há agente. O nó aparece aqui sozinho quando o container do agente sobe com o mesmo AGENT_TOKEN do hub (o instalador já deixa assim); agente em outra máquina começa por Novo Nó. Sem nó, a conta não tem quem jogue por ela: o tour espera e continua quando o nó aparecer.";
      },
      tone: function (s) { return s.online ? "" : "warn"; },
      waits: function (s) { return !s.nodes; }, until: function (s) { return s.nodes > 0; },
      waitText: "Esperando o agente conectar", readyText: "O agente conectou",
      cta: function (s) { return s.online ? null : { label: "Saúde do sistema", href: "/settings/#saude" }; } },

    { page: /^\/accounts\/create\/$/, go: "/accounts/create/", target: "#id_node", wrap: ".form-group", icon: "bi-hdd-network",
      chapter: "2 · A conta", title: "Quem joga por ela",
      text: function (s) {
        return s.nodes ? "Escolha o nó que vai jogar por esta conta. Cada conta fica sempre no mesmo nó."
          : "A lista está vazia porque ainda não há nó. Volte aqui depois que o agente conectar.";
      } },
    { page: /^\/accounts\/create\/$/, target: "#id_label", wrap: ".form-group", icon: "bi-tag",
      chapter: "2 · A conta", title: "Nome", text: "Um nome só seu para reconhecer a conta no hub, como \"Principal\"." },
    { page: /^\/accounts\/create\/$/, target: "#id_email", wrap: ".form-group", icon: "bi-envelope",
      chapter: "2 · A conta", title: "E-mail e senha",
      text: "Os mesmos do login do GameForge (o do Ikariam). A senha é guardada criptografada." },
    { page: /^\/accounts\/create\/$/, target: "#id_gf_token", wrap: ".form-group", icon: "bi-key",
      chapter: "2 · A conta", title: "Token (opcional)",
      text: "Com o cookie gf-token-production, o hub já busca os mundos ao salvar. Sem ele, os mundos são buscados na página da conta." },
    { page: /^\/accounts\/create\/$/, target: '[data-tour="form-submit"]', icon: "bi-check-lg",
      chapter: "2 · A conta", title: "Criar Conta",
      text: function (s) {
        return s.accounts ? "Clique aqui quando for cadastrar uma conta. Para seguir o tour, vamos usar a conta que você já tem."
          : "Preencha e clique em Criar Conta. O tour continua na lista de contas.";
      },
      waits: function (s) { return !s.accounts; }, until: function (s) { return s.accounts > 0; },
      waitText: "Esperando a conta ser salva", readyText: "Conta salva" },

    { page: /^\/accounts\/$/, go: "/accounts/", target: '[data-tour="account-row"]', icon: "bi-person-badge",
      skip: function (s) { return !s.accounts; },
      chapter: "3 · Os mundos", title: "Sua conta",
      text: "Cada conta do GameForge aparece aqui. Abra a conta para buscar os mundos em que ela joga." },
    { page: ACCOUNT_PAGE, go: function (s) { return s.first_account ? "/accounts/" + s.first_account + "/" : "/accounts/"; },
      target: '[data-tour="discover"]', icon: "bi-search", skip: function (s) { return !s.first_account; },
      chapter: "3 · Os mundos", title: "Discover Characters",
      text: function (s) {
        return s.worlds ? "Entra no lobby do GameForge e traz cada mundo da conta. Vira um job: leva alguns segundos e precisa do agente online."
          : "Clique aqui para trazer os mundos da conta (com o token, ele já rodou ao salvar). É um job: leva alguns segundos e precisa do agente online. O tour continua quando os mundos chegarem.";
      },
      waits: function (s) { return !s.worlds; }, until: function (s) { return s.worlds > 0; },
      waitText: "Esperando os mundos chegarem", readyText: "Os mundos chegaram" },
    { page: ACCOUNT_PAGE, go: function (s) { return s.first_account ? "/accounts/" + s.first_account + "/" : "/accounts/"; },
      target: '[data-tour="worlds"]', icon: "bi-people", skip: function (s) { return !s.first_account; },
      chapter: "3 · Os mundos", title: "Subcontas",
      text: function (s) {
        return s.worlds ? "Cada linha é um mundo (" + plural(s.worlds, "subconta ativa", "subcontas ativas") + " no hub). Pause as que o Ikabot não deve tocar."
          : "Os mundos aparecem aqui quando o Discover termina. Cada um vira uma subconta.";
      } },

    { target: '[data-tour="novo-job"]', icon: "bi-plus-lg",
      chapter: "4 · O primeiro job", title: "Novo Job",
      text: "Tudo o que o Ikabot faz no jogo é um job. Novo Job → escolha a subconta → Verificar Status: ele lê cidades, recursos e edifícios." },
    { target: 'a.sidebar-item[href="/jobs/"]', icon: "bi-list-task",
      chapter: "4 · O primeiro job", title: "Fila de Jobs",
      text: "Acompanhe cada job: na fila, executando, concluído ou com erro, com o log de cada passo." },
    { target: 'a.sidebar-item[href="/game/"]', icon: "bi-joystick",
      chapter: "4 · O primeiro job", title: "Painel do Jogo",
      text: "Depois do Verificar Status, as cidades da subconta aparecem aqui." },
    { target: 'a.sidebar-item[href="/game/actions/"]', icon: "bi-controller",
      chapter: "Daqui em diante", title: "Ações do Jogo",
      text: "Construir, transportar, pesquisar, vender e o resto: escolha a ação e a subconta." },

    { icon: "bi-flag", title: "Tudo pronto", final: true,
      text: "Se algo não andar, Configurações → Saúde do sistema mostra o que falta. O tour pode ser revisto por lá." },
  ];

  var GAP = 14;          // between the lit part and the card
  var PAD = 6;           // light around the lit part
  var EDGE = 12;         // card never closer than this to the window edge
  var STORE = "iktour";  // sessionStorage: the step to resume after a page change
  var STORE_TTL = 30 * 60 * 1000;

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
    ".iktour-chapter{margin-right:auto;font-size:.68rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:#a67c2e}",
    ".iktour-count{font-size:.72rem;font-weight:600;color:var(--ik-muted,#6f6b63)}",
    ".iktour-card.warn .iktour-icon{background:linear-gradient(135deg,#d9894a,#b3402f);box-shadow:0 4px 10px rgba(179,64,47,.35)}",
    ".iktour-card.warn .iktour-chapter{color:#b3402f}",
    ".iktour-close{display:grid;place-items:center;width:28px;height:28px;border:0;border-radius:8px;background:transparent;",
    "  color:var(--ik-muted,#6f6b63);font-size:1rem;cursor:pointer}",
    ".iktour-close:hover{background:rgba(0,0,0,.06);color:var(--ik-ink,#2d2a24)}",
    ".iktour-title{margin:0 0 6px;font-family:Rajdhani,inherit;font-size:1.45rem;font-weight:700;line-height:1.1;color:var(--ik-ink,#2d2a24)}",
    ".iktour-text{margin:0;font-size:.9rem;line-height:1.5;color:#4a463e}",
    ".iktour-foot{display:flex;align-items:center;justify-content:flex-end;gap:8px;margin-top:16px}",
    ".iktour-bar{flex:1;min-width:40px;height:4px;margin-right:6px;border-radius:99px;background:rgba(138,129,114,.2);overflow:hidden}",
    ".iktour-bar i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#c99c4e,#a67c2e);transition:width .35s ease}",
    ".iktour-btn{display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:9px;font-size:.84rem;font-weight:600;",
    "  border:1px solid transparent;cursor:pointer;text-decoration:none;line-height:1.2;white-space:nowrap}",
    ".iktour-btn.ghost{background:transparent;color:var(--ik-muted,#6f6b63)}.iktour-btn.ghost:hover{color:var(--ik-ink,#2d2a24);background:rgba(0,0,0,.05)}",
    ".iktour-btn.primary{background:var(--ik-sea,#4f86b9);color:#fff}.iktour-btn.primary:hover{background:var(--ik-sea-dark,#3e719f)}",
    ".iktour-btn.gold{background:linear-gradient(135deg,#c99c4e,#a67c2e);color:#fff}",
    ".iktour-btn:focus{outline:none}.iktour-btn:focus-visible,.iktour-close:focus-visible{box-shadow:0 0 0 3px rgba(79,134,185,.35)}",
    ".iktour-chip{position:fixed;right:18px;bottom:18px;z-index:9990;display:flex;align-items:center;gap:12px;max-width:calc(100vw - 36px);",
    "  padding:10px 10px 10px 14px;border-radius:14px;background:var(--ik-paper,#fffdf7);color:var(--ik-ink,#2d2a24);",
    "  border:1px solid rgba(201,156,78,.5);box-shadow:0 12px 32px rgba(0,0,0,.22);animation:iktour-in .3s ease}",
    ".iktour-chip > i{font-size:1.2rem;color:#a67c2e}.iktour-chip:not(.ready) > i{animation:iktour-spin 1.2s linear infinite}",
    ".iktour-chip.ready > i{color:#2d7a2d}",
    ".iktour-chip span{display:flex;flex-direction:column;line-height:1.25}",
    ".iktour-chip small{font-size:.66rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#a67c2e}",
    ".iktour-chip b{font-size:.88rem;font-weight:600}",
    "@keyframes iktour-spin{to{transform:rotate(360deg)}}",
    "@keyframes iktour-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}",
    "@media (prefers-reduced-motion:reduce){.iktour-spot,.iktour-card,.iktour-bar i{transition:none!important}.iktour-chip,.iktour-chip > i{animation:none!important}}",
  ].join("\n");


  var root, spot, card, index = 0, lastFocus, rafPending, state = {}, config = {};
  var chip, poll, resuming;   // the corner chip while the tour waits for the hub (agent, account, worlds)

  function el(tag, cls, html) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html != null) node.innerHTML = html;
    return node;
  }

  function resolve(value) { return typeof value === "function" ? value(state) : value; }

  function onPage(step) { return !step.page || step.page.test(window.location.pathname); }

  function findTarget(step) {
    if (!step.target) return null;
    var node = document.querySelector(step.target);
    if (node && step.wrap) node = node.closest(step.wrap) || node;
    return node;
  }

  // ── where the tour stopped, kept across page changes ──
  function save(i, waiting, gate) {
    var data = { i: i, w: !!waiting, t: Date.now() };
    if (gate != null) data.u = gate;   // the step whose until() lets the tour go on
    try { window.sessionStorage.setItem(STORE, JSON.stringify(data)); } catch (e) { /* private mode */ }
  }
  function load() {
    try {
      var saved = JSON.parse(window.sessionStorage.getItem(STORE) || "null");
      if (saved && Date.now() - saved.t < STORE_TTL && STEPS[saved.i]) return saved;
    } catch (e) { /* nothing saved */ }
    return null;
  }
  function forget() { try { window.sessionStorage.removeItem(STORE); } catch (e) { /* private mode */ } }

  function fetchState() {
    if (!config.stateUrl) return Promise.resolve();
    return window.fetch(config.stateUrl, { credentials: "same-origin", cache: "no-store", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (response) { return response.ok ? response.json() : {}; })
      .catch(function () { return {}; })
      .then(function (data) { state = data || {}; });
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
      '<div class="iktour-head"><span class="iktour-icon"><i class="bi"></i></span><span class="iktour-chapter"></span>' +
      '<span class="iktour-count"></span>' +
      '<button type="button" class="iktour-close" aria-label="Fechar o tour"><i class="bi bi-x-lg"></i></button></div>' +
      '<h3 class="iktour-title" id="iktour-title"></h3><p class="iktour-text"></p>' +
      '<div class="iktour-foot"><span class="iktour-bar"><i></i></span>' +
      '<button type="button" class="iktour-btn ghost" data-act="back"><i class="bi bi-arrow-left"></i> Voltar</button>' +
      '<a class="iktour-btn gold" data-act="cta" hx-boost="false"></a>' +
      '<button type="button" class="iktour-btn primary" data-act="next"></button></div>';
    root.appendChild(el("div", "iktour-shield"));
    root.appendChild(spot);
    root.appendChild(card);
    document.body.appendChild(root);

    card.querySelector(".iktour-close").addEventListener("click", finish);
    card.querySelector('[data-act="back"]').addEventListener("click", function () { go(index - 1, -1); });
    card.querySelector('[data-act="next"]').addEventListener("click", next);
    card.querySelector('[data-act="cta"]').addEventListener("click", function () { forget(); close(); });
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", schedule);
    window.addEventListener("scroll", schedule, true);
  }

  function next() {
    var step = STEPS[index];
    if (index >= STEPS.length - 1) { finish(); return; }
    if (resolve(step.waits)) {          // the user (or the agent) acts now; the tour comes back when it is done
      save(index + 1, true, step.until ? index : null);
      close();
      if (step.until) resume();
      return;
    }
    go(index + 1, 1);
  }

  function onKey(event) {
    if (!root) return;
    if (event.key === "Escape") { event.preventDefault(); finish(); }
    else if (event.key === "ArrowRight") { event.preventDefault(); next(); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); if (index > 0) go(index - 1, -1); }
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
    window.requestAnimationFrame(function () { rafPending = false; if (root) place(false); });
  }

  function fill(step) {
    card.className = "iktour-card is-hidden" + (resolve(step.tone) === "warn" ? " warn" : "");
    card.querySelector(".iktour-icon i").className = "bi " + (step.icon || "bi-signpost-split");
    card.querySelector(".iktour-chapter").textContent = step.chapter || "Ikabot";
    card.querySelector(".iktour-count").textContent = (index + 1) + "/" + STEPS.length;
    card.querySelector(".iktour-title").textContent = step.title;
    card.querySelector(".iktour-text").textContent = resolve(step.text);
    card.querySelector(".iktour-bar i").style.width = Math.round((index + 1) * 100 / STEPS.length) + "%";
    card.querySelector('[data-act="back"]').style.display = index > 0 && !step.final ? "" : "none";
    var waits = resolve(step.waits);
    var nextButton = card.querySelector('[data-act="next"]');
    nextButton.innerHTML = index === 0 ? 'Começar <i class="bi bi-arrow-right"></i>'
      : step.final ? "Concluir"
      : waits ? 'Entendi <i class="bi bi-check-lg"></i>'
      : 'Próximo <i class="bi bi-arrow-right"></i>';
    var cta = resolve(step.cta);
    var link = card.querySelector('[data-act="cta"]');
    if (cta) {
      link.style.display = "";
      link.setAttribute("href", cta.href);
      link.innerHTML = cta.label + ' <i class="bi bi-arrow-right"></i>';
    } else {
      link.style.display = "none";
    }
  }

  function place(scroll) {
    var step = STEPS[index];
    var target = findTarget(step);
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

  // ── moving between steps ──
  function go(target, direction) {
    if (target < 0 || target >= STEPS.length) return;
    var step = STEPS[target];
    if (resolve(step.skip)) { go(target + direction, direction); return; }   // nothing to show yet (no account)
    if (!onPage(step)) {
      var url = resolve(step.go);
      if (url) {                        // the step lives on another page: open it there
        save(target, false);
        close();
        window.location.href = url;
        return;
      }
      go(target + direction, direction); // a step of a page we are not on, reached backwards: the page's first step opens it
      return;
    }
    show(target);
  }

  function show(target) {
    if (!root) build();
    card.classList.add("is-hidden");
    window.setTimeout(function () {
      if (!root) return;
      index = target;
      save(index, false);
      fill(STEPS[index]);
      place(true);
      card.classList.remove("is-hidden");
      card.querySelector('[data-act="next"]').focus({ preventScroll: true });
    }, root.classList.contains("is-entering") ? 0 : 180);
  }

  function open(target) {
    if (root) return;
    lastFocus = document.activeElement;
    fetchState().then(function () {
      if (root) return;
      build();
      go(target, 1);
      window.requestAnimationFrame(function () {
        window.requestAnimationFrame(function () { if (root) root.classList.remove("is-entering"); });
      });
    });
  }

  function close() {
    if (!root) return;
    var node = root;
    root = null;
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("resize", schedule);
    window.removeEventListener("scroll", schedule, true);
    node.classList.add("is-entering");
    node.querySelector(".iktour-card").classList.add("is-hidden");
    window.setTimeout(function () { node.remove(); }, 320);
    if (lastFocus && lastFocus.focus && document.contains(lastFocus)) lastFocus.focus({ preventScroll: true });
  }

  function finish() { chipHide(); forget(); close(); }

  function start() { chipHide(); forget(); close(); open(0); }

  // ── waiting: a chip in the corner says what the tour waits for, and turns into "Continuar" ──
  function chipShow(saved, ready) {
    var gate = STEPS[saved.u];
    injectStyle();
    if (!chip) { chip = el("div"); document.body.appendChild(chip); }
    chip.className = "iktour-chip" + (ready ? " ready" : "");
    chip.setAttribute("data-path", window.location.pathname);
    chip.innerHTML = '<i class="bi ' + (ready ? "bi-check-circle-fill" : "bi-arrow-repeat") + '"></i>' +
      '<span><small></small><b></b></span>' +
      (ready ? '<button type="button" class="iktour-btn primary" data-act="go">Continuar <i class="bi bi-arrow-right"></i></button>' : "") +
      '<button type="button" class="iktour-close" data-act="stop" aria-label="Encerrar o tour"><i class="bi bi-x-lg"></i></button>';
    chip.querySelector("small").textContent = "Tour · " + (gate.chapter || "Ikabot");
    chip.querySelector("b").textContent = ready ? gate.readyText : gate.waitText;
    chip.querySelector('[data-act="stop"]').addEventListener("click", finish);
    if (ready) chip.querySelector('[data-act="go"]').addEventListener("click", function () { continueTo(saved.i); });
  }

  function chipHide() {
    if (poll) { window.clearInterval(poll); poll = null; }
    if (chip) { chip.remove(); chip = null; }
  }

  function continueTo(i) {
    chipHide();
    var step = STEPS[i];
    save(i, false);
    if (onPage(step)) { window.location.reload(); return; }   // fresh page: the new node, account or worlds show up
    window.location.href = resolve(step.go) || window.location.href;
  }

  // the tour was left on another page (a page change or the user saving the form): pick it up here
  function resume() {
    if (root || resuming) return false;
    var saved = load();
    if (!saved) { chipHide(); return false; }
    if (saved.u == null) {
      if (!onPage(STEPS[saved.i])) return false;
      open(saved.i);
      return true;
    }
    if (chip && chip.getAttribute("data-path") === window.location.pathname) return true;   // already said here
    resuming = true;
    fetchState().then(function () {
      resuming = false;
      var gate = STEPS[saved.u];
      var ready = !!resolve(gate.until);
      if (ready && onPage(STEPS[saved.i])) {    // done, and already where the tour goes on (the account list after saving)
        chipHide();
        save(saved.i, false);
        open(saved.i);
        return;
      }
      chipShow(saved, ready);
      if (!ready && !poll) {
        poll = window.setInterval(function () {
          fetchState().then(function () {
            if (!resolve(gate.until) || !chip) return;
            window.clearInterval(poll); poll = null;
            chipShow(saved, true);
          });
        }, 10000);
      }
    });
    return true;
  }

  function markShown(url) {
    var headers = { "X-Requested-With": "XMLHttpRequest" };
    try { Object.assign(headers, JSON.parse(document.body.getAttribute("hx-headers") || "{}")); } catch (e) { /* no token: the server says 403 */ }
    window.fetch(url, { method: "POST", headers: headers, credentials: "same-origin", keepalive: true }).catch(function () {});
  }

  window.ikTour = { start: start, finish: finish };

  var script = document.querySelector("script[data-tour-script]");
  if (script) {
    config.stateUrl = script.getAttribute("data-state-url");
    var boot = function () {
      if (resume()) return;
      if (script.hasAttribute("data-autostart")) {
        // shown once: marked as soon as it opens, so closing the tab does not bring it back
        markShown(script.getAttribute("data-done-url"));
        window.setTimeout(function () { open(0); }, 500);
      }
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
    // boosted navigation (menu, saved form) swaps the page without loading this script again
    document.body.addEventListener("htmx:afterSettle", function () { window.setTimeout(resume, 60); });
    window.addEventListener("popstate", function () { window.setTimeout(resume, 60); });
  }
})();
