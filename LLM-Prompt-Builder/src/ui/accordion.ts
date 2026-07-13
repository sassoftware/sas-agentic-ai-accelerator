/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Create a Bootstrap accordion item (header + collapsible body) inside the given
 * container. Collapse behaviour is driven by Bootstrap's `data-bs-*` data-api,
 * which is registered when `bootstrap/js/dist/collapse` is imported at startup.
 */
export function createAccordionItem(
  accordionContainer: HTMLElement,
  baselineID: string,
  itemID: string,
  interfaceText: Record<string, string> | string
): void {
  const accordionItem = document.createElement('div');
  accordionItem.setAttribute('class', 'accordion-item');

  const accordionHeader = document.createElement('h2');
  accordionHeader.setAttribute('class', 'accordion-header');

  const accordionButton = document.createElement('button');
  accordionButton.setAttribute('class', 'accordion-button collapsed');
  accordionButton.setAttribute('type', 'button');
  accordionButton.setAttribute('data-bs-toggle', 'collapse');
  accordionButton.setAttribute(
    'data-bs-target',
    `#${baselineID}-${itemID}-accordionBody`
  );
  accordionButton.setAttribute('aria-expanded', 'false');
  accordionButton.setAttribute(
    'aria-controls',
    `${baselineID}-${itemID}-accordionBody`
  );

  if (typeof interfaceText === 'object') {
    accordionButton.innerText = interfaceText[itemID] ?? itemID;
  } else {
    accordionButton.innerText = interfaceText;
  }

  accordionHeader.appendChild(accordionButton);
  accordionItem.appendChild(accordionHeader);

  const accordionCollapse = document.createElement('div');
  accordionCollapse.setAttribute('id', `${baselineID}-${itemID}-accordionBody`);
  accordionCollapse.setAttribute('class', 'accordion-collapse collapse');
  accordionCollapse.setAttribute('data-bs-parent', `#${baselineID}-accordion`);

  const accordionBody = document.createElement('div');
  accordionBody.setAttribute('class', 'accordion-body');
  accordionBody.setAttribute('id', `${baselineID}-${itemID}-content`);

  accordionCollapse.appendChild(accordionBody);
  accordionItem.appendChild(accordionCollapse);
  accordionContainer.appendChild(accordionItem);
}
