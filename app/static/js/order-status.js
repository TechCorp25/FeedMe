/**
 * Live status for orders that are still moving.
 *
 * An enhancement, never the source: every status on the page is rendered
 * by the server on first request (03-FRONTEND.md), so with this file
 * absent, or broken, a customer sees the same thing one refresh later.
 *
 * Only non-terminal orders are polled, and an order that reaches a
 * terminal status stops being polled — a collected order never changes
 * again, and asking about it forever would be asking a question that is
 * already answered. Nothing here writes: it reads one JSON endpoint that
 * is scoped to the signed-in customer at the repository.
 */
const POLL_INTERVAL_MS = 60000;

/** Statuses after which an order never changes again (04-WORKFLOWS.md). */
const TERMINAL = new Set(['collected', 'delivered', 'cancelled']);

const watched = new Map();

document.querySelectorAll('[data-order-status]').forEach((element) => {
  const reference = element.getAttribute('data-order-status');
  const label = element.querySelector('[data-order-status-label]');
  if (!reference || !label) return;
  if (TERMINAL.has(label.textContent.trim().toLowerCase())) return;
  watched.set(reference, label);
});

/** The cancel control and the sentence that replaces it, if on this page. */
const cancelBlock = document.querySelector('[data-order-cancel]');
const cancelClosed = document.querySelector('[data-order-cancel-closed]');

let timer = null;
if (watched.size) {
  timer = window.setInterval(poll, POLL_INTERVAL_MS);
  // Asking again the moment a backgrounded tab is looked at is the one
  // time a customer is actually waiting for the answer.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') poll();
  });
}

async function poll() {
  await Promise.all([...watched.keys()].map(refresh));
  if (!watched.size && timer !== null) {
    window.clearInterval(timer);
    timer = null;
  }
}

async function refresh(reference) {
  const label = watched.get(reference);
  if (!label) return;

  let payload;
  try {
    const response = await fetch(
      `/api/orders/${encodeURIComponent(reference)}/status`,
      { headers: { Accept: 'application/json' }, credentials: 'same-origin' },
    );
    // Only a settled answer stops the polling. A signed-out session is
    // redirected to the sign-in page and a reference that is not this
    // customer's answers 404: in both cases there is no status coming,
    // ever, so asking again would be asking nothing. A 500, a 503 or a
    // 429 is the opposite — the answer exists and the server could not
    // give it this second — and treating those as final would leave the
    // status frozen until the customer thought to reload.
    if (response.redirected || response.status === 404) {
      watched.delete(reference);
      return;
    }
    if (!response.ok) return;
    payload = await response.json();
  } catch {
    // Offline, or the request was cut off. The rendered status is still
    // what the server last said, so it is left alone and asked again on
    // the next tick.
    return;
  }

  if (typeof payload?.status_label === 'string') {
    label.textContent = payload.status_label;
  }
  // The kitchen can start preparing an order while this page sits open.
  // Leaving the cancel button drawn would contradict the status beside
  // it and offer a control that can now only produce an error.
  if (payload?.can_cancel === false) closeCancellation(payload.is_terminal);
  if (payload?.is_terminal) watched.delete(reference);
}

/**
 * Take the cancel control off a page it no longer applies to.
 *
 * The sentence that replaces it explains that the kitchen has started,
 * so it is only right while the order is still live. An order that
 * reached a terminal status — cancelled in another tab, or collected —
 * says so in the status stamp already, and telling somebody their
 * cancelled order is being prepared would be worse than saying nothing.
 */
function closeCancellation(isTerminal) {
  if (cancelBlock) cancelBlock.hidden = true;
  if (cancelClosed) cancelClosed.hidden = Boolean(isTerminal);
}
