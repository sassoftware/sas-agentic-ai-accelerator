/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The card both builders group their controls in: a heading, then a grey
 * "Instructions" box on the left that holds the explanatory text and the
 * controls to its right. Blocks that need the full width (the prompt editors,
 * the experiment tracker, result tables) go into `wide`, below the two columns.
 */

export interface InstructionCard {
  /** The card itself; append it to a step pane. */
  element: HTMLDivElement;
  /** The Instructions box - append further paragraphs or links here. */
  instructions: HTMLElement;
  /** Right-hand column for the card's controls. */
  controls: HTMLDivElement;
  /** Full-width area below the columns; stays collapsed while empty. */
  wide: HTMLDivElement;
}

export function createInstructionCard(
  heading: HTMLElement,
  instructionsTitle: string,
  instructionTexts: HTMLElement[]
): InstructionCard {
  const element = document.createElement('div');
  element.classList.add('pb-section');
  element.appendChild(heading);

  const columns = document.createElement('div');
  columns.classList.add('pb-card-columns');

  const instructions = document.createElement('aside');
  instructions.classList.add('pb-instructions');
  const title = document.createElement('p');
  title.classList.add('pb-instructions-title');
  title.innerText = instructionsTitle;
  instructions.appendChild(title);
  instructionTexts.forEach((text) => instructions.appendChild(text));

  const controls = document.createElement('div');
  controls.classList.add('pb-card-controls');

  columns.appendChild(instructions);
  columns.appendChild(controls);
  element.appendChild(columns);

  const wide = document.createElement('div');
  wide.classList.add('pb-card-wide');
  element.appendChild(wide);

  return { element, instructions, controls, wide };
}
