/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * A dialog that chooses a folder by walking a tree, for the Documents step of
 * a RAG setup: the file system of the compute server or SAS Content. Each call
 * builds a fresh Bootstrap modal and resolves once it is hidden - with the
 * folder that was chosen, or null for any other way of closing it.
 *
 * What the dialog shows is the folder it is in: a breadcrumb of its path, the
 * folders directly below it, and "Choose this folder", which takes it. A
 * folder of the list is opened with a click. `floor` is the folder the dialog
 * may not go above (the start folder an admin set, without leave to browse
 * above it); without one it can go up to `/`. A listing that answers after
 * another folder was opened is dropped.
 */

import Modal from 'bootstrap/js/dist/modal';

export interface FolderPickerText {
  title: string;
  /** What the top of the tree is called in the breadcrumb, such as "SAS Content". */
  top: string;
  up: string;
  choose: string;
  cancel: string;
  /** The status while the first listing waits, such as for a compute session. */
  loading: string;
  empty: string;
  /** A folder that cannot be listed; `{path}` stands for its path. */
  unavailable: string;
  /** Any other failure; `{error}` stands for its message. */
  failed: string;
  /** Under the list: `{path}` stands for the folder that "Choose this folder" takes. */
  current: string;
  /** Why the top of the tree cannot be chosen. */
  topNotChosen: string;
}

export interface FolderPickerOptions {
  text: FolderPickerText;
  /** The folder the dialog opens in. */
  start: string;
  /** The folder it may not go above; null: up to `/`. */
  floor: string | null;
  /** The folders below `path` by name, or null when `path` cannot be listed. */
  list(path: string): Promise<string[] | null>;
  /** Id prefix of the dialog's elements, for tests and labels. */
  idPrefix: string;
}

/** `path` without a trailing slash, with one leading slash; `/` stays `/`. */
export function normalFolder(path: string): string {
  const segments = String(path ?? '')
    .split('/')
    .map((segment) => segment.trim())
    .filter((segment) => segment !== '');
  return `/${segments.join('/')}`;
}

/** Whether `path` is `folder` or lies below it. */
export function isWithin(path: string, folder: string): boolean {
  const inner = normalFolder(path);
  const outer = normalFolder(folder);
  return outer === '/' || inner === outer || inner.startsWith(`${outer}/`);
}

function parentOf(path: string): string {
  const segments = normalFolder(path).split('/').filter(Boolean);
  return `/${segments.slice(0, -1).join('/')}`;
}

function childOf(path: string, name: string): string {
  return normalFolder(`${normalFolder(path)}/${name}`);
}

export function showFolderPicker(options: FolderPickerOptions): Promise<string | null> {
  const { text, idPrefix } = options;
  const floor = options.floor ? normalFolder(options.floor) : null;
  let path = normalFolder(options.start);
  if (floor && !isWithin(path, floor)) path = floor;

  return new Promise((resolve) => {
    const wrapper = document.createElement('div');
    wrapper.classList.add('modal', 'fade');
    wrapper.id = `${idPrefix}-folder-picker`;
    wrapper.setAttribute('tabindex', '-1');
    wrapper.setAttribute('aria-labelledby', `${idPrefix}-folder-picker-title`);
    const dialog = document.createElement('div');
    dialog.classList.add('modal-dialog', 'modal-dialog-scrollable');
    const content = document.createElement('div');
    content.classList.add('modal-content');

    const header = document.createElement('div');
    header.classList.add('modal-header');
    const title = document.createElement('h2');
    title.classList.add('modal-title', 'fs-5');
    title.id = `${idPrefix}-folder-picker-title`;
    title.textContent = text.title;
    const close = document.createElement('button');
    close.type = 'button';
    close.classList.add('btn-close');
    close.setAttribute('data-bs-dismiss', 'modal');
    close.setAttribute('aria-label', text.cancel);
    header.append(title, close);

    const body = document.createElement('div');
    body.classList.add('modal-body');
    const navRow = document.createElement('div');
    navRow.classList.add('d-flex', 'align-items-center', 'gap-2', 'mb-2');
    const upButton = document.createElement('button');
    upButton.type = 'button';
    upButton.id = `${idPrefix}-folder-picker-up`;
    upButton.classList.add('btn', 'btn-outline-secondary', 'btn-sm');
    upButton.textContent = text.up;
    const crumbs = document.createElement('nav');
    crumbs.id = `${idPrefix}-folder-picker-path`;
    crumbs.setAttribute('aria-label', text.title);
    crumbs.classList.add('d-flex', 'flex-wrap', 'align-items-center', 'gap-1', 'small', 'text-break');
    navRow.append(upButton, crumbs);
    const status = document.createElement('div');
    status.id = `${idPrefix}-folder-picker-status`;
    status.classList.add('small', 'text-body-secondary', 'mb-2');
    status.setAttribute('role', 'status');
    const listing = document.createElement('div');
    listing.id = `${idPrefix}-folder-picker-list`;
    listing.classList.add('list-group');
    const currentLine = document.createElement('div');
    currentLine.id = `${idPrefix}-folder-picker-current`;
    currentLine.classList.add('small', 'mt-3', 'text-break');
    body.append(navRow, status, listing, currentLine);

    const footer = document.createElement('div');
    footer.classList.add('modal-footer');
    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.classList.add('btn', 'btn-secondary');
    cancelButton.setAttribute('data-bs-dismiss', 'modal');
    cancelButton.textContent = text.cancel;
    const chooseButton = document.createElement('button');
    chooseButton.type = 'button';
    chooseButton.id = `${idPrefix}-folder-picker-choose`;
    chooseButton.classList.add('btn', 'btn-primary');
    chooseButton.textContent = text.choose;
    footer.append(cancelButton, chooseButton);

    content.append(header, body, footer);
    dialog.appendChild(content);
    wrapper.appendChild(dialog);

    let chosen: string | null = null;
    // the listing that is drawn; an older one that answers late is dropped
    let shown = 0;
    let first = true;

    const crumbButton = (label: string, target: string, current: boolean): HTMLElement => {
      if (current) {
        const span = document.createElement('span');
        span.classList.add('fw-semibold');
        span.textContent = label;
        span.setAttribute('aria-current', 'location');
        return span;
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.classList.add('btn', 'btn-link', 'btn-sm', 'p-0');
      button.textContent = label;
      button.addEventListener('click', () => void open(target));
      return button;
    };

    const drawPath = (): void => {
      const separator = (): HTMLElement => {
        const span = document.createElement('span');
        span.classList.add('text-body-secondary');
        span.textContent = '/';
        return span;
      };
      const parts: HTMLElement[] = [];
      // With a floor the breadcrumb starts at it: what is above it cannot be opened.
      const base = floor ?? '/';
      parts.push(crumbButton(base === '/' ? text.top : base, base, path === base));
      let walked = base;
      for (const segment of normalFolder(path).slice(base === '/' ? 1 : base.length + 1).split('/').filter(Boolean)) {
        walked = childOf(walked, segment);
        parts.push(separator(), crumbButton(segment, walked, walked === path));
      }
      crumbs.replaceChildren(...parts);
      upButton.disabled = path === '/' || path === floor;
      currentLine.textContent = text.current.replace('{path}', path);
      chooseButton.disabled = path === '/';
      chooseButton.title = path === '/' ? text.topNotChosen : '';
    };

    async function open(target: string): Promise<void> {
      path = normalFolder(target);
      const mine = ++shown;
      drawPath();
      listing.replaceChildren();
      status.textContent = first ? text.loading : '…';
      listing.setAttribute('aria-busy', 'true');
      let names: string[] | null;
      try {
        names = await options.list(path);
      } catch (error) {
        if (mine !== shown) return;
        listing.removeAttribute('aria-busy');
        status.textContent = text.failed.replace('{error}', error instanceof Error ? error.message : String(error));
        return;
      }
      if (mine !== shown) return;
      first = false;
      listing.removeAttribute('aria-busy');
      if (names === null) {
        status.textContent = text.unavailable.replace('{path}', path);
        return;
      }
      status.textContent = names.length === 0 ? text.empty : '';
      listing.replaceChildren(
        ...names.map((name) => {
          const item = document.createElement('button');
          item.type = 'button';
          item.classList.add('list-group-item', 'list-group-item-action', 'text-break');
          item.dataset.folder = name;
          item.textContent = name;
          item.addEventListener('click', () => void open(childOf(path, name)));
          return item;
        })
      );
    }

    upButton.addEventListener('click', () => {
      if (!upButton.disabled) void open(parentOf(path));
    });
    const modal = new Modal(wrapper);
    chooseButton.addEventListener('click', () => {
      if (path === '/') return;
      chosen = path;
      modal.hide();
    });
    wrapper.addEventListener('hidden.bs.modal', () => {
      modal.dispose();
      wrapper.remove();
      resolve(chosen);
    });
    wrapper.addEventListener('shown.bs.modal', () => (listing.querySelector('button') as HTMLElement | null)?.focus());

    document.body.appendChild(wrapper);
    modal.show();
    void open(path);
  });
}
