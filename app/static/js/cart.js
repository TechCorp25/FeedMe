/**
 * Cart mutation without a page load.
 *
 * Progressive by construction: every control this module touches is a real
 * form that posts to a real route and redirects (03-FRONTEND.md). Nothing
 * here supplies content, and nothing here is the only way to reach the
 * cart — with this file absent, or broken, the same forms still work.
 *
 * A failed request restores the previous UI state and says so. It never
 * fails silently, and it never leaves the badge showing a number the
 * server did not send.
 */
const ENDPOINT = '/api/cart';

const csrfToken = document
  .querySelector('meta[name="csrf-token"]')
  ?.getAttribute('content');

const forms = document.querySelectorAll('[data-cart-form]');
if (forms.length && csrfToken) {
  forms.forEach((form) => form.addEventListener('submit', onSubmit));
}

function counters() {
  return {
    badge: document.querySelector('[data-cart-count]'),
    label: document.querySelector('[data-cart-count-label]'),
  };
}

function paintCount(count) {
  const { badge, label } = counters();
  if (badge) badge.textContent = String(count);
  if (label) label.textContent = `${count} item${count === 1 ? '' : 's'}`;
}

/** A message beside the control that caused it, announced politely. */
function announce(form, message, tone) {
  let region = form.querySelector('[data-cart-message]');
  if (!region) {
    region = document.createElement('p');
    region.setAttribute('data-cart-message', '');
    region.setAttribute('role', 'status');
    region.setAttribute('aria-live', 'polite');
    form.append(region);
  }
  region.className = `cart-message cart-message--${tone}`;
  region.textContent = message;
}

function payloadFor(form) {
  const data = new FormData(form);
  const action = form.dataset.cartAction;
  const payload = {
    action,
    item_type: data.get('item_type'),
    item_id: data.get('item_id'),
  };
  if (action !== 'remove') {
    payload.quantity = Number(data.get('quantity'));
  }
  return payload;
}

async function onSubmit(event) {
  const form = event.currentTarget;
  const action = form.dataset.cartAction;

  // The cart page is a list of the very lines these controls change, so a
  // reload there is the honest redraw. Only the add control, which sits on
  // a page the customer is still reading, is worth handling in place.
  if (action !== 'add') return;

  event.preventDefault();
  const { badge } = counters();
  const previous = badge ? badge.textContent : null;
  const button = form.querySelector('button[type="submit"]');
  if (button) button.disabled = true;

  try {
    const response = await fetch(ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify(payloadFor(form)),
    });

    if (!response.ok) {
      if (badge && previous !== null) badge.textContent = previous;
      const body = await response.json().catch(() => ({}));
      announce(
        form,
        body.error === 'unavailable'
          ? 'That item is no longer available.'
          : 'That could not be added to your cart. Please try again.',
        'error'
      );
      return;
    }

    const body = await response.json();
    paintCount(body.item_count);
    announce(form, 'Added to your cart.', 'success');
  } catch {
    if (badge && previous !== null) badge.textContent = previous;
    announce(form, 'Your cart could not be reached. Please try again.', 'error');
  } finally {
    if (button) button.disabled = false;
  }
}
