/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Small Bootstrap toast helper for transient notifications. Toasts stack in a
 * shared fixed container in the bottom-right corner, auto-dismiss after a few
 * seconds, and remove themselves from the DOM once hidden.
 */

import Toast from 'bootstrap/js/dist/toast';

const TOAST_CONTAINER_ID = 'pb-toast-container';

export function showToast(message: string): void {
  let toastContainer = document.getElementById(TOAST_CONTAINER_ID);
  if (!toastContainer) {
    toastContainer = document.createElement('div');
    toastContainer.id = TOAST_CONTAINER_ID;
    toastContainer.classList.add('toast-container', 'position-fixed', 'bottom-0', 'end-0', 'p-3');
    document.body.appendChild(toastContainer);
  }

  const toastElement = document.createElement('div');
  toastElement.classList.add('toast', 'align-items-center');
  toastElement.setAttribute('role', 'status');
  toastElement.setAttribute('aria-live', 'polite');
  toastElement.setAttribute('aria-atomic', 'true');
  const toastFlex = document.createElement('div');
  toastFlex.classList.add('d-flex');
  const toastBody = document.createElement('div');
  toastBody.classList.add('toast-body');
  toastBody.textContent = message;
  const toastCloseButton = document.createElement('button');
  toastCloseButton.type = 'button';
  toastCloseButton.classList.add('btn-close', 'me-2', 'm-auto');
  toastCloseButton.setAttribute('data-bs-dismiss', 'toast');
  toastCloseButton.setAttribute('aria-label', 'Close');
  toastFlex.appendChild(toastBody);
  toastFlex.appendChild(toastCloseButton);
  toastElement.appendChild(toastFlex);
  toastContainer.appendChild(toastElement);

  toastElement.addEventListener('hidden.bs.toast', () => toastElement.remove());
  new Toast(toastElement, { delay: 5000 }).show();
}
