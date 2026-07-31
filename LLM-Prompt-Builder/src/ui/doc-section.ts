/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * The optional governance documentation block — five model-card fields, each
 * with an info tooltip, collapsed by default.
 *
 * These are the mdb model-card keys, and they are stored as SAS Model Manager
 * *attributes* so the model page shows them wherever the artifact is opened,
 * not only inside the builder that authored it. Collapsed and optional by
 * construction: documentation nobody is forced to write is documentation that
 * gets written honestly, and an empty field is a truthful "not stated" rather
 * than a box someone filled to get past a validator.
 *
 * Shared by the Prompt Builder and the RAG Builder — a prompt and a RAG setup
 * are both governed artifacts and answer the same five questions, so they
 * should not answer them through two different controls.
 */

import Tooltip from 'bootstrap/js/dist/tooltip';

/** The mdb model-card keys captured per artifact, in display order. */
export const MODEL_CARD_FIELDS = [
  'modelPurpose',
  'intendedUse',
  'expectedBenefit',
  'outOfScopeUseCases',
  'limitations',
] as const;

export type ModelCardField = (typeof MODEL_CARD_FIELDS)[number];

/** A field's visible label and the text behind its ⓘ. */
export interface DocFieldText {
  label: string;
  info: string;
}

export interface DocSectionText {
  /** The <summary> line of the collapsed block. */
  sectionLabel: string;
  /** The muted sentence under it. */
  sectionHint: string;
  /** Per-field label + tooltip. */
  fields: Record<ModelCardField, DocFieldText>;
}

export interface DocSection {
  /** The <details> element to place in the page. */
  section: HTMLDetailsElement;
  /** The textareas, by field, for callers that need direct access. */
  inputs: Record<ModelCardField, HTMLTextAreaElement>;
  /** Current values, every field present (blank when unwritten). */
  values(): Record<ModelCardField, string>;
  /** Fill the fields; a missing key clears rather than keeps a stale value. */
  setValues(values: Partial<Record<ModelCardField, unknown>>): void;
  /** Clear every field. */
  clear(): void;
}

/**
 * A label with an info icon whose tooltip explains the field.
 *
 * The tooltip carries the *guidance* — what belongs in the field and why a
 * reviewer will look for it — which is the part a short label cannot hold and
 * the part that makes an optional field get filled in well.
 */
export function createInfoLabel(labelText: string, infoHtml: string): HTMLDivElement {
  const labelContainer = document.createElement('div');
  labelContainer.classList.add('info-container');
  labelContainer.append(`${labelText}: `);
  const infoIcon = document.createElement('span');
  infoIcon.classList.add('info-icon');
  infoIcon.innerHTML = '&#x2139;&#xFE0F;';
  infoIcon.setAttribute('tabindex', '0');
  infoIcon.setAttribute('role', 'button');
  infoIcon.setAttribute('aria-label', labelText);
  infoIcon.setAttribute('data-bs-toggle', 'tooltip');
  new Tooltip(infoIcon, { title: infoHtml, html: true, container: 'body' });
  labelContainer.appendChild(infoIcon);
  return labelContainer;
}

/**
 * Build the collapsible five-field block.
 *
 * Saving is the caller's business — a prompt writes its own attributes on a
 * dedicated button, a RAG setup rides along with the setup save — so no save
 * control is created here.
 */
export function createDocSection(idPrefix: string, text: DocSectionText): DocSection {
  const section = document.createElement('details');
  section.classList.add('pb-doc-section', 'mt-2', 'mb-2');

  const summary = document.createElement('summary');
  summary.classList.add('fw-semibold');
  summary.innerText = text.sectionLabel;
  section.appendChild(summary);

  const hint = document.createElement('p');
  hint.classList.add('small', 'text-muted', 'mt-1', 'mb-2');
  hint.innerText = text.sectionHint;
  section.appendChild(hint);

  const inputs = {} as Record<ModelCardField, HTMLTextAreaElement>;
  for (const field of MODEL_CARD_FIELDS) {
    const wrap = document.createElement('div');
    wrap.classList.add('mb-2');
    wrap.appendChild(createInfoLabel(text.fields[field].label, text.fields[field].info));
    const textarea = document.createElement('textarea');
    textarea.classList.add('form-control');
    textarea.rows = 2;
    textarea.id = `${idPrefix}-doc-${field}`;
    inputs[field] = textarea;
    wrap.appendChild(textarea);
    section.appendChild(wrap);
  }

  const values = (): Record<ModelCardField, string> => {
    const out = {} as Record<ModelCardField, string>;
    for (const field of MODEL_CARD_FIELDS) out[field] = inputs[field].value;
    return out;
  };

  const setValues = (source: Partial<Record<ModelCardField, unknown>>): void => {
    for (const field of MODEL_CARD_FIELDS) {
      const value = source[field];
      inputs[field].value = value == null ? '' : String(value);
    }
  };

  const clear = (): void => setValues({});

  return { section, inputs, values, setValues, clear };
}
