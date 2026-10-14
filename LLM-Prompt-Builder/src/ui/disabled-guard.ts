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
 * the page, for every control of the page, the dialogs included.
 */
export function refuseClicksOnDisabledControls(target: Document = document): void {
  target.addEventListener(
    'click',
    (event) => {
      const control = event.target instanceof Element ? event.target.closest('button, input, select, textarea') : null;
      if (control?.matches(':disabled')) {
        event.stopImmediatePropagation();
        event.preventDefault();
      }
    },
    true
  );
}
