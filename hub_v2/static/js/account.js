// Mobile navigation is scoped to the personal account page; desktop behavior stays shared.
document.addEventListener('DOMContentLoaded', () => {
  if (!document.body.classList.contains('ik-account')) return;
  const toggle = document.getElementById('sidebar-toggle');
  const shade = document.querySelector('.account-menu-shade');
  const mobile = window.matchMedia('(max-width: 760px)');
  const close = () => {
    document.body.classList.remove('account-menu-open');
    toggle?.setAttribute('aria-expanded', 'false');
  };
  toggle?.setAttribute('aria-controls', 'main-sidebar');
  toggle?.setAttribute('aria-label', 'Abrir menu de navegação');
  toggle?.setAttribute('aria-expanded', 'false');
  toggle?.addEventListener('click', () => {
    if (!mobile.matches) return;
    const open = document.body.classList.toggle('account-menu-open');
    toggle.setAttribute('aria-expanded', String(open));
  });
  shade?.addEventListener('click', close);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && document.body.classList.contains('account-menu-open')) {
      close(); toggle?.focus();
    }
  });
  mobile.addEventListener('change', close);
});
