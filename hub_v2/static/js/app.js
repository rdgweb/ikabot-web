/**
 * ikabot hub — Global JS
 * HTMX config, Alpine.js components, toast manager, sidebar, clock
 */

// ── Alpine.js components registered via alpine:init ──────────────────────────


// ── HTMX Configuration ──

document.addEventListener("htmx:configRequest", (e) => {
  // Include CSRF token in all HTMX requests
  const csrfToken = document.querySelector("[name=csrfmiddlewaretoken]")?.value
    || document.cookie.match(/csrftoken=([^;]+)/)?.[1];
  if (csrfToken) {
    e.detail.headers["X-CSRFToken"] = csrfToken;
  }
});

// ── HTMX loading feedback ──
//
// Two distinct contexts:
//   1. Full page navigation (target = #page-content) → loading_indicator.html handles spinner
//   2. In-page partial swap (any other target)       → subtle opacity fade on the target element
//
// Alpine re-init: by htmx:afterSwap all <script> tags in swapped content have run,
// so gameDashboard() and other page-specific functions are already in window.
(function () {
  const PAGE_CONTENT_ID = "page-content";
  let _partialTarget = null;

  document.body.addEventListener("htmx:beforeRequest", (e) => {
    const target = e.detail && e.detail.target;
    const isPageSwap = target && target.id === PAGE_CONTENT_ID;

    if (isPageSwap) {
      // Full-page nav — loading_indicator.html handles the spinner via body.htmx-loading
      document.body.classList.add("htmx-loading");
      document.body.setAttribute("data-loading-style",
        localStorage.getItem("ikLoadingStyle") || "3");
    } else if (target && target !== document.body) {
      // In-page partial — subtle fade on the target
      _partialTarget = target;
      target.classList.add("htmx-partial-loading");
    }
  });

  document.body.addEventListener("htmx:afterSwap", (e) => {
    // Clean up partial loading state
    if (_partialTarget) {
      _partialTarget.classList.remove("htmx-partial-loading");
      _partialTarget = null;
    }
    // Re-init Alpine for newly swapped content
    if (window.Alpine) {
      const target = e.detail && e.detail.target;
      if (target) Alpine.initTree(target);
    }
  });

  document.body.addEventListener("htmx:afterRequest", (e) => {
    // Clean up full-page loading state (spinner)
    document.body.classList.remove("htmx-loading");
    document.body.removeAttribute("data-loading-style");
    if (_partialTarget) {
      _partialTarget.classList.remove("htmx-partial-loading");
      _partialTarget = null;
    }
  });
})();

// Show toast on HTMX errors
document.addEventListener("htmx:responseError", (e) => {
  window.dispatchEvent(new CustomEvent("toast", {
    detail: { type: "error", message: "Erro ao processar requisição." }
  }));
});

// HX-Trigger "toast" events are handled automatically by HTMX:
// HTMX parses the HX-Trigger header and dispatches a "toast" event
// on the body, which the Alpine.js toast component listens to via
// @toast.window="add($event.detail)"

// ── Alpine.js Components ──

// Toast notification manager
function toastManager() {
  return {
    toasts: [],
    _id: 0,
    add(detail) {
      const id = ++this._id;
      const toast = { id, visible: true, type: detail.type || "info", message: detail.message };
      this.toasts.push(toast);
      setTimeout(() => this.remove(id), detail.duration || 5000);
    },
    remove(id) {
      const toast = this.toasts.find((t) => t.id === id);
      if (toast) toast.visible = false;
      setTimeout(() => {
        this.toasts = this.toasts.filter((t) => t.id !== id);
      }, 300);
    },
  };
}

// Confirm dialog manager
function confirmDialog() {
  return {
    visible: false,
    title: "",
    message: "",
    action: "",
    method: "post",
    confirmLabel: "Confirmar",
    confirmStyle: "danger",
    _onConfirm: null,
    open(detail) {
      this.title = detail.title || "Confirmar acao";
      this.message = detail.message || "Tem certeza?";
      this.action = detail.action || "";
      this.method = detail.method || "post";
      this.confirmLabel = detail.confirmLabel || "Confirmar";
      this.confirmStyle = detail.style || "danger";
      this._onConfirm = detail.onConfirm || null;
      this.visible = true;
    },
    submit() {
      if (!this.action && !this._onConfirm) return;

      if (this.action) {
        const form = document.createElement("form");
        form.action = this.action;
        form.method = this.method || "post";
        form.style.display = "none";

        if (form.method.toLowerCase() !== "get") {
          const csrfToken =
            document.querySelector("[name=csrfmiddlewaretoken]")?.value
            || JSON.parse(document.body.getAttribute("hx-headers") || "{}")["X-CSRFToken"]
            || document.cookie.match(/csrftoken=([^;]+)/)?.[1]
            || "";

          const csrfInput = document.createElement("input");
          csrfInput.type = "hidden";
          csrfInput.name = "csrfmiddlewaretoken";
          csrfInput.value = csrfToken;
          form.appendChild(csrfInput);
        }

        document.body.appendChild(form);
        this.close();
        form.submit();
        return;
      }

      if (this._onConfirm) {
        this._onConfirm();
        this.close();
      }
    },
    close() {
      this.visible = false;
      this.title = "";
      this.message = "";
      this.action = "";
      this.method = "post";
      this.confirmLabel = "Confirmar";
      this.confirmStyle = "danger";
      this._onConfirm = null;
    },
  };
}

function openConfirm(detail) {
  window.dispatchEvent(new CustomEvent("confirm", { detail }));
  return false;
}

function refreshCurrentPageContent() {
  htmx.ajax("GET", window.location.pathname + window.location.search, {
    target: "#page-content",
    swap: "innerHTML"
  });
}

function refreshTableContent() {
  const tableContent = document.querySelector("#table-content");
  if (!tableContent) return false;

  htmx.ajax("GET", window.location.pathname + window.location.search, {
    target: "#table-content",
    select: "#table-content",
    swap: "innerHTML"
  });
  return true;
}

function refreshGameAccountsSection() {
  const section = document.querySelector("#game-accounts-section");
  if (!section) return false;

  htmx.ajax("GET", window.location.pathname + window.location.search, {
    target: "#game-accounts-section",
    select: "#game-accounts-section",
    swap: "outerHTML"
  });
  return true;
}

// ── Modal Helpers ──

/**
 * Open a modal by dispatching its custom event.
 * Usage: openModal('myModal')
 */
function openModal(modalId) {
  window.dispatchEvent(new CustomEvent("open-modal-" + modalId));
}

/**
 * Close all open modals.
 * Usage: closeModal()
 */
function closeModal() {
  window.dispatchEvent(new CustomEvent("close-modal"));
}

// ── Sidebar Collapse ──

function initSidebarCollapse() {
  if (localStorage.getItem("sidebar-collapsed") === "true") {
    document.body.classList.add("sidebar-collapsed");
  }
}

function toggleSidebar() {
  const isCollapsed = document.body.classList.toggle("sidebar-collapsed");
  localStorage.setItem("sidebar-collapsed", isCollapsed);
}

// ── Clock Update (Topbar) ──

const GAME_TIMEZONE = "Europe/Berlin"; // Ikariam server timezone (CET/CEST)

function initClock() {
  const clockEl     = document.querySelector("[data-clock]");
  const gameClockEl = document.querySelector("[data-game-clock]");
  const gameTzEl    = document.querySelector("[data-game-tz]");

  function updateClock() {
    const now = new Date();

    // Local time (HH:MM)
    if (clockEl) {
      clockEl.textContent =
        String(now.getHours()).padStart(2, "0") + ":" +
        String(now.getMinutes()).padStart(2, "0");
    }

    // Game server time (HH:MM:SS) in Europe/Berlin
    if (gameClockEl) {
      const timeParts = new Intl.DateTimeFormat("pt-BR", {
        timeZone: GAME_TIMEZONE,
        hour: "2-digit", minute: "2-digit", second: "2-digit",
        hour12: false,
      }).formatToParts(now);
      const h = timeParts.find(p => p.type === "hour")?.value   ?? "--";
      const m = timeParts.find(p => p.type === "minute")?.value ?? "--";
      const s = timeParts.find(p => p.type === "second")?.value ?? "--";
      gameClockEl.textContent = h + ":" + m + ":" + s;
    }

    // TZ abbreviation (CEST / CET)
    if (gameTzEl) {
      const tzAbbr = new Intl.DateTimeFormat("en", {
        timeZone: GAME_TIMEZONE, timeZoneName: "short",
      }).formatToParts(now).find(p => p.type === "timeZoneName")?.value ?? "";
      gameTzEl.textContent = tzAbbr;
    }
  }

  updateClock();
  setInterval(updateClock, 1000); // game clock updates every second
}

// ── Django Messages → Toasts ──

function initDjangoMessages() {
  document.querySelectorAll("[data-toast]").forEach((el) => {
    window.dispatchEvent(new CustomEvent("toast", {
      detail: { type: el.dataset.toastType || "info", message: el.dataset.toast }
    }));
    el.remove();
  });
}

// ── Init on DOM Ready ──

document.addEventListener("DOMContentLoaded", () => {
  initSidebarCollapse();
  initClock();
  initDjangoMessages();
  updateSidebarActiveState();
});

// Re-process Django messages after HTMX swaps (boosted navigation)
document.body.addEventListener("htmx:afterSettle", () => {
  initDjangoMessages();
  updateSidebarActiveState();
  initClock();
});

// Close modal + refresh jobs list when a job is created via HTMX trigger
document.body.addEventListener("closeModal", () => {
  closeModal();
});

document.body.addEventListener("jobsCreated", () => {
  if (window.location.pathname.startsWith("/jobs/")) {
    refreshCurrentPageContent();
    return;
  }

  refreshTableContent();
});

document.body.addEventListener("accountToggled", () => {
  if (!refreshTableContent()) {
    refreshCurrentPageContent();
  }
});

document.body.addEventListener("nodeToggled", () => {
  refreshCurrentPageContent();
});

document.body.addEventListener("gameAccountToggled", () => {
  if (!refreshGameAccountsSection()) {
    refreshCurrentPageContent();
  }
});

// ── Sidebar Active State (after HTMX navigation) ──

function updateSidebarActiveState() {
  const path = window.location.pathname;
  const items = Array.from(document.querySelectorAll(".sidebar-item"));

  // Find the best (longest) matching href
  let bestMatch = null;
  let bestLen = 0;
  items.forEach(function (item) {
    const href = item.getAttribute("href");
    if (!href) return;
    const isMatch =
      (href === "/" && path === "/") ||
      (href !== "/" && path.startsWith(href));
    if (isMatch && href.length > bestLen) {
      bestMatch = item;
      bestLen = href.length;
    }
  });

  // Apply active class only to the best match
  items.forEach(function (item) {
    if (item === bestMatch) {
      item.classList.add("active");
    } else {
      item.classList.remove("active");
    }
  });
}
