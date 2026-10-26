/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The page around the two builders.
 *
 * One page builds up to three kinds of thing: a prompt template for LLMs, a
 * decision template for decision models (both the Prompt Builder, one flow of
 * steps) and a RAG setup (the RAG Builder, a flow of its own). The shell
 * decides which kinds this object offers, mounts the builders, shows one flow
 * at a time and owns the "What are you building?" choice that moves between
 * them.
 *
 * Rules the rest of this file follows:
 *
 * - Both flows live in the document at the same time and are hidden with the
 *   `hidden` attribute, never detached: the Prompt Builder finds its own
 *   elements with `document.getElementById`, and a hidden builder keeps its
 *   state, its timers and its running jobs. Moving between the flows therefore
 *   destroys nothing and is never refused.
 * - The RAG Builder is built the first time it is chosen. Until that build has
 *   succeeded the prompt flow stays on screen, so a failed load leaves the
 *   person where the choice (and the way back) is.
 * - Each flow has its own instance of the kind selector at the top of its
 *   Setup card, and each instance shows the kind of the flow it sits in. After
 *   a change of flow the target opens on its Setup step with the focus on the
 *   chosen card, so the control stays where the person just clicked.
 */

import { getConfig, isConfigured, isRagConfigured } from '../config';
import { initAppState, getAppState } from '../state/app-state';
import { getUserInfo } from '../api/identity-api';
import { getInterfaceLanguage } from '../i18n/i18n';
import { initVaIntegration } from '../va/ddc';
import { createKindSelector, type BuilderKind, type KindSelector, type KindSelectorKind } from '../ui/kind-selector';
import { buildPromptBuilder, type PromptBuilderController } from './prompt-builder';
import { buildRagBuilder } from './rag-builder';
import { closeFolderSession, openFolderSession } from '../api/compute-files-api';
import type { PromptBuilderText } from '../types';

/** Prefix of the builders' element ids (`app-obj-LPB-…`, `app-obj-RGB-…`). */
const PANE_ID = 'app';

/** What the shell asks of the RAG Builder once it is built. */
interface RagBuilderController {
  showSetup(): void;
}

type Flow = 'prompt' | 'rag';

/** A Bootstrap alert with a heading, a message and, optionally, a list. */
function createAlert(
  variant: 'info' | 'danger',
  heading: string,
  message: string,
  items: string[] = []
): HTMLDivElement {
  const box = document.createElement('div');
  box.className = `alert alert-${variant}`;
  box.setAttribute('role', 'alert');

  const title = document.createElement('h4');
  title.className = 'alert-heading';
  title.textContent = heading;
  box.appendChild(title);

  const body = document.createElement('p');
  body.className = 'mb-0';
  body.textContent = message;
  box.appendChild(body);

  if (items.length > 0) {
    const list = document.createElement('ul');
    list.className = 'mt-2 mb-0';
    items.forEach((item) => {
      const entry = document.createElement('li');
      entry.textContent = item;
      list.appendChild(entry);
    });
    box.appendChild(list);
  }
  return box;
}

/** The spinner shown in a flow's area while its builder fetches its metadata. */
function createLoadingIndicator(label: string): HTMLDivElement {
  const indicator = document.createElement('div');
  indicator.className = 'd-flex justify-content-center p-5';
  const spinner = document.createElement('div');
  spinner.className = 'spinner-border';
  spinner.style.color = 'var(--pb-primary)';
  spinner.setAttribute('role', 'status');
  const spinnerLabel = document.createElement('span');
  spinnerLabel.className = 'visually-hidden';
  spinnerLabel.textContent = label;
  spinner.appendChild(spinnerLabel);
  indicator.appendChild(spinner);
  return indicator;
}

/**
 * Start the page inside `root`.
 *
 * Resolves when the first flow is on screen, or when a message says why none
 * is. It does not reject: every failure ends as an alert on the page.
 */
export async function mountBuilderShell(root: HTMLElement): Promise<void> {
  const config = getConfig();
  const { promptBuilder, ragBuilder, features, ragBlockedReason } = config;
  initAppState({ viyaHost: config.viyaHost });

  // Drive the VA Options pane; a no-op outside a VA embed. First, before the
  // configuration check and before anything is awaited: VA sends the message
  // the pane answers once, and an object that is not configured yet or cannot
  // load its data is exactly the one whose author needs the pane.
  initVaIntegration(config);

  const interfaceText = getInterfaceLanguage();
  const text = (interfaceText.promptBuilder ?? {}) as PromptBuilderText;
  const say = (key: string, fallback: string): string => {
    const value = text[key];
    return typeof value === 'string' ? value : fallback;
  };

  // What this object can offer. RAG has three conditions that are told apart
  // on purpose: switched on, configured, and not blocked - a blocked RAG is
  // shown as a card that cannot be chosen, with the reason, so the author
  // learns why the kind they switched on is not there.
  const promptConfigured = isConfigured(promptBuilder);
  const ragConfigured = features.rag && isRagConfigured(ragBuilder);
  const ragUsable = ragConfigured && ragBlockedReason === null;

  // Do NOT call SAS Viya until one side has been configured. When the author
  // sets the values in the Options pane, VA mirrors them into the iframe URL
  // and reloads us, so this runs again with the configuration present.
  if (!promptConfigured && !ragUsable) {
    const missingForPrompt = [
      promptBuilder.modelRepositoryID ? '' : say('promptBuilderConfigOptionRepository', 'the Model Manager repository ID'),
      promptBuilder.llmProjectID ? '' : say('promptBuilderConfigOptionLLMProject', 'the LLM project ID'),
      promptBuilder.SCREndpoint ? '' : say('promptBuilderConfigOptionSCREndpoint', 'the SCR endpoint'),
    ].filter((name) => name !== '');
    const missingForRag = [
      features.rag ? '' : say('promptBuilderConfigOptionEnableRag', 'the checkbox "Enable RAG setups" ticked'),
      ragBuilder.modelRepositoryID ? '' : say('promptBuilderConfigOptionRepository', 'the Model Manager repository ID'),
      ragBlockedReason === 'credentialDomainNone'
        ? say('promptBuilderConfigOptionCredentialDomain', 'a credential domain other than none')
        : '',
    ].filter((name) => name !== '');
    root.replaceChildren(
      createAlert(
        'info',
        say('promptBuilderConfigNeededHeading', 'Configuration required'),
        say(
          'promptBuilderConfigNeededMessage',
          "Nothing is requested from SAS Viya until this object is configured. Set the missing options in this object's Options pane (or append them as URL parameters); completing one of the two lines below is enough for the page to load."
        ),
        [
          say('promptBuilderConfigNeededPrompt', 'Prompt and decision templates need: {missing}.').replace(
            '{missing}',
            missingForPrompt.join(', ')
          ),
          say('promptBuilderConfigNeededRag', 'RAG setups need: {missing}.').replace('{missing}', missingForRag.join(', ')),
        ]
      )
    );
    return;
  }

  // Best-effort: stamp the current user on newly created prompts and setups.
  try {
    const user = await getUserInfo();
    getAppState().userName = user?.id ?? null;
  } catch {
    getAppState().userName = null;
  }

  // One area per flow, both children of the root for the life of the page.
  // The areas carry no layout of their own: the step header inside a builder
  // is sticky, which an ancestor with overflow, transform or containment
  // would break.
  const createArea = (flow: Flow): HTMLDivElement => {
    const area = document.createElement('div');
    area.id = `${PANE_ID}-area-${flow}`;
    area.classList.add('pb-shell-area');
    area.hidden = true;
    return area;
  };
  const promptArea = createArea('prompt');
  const ragArea = createArea('rag');
  root.replaceChildren(promptArea, ragArea);

  const loadingText = say('promptBuilderKindLoading', 'Loading…');
  const ragLoadErrorHeading = say('promptBuilderRagLoadErrorHeading', 'Could not load the RAG Builder');
  const ragLoadErrorMessage = say(
    'promptBuilderRagLoadErrorMessage',
    'Check that the configured Model Manager repository exists and is reachable in this environment, and that you are signed in to SAS Viya.'
  );

  // The cards of the prompt flow's selector: every kind this object offers.
  const promptFlowKinds: KindSelectorKind[] = [];
  if (promptConfigured) {
    promptFlowKinds.push({ kind: 'llm' });
    if (features.decision) promptFlowKinds.push({ kind: 'decision' });
  }
  if (ragConfigured) {
    promptFlowKinds.push(
      ragUsable
        ? { kind: 'rag' }
        : {
            kind: 'rag',
            disabledReason: say(
              'promptBuilderKindRagNeedsCredentialDomain',
              'Not available: RAG needs a credential domain, and the Options pane of this object sets the credential domain to none.'
            ),
          }
    );
  }

  let promptController: PromptBuilderController | null = null;
  let promptSelector: KindSelector | null = null;
  let ragController: RagBuilderController | null = null;
  let ragSelector: KindSelector | null = null;
  // The RAG build that is under way or done. Memoised, because a second build
  // would mount a second set of the same element ids; cleared when a build
  // fails, so that choosing RAG again starts a fresh one.
  let ragBuild: Promise<void> | null = null;
  let ragBuilt = false;

  /** Show one flow, on its Setup step, with the focus on its chosen card. */
  function showFlow(flow: Flow): void {
    // Unhide before showSetup: bringing a step into view does nothing while
    // its builder is hidden.
    promptArea.hidden = flow !== 'prompt';
    ragArea.hidden = flow !== 'rag';
    // The compute session the folder picker of the Documents step lists the
    // compute server in: started when the RAG part is shown, so that it is
    // ready - it takes about half a minute - by the time a folder is chosen,
    // and deleted when a prompt or decision template is shown again (see
    // compute-files-api.ts for how it ends when the page is left).
    if (flow === 'rag' && ragUsable) openFolderSession(String(ragBuilder.computeContext ?? ''));
    else closeFolderSession();
    if (flow === 'prompt') {
      promptController?.showSetup();
      promptSelector?.focusChecked();
    } else {
      ragController?.showSetup();
      ragSelector?.focusChecked();
    }
  }

  /**
   * Build the RAG Builder into its (hidden) area, once. Rejects when the
   * build fails; nothing of a failed build stays behind.
   */
  function ensureRagBuilt(): Promise<void> {
    if (ragBuild) return ragBuild;
    // Created on the first attempt and reused by a retry. It offers the way
    // back to the prompt kinds only when there is a prompt flow to go back to.
    ragSelector ??= createKindSelector({
      idPrefix: ragBuilder.id,
      kinds: promptController ? promptFlowKinds : [{ kind: 'rag' }],
      checked: 'rag',
      text,
      onSelect: onRagFlowSelect,
    });
    const selector = ragSelector;
    const build = (async (): Promise<void> => {
      let controller: RagBuilderController | null = null;
      const element = await buildRagBuilder(ragBuilder, PANE_ID, interfaceText, {
        kindSelector: selector.element,
        onReady: (readyController) => {
          controller = readyController;
        },
      });
      ragArea.replaceChildren(element);
      ragController = controller;
      ragBuilt = true;
    })();
    ragBuild = build;
    build.catch(() => {
      if (ragBuild === build) ragBuild = null;
    });
    return build;
  }

  /** A card was chosen in the prompt flow. */
  function onPromptFlowSelect(kind: BuilderKind): void {
    if (kind === 'rag') {
      void openRagFromPromptFlow();
      return;
    }
    // One of the builder's own kinds. It may refuse (and says why itself);
    // the mark then goes back to the kind that is still shown.
    if (promptController && !promptController.setKind(kind)) {
      promptSelector?.setChecked(promptController.currentKind());
    }
  }

  /** RAG was chosen in the prompt flow: load it if need be, then change flow. */
  async function openRagFromPromptFlow(): Promise<void> {
    const selector = promptSelector;
    const kindBefore: BuilderKind = promptController?.currentKind() ?? 'llm';
    selector?.setNotice(null);
    if (!ragBuilt) {
      // The prompt flow stays on screen while RAG loads: the spinner sits on
      // the card that was chosen, and no other card can be chosen meanwhile.
      // Nothing else of it can be used either. The flow is swapped when the
      // build ends, whatever is on screen by then, and a dialog opened in
      // between would be hidden with its backdrop left over the RAG flow.
      selector?.setBusy('rag');
      promptArea.inert = true;
      promptArea.setAttribute('aria-busy', 'true');
      const releasePromptFlow = (): void => {
        promptArea.inert = false;
        promptArea.removeAttribute('aria-busy');
      };
      try {
        await ensureRagBuilt();
      } catch (error) {
        console.error('Failed to build the RAG Builder.', error);
        // Before the focus goes back to the selector: nothing inside an inert
        // area takes the focus.
        releasePromptFlow();
        selector?.setBusy(null);
        selector?.setChecked(kindBefore);
        selector?.setNotice(
          `${ragLoadErrorHeading}. ${ragLoadErrorMessage} ${say('promptBuilderRagLoadErrorRetry', 'Choose the RAG setup card again to retry.')}`
        );
        selector?.focusChecked();
        return;
      }
      releasePromptFlow();
      selector?.setBusy(null);
    }
    // This instance sits in the prompt flow, so it goes back to showing the
    // prompt kind - that is what the person finds when they return.
    selector?.setChecked(kindBefore);
    showFlow('rag');
  }

  /** A card was chosen in the RAG flow. */
  function onRagFlowSelect(kind: BuilderKind): void {
    if (kind === 'rag') return;
    // This instance sits in the RAG flow and keeps showing RAG.
    ragSelector?.setChecked('rag');
    // Going back to the prompt flow is never refused. A change of kind on the
    // way may be (a run is in flight): the builder then says so and the
    // prompt flow is shown in the kind it has, with that run in it.
    promptController?.setKind(kind);
    showFlow('prompt');
  }

  if (promptConfigured) {
    promptSelector = createKindSelector({
      idPrefix: promptBuilder.id,
      kinds: promptFlowKinds,
      checked: 'llm',
      text,
      onSelect: onPromptFlowSelect,
    });
    const selector = promptSelector;
    promptArea.replaceChildren(createLoadingIndicator(loadingText));
    promptArea.hidden = false;
    try {
      let controller: PromptBuilderController | null = null;
      const element = await buildPromptBuilder(promptBuilder, PANE_ID, interfaceText, {
        features: { decision: features.decision, optimization: features.optimization },
        kindSelector: selector.element,
        onKindChanged: (kind) => selector.setChecked(kind),
        onReady: (readyController) => {
          controller = readyController;
        },
      });
      promptArea.replaceChildren(element);
      promptController = controller;
      return;
    } catch (error) {
      console.error('Failed to build the Prompt Builder.', error);
      // The prompt flow does not exist on this page. Its alert goes above the
      // areas, where it stays whatever is shown below; RAG, if this object
      // offers it, is unaffected and is opened as the only kind.
      promptSelector = null;
      promptArea.replaceChildren();
      promptArea.hidden = true;
      root.insertBefore(
        createAlert(
          'danger',
          say('promptBuilderLoadErrorHeading', 'Could not load the Prompt Builder'),
          [
            say(
              'promptBuilderLoadErrorMessage',
              'Check that the configured Model Manager repository, LLM project, and SCR endpoint exist and are reachable in this environment, and that you are signed in to SAS Viya.'
            ),
            ragUsable
              ? say('promptBuilderLoadErrorRagUnaffected', 'RAG setups are not affected by this and can be built below.')
              : '',
          ]
            .filter((sentence) => sentence !== '')
            .join(' ')
        ),
        promptArea
      );
      if (!ragUsable) return;
    }
  }

  // RAG is the only flow of this page: built right away, with its failure
  // shown in place of the page instead of under a selector that is not there.
  // The Prompt Builder would have carried the page's one h1; without it the
  // shell provides one for the document outline.
  const outlineHeading = document.createElement('h1');
  outlineHeading.className = 'visually-hidden';
  const ragText = (interfaceText.ragBuilder ?? {}) as Record<string, unknown>;
  outlineHeading.textContent = typeof ragText.ragBuilderHeading === 'string' ? ragText.ragBuilderHeading : 'RAG Builder';
  root.insertBefore(outlineHeading, root.firstChild);
  ragArea.replaceChildren(createLoadingIndicator(loadingText));
  ragArea.hidden = false;
  try {
    await ensureRagBuilt();
    showFlow('rag');
  } catch (error) {
    console.error('Failed to build the RAG Builder.', error);
    ragArea.replaceChildren(createAlert('danger', ragLoadErrorHeading, ragLoadErrorMessage));
  }
}
