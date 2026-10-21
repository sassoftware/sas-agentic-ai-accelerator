/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * What is open cannot be changed while a write to SAS Model Manager is out.
 *
 * A save, a manifest or a delete is a row of requests, and between two of
 * them a builder reads the page again: the item its picker names, the runs,
 * the fields. With another item opened in between, Save Experiments uploaded
 * a tracker without a single run to the prompt-test it had been clicked for,
 * and Save setup wrote one part of a setup to the one that was open at the
 * click and the rest to the one opened after it - where the note then said
 * "saved".
 *
 * So every such write runs through `during`. While one is out, the controls
 * that decide what is open - the kind of thing, the project, the item, Create
 * and Delete - cannot be used; they can again when the last write has ended,
 * whether it went through, was refused or threw. What a write has to say is
 * thereby said while the item it was made for is still the one on the page.
 *
 * The controls are those inside `block`, a fieldset the builder puts them
 * into on its Setup card. A disabled fieldset disables every field and
 * button it holds and leaves the `disabled` state of each of them alone: a
 * button that is off for a reason of its own is still off afterwards, and one
 * that is switched on meanwhile cannot be used before the write has ended.
 *
 * A disabled control stops a person, not a script: the search field that
 * stands in for a list sets the value of its list and sends `change` itself,
 * and so can any other code. While a write is out such a change is undone -
 * a list gets back the entry it had when the write began (its first entry,
 * the placeholder, where that one has left the list meanwhile), a tick box
 * or a choice its state - and goes no further: the builder's own handlers
 * never hear of it.
 *
 * A creation does not come through here. Its dialog lies over the page and
 * cannot be closed while the creation is out (see createCreateModal), so
 * nothing under it can be chosen meanwhile.
 *
 * The buttons that write the open item - a save, a manifest, a delete - are
 * off for as long as any write is out, its own included: a write is a row of
 * requests, and a second one started between two of them wrote over what the
 * first had just written (a Save setup that landed after the job reference
 * of a manifest left the stored setup without its job). Those that are not
 * inside `block` follow `busy`, and the builder sets them again when it
 * changes (`onChange`).
 */
export interface SelectionLock {
  /** Holds the controls that decide what is open; `aria-busy` while a write is out. */
  block: HTMLFieldSetElement;
  /**
   * Run a write with the selection locked. Resolves and rejects as `write`
   * does; the lock is taken before `write` is called and given back when its
   * promise has settled. Writes may overlap and may be nested.
   */
  during<T>(write: () => Promise<T>): Promise<T>;
  /** Whether a write is out. */
  readonly busy: boolean;
  /** Called when the first write begins (before it is called) and when the last one has ended. */
  onChange(listener: () => void): void;
}

export function createSelectionLock(id: string): SelectionLock {
  const block = document.createElement('fieldset');
  block.id = id;
  let writesOut = 0;
  // What the lists and the tick boxes held when the first write began.
  const held = new Map<HTMLSelectElement | HTMLInputElement, string | boolean>();
  block.addEventListener(
    'change',
    (event) => {
      const control = event.target;
      if (writesOut === 0 || !(control instanceof HTMLSelectElement || control instanceof HTMLInputElement) || !held.has(control)) return;
      if (control instanceof HTMLSelectElement) {
        control.value = String(held.get(control));
        if (control.selectedIndex < 0) control.selectedIndex = 0;
      } else {
        // A choice that was made took the mark from the other choices of its
        // group: all of them get theirs back.
        held.forEach((state, box) => {
          if (box instanceof HTMLInputElement) box.checked = Boolean(state);
        });
      }
      event.stopImmediatePropagation();
    },
    // Before the handlers of the control itself.
    true
  );
  const listeners: Array<() => void> = [];
  const changed = (): void => listeners.forEach((listener) => listener());
  return {
    block,
    async during<T>(write: () => Promise<T>): Promise<T> {
      const first = writesOut === 0;
      if (first) {
        held.clear();
        block.querySelectorAll('select').forEach((list) => held.set(list, list.value));
        block.querySelectorAll<HTMLInputElement>('input[type="checkbox"], input[type="radio"]').forEach((box) => held.set(box, box.checked));
      }
      writesOut += 1;
      block.disabled = true;
      block.setAttribute('aria-busy', 'true');
      if (first) changed();
      try {
        return await write();
      } finally {
        writesOut -= 1;
        if (writesOut === 0) {
          block.disabled = false;
          block.removeAttribute('aria-busy');
          changed();
        }
      }
    },
    get busy(): boolean {
      return writesOut > 0;
    },
    onChange(listener: () => void): void {
      listeners.push(listener);
    },
  };
}
