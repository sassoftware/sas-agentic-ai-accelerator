/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Progressive-enhancement combobox: wraps an existing <select> with a text
 * input whose dropdown list filters live as you type — so the filtering and
 * the list are one control, visible together.
 *
 * The select stays in the DOM as the SINGLE source of truth: programmatic
 * code (and browser automation) keeps setting its value, repopulating its
 * options and listening to its change events. The combobox mirrors it both
 * ways — a change listener follows selections, a MutationObserver follows
 * repopulation — and collapses the select itself to a transparent 1x1 box
 * that stays scriptable but leaves the visual work to the input.
 *
 * Contract: option 0 is the picker's placeholder (every enhanced picker in
 * this app builds its list that way); its text becomes the input placeholder
 * and it is never listed as a pickable entry.
 */

export function attachCombobox(select: HTMLSelectElement): void {
  const parent = select.parentElement;
  if (!parent || select.dataset.combobox === 'attached') return;
  select.dataset.combobox = 'attached';

  const wrapper = document.createElement('div');
  wrapper.classList.add('pb-combobox');
  wrapper.style.position = 'relative';
  // Inherit the select's sizing so fixed-width picker rows and full-width
  // form rows keep their layout.
  if (select.style.width && select.style.width !== 'auto') {
    wrapper.style.width = select.style.width;
    wrapper.style.display = 'inline-block';
  }
  parent.insertBefore(wrapper, select);

  const input = document.createElement('input');
  input.type = 'text';
  input.id = `${select.id}-combobox`;
  input.autocomplete = 'off';
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-expanded', 'false');
  input.setAttribute('aria-controls', `${select.id}-combobox-list`);
  // form-select (not form-control) keeps the familiar dropdown caret.
  input.classList.add('form-select');
  if (select.classList.contains('form-select-sm')) input.classList.add('form-select-sm');

  const menu = document.createElement('div');
  menu.id = `${select.id}-combobox-list`;
  menu.classList.add('dropdown-menu');
  menu.style.position = 'absolute';
  menu.style.top = '100%';
  menu.style.left = '0';
  menu.style.minWidth = '100%';
  menu.style.maxHeight = '15rem';
  menu.style.overflowY = 'auto';

  wrapper.appendChild(input);
  wrapper.appendChild(menu);
  wrapper.appendChild(select);
  // Collapse the select to a transparent 1x1 box: gone visually and from the
  // tab order, still present with a real bounding box for scripts and tests.
  select.style.position = 'absolute';
  select.style.left = '0';
  select.style.bottom = '0';
  select.style.width = '1px';
  select.style.height = '1px';
  select.style.opacity = '0';
  select.style.pointerEvents = 'none';
  select.tabIndex = -1;
  select.setAttribute('aria-hidden', 'true');

  // null = the user is not typing a filter (opening shows the FULL list —
  // filtering by the selection's own label would show just that one entry).
  let typedFilter: string | null = null;
  let activeIndex = -1;

  const entries = (): HTMLOptionElement[] => [...select.options].slice(1);
  const selectionLabel = (): string =>
    select.selectedIndex > 0 ? (select.options[select.selectedIndex]?.text ?? '') : '';

  function syncFromSelect(): void {
    input.value = selectionLabel();
    input.placeholder = select.options[0]?.text ?? '';
  }

  function close(): void {
    menu.classList.remove('show');
    input.setAttribute('aria-expanded', 'false');
    typedFilter = null;
    activeIndex = -1;
  }

  function pick(value: string): void {
    select.value = value;
    select.dispatchEvent(new Event('change'));
    syncFromSelect();
    close();
  }

  function renderMenu(): void {
    const needle = (typedFilter ?? '').trim().toLowerCase();
    menu.innerHTML = '';
    entries()
      .filter((option) => needle === '' || option.text.toLowerCase().includes(needle))
      .forEach((option, index) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.classList.add('dropdown-item');
        if (select.selectedIndex > 0 && option.value === select.value) item.classList.add('fw-semibold');
        if (index === activeIndex) item.classList.add('active');
        item.innerText = option.text;
        item.dataset.value = option.value;
        // mousedown (with the default prevented) so the input keeps focus and
        // its blur handler cannot close the menu before the pick lands.
        item.addEventListener('mousedown', (event) => {
          event.preventDefault();
          pick(option.value);
        });
        menu.appendChild(item);
      });
    menu.classList.add('show');
    input.setAttribute('aria-expanded', 'true');
  }

  function openMenu(): void {
    typedFilter = null;
    activeIndex = -1;
    renderMenu();
  }

  input.addEventListener('focus', openMenu);
  input.addEventListener('click', openMenu);
  input.addEventListener('input', () => {
    typedFilter = input.value;
    activeIndex = -1;
    renderMenu();
  });
  input.addEventListener('keydown', (event) => {
    const items = [...menu.querySelectorAll<HTMLButtonElement>('button.dropdown-item')];
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!menu.classList.contains('show')) {
        openMenu();
        return;
      }
      activeIndex = Math.min(Math.max(activeIndex + (event.key === 'ArrowDown' ? 1 : -1), 0), items.length - 1);
      items.forEach((item, index) => item.classList.toggle('active', index === activeIndex));
      items[activeIndex]?.scrollIntoView({ block: 'nearest' });
    } else if (event.key === 'Enter') {
      event.preventDefault();
      // Enter picks the highlighted entry — or the only remaining match.
      const target = items[activeIndex] ?? (items.length === 1 ? items[0] : undefined);
      if (target?.dataset.value !== undefined) pick(target.dataset.value);
    } else if (event.key === 'Escape') {
      syncFromSelect();
      close();
    }
  });
  input.addEventListener('blur', () => {
    // Leaving without picking never changes the selection — the input just
    // snaps back to the selected entry's label.
    syncFromSelect();
    close();
  });

  select.addEventListener('change', syncFromSelect);
  const observer = new MutationObserver(() => {
    syncFromSelect();
    if (menu.classList.contains('show')) renderMenu();
  });
  observer.observe(select, { childList: true, subtree: true });

  syncFromSelect();
}
