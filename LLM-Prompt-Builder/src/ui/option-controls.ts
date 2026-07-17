/*
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Reusable typed LLM option controls.
 *
 * Model definitions describe their scoring options in options.json; entries
 * may carry additive typed fields emitted by the Model Definition Builder:
 *   type:   'enum' | 'bool' | 'string'   (absent = numeric)
 *   values: string[]                     (enum only)
 *   label:  human-readable display label
 * This module turns one entry into a form control. Enums with up to five
 * values render as a segmented button group (with a hidden input carrying
 * the selected value under the conventional element id, so value collection
 * stays uniform); larger enums fall back to a select.
 */

export interface TypedOptionMeta {
  default: unknown;
  type?: 'enum' | 'bool' | 'string';
  values?: string[];
  label?: string;
  description?: string;
  [key: string]: unknown;
}

const SEGMENT_MAX_VALUES = 5;

/** Human-readable label: explicit label field, else "snake_case_key" -> "Snake Case Key". */
export function optionDisplayLabel(key: string, meta?: TypedOptionMeta): string {
  if (meta?.label) return String(meta.label);
  return key
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function displayValue(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * Creates the control for one option. The element carrying the current value
 * always has the id `controlId`: an <input>/<select> whose .value (or
 * .checked for bools) is read at run time.
 */
export function createTypedOptionControl(key: string, meta: TypedOptionMeta, controlId: string): HTMLElement {
  if (meta?.type === 'enum' && Array.isArray(meta.values)) {
    if (meta.values.length <= SEGMENT_MAX_VALUES) return createSegmentedControl(key, meta, controlId);
    return createSelectControl(meta, controlId);
  }
  if (meta?.type === 'bool') {
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = controlId;
    checkbox.className = 'form-check-input';
    checkbox.checked = meta.default === true || meta.default === 'true';
    return checkbox;
  }
  if (meta?.type === 'string') {
    const textInput = document.createElement('input');
    textInput.type = 'text';
    textInput.id = controlId;
    textInput.className = 'form-control form-control-sm';
    textInput.value = String(meta?.default ?? '');
    return textInput;
  }
  const numberInput = document.createElement('input');
  numberInput.type = 'number';
  numberInput.id = controlId;
  numberInput.step = 'any';
  numberInput.value = String(meta?.default ?? '');
  return numberInput;
}

function createSelectControl(meta: TypedOptionMeta, controlId: string): HTMLSelectElement {
  const select = document.createElement('select');
  select.id = controlId;
  select.className = 'form-select form-select-sm';
  (meta.values ?? []).forEach((enumValue) => {
    const opt = document.createElement('option');
    opt.value = enumValue;
    opt.innerText = displayValue(enumValue);
    if (enumValue === String(meta.default)) opt.selected = true;
    select.appendChild(opt);
  });
  return select;
}

function createSegmentedControl(key: string, meta: TypedOptionMeta, controlId: string): HTMLElement {
  const container = document.createElement('div');
  container.className = 'option-segment-container';

  // The hidden input is the single source of the selected value; the radios
  // are presentation. Everything that reads or restores option values keeps
  // addressing #controlId.value.
  const hidden = document.createElement('input');
  hidden.type = 'hidden';
  hidden.id = controlId;
  hidden.value = String(meta.default ?? '');
  hidden.dataset.segmented = 'true';
  container.appendChild(hidden);

  const group = document.createElement('div');
  group.className = 'btn-group btn-group-sm option-segment';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', optionDisplayLabel(key, meta));

  (meta.values ?? []).forEach((enumValue, valueIndex) => {
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.className = 'btn-check';
    radio.name = `${controlId}-seg`;
    radio.id = `${controlId}-seg-${valueIndex}`;
    radio.value = enumValue;
    radio.autocomplete = 'off';
    radio.checked = enumValue === String(meta.default);
    radio.addEventListener('change', () => {
      if (radio.checked) hidden.value = enumValue;
    });
    const label = document.createElement('label');
    label.className = 'btn btn-outline-primary';
    label.htmlFor = radio.id;
    label.innerText = displayValue(enumValue);
    group.appendChild(radio);
    group.appendChild(label);
  });

  container.appendChild(group);
  return container;
}

/** After programmatically setting a segmented control's hidden input value
 *  (e.g. when loading a saved run), reflect it in the radio buttons. */
export function syncSegmentedControl(hidden: HTMLInputElement): void {
  if (hidden.dataset.segmented !== 'true') return;
  const radios = document.getElementsByName(`${hidden.id}-seg`);
  radios.forEach((node) => {
    const radio = node as HTMLInputElement;
    radio.checked = radio.value === hidden.value;
  });
}
