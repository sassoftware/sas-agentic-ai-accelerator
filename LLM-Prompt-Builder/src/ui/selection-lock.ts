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
 * A creation does not come through here. Its dialog lies over the page and
 * cannot be closed while the creation is out (see createCreateModal), so
 * nothing under it can be chosen meanwhile.
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
}

export function createSelectionLock(id: string): SelectionLock {
  const block = document.createElement('fieldset');
  block.id = id;
  let writesOut = 0;
  return {
    block,
    async during<T>(write: () => Promise<T>): Promise<T> {
      writesOut += 1;
      block.disabled = true;
      block.setAttribute('aria-busy', 'true');
      try {
        return await write();
      } finally {
        writesOut -= 1;
        if (writesOut === 0) {
          block.disabled = false;
          block.removeAttribute('aria-busy');
        }
      }
    },
  };
}
