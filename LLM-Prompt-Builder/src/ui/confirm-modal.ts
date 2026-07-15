/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Promise-based Bootstrap confirmation modal. Each call builds a fresh modal,
 * shows it, and resolves once it is fully hidden — true when the confirm
 * button was clicked, false for any other dismissal (cancel button, the X,
 * Escape, or a backdrop click). The modal element is removed from the DOM
 * after use, so calls can be awaited back to back (e.g. one confirmation per
 * prompt when deleting a project).
 */

import Modal from 'bootstrap/js/dist/modal';

export interface ConfirmModalOptions {
  title: string;
  /** Body content: strings become <p> elements (set via textContent). */
  body: (HTMLElement | string)[];
  confirmText: string;
  cancelText: string;
  /** Bootstrap button class for the confirm button; defaults to 'btn-danger'. */
  confirmClass?: string;
}

export function showConfirmModal(options: ConfirmModalOptions): Promise<boolean> {
  return new Promise((resolve) => {
    const confirmModalWrapper = document.createElement('div');
    confirmModalWrapper.classList.add('modal', 'fade');
    confirmModalWrapper.setAttribute('tabindex', '-1');
    const confirmModalDialog = document.createElement('div');
    confirmModalDialog.classList.add('modal-dialog');
    const confirmModalContent = document.createElement('div');
    confirmModalContent.classList.add('modal-content');
    // Create the modal header
    const confirmModalHeader = document.createElement('div');
    confirmModalHeader.classList.add('modal-header');
    const confirmModalTitle = document.createElement('h2');
    confirmModalTitle.classList.add('modal-title', 'fs-5');
    confirmModalTitle.textContent = options.title;
    const confirmModalCloseButton = document.createElement('button');
    confirmModalCloseButton.type = 'button';
    confirmModalCloseButton.classList.add('btn-close');
    confirmModalCloseButton.setAttribute('data-bs-dismiss', 'modal');
    confirmModalCloseButton.setAttribute('aria-label', 'Close');
    confirmModalHeader.appendChild(confirmModalTitle);
    confirmModalHeader.appendChild(confirmModalCloseButton);
    // Create the modal body
    const confirmModalBody = document.createElement('div');
    confirmModalBody.classList.add('modal-body');
    options.body.forEach((bodyItem) => {
      if (typeof bodyItem === 'string') {
        const bodyParagraph = document.createElement('p');
        bodyParagraph.textContent = bodyItem;
        confirmModalBody.appendChild(bodyParagraph);
      } else {
        confirmModalBody.appendChild(bodyItem);
      }
    });
    // Create the modal footer with the cancel and confirm buttons
    const confirmModalFooter = document.createElement('div');
    confirmModalFooter.classList.add('modal-footer');
    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.classList.add('btn', 'btn-secondary');
    cancelButton.setAttribute('data-bs-dismiss', 'modal');
    cancelButton.textContent = options.cancelText;
    const confirmButton = document.createElement('button');
    confirmButton.type = 'button';
    confirmButton.classList.add('btn', options.confirmClass ?? 'btn-danger');
    confirmButton.textContent = options.confirmText;
    confirmModalFooter.appendChild(cancelButton);
    confirmModalFooter.appendChild(confirmButton);
    // Append elements together
    confirmModalContent.appendChild(confirmModalHeader);
    confirmModalContent.appendChild(confirmModalBody);
    confirmModalContent.appendChild(confirmModalFooter);
    confirmModalDialog.appendChild(confirmModalContent);
    confirmModalWrapper.appendChild(confirmModalDialog);

    let confirmed = false;
    const modal = new Modal(confirmModalWrapper);
    confirmButton.onclick = () => {
      confirmed = true;
      modal.hide();
    };
    // Single exit path: every way of closing the modal ends in 'hidden.bs.modal'.
    confirmModalWrapper.addEventListener('hidden.bs.modal', () => {
      modal.dispose();
      confirmModalWrapper.remove();
      resolve(confirmed);
    });

    document.body.appendChild(confirmModalWrapper);
    modal.show();
  });
}
