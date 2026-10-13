/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The step shell both builders sit in: one slim row of numbered steps with
 * Back and Continue beside it, and one pane per step below. Only the current
 * pane is shown; the others stay in the DOM (hidden), so every control keeps
 * its id and its value while the user moves around.
 *
 * Navigation is free - any step can be opened at any time. Only the current
 * step is highlighted: a step is a place, not a task, and marking one as done
 * would claim something the builders cannot know - steps get revisited, and
 * some are skipped on purpose.
 *
 * The first step chooses the item (a prompt-test, a RAG setup); the steps
 * after it show that item. Because their panes never leave the DOM, what an
 * item put into them stays there until something takes it out - so the shell
 * also keeps how those steps looked on the page that was just loaded, and
 * puts them back to that when a builder starts another item
 * (rememberFreshState, restoreFreshState).
 */

/** A control that holds a value a person, or an item that was opened, put there. */
export type StepField = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

/** A tick box or one choice of a group: its value is whether it is ticked. */
function isTick(field: StepField): field is HTMLInputElement {
  return field instanceof HTMLInputElement && (field.type === 'checkbox' || field.type === 'radio');
}

export interface StepDefinition {
  /** Stable key, used in the pane and button ids. */
  key: string;
  label: string;
}

export interface StepperLabels {
  back: string;
  continue: string;
  /** Accessible name of the step list. */
  navigation: string;
}

export interface Stepper {
  /** Header and panes; append this to the page. */
  element: HTMLDivElement;
  /** One pane per step, in step order - fill these with the step's cards. */
  panes: HTMLDivElement[];
  current(): number;
  /** Open a step. `scroll` brings the top of the shell into view. */
  goTo(index: number, options?: { scroll?: boolean }): void;
  /** Called after the current step changed. */
  onChange(listener: (index: number) => void): void;
  /**
   * Show or hide a step. A hidden step keeps its pane (and every control in
   * it) but leaves the row, the numbering and the Back/Continue order - the
   * builders use it for the steps only one prompt kind has. Hiding the
   * current step moves to the nearest visible one.
   */
  setVisible(index: number, visible: boolean): void;
  /**
   * Remember what every field of the steps after the first holds right now.
   * A builder calls this once, when its page is built and before an item is
   * opened: it is the state of those steps on a freshly loaded page, with
   * every default the builder gave a field (a temperature from the model's
   * options, a judge model from the report options), so no default has to be
   * written down a second time to get back to it.
   */
  rememberFreshState(): void;
  /**
   * Put every remembered field back to that value and return the fields that
   * held something else. Only the values are set; no event is sent, because
   * what a field's listeners do (some load a list from the server) is the
   * builder's to decide - it re-runs what follows from the fields, or sends
   * the event itself.
   *
   * Fields that did not exist when the state was remembered - a row a person
   * added, a control drawn for a result - are not known here: the builder
   * removes those itself.
   */
  restoreFreshState(): StepField[];
}

export function createStepper(baseID: string, steps: StepDefinition[], labels: StepperLabels): Stepper {
  const element = document.createElement('div');
  element.id = `${baseID}-stepper`;
  element.classList.add('pb-stepper');

  const header = document.createElement('div');
  header.classList.add('pb-stepper-header');

  const nav = document.createElement('nav');
  nav.setAttribute('aria-label', labels.navigation);
  const list = document.createElement('ol');
  list.classList.add('pb-stepper-steps');
  nav.appendChild(list);

  const actions = document.createElement('div');
  actions.classList.add('pb-stepper-actions');
  const backButton = document.createElement('button');
  backButton.type = 'button';
  backButton.id = `${baseID}-stepper-back`;
  backButton.classList.add('btn', 'btn-outline-secondary', 'btn-sm');
  backButton.innerText = labels.back;
  const continueButton = document.createElement('button');
  continueButton.type = 'button';
  continueButton.id = `${baseID}-stepper-continue`;
  continueButton.classList.add('btn', 'btn-primary', 'btn-sm');
  continueButton.innerText = labels.continue;
  actions.appendChild(backButton);
  actions.appendChild(continueButton);

  header.appendChild(nav);
  header.appendChild(actions);
  element.appendChild(header);

  const stepItems: HTMLLIElement[] = [];
  const stepButtons: HTMLButtonElement[] = [];
  const panes: HTMLDivElement[] = [];
  const listeners: Array<(index: number) => void> = [];
  let currentIndex = 0;
  const hidden = new Set<number>();
  const visibleIndices = (): number[] => steps.map((_, index) => index).filter((index) => !hidden.has(index));

  steps.forEach((step, index) => {
    const item = document.createElement('li');
    item.classList.add('pb-step');
    const button = document.createElement('button');
    button.type = 'button';
    button.id = `${baseID}-step-${step.key}`;
    button.classList.add('pb-step-button');
    button.setAttribute('aria-controls', `${baseID}-pane-${step.key}`);
    // The name stays available where a narrow layout shows the number only.
    button.title = step.label;
    button.setAttribute('aria-label', `${index + 1}. ${step.label}`);
    const marker = document.createElement('span');
    marker.classList.add('pb-step-marker');
    marker.innerText = String(index + 1);
    const label = document.createElement('span');
    label.classList.add('pb-step-label');
    label.innerText = step.label;
    button.appendChild(marker);
    button.appendChild(label);
    button.onclick = () => goTo(index);
    item.appendChild(button);
    list.appendChild(item);
    stepItems.push(item);
    stepButtons.push(button);

    const pane = document.createElement('div');
    pane.id = `${baseID}-pane-${step.key}`;
    pane.classList.add('pb-step-pane');
    pane.setAttribute('role', 'region');
    pane.setAttribute('aria-labelledby', button.id);
    element.appendChild(pane);
    panes.push(pane);
  });

  function render(): void {
    const visible = visibleIndices();
    steps.forEach((_, index) => {
      const isCurrent = index === currentIndex;
      stepItems[index].hidden = hidden.has(index);
      // Numbered by position among the visible steps, so hiding one leaves no gap.
      const number = visible.indexOf(index) + 1;
      (stepButtons[index].firstElementChild as HTMLElement).innerText = String(number || '');
      stepButtons[index].setAttribute('aria-label', `${number || ''}. ${steps[index].label}`);
      stepItems[index].classList.toggle('is-current', isCurrent);
      if (isCurrent) stepButtons[index].setAttribute('aria-current', 'step');
      else stepButtons[index].removeAttribute('aria-current');
      panes[index].hidden = !isCurrent;
    });
    backButton.classList.toggle('invisible', currentIndex === visible[0]);
    continueButton.classList.toggle('invisible', currentIndex === visible[visible.length - 1]);
  }

  function goTo(index: number, options?: { scroll?: boolean }): void {
    let target = Math.min(Math.max(index, 0), steps.length - 1);
    if (hidden.has(target)) {
      // Aim past a hidden step in the direction of travel.
      const visible = visibleIndices();
      const forward = target >= currentIndex;
      target = forward
        ? (visible.find((candidate) => candidate > target) ?? visible[visible.length - 1])
        : ([...visible].reverse().find((candidate) => candidate < target) ?? visible[0]);
    }
    const changed = target !== currentIndex;
    currentIndex = target;
    render();
    if (options?.scroll ?? changed) element.scrollIntoView({ block: 'start' });
    if (changed) listeners.forEach((listener) => listener(currentIndex));
  }

  backButton.onclick = () => goTo(currentIndex - 1);
  continueButton.onclick = () => goTo(currentIndex + 1);

  function setVisible(index: number, visible: boolean): void {
    if (visible) hidden.delete(index);
    else hidden.add(index);
    if (hidden.has(currentIndex)) goTo(currentIndex, { scroll: false });
    else render();
  }
  render();

  // What the fields of the later steps held when the builder had built them.
  const freshState = new Map<StepField, string | boolean>();
  function rememberFreshState(): void {
    freshState.clear();
    panes.slice(1).forEach((pane) => {
      pane.querySelectorAll<StepField>('input, textarea, select').forEach((field) => {
        // The value of a file input cannot be set by a script.
        if (field instanceof HTMLInputElement && field.type === 'file') return;
        freshState.set(field, isTick(field) ? field.checked : field.value);
      });
    });
  }
  function restoreFreshState(): StepField[] {
    const changed: StepField[] = [];
    freshState.forEach((fresh, field) => {
      if (isTick(field)) {
        if (field.checked === fresh) return;
        field.checked = fresh === true;
      } else {
        if (field.value === fresh) return;
        field.value = String(fresh);
      }
      changed.push(field);
    });
    return changed;
  }

  return {
    element,
    panes,
    current: () => currentIndex,
    goTo,
    onChange(listener: (index: number) => void): void {
      listeners.push(listener);
    },
    setVisible,
    rememberFreshState,
    restoreFreshState,
  };
}
