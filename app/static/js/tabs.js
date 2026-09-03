/**
 * Upgrades the served item-tab markup to an ARIA tablist.
 *
 * The server sends four complete panels with their own headings, anchored
 * by id. Without this module they stack and the strip is a set of in-page
 * links — which is a working page, not a degraded one. This module only
 * toggles visibility; it never fetches or injects content.
 *
 * Progressive: exits quietly when the markup is not on the page.
 */
const container = document.querySelector('[data-item-tabs]');
const tabs = container ? Array.from(container.querySelectorAll('[data-tab]')) : [];
const panels = container
  ? Array.from(container.querySelectorAll('[data-tab-panel]'))
  : [];

function panelFor(tab) {
  return panels.find((panel) => `#${panel.id}` === tab.getAttribute('href'));
}

function select(tab, { focus = false, updateHash = true } = {}) {
  tabs.forEach((candidate) => {
    const isActive = candidate === tab;
    const panel = panelFor(candidate);
    candidate.setAttribute('aria-selected', String(isActive));
    // Roving tabindex: one stop for the whole strip, arrows move within it.
    candidate.tabIndex = isActive ? 0 : -1;
    if (panel) panel.hidden = !isActive;
  });

  if (updateHash) {
    // replaceState, not a hash assignment: the tab stays linkable and
    // survives reload without the browser scrolling the panel into view
    // and without stacking a history entry per tab press.
    history.replaceState(null, '', tab.getAttribute('href'));
  }
  if (focus) tab.focus();
}

function tabForHash(hash) {
  return tabs.find((tab) => tab.getAttribute('href') === hash);
}

function onKeydown(event) {
  const index = tabs.indexOf(event.target);
  if (index === -1) return;

  const moves = {
    ArrowRight: index + 1,
    ArrowLeft: index - 1,
    Home: 0,
    End: tabs.length - 1,
  };
  const next = moves[event.key];
  if (next === undefined) return;

  event.preventDefault();
  select(tabs[(next + tabs.length) % tabs.length], { focus: true });
}

if (container && tabs.length > 0 && panels.length === tabs.length) {
  container.querySelector('.tab-strip')?.setAttribute('role', 'tablist');

  tabs.forEach((tab) => {
    const panel = panelFor(tab);
    if (!panel) return;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', panel.id);
    tab.id = `${panel.id}-tab`;
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', tab.id);
    // The panel heading is the tab label once the strip is a tablist;
    // keeping both would read the same words twice to a screen reader.
    panel.querySelector('.tab-panel__heading')?.classList.add('visually-hidden');
    panel.tabIndex = 0;
  });

  tabs.forEach((tab) => {
    tab.addEventListener('click', (event) => {
      event.preventDefault();
      select(tab);
    });
    tab.addEventListener('keydown', onKeydown);
  });

  // A link into a specific tab, and the back button, both land here.
  window.addEventListener('hashchange', () => {
    const tab = tabForHash(window.location.hash);
    if (tab) select(tab, { updateHash: false });
  });

  select(tabForHash(window.location.hash) ?? tabs[0], { updateHash: false });
}
