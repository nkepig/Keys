(() => {
  const TOAST_ID = 'keys-toast';
  let toastTimer = null;

  function ensureToastEl() {
    let el = document.getElementById(TOAST_ID);
    if (el) return el;
    el = document.createElement('div');
    el.id = TOAST_ID;
    el.setAttribute('data-ui', 'toast');
    el.setAttribute('data-testid', 'toast');
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.hidden = true;
    document.body.appendChild(el);
    return el;
  }

  function showToast(message, type = 'success', duration = 2600) {
    const el = ensureToastEl();
    el.textContent = message;
    el.classList.remove('is-error', 'is-copy');
    if (type === 'error') el.classList.add('is-error');
    if (type === 'copy') el.classList.add('is-copy');
    el.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.hidden = true;
      toastTimer = null;
    }, duration);
  }

  async function apiFetch(url, options = {}) {
    try {
      const res = await fetch(url, { credentials: 'same-origin', ...options });
      const err = !res.ok ? await res.json().catch(() => ({})) : {};
      if (res.status === 401 && err.detail === '未登录') {
        window.location.href =
          '/login?next=' + encodeURIComponent(window.location.pathname + window.location.search);
        return null;
      }
      if (!res.ok) {
        showToast(err.detail || `请求失败 (${res.status})`, 'error');
        return null;
      }
      return res;
    } catch {
      showToast('网络错误，请检查连接', 'error');
      return null;
    }
  }

  async function logout() {
    try {
      await fetch('/logout', { method: 'POST', credentials: 'same-origin' });
    } catch {
      /* ignore */
    }
    window.location.href = '/login';
  }

  function initMobileNav() {
    const toggle = document.querySelector('[data-testid="mobile-nav-toggle"]');
    const panel = document.querySelector('[data-ui="mobile-nav-panel"]');
    if (!toggle || !panel) return;

    const close = () => {
      panel.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
    };

    toggle.addEventListener('click', () => {
      const open = panel.hidden;
      panel.hidden = !open;
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !panel.hidden) close();
    });

    document.addEventListener('click', (e) => {
      if (panel.hidden) return;
      if (e.target.closest('[data-testid="mobile-nav-toggle"]')) return;
      if (e.target.closest('[data-ui="mobile-nav-panel"]')) return;
      close();
    });
  }

  window.KeysUI = { apiFetch, logout, showToast };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initMobileNav);
  } else {
    initMobileNav();
  }
})();
