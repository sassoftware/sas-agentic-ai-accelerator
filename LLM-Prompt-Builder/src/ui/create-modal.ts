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

import Modal from 'bootstrap/js/dist/modal';

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
  /** Said in the dialog when its button is clicked with no name entered. */
  nameRequiredText?: string;
  /**
   * Said in the dialog when `onSave` ends with an error it did not report
   * itself. Whether the thing was created is not known then, so the text
   * says to look before trying again.
   */
  failedText?: string;
}

/** What the dialog hands to `onSave`. */
export interface CreateModalDialog {
  /**
   * Say inside the dialog why nothing was created (a name that is missing, a
   * refusal of the service). The dialog is open over the page, so a message
   * anywhere else on the page lies behind its backdrop or out of view.
   */
  showError(message: string): void;
  /**
   * Close the dialog: the thing was created. While `onSave` is pending this
   * is the only way the dialog closes - Escape, Cancel, the X and a click
   * beside the dialog are ignored until the creation has ended.
   */
  close(): void;
}

/**
 * What stood in the two fields at the click, as it was typed. The name is the
 * one the dialog checked; a caller that reads the fields again after waiting
 * for a request creates a thing under whatever stands there by then.
 */
export interface CreateModalEntry {
  name: string;
  description: string;
}

/**
 * What does not make a name, because nobody can see it. A character is not
 * visible when it is
 *
 * - white space: `\s` of JavaScript and the Unicode property `White_Space`
 *   (blank, tab, no-break space, the spaces U+2000 to U+200A, the ideographic
 *   space U+3000, the next-line control U+0085, ...);
 * - one of the characters Unicode sets apart as not displayed, the property
 *   `Default_Ignorable_Code_Point`: the zero-width space U+200B, the
 *   zero-width non-joiner and joiner U+200C and U+200D, the word joiner
 *   U+2060, the byte order mark U+FEFF, the soft hyphen U+00AD, the combining
 *   grapheme joiner U+034F, the direction marks (U+061C, U+200E, U+200F,
 *   U+202A to U+202E, U+2066 to U+2069), the Mongolian vowel separator
 *   U+180E, the Hangul fillers U+115F, U+1160, U+3164 and U+FFA0, the
 *   variation selectors and the tag characters;
 * - a control character (general category `Cc`: U+0000 to U+001F, U+007F to
 *   U+009F), which has no shape of its own - a browser draws nothing for it
 *   or a box that stands for "no glyph";
 * - one of three characters whose shape is defined as empty and which are in
 *   none of the classes above: the braille pattern blank U+2800 (a braille
 *   cell with no dot raised), the musical null notehead U+1D159 and the
 *   Khitan small script filler U+16FE4.
 *
 * Every other character counts as visible: letters of any script, digits,
 * punctuation, symbols, emoji, a combining mark on its own. The list is only
 * ever used to ask whether a name has a visible character at all; no name is
 * rewritten with it.
 */
const NOT_VISIBLE = /[\s\p{White_Space}\p{Default_Ignorable_Code_Point}\p{Cc}\u2800\u{1D159}\u{16FE4}]/gu;

/**
 * Whether a name shows nothing: it is empty, or no character of it is visible
 * (see `NOT_VISIBLE`). `trim()` alone lets a zero-width space through, white
 * space and the not-displayed characters together let a braille blank
 * through, and the thing was then created under a name nobody can see. A name
 * with one visible character is a name, whatever else stands beside it.
 */
export function isBlankName(name: string): boolean {
  return name.replace(NOT_VISIBLE, '') === '';
}

/**
 * Append a trigger button and its modal to `container`.
 *
 * `prefix` names the DOM ids the caller reads back: `<prefix>Modal`,
 * `<prefix>Name`, `<prefix>Description`; a message of `showError` is in
 * `<prefix>Error`.
 *
 * `onSave` runs on a click on the dialog's button, with the name and the
 * description as they stood in the fields at that click (`entry`): the caller
 * creates the thing from those, not from the fields. While the promise it
 * returns is pending the button and the two fields are disabled and further
 * clicks are ignored: a double click must not create the thing twice, and what
 * is being created cannot be typed over. A message of the click before is
 * taken away when the next one starts and when the dialog is opened again.
 *
 * While `onSave` is pending the dialog cannot be closed either: Cancel and the
 * X are disabled, and Escape and a click beside the dialog are ignored. A
 * creation that is out cannot be called back, and a dialog closed over it
 * left its answer - a refusal above all - in a place nobody looks at. The
 * caller closes the dialog itself with `dialog.close()` once the thing exists;
 * that empties the two fields, which otherwise keep what was typed.
 *
 * `onSave` reports what went wrong with `dialog.showError`. Should it end with
 * an error instead (a request that throws where no one expected it), the
 * dialog catches it, writes it to the console and says `failedText`: a click
 * never ends without a word, and no error reaches the page.
 *
 * The focus stays in the dialog. Disabling the element that has it - the
 * Create button after Enter, the space bar or a click - lets the browser drop
 * the focus to <body>, outside the dialog, and Bootstrap hears Escape on the
 * dialog only. So the focus waits on the dialog itself while the creation is
 * out, and goes back to the element that had it when the creation has ended
 * and the dialog is still open. Escape closes an open dialog with no creation
 * pending wherever the focus is, <body> included.
 *
 * A name that shows nothing (see `isBlankName`: empty, or no visible
 * character in it) never reaches `onSave`: the dialog says that a name is
 * needed (`nameRequiredText`), marks the field and puts the cursor into it,
 * and its button stays usable. Sent on, the empty name was refused by SAS
 * Model Manager, and the person read that a thing of this name may already
 * exist. Every other rule about a name is the caller's.
 */
export function createCreateModal(
  container: HTMLElement,
  prefix: string,
  text: CreateModalText,
  onSave: (dialog: CreateModalDialog, entry: CreateModalEntry) => void | Promise<void>
): void {
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.classList.add('btn', 'btn-outline-secondary');
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
  // Each field is its label above an input as wide as the dialog. Label and
  // input on one line left the input at the browser's default width, beside
  // a label of any length: a name was cut off while it was typed.
  const field = (id: string, labelText: string | undefined, last: boolean): HTMLInputElement => {
    const group = document.createElement('div');
    group.classList.add('pb-create-field', last ? 'mb-0' : 'mb-3');
    const label = document.createElement('label');
    label.classList.add('form-label');
    label.htmlFor = id;
    label.textContent = labelText ?? '';
    const input = document.createElement('input');
    input.type = 'text';
    input.classList.add('form-control');
    input.id = id;
    group.appendChild(label);
    group.appendChild(input);
    body.appendChild(group);
    return input;
  };
  const nameInput = field(`${prefix}Name`, text.nameLabel, false);
  const descriptionInput = field(`${prefix}Description`, text.descriptionLabel, true);
  const error = document.createElement('div');
  error.id = `${prefix}Error`;
  error.classList.add('alert', 'alert-danger', 'pb-create-error', 'py-2', 'mt-3', 'mb-0');
  error.setAttribute('role', 'alert');
  error.hidden = true;
  body.appendChild(error);
  content.appendChild(body);
  const clearError = (): void => {
    error.textContent = '';
    error.hidden = true;
    nameInput.classList.remove('is-invalid');
    nameInput.removeAttribute('aria-invalid');
    nameInput.removeAttribute('aria-describedby');
  };
  // The mark of a missing name goes as soon as something is typed; the
  // message stays until the next click, like every other one.
  nameInput.addEventListener('input', () => {
    nameInput.classList.remove('is-invalid');
    nameInput.removeAttribute('aria-invalid');
  });
  // `saving`: a creation is out. `closed`: the caller has closed the dialog
  // over it (the thing exists), which is the one closing a creation lets by.
  let saving = false;
  let closed = false;
  const dialogHandle: CreateModalDialog = {
    showError: (message) => {
      error.textContent = message;
      error.hidden = false;
    },
    close: () => {
      closed = true;
      // The thing exists: the dialog opens empty for the next one. What was
      // typed is kept only where nothing was created (Cancel, a refusal) -
      // kept after a creation, the next opening offered the name and the
      // description of the thing just created, and the description went to
      // SAS Model Manager with the next one.
      nameInput.value = '';
      descriptionInput.value = '';
      Modal.getInstance(wrapper)?.hide();
    },
  };
  wrapper.addEventListener('show.bs.modal', clearError);
  // Escape, Cancel, the X and a click beside the dialog all end in this
  // event; while a creation is out it is refused.
  wrapper.addEventListener('hide.bs.modal', (event) => {
    if (saving && !closed) event.preventDefault();
  });
  // Bootstrap listens for Escape on the dialog, so a key pressed while the
  // focus is outside it (on <body>) closed nothing. While the dialog is open
  // the document is listened to as well, for the keys the dialog did not get.
  const closeOnEscape = (event: KeyboardEvent): void => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    if (event.target instanceof Node && wrapper.contains(event.target)) return;
    Modal.getInstance(wrapper)?.hide();
  };
  wrapper.addEventListener('show.bs.modal', () => document.addEventListener('keydown', closeOnEscape));
  wrapper.addEventListener('hidden.bs.modal', () => document.removeEventListener('keydown', closeOnEscape));

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
  const setPending = (pending: boolean): void => {
    saving = pending;
    for (const control of [save, cancel, close, nameInput, descriptionInput]) control.disabled = pending;
  };
  save.onclick = async () => {
    if (saving) return;
    clearError();
    // Read once: what is checked here is what the caller creates.
    const entry: CreateModalEntry = { name: nameInput.value, description: descriptionInput.value };
    if (isBlankName(entry.name)) {
      dialogHandle.showError(text.nameRequiredText ?? 'A name is required.');
      nameInput.classList.add('is-invalid');
      nameInput.setAttribute('aria-invalid', 'true');
      nameInput.setAttribute('aria-describedby', error.id);
      nameInput.focus();
      return;
    }
    // Where the focus is now is where it belongs afterwards. Every control of
    // the dialog is about to be disabled, so it waits on the dialog itself.
    const active = document.activeElement;
    const hadFocus = active instanceof HTMLElement && active !== wrapper && wrapper.contains(active) ? active : null;
    closed = false;
    setPending(true);
    wrapper.focus({ preventScroll: true });
    try {
      await onSave(dialogHandle, entry);
    } catch (failure) {
      console.error('A create dialog ended with an error.', failure);
      dialogHandle.showError(text.failedText ?? 'The creation did not finish. Check in SAS Model Manager whether it was created before you try again.');
    } finally {
      setPending(false);
      // Still open (a refusal, a failure): the focus goes back to the element
      // that had it, unless it has been put on a control of the dialog
      // meanwhile; in any case it is inside the dialog.
      if (!closed && wrapper.classList.contains('show')) {
        const now = document.activeElement;
        if (hadFocus && (now === wrapper || !wrapper.contains(now))) hadFocus.focus();
        else if (!wrapper.contains(now)) wrapper.focus({ preventScroll: true });
      }
    }
  };
  footer.appendChild(cancel);
  footer.appendChild(save);
  content.appendChild(footer);

  dialog.appendChild(content);
  wrapper.appendChild(dialog);
  container.appendChild(toggle);
  container.appendChild(wrapper);
}
