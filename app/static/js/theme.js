/**
 * Dark-mode toggle. Class strategy, persisted in localStorage, honouring
 * prefers-color-scheme on a first visit.
 *
 * Progressive: the toggle button is hidden in the served HTML and only
 * revealed here, so a JS-less visitor is never shown a dead control.
 */
const STORAGE_KEY = 'feedme:theme';
const root = document.documentElement;

function stored() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function persist(theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* private mode: the toggle still works for this page view */
  }
}

function apply(theme) {
  root.classList.toggle('dark', theme === 'dark');
}

function current() {
  return root.classList.contains('dark') ? 'dark' : 'light';
}

const preferred =
  stored() ??
  (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
apply(preferred);

const toggle = document.querySelector('[data-theme-toggle]');
if (toggle) {
  toggle.hidden = false;
  toggle.setAttribute('aria-pressed', String(current() === 'dark'));
  toggle.addEventListener('click', () => {
    const next = current() === 'dark' ? 'light' : 'dark';
    apply(next);
    persist(next);
    toggle.setAttribute('aria-pressed', String(next === 'dark'));
  });
}
