/**
 * app.js — shared JS for all pages
 */

/**
 * Trigger a manual data refresh via the /api/refresh endpoint.
 * Updates the refresh button state while the request is in flight.
 */
async function triggerRefresh() {
  const btn = document.getElementById('refreshBtn');
  if (!btn) return;

  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-arrows-rotate fa-spin me-1"></i>Refreshing…';

  try {
    const resp = await fetch('/api/refresh', { method: 'POST' });
    const data = await resp.json();

    if (data.status === 'success') {
      const r = data.result;
      showToast(
        `Refresh complete: +${r.events_new} events, ${r.events_updated} updated, +${r.market_new} market rows`,
        'success'
      );
      // Reload the page after a short delay so updated data is visible
      setTimeout(() => location.reload(), 1500);
    } else {
      showToast(`Refresh failed: ${data.message}`, 'danger');
    }
  } catch (err) {
    showToast(`Network error: ${err}`, 'danger');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fa-solid fa-arrows-rotate me-1"></i>Refresh Data';
  }
}

/**
 * Show a Bootstrap toast notification.
 */
function showToast(message, type = 'success') {
  // Ensure a toast container exists
  let container = document.getElementById('toastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
    container.style.zIndex = '9999';
    document.body.appendChild(container);
  }

  const id = 'toast-' + Date.now();
  const bgClass = type === 'success' ? 'bg-success' : 'bg-danger';
  const html = `
    <div id="${id}" class="toast align-items-center ${bgClass} text-white border-0"
         role="alert" aria-live="assertive">
      <div class="d-flex">
        <div class="toast-body">${message}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto"
                data-bs-dismiss="toast"></button>
      </div>
    </div>`;
  container.insertAdjacentHTML('beforeend', html);

  const toastEl = document.getElementById(id);
  const toast   = new bootstrap.Toast(toastEl, { delay: 5000 });
  toast.show();
  toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}
