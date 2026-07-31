/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * The "create a thing" dialog: a trigger button plus a Bootstrap modal
 * carrying a name and a description.
 *
 * Creating a governed artifact is a deliberate act, and a dialog says so —
 * where a text box wedged beside a picker reads as part of choosing. It also
 * gives the description somewhere to live at creation time, which is the only
 * moment anyone reliably writes one.
 *
 * Shared by the Prompt Builder (projects, prompts) and the RAG Builder
 * (projects, setups).
 */

/**
 * Every field is optional because the callers read them out of a loaded i18n
 * bundle, where a missing key is a real possibility rather than a type error.
 * The dialog renders with a blank in that slot instead of refusing to build.
 */
export interface CreateModalText {
  /** Used for both the trigger button and the dialog title. */
  modalTitle?: string;
  /** Optional sentence above the inputs. */
  modalDescription?: string;
  nameLabel?: string;
  descriptionLabel?: string;
  closeButtonText?: string;
  saveButtonText?: string;
}

/**
 * Append a trigger button and its modal to `container`.
 *
 * `prefix` names the DOM ids the caller reads back: `<prefix>Modal`,
 * `<prefix>Name`, `<prefix>Description`.
 */
export function createCreateModal(
  container: HTMLElement,
  prefix: string,
  text: CreateModalText,
  onSave: () => void
): void {
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.classList.add('btn', 'btn-primary');
  toggle.setAttribute('data-bs-toggle', 'modal');
  toggle.setAttribute('data-bs-target', `#${prefix}Modal`);
  toggle.innerHTML = text.modalTitle ?? '';

  const wrapper = document.createElement('div');
  wrapper.classList.add('modal', 'fade');
  wrapper.setAttribute('id', `${prefix}Modal`);
  wrapper.setAttribute('tabindex', '-1');

  const dialog = document.createElement('div');
  dialog.classList.add('modal-dialog');
  const content = document.createElement('div');
  content.classList.add('modal-content');

  const header = document.createElement('div');
  header.classList.add('modal-header');
  const title = document.createElement('h2');
  title.classList.add('modal-title', 'fs-5');
  title.innerHTML = text.modalTitle ?? '';
  const close = document.createElement('button');
  close.type = 'button';
  close.classList.add('btn-close');
  close.setAttribute('data-bs-dismiss', 'modal');
  close.setAttribute('aria-label', 'Close');
  header.appendChild(title);
  header.appendChild(close);
  content.appendChild(header);

  const body = document.createElement('div');
  body.classList.add('modal-body');
  if (text.modalDescription) {
    const description = document.createElement('p');
    description.innerText = text.modalDescription;
    body.appendChild(description);
  }
  const nameText = document.createElement('span');
  nameText.innerHTML = `${text.nameLabel}:`;
  const nameInput = document.createElement('input');
  nameInput.setAttribute('type', 'text');
  nameInput.setAttribute('placeholder', text.nameLabel ?? '');
  nameInput.setAttribute('id', `${prefix}Name`);
  const descriptionText = document.createElement('span');
  descriptionText.innerHTML = `${text.descriptionLabel}:`;
  const descriptionInput = document.createElement('input');
  descriptionInput.setAttribute('type', 'text');
  descriptionInput.setAttribute('placeholder', text.descriptionLabel ?? '');
  descriptionInput.setAttribute('id', `${prefix}Description`);
  body.appendChild(nameText);
  body.appendChild(nameInput);
  body.appendChild(document.createElement('br'));
  body.appendChild(descriptionText);
  body.appendChild(descriptionInput);
  content.appendChild(body);

  const footer = document.createElement('div');
  footer.classList.add('modal-footer');
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.classList.add('btn', 'btn-secondary');
  cancel.setAttribute('data-bs-dismiss', 'modal');
  cancel.innerHTML = text.closeButtonText ?? '';
  const save = document.createElement('button');
  save.type = 'button';
  save.classList.add('btn', 'btn-primary');
  save.innerHTML = text.saveButtonText ?? '';
  save.onclick = () => onSave();
  footer.appendChild(cancel);
  footer.appendChild(save);
  content.appendChild(footer);

  dialog.appendChild(content);
  wrapper.appendChild(dialog);
  container.appendChild(toggle);
  container.appendChild(wrapper);
}
