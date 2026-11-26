// Keep the fields mounted: only HTMX's server-calculated result is replaced.
(() => {
  const form = document.getElementById('estimate-form');
  const result = document.getElementById('estimate-result');
  const error = document.getElementById('estimate-network-error');
  if (!form || !result || !error) return;
  const failed = () => {
    result.removeAttribute('aria-busy');
    result.replaceChildren();
    error.hidden = false;
  };
  form.addEventListener('htmx:beforeRequest', () => {
    result.setAttribute('aria-busy', 'true');
    error.hidden = true;
  });
  form.addEventListener('htmx:afterRequest', event => {
    result.removeAttribute('aria-busy');
    if (event.detail.failed) failed();
  });
  form.addEventListener('htmx:sendError', failed);
  form.addEventListener('htmx:timeout', failed);
})();
