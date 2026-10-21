/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A control that is off starts nothing - whichever way the click comes.
 *
 * A click of the mouse or of the keyboard on a disabled button never reaches
 * its listeners, and neither does `button.click()`. A click event a script
 * sends to it does: the browser hands it to the button's own listeners and to
 * those of the page around it (Bootstrap's, which open a dialog). A button is
 * off while what it starts is running or a write is out, so such a click
 * started that action a second time. This stops a click on a disabled button
 * or input - also one off because its fieldset is - before any listener of
 * the page, for every control of the page, the dialogs included. It listens
 * on the window in the capture phase, the first stop of every click: a
 * listener on the document in that phase - Bootstrap's for `data-bs-toggle`,
 * registered when its modules are imported - comes after it, whichever was
 * registered first. A click sent to a node inside a control - the text of a
 * button, which is no element - is a click on that control as well.
 */
export function refuseClicksOnDisabledControls(target: Window = window): void {
  target.addEventListener(
    'click',
    (event) => {
      const node = event.target instanceof Node ? event.target : null;
      const element = node instanceof Element ? node : (node?.parentElement ?? null);
      const control = element ? element.closest('button, input, select, textarea') : null;
      if (control?.matches(':disabled')) {
        event.stopImmediatePropagation();
        event.preventDefault();
      }
    },
    true
  );
}
