/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * The name/creator filter row that sits above a long picker.
 *
 * Model Manager projects accumulate: a deployment with two hundred of them
 * turns a plain dropdown into a scroll hunt. Narrowing by name and by who
 * made it is how the Prompt Builder has always solved that, and the RAG
 * Builder's project and setup pickers face the same list.
 *
 * Extracted here so the two builders share one control rather than two that
 * drift. Labels are passed in: each caller owns its own i18n keys.
 */

import type { DropdownOption } from '../types/models';

export interface ListFilterLabels {
  /** Placeholder and aria-label of the name box. */
  namePlaceholder: string;
  /** aria-label of the creator dropdown. */
  userLabel: string;
  /** The "everyone" entry of the creator dropdown. */
  userAll: string;
}

export interface ListFilter {
  filterRow: HTMLDivElement;
  nameInput: HTMLInputElement;
  userSelect: HTMLSelectElement;
  /** Rebuild the creator dropdown from the distinct authors of `items`. */
  setUsers(items: DropdownOption[]): void;
}

export function createListFilter(
  idPrefix: string,
  labels: ListFilterLabels,
  onFilterChange: () => void
): ListFilter {
  const filterRow = document.createElement('div');
  filterRow.classList.add('row', 'g-2', 'mb-2', 'pb-list-filter');

  const nameColumn = document.createElement('div');
  nameColumn.classList.add('col-md-8');
  const nameInput = document.createElement('input');
  nameInput.type = 'search';
  nameInput.id = `${idPrefix}-name`;
  nameInput.classList.add('form-control', 'form-control-sm');
  nameInput.placeholder = labels.namePlaceholder;
  nameInput.setAttribute('aria-label', labels.namePlaceholder);
  nameInput.oninput = onFilterChange;
  nameColumn.appendChild(nameInput);

  const userColumn = document.createElement('div');
  userColumn.classList.add('col-md-4');
  const userSelect = document.createElement('select');
  userSelect.id = `${idPrefix}-user`;
  userSelect.classList.add('form-select', 'form-select-sm');
  userSelect.setAttribute('aria-label', labels.userLabel);
  userSelect.onchange = onFilterChange;
  userColumn.appendChild(userSelect);

  filterRow.appendChild(nameColumn);
  filterRow.appendChild(userColumn);

  const setUsers = (items: DropdownOption[]): void => {
    const previousUser = userSelect.value;
    const users = new Set<string>();
    items.forEach((item) => {
      if (typeof item.createdBy === 'string' && item.createdBy) users.add(item.createdBy);
      if (typeof item.modifiedBy === 'string' && item.modifiedBy) users.add(item.modifiedBy);
    });
    userSelect.innerHTML = '';
    const allUsersOption = document.createElement('option');
    allUsersOption.value = '';
    allUsersOption.innerText = labels.userAll;
    userSelect.appendChild(allUsersOption);
    [...users].sort().forEach((user) => {
      const userOption = document.createElement('option');
      userOption.value = user;
      userOption.innerText = user;
      userSelect.appendChild(userOption);
    });
    // Keep a still-valid choice; fall back to "everyone" when the previously
    // filtered author no longer appears in the list.
    userSelect.value = users.has(previousUser) ? previousUser : '';
  };

  setUsers([]);
  return { filterRow, nameInput, userSelect, setUsers };
}

/**
 * Repaint a picker through its filter.
 *
 * The CURRENTLY SELECTED item always survives the filter. Filtering away what
 * is already selected would make the picker read as though nothing were
 * chosen, and any save that followed would target the placeholder.
 */
export function renderFilteredOptions(
  dropdown: HTMLSelectElement,
  items: DropdownOption[],
  nameInput: HTMLInputElement,
  userSelect: HTMLSelectElement,
  placeholderText: string
): void {
  const selectedValue = dropdown.value;
  const nameFilter = nameInput.value.trim().toLowerCase();
  const userFilter = userSelect.value;
  dropdown.innerHTML = '';
  const placeholderOption = document.createElement('option');
  placeholderOption.value = placeholderText;
  placeholderOption.innerHTML = placeholderText;
  dropdown.appendChild(placeholderOption);
  items
    .filter(
      (item) =>
        item.value === selectedValue ||
        (String(item.innerHTML ?? '')
          .toLowerCase()
          .includes(nameFilter) &&
          (userFilter === '' || item.createdBy === userFilter || item.modifiedBy === userFilter))
    )
    .forEach((item) => {
      const listOption = document.createElement('option');
      listOption.value = item.value;
      listOption.innerHTML = item.innerHTML;
      dropdown.appendChild(listOption);
    });
  dropdown.value = [...dropdown.options].some((option) => option.value === selectedValue)
    ? selectedValue
    : placeholderText;
}
