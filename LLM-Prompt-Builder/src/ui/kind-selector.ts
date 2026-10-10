/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * "What are you building?" - the first block of a Setup card: one radio card
 * per kind, each with a title and a sentence that says when it is the right
 * one.
 *
 * The page has one of these on the Setup card of each flow (the prompt steps
 * and the RAG steps), so the choice is always in the same place. The selector
 * itself decides nothing: it reports the card that was chosen and shows what
 * it is told to show. Whether a choice is accepted, and what follows from it,
 * is the caller's business - which is why `setChecked` does not report back.
 */

import type { PromptBuilderText } from '../types';

/** The two kinds of template the prompt side builds. */
export type PromptKind = 'llm' | 'decision';
/** Everything the page can build. */
export type BuilderKind = PromptKind | 'rag';

export interface KindSelectorKind {
  kind: BuilderKind;
  /**
   * Set when the kind exists on this page but cannot be used: its card is
   * shown, cannot be chosen, and carries this sentence as the reason. A kind
   * that is switched off is left out of the list instead.
   */
  disabledReason?: string;
}

export interface KindSelectorOptions {
  /** Prefix of the element ids and the radio group name, e.g. `LPB`. */
  idPrefix: string;
  /** The cards, in the order they are shown. */
  kinds: KindSelectorKind[];
  /** The card that starts out chosen. */
  checked: BuilderKind;
  /** The `promptBuilder` section of the locale file. */
  text: PromptBuilderText;
  /** A person chose a card. Not called for `setChecked`. */
  onSelect(kind: BuilderKind): void;
}

export interface KindSelector {
  /** Heading, cards, note and notice; hidden when there is nothing to choose. */
  element: HTMLElement;
  /** Show a kind as the chosen one. A kind without a card is ignored. */
  setChecked(kind: BuilderKind): void;
  /**
   * Mark a kind as being loaded: its card shows a spinner and no card can be
   * chosen until `setBusy(null)`.
   */
  setBusy(kind: BuilderKind | null): void;
  /** Put the keyboard focus on the chosen card's radio. */
  focusChecked(): void;
  /** Show a problem under the cards (a kind that could not be loaded), or clear it with null. */
  setNotice(message: string | null): void;
}

/** Locale keys of a kind's title and its one-sentence description. */
const KIND_TEXT: Record<BuilderKind, { title: string; description: string }> = {
  llm: { title: 'promptBuilderPromptKindLLM', description: 'promptBuilderPromptKindLLMDescription' },
  decision: { title: 'promptBuilderPromptKindDecision', description: 'promptBuilderPromptKindDecisionDescription' },
  rag: { title: 'promptBuilderKindRag', description: 'promptBuilderKindRagDescription' },
};

export function createKindSelector(options: KindSelectorOptions): KindSelector {
  const { idPrefix, kinds, text } = options;
  const say = (key: string): string => {
    const value = text[key];
    return typeof value === 'string' ? value : '';
  };

  const element = document.createElement('div');
  element.id = `${idPrefix}-kind-selector`;
  element.classList.add('pb-kind-selector');
  // One kind is no choice: the block would only repeat what the page already is.
  element.hidden = kinds.length < 2;

  // A sub-heading of the Setup card, on the level of the card's other
  // sub-headings - the card's own title stays the largest text on it.
  const heading = document.createElement('h3');
  heading.id = `${idPrefix}-kind-heading`;
  heading.classList.add('pb-kind-heading');
  heading.innerText = say('promptBuilderKindHeading');
  element.appendChild(heading);

  const group = document.createElement('div');
  group.id = `${idPrefix}-kind-options`;
  group.classList.add('pb-kind-options');
  group.setAttribute('role', 'radiogroup');
  group.setAttribute('aria-labelledby', heading.id);
  element.appendChild(group);

  const radios = new Map<BuilderKind, HTMLInputElement>();
  const cards = new Map<BuilderKind, HTMLLabelElement>();
  const spinners = new Map<BuilderKind, HTMLElement>();
  /** The kinds whose card can never be chosen, whatever is being loaded. */
  const unavailable = new Set<BuilderKind>();

  kinds.forEach(({ kind, disabledReason }) => {
    const card = document.createElement('label');
    card.classList.add('pb-kind-option');
    card.htmlFor = `${idPrefix}-kind-${kind}`;

    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = `${idPrefix}-kind`;
    radio.id = card.htmlFor;
    radio.value = kind;
    radio.classList.add('form-check-input');
    radio.checked = kind === options.checked;

    const body = document.createElement('span');
    body.classList.add('pb-kind-body');
    const title = document.createElement('span');
    title.classList.add('pb-kind-title');
    title.innerText = say(KIND_TEXT[kind].title);
    // Shown while this kind is being loaded (see setBusy).
    const spinner = document.createElement('span');
    spinner.classList.add('spinner-border', 'spinner-border-sm', 'pb-kind-spinner');
    spinner.setAttribute('role', 'status');
    spinner.hidden = true;
    const spinnerLabel = document.createElement('span');
    spinnerLabel.classList.add('visually-hidden');
    spinnerLabel.innerText = say('promptBuilderKindLoading');
    spinner.appendChild(spinnerLabel);
    title.appendChild(spinner);
    const description = document.createElement('span');
    description.classList.add('pb-kind-description');
    description.innerText = say(KIND_TEXT[kind].description);
    body.appendChild(title);
    body.appendChild(description);

    if (disabledReason) {
      unavailable.add(kind);
      radio.disabled = true;
      card.classList.add('is-disabled');
      const reason = document.createElement('span');
      reason.id = `${idPrefix}-kind-${kind}-reason`;
      reason.classList.add('pb-kind-reason');
      reason.innerText = disabledReason;
      body.appendChild(reason);
      radio.setAttribute('aria-describedby', reason.id);
    }

    radio.addEventListener('change', () => {
      if (radio.checked) options.onSelect(kind);
    });

    card.appendChild(radio);
    card.appendChild(body);
    group.appendChild(card);
    radios.set(kind, radio);
    cards.set(kind, card);
    spinners.set(kind, spinner);
  });

  // What the choice means, then one sentence per kind that needs one - only
  // for the kinds on offer, so nobody reads advice about a card that is not there.
  const offered = (kind: BuilderKind): boolean => kinds.some((entry) => entry.kind === kind);
  const note = document.createElement('small');
  note.classList.add('text-muted', 'd-block', 'mb-3', 'pb-kind-note');
  note.innerText = [
    say('promptBuilderKindNote'),
    offered('rag') ? say('promptBuilderKindNoteRag') : '',
    offered('decision') ? say('promptBuilderKindNoteDecision') : '',
  ]
    .filter((sentence) => sentence !== '')
    .join(' ');
  element.appendChild(note);

  const notice = document.createElement('div');
  notice.id = `${idPrefix}-kind-notice`;
  notice.classList.add('alert', 'alert-danger', 'pb-kind-notice');
  notice.setAttribute('role', 'alert');
  notice.hidden = true;
  element.appendChild(notice);

  return {
    element,
    setChecked(kind: BuilderKind): void {
      const radio = radios.get(kind);
      // Setting the property raises no change event, so onSelect stays silent.
      if (radio) radio.checked = true;
    },
    setBusy(kind: BuilderKind | null): void {
      const busy = kind !== null;
      group.setAttribute('aria-busy', String(busy));
      radios.forEach((radio, radioKind) => {
        radio.disabled = busy || unavailable.has(radioKind);
      });
      cards.forEach((card, cardKind) => card.classList.toggle('is-busy', cardKind === kind));
      spinners.forEach((spinner, spinnerKind) => {
        spinner.hidden = spinnerKind !== kind;
      });
    },
    focusChecked(): void {
      const checkedRadio = [...radios.values()].find((radio) => radio.checked && !radio.disabled);
      // Without scrolling: the caller has just brought the Setup step into
      // view, and the radio sits at the top of it.
      checkedRadio?.focus({ preventScroll: true });
    },
    setNotice(message: string | null): void {
      notice.innerText = message ?? '';
      notice.hidden = !message;
    },
  };
}
