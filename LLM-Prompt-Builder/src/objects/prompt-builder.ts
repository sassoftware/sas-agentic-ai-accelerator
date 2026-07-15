/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Create a Prompt Builder Object
 */

import type { PromptBuilderConfig, InterfaceText, PromptBuilderText } from '../types';
import type { DependentDecision } from '../types/relationships';
import { getAppState } from '../state/app-state';
import { getFileContent } from '../api/files-api';
import {
  getModelProjects,
  getModelProjectModels,
  getModelRepositoryInformation,
  createModelProject,
  createModel,
  getModelContents,
  createModelContent,
  createModelVersion,
  deleteModelContent,
  getModelVariables,
  deleteModelVariable,
  deleteModel,
  deleteModelProject,
} from '../api/models-api';
import { getModelDependentDecisions } from '../api/relationships-api';
import { callSCRLLM } from '../api/scr-api';
import { createAccordionItem } from '../ui/accordion';
import { showConfirmModal } from '../ui/confirm-modal';
import { showToast } from '../ui/toast';
import { escapeHtml } from '../ui/dom-helpers';
import { renderMarkdown } from '../ui/markdown';
import { isValidDS2VariableName, validateAndCorrectPackageName } from '../util/validation';
import Modal from 'bootstrap/js/dist/modal';

interface ModelOption {
  default: unknown;
  [key: string]: unknown;
}

interface AvailableLLM {
  id: string;
  name: string;
  fileURI?: string;
  options?: Record<string, ModelOption>;
  [key: string]: unknown;
}

interface ExperimentResult {
  modelName: string;
  data: {
    run_time: number;
    output_length: number;
    prompt_length: number;
    response: string;
    error?: string;
    fastest_prompt?: boolean;
    fewest_tokens_prompt?: boolean;
    [key: string]: unknown;
  };
  options: Record<string, unknown>;
}

/** A user-defined prompt variable, referenced as {{name}} in the prompts. */
interface PromptVariable {
  name: string;
  description: string;
  type: 'string' | 'decimal';
  value: string;
}

interface ExperimentTrackerEntry {
  systemPrompt: string;
  userPrompt: string;
  variables?: PromptVariable[];
  [modelName: string]: unknown;
}

/** Entry keys that are metadata rather than per-model experiment results. */
const TRACKER_META_KEYS = ['systemPrompt', 'userPrompt', 'author', 'variables'];

interface ModelExperimentData {
  best_prompt: boolean | null;
  fastest_prompt: boolean | null;
  fewest_tokens_prompt: boolean | null;
  output_length: number | null;
  prompt_length: number | null;
  run_time: number | null;
  options: Record<string, unknown> | null;
  response: string;
}

interface PETRow {
  runId: number;
  systemPrompt: string;
  userPrompt: string;
  /** Variable definitions of the run; only set on the run's header row. */
  variables?: PromptVariable[] | null;
  model: string;
  options: string;
  response: string;
  run_time: number | null;
  prompt_length: number | null;
  output_length: number | null;
  best_prompt: boolean | number | null;
  fastest_prompt: boolean | null;
  fewest_tokens_prompt: boolean | null;
}

interface ModalText {
  modalTitle?: string;
  nameLabel?: string;
  descriptionLabel?: string;
  closeButtonText?: string;
  saveButtonText?: string;
}

/**
 * Handle a click on the "Open in SAS Model Manager" link. Tries to open a new
 * browser tab; when running inside VA's sandboxed DDC iframe (no 'allow-popups'),
 * window.open is blocked, so we copy the URL to the clipboard and briefly show a
 * hint instead. The link keeps its href, so the browser's own right-click
 * "Open link in new tab" always works regardless.
 */
function openModelManagerLink(
  event: MouseEvent,
  anchor: HTMLAnchorElement,
  interfaceText: PromptBuilderText
): void {
  event.preventDefault();
  const url = anchor.href;

  const opened = window.open(url, '_blank', 'noopener,noreferrer');
  if (opened) return;

  // Popup blocked by the sandbox — copy the link and let the user open it.
  copyToClipboard(url);
  const original = anchor.innerHTML;
  anchor.textContent =
    (interfaceText?.promptBuilderOpenInMMCopied as string) ??
    'Link copied — open it in a new tab';
  window.setTimeout(() => {
    anchor.innerHTML = original;
  }, 2500);
}

/** Copy text to the clipboard, falling back to a hidden-textarea + execCommand. */
function copyToClipboard(text: string): void {
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      navigator.clipboard.writeText(text).catch(() => legacyCopyToClipboard(text));
      return;
    }
  } catch {
    /* fall through to the legacy path */
  }
  legacyCopyToClipboard(text);
}

function legacyCopyToClipboard(text: string): void {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  try {
    document.execCommand('copy');
  } catch {
    /* clipboard unavailable — nothing more we can do */
  }
  document.body.removeChild(textarea);
}

export async function buildPromptBuilder(
  definition: PromptBuilderConfig,
  paneID: string,
  interfaceText?: InterfaceText
): Promise<HTMLElement> {
    const promptBuilderObject = definition;
    const promptBuilderInterfaceText = (interfaceText?.promptBuilder ?? {}) as PromptBuilderText;
    const VIYA = getAppState().config.viyaHost;

    // Experiment-tracker rows for THIS object instance. Kept in the closure
    // (not on window) so two prompt-builder panes don't clobber each other.
    let petRows: PETRow[] = [];

    const promptBuilderContainer = document.createElement('div');
    promptBuilderContainer.setAttribute('id', `${paneID}-obj-${promptBuilderObject?.id}`);

    // Add the intro piece to the Prompt Builder
    const promptBuilderHeader = document.createElement('h1');
    promptBuilderHeader.innerText = promptBuilderInterfaceText?.promptBuilderHeading as string;
    const promptBuilderDescription = document.createElement('p');
    promptBuilderDescription.innerText = promptBuilderInterfaceText?.promptBuilderDescription as string;

    // Add the project selection/creation
    const promptBuilderProjectHeader = document.createElement('h2');
    promptBuilderProjectHeader.innerText = promptBuilderInterfaceText?.promptBuilderProjectHeader as string;
    // Select from existing projects
    const promptBuilderProjectSelectorHeader = document.createElement('h2');
    promptBuilderProjectSelectorHeader.innerText = `${promptBuilderInterfaceText?.projectSelect}:`;
    const promptBuilderProjectSelectorDropdown = document.createElement('select');
    promptBuilderProjectSelectorDropdown.setAttribute('class', 'form-select');
    promptBuilderProjectSelectorDropdown.setAttribute('id', `${promptBuilderObject?.id}-project-dropdown`);
    promptBuilderProjectSelectorDropdown.onchange = async function () {
      const self = this as unknown as HTMLSelectElement;
      // Reset the in-memory experiment state of the previously selected prompt
      resetExperimentTrackerState();
      // Reset the prompt selector
      promptBuilderPromptSelectorDropdown.innerHTML = '';
      const tmpPromptBuilderPromptSelectorItem = document.createElement('option');
      tmpPromptBuilderPromptSelectorItem.value = `${promptBuilderInterfaceText?.promptSelect}`;
      tmpPromptBuilderPromptSelectorItem.innerHTML = `${promptBuilderInterfaceText?.promptSelect}`;
      promptBuilderPromptSelectorDropdown.append(tmpPromptBuilderPromptSelectorItem);

      // Get the prompts from the selected projects
      const currentProject = self.options[self.selectedIndex].value;
      // Enable project deletion only for a real project selection
      deleteProjectButton.disabled = currentProject === `${promptBuilderInterfaceText?.projectSelect}`;
      try {
        const currentProjectPrompts = await getModelProjectModels(currentProject);
        for (const existingPrompt in currentProjectPrompts) {
          const promptObj = document.createElement('option');
          promptObj.value = currentProjectPrompts[existingPrompt]?.value;
          promptObj.innerHTML = currentProjectPrompts[existingPrompt]?.innerHTML;
          promptBuilderPromptSelectorDropdown.append(promptObj);
        }
      } catch (error) {
        console.error('Failed to load prompts for the selected project.', error);
      }
    };
    // Add all of the projects to the dropdown
    const promptBuilderProjectSelectorItem = document.createElement('option');
    promptBuilderProjectSelectorItem.value = `${promptBuilderInterfaceText?.projectSelect}`;
    promptBuilderProjectSelectorItem.innerHTML = `${promptBuilderInterfaceText?.projectSelect}`;
    promptBuilderProjectSelectorDropdown.append(promptBuilderProjectSelectorItem);
    // Get all projects in the specified repository
    const existingProjects = await getModelProjects(`contains(tags,'Prompt-Engineering')`);
    // Add the projects to the dropdown
    for (const existingProject in existingProjects) {
      const projectMod = document.createElement('option');
      projectMod.value = existingProjects[existingProject]?.value;
      projectMod.innerHTML = existingProjects[existingProject]?.innerHTML;
      promptBuilderProjectSelectorDropdown.append(projectMod);
    }
    // Add the existing prompt selector
    const promptBuilderPromptHeader = document.createElement('h2');
    promptBuilderPromptHeader.innerText = `${promptBuilderInterfaceText?.promptSelect}:`;
    const promptBuilderPromptSelectorDropdown = document.createElement('select');
    promptBuilderPromptSelectorDropdown.setAttribute('class', 'form-select');
    promptBuilderPromptSelectorDropdown.setAttribute('id', `${promptBuilderObject?.id}-prompt-dropdown`);
    const promptBuilderPromptSelectorItem = document.createElement('option');
    promptBuilderPromptSelectorItem.value = `${promptBuilderInterfaceText?.promptSelect}`;
    promptBuilderPromptSelectorItem.innerHTML = `${promptBuilderInterfaceText?.promptSelect}`;
    promptBuilderPromptSelectorDropdown.append(promptBuilderPromptSelectorItem);
    promptBuilderPromptSelectorDropdown.onchange = async function () {
      const self = this as unknown as HTMLSelectElement;
      // Reset the in-memory experiment state of the previously selected prompt
      resetExperimentTrackerState();
      const promptBuilderPromptSelectedModelID = self.options[self.selectedIndex].value;
      // Get the ID of a previously created Prompt Experiment Tracker and delete it
      let promptBuilderAvailablePTE: Awaited<ReturnType<typeof getModelContents>> = [];
      try {
        promptBuilderAvailablePTE = await getModelContents(promptBuilderPromptSelectedModelID);
      } catch (error) {
        console.error('Failed to load model contents for the selected prompt.', error);
      }
      for (const promptBuilderAvailablepte in promptBuilderAvailablePTE) {
        if (promptBuilderAvailablePTE[promptBuilderAvailablepte]?.name === 'Prompt-Experiment-Tracker.json') {
          // A tracker that cannot be parsed must not abort the handler — the
          // Model Manager link and delete buttons below still have to work.
          try {
            // Reset the prompt tracker to nothing
            promptExperimentTrackerRunID = 0;
            const promptBuilderCurrentPTE = await getFileContent(promptBuilderAvailablePTE[promptBuilderAvailablepte].fileUri!);
            const promptBuilderCurrentPTEContent: PETRow[] = await promptBuilderCurrentPTE.json();
            const promptBuilderPreviousExperiment: ExperimentTrackerEntry[] = [];
            let promptBuilderPreviousRunID = 0;
            promptBuilderCurrentPTEContent.forEach((value) => {
              if (value.runId !== promptBuilderPreviousRunID) {
                const loadedRun: ExperimentTrackerEntry = {
                  systemPrompt: value.systemPrompt,
                  userPrompt: value.userPrompt,
                };
                if (Array.isArray(value.variables)) loadedRun.variables = value.variables;
                promptBuilderPreviousExperiment.push(loadedRun);
                promptBuilderPreviousRunID = value.runId;
              } else {
                // Index the last pushed run: persisted runIds can have gaps
                // (a run whose experiments all failed produces no rows), so
                // runId - 1 is not a safe array position.
                (promptBuilderPreviousExperiment[promptBuilderPreviousExperiment.length - 1] as Record<string, unknown>)[value?.model] = {
                  best_prompt: value?.best_prompt,
                  fastest_prompt: value?.fastest_prompt ?? false,
                  fewest_tokens_prompt: value?.fewest_tokens_prompt ?? false,
                  output_length: value?.output_length,
                  prompt_length: value?.prompt_length,
                  run_time: value?.run_time,
                  options: JSON.parse(
                    value?.options
                      .replace(/(\w+):/g, '"$1":')
                      .replace(/"API_KEY":"?([^",}]+)"?/g, function (_match: string, p1: string) {
                        return `"API_KEY":"${p1}"`;
                      })
                  ),
                  response: value?.response,
                };
              }
            });
            // Assign the tracker before rendering so the saveable rows are rebuilt
            // from the freshly loaded runs (the render reads the closure variable).
            promptExperimentTracker = [...promptBuilderPreviousExperiment];
            createPromptExperimentTracker(promptExperimentTracker);
            // Bring the most recent best prompt straight into the workbench
            loadMostRecentBestRun();
          } catch (error) {
            console.error('Failed to load the Prompt-Experiment-Tracker for the selected prompt.', error);
          }
        }
      }
      // Enable prompt deletion only for a real prompt selection
      deletePromptButton.disabled =
        promptBuilderPromptSelectedModelID === `${promptBuilderInterfaceText?.promptSelect}`;
      // Activate link to SAS Model Manager
      const tmpOpenInMMButton = document.getElementById(`${promptBuilderObject?.id}-openInMMButton`) as HTMLAnchorElement | null;
      if (tmpOpenInMMButton) {
        tmpOpenInMMButton.href = `${VIYA}/SASModelManager/models/${promptBuilderPromptSelectedModelID}/files`;
        tmpOpenInMMButton.classList.remove('disabled');
        tmpOpenInMMButton.removeAttribute('aria-disabled');
        tmpOpenInMMButton.onclick = (event) =>
          openModelManagerLink(event, tmpOpenInMMButton, promptBuilderInterfaceText);
      }
    };

    // Add the creation prompt buttons and modals
    const promptBuilderModalButtonContainer = document.createElement('div');
    promptBuilderModalButtonContainer.setAttribute('id', `${promptBuilderObject?.id}-modal-button-container`);

    // Function to call when creating a new project
    async function promptBuilderCreateProject(): Promise<void> {
      const modal = document.getElementById('promptBuilderCreateProjectModal');
      if (modal) {
        const btn = (modal.lastChild as HTMLElement)?.lastChild?.lastChild?.lastChild as HTMLButtonElement | null;
        if (btn) btn.disabled = true;
      }
      const promptBuilderRepositoryInformation = await getModelRepositoryInformation(promptBuilderObject?.modelRepositoryID as string);
      const promptBuilderNewProjectDefinition = {
        name: (document.getElementById('promptBuilderCreateProjectName') as HTMLInputElement).value,
        description: (document.getElementById('promptBuilderCreateProjectDescription') as HTMLInputElement).value,
        function: 'Prompt',
        repositoryId: promptBuilderObject?.modelRepositoryID as string,
        folderId: promptBuilderRepositoryInformation?.folderId,
        properties: [
          {
            name: 'Origin',
            value: 'Prompt Builder',
            type: 'string',
          },
        ],
        tags: ['LLM', 'Prompt-Engineering'],
      };
      const promptBuilderNewProjectObject = await createModelProject(promptBuilderNewProjectDefinition);
      const newPromptBuilderProjectSelectorItem = document.createElement('option');
      newPromptBuilderProjectSelectorItem.value = `${promptBuilderNewProjectObject?.id}`;
      newPromptBuilderProjectSelectorItem.innerHTML = `${promptBuilderNewProjectObject?.name}`;
      promptBuilderProjectSelectorDropdown.append(newPromptBuilderProjectSelectorItem);
      // Set the newly created project as the currently selected project
      promptBuilderProjectSelectorDropdown.value = `${promptBuilderNewProjectObject?.id}`;
      promptBuilderProjectSelectorDropdown.dispatchEvent(new Event('change'));
      if (modal) {
        const btn = (modal.lastChild as HTMLElement)?.lastChild?.lastChild?.lastChild as HTMLButtonElement | null;
        if (btn) btn.disabled = false;
      }
      const modalInstance = Modal.getInstance(document.getElementById('promptBuilderCreateProjectModal')!);
      if (modalInstance) modalInstance.hide();
    }

    // Function to call when creating a new prompt
    async function promptBuilderCreatePrompt(): Promise<void> {
      const modal = document.getElementById('promptBuilderCreatePromptModal');
      if (modal) {
        const btn = (modal.lastChild as HTMLElement)?.lastChild?.lastChild?.lastChild as HTMLButtonElement | null;
        if (btn) btn.disabled = true;
      }
      const promptBuilderNewPromptDefinition = {
        name: (document.getElementById('promptBuilderCreatePromptName') as HTMLInputElement).value,
        description: (document.getElementById('promptBuilderCreatePromptDescription') as HTMLInputElement).value,
        function: 'Prompting',
        tool: 'Prompt-Builder',
        modelere: getAppState().userName,
        projectId: promptBuilderProjectSelectorDropdown.options[promptBuilderProjectSelectorDropdown.selectedIndex].value,
        algorithm: 'Prompt-Template',
        tags: ['LLM', 'Prompt-Template'],
        scoreCodeType: 'python',
      };
      const promptBuilderNewPromptObject = await createModel(promptBuilderNewPromptDefinition);
      const newPromptBuilderPromptSelectorItem = document.createElement('option');
      newPromptBuilderPromptSelectorItem.value = `${promptBuilderNewPromptObject?.items?.[0]?.id}`;
      newPromptBuilderPromptSelectorItem.innerHTML = `${promptBuilderNewPromptObject?.items?.[0]?.name}`;
      promptBuilderPromptSelectorDropdown.append(newPromptBuilderPromptSelectorItem);
      // Set the newly created project as the currently selected project
      promptBuilderPromptSelectorDropdown.value = `${promptBuilderNewPromptObject?.items?.[0]?.id}`;
      promptBuilderPromptSelectorDropdown.dispatchEvent(new Event('change'));
      if (modal) {
        const btn = (modal.lastChild as HTMLElement)?.lastChild?.lastChild?.lastChild as HTMLButtonElement | null;
        if (btn) btn.disabled = false;
      }
      const modalInstance = Modal.getInstance(document.getElementById('promptBuilderCreatePromptModal')!);
      if (modalInstance) modalInstance.hide();
    }

    // Clear all in-memory experiment state and deactivate the prompt-bound
    // actions. Used when the project/prompt selection changes and after a
    // prompt or project was deleted.
    function resetExperimentTrackerState(): void {
      const prommpExperimentTargetContainer = document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-pet`);
      if (prommpExperimentTargetContainer) prommpExperimentTargetContainer.innerHTML = '';
      promptExperimentTracker = [];
      promptExperimentTrackerRunID = 0;
      petRows = [];
      experimentsModified = false;
      promptExperimentResultContainer.innerHTML = '';
      openInMMButton.classList.add('disabled');
      openInMMButton.setAttribute('aria-disabled', 'true');
      openInMMButton.removeAttribute('href');
      openInMMButton.onclick = null;
      deletePromptButton.disabled = true;
    }

    // Build the confirmation-modal body describing which SAS Intelligent
    // Decisioning decisions use a prompt. null means the check itself failed;
    // the user is warned but can still make an explicit choice.
    function buildUsageBody(decisions: DependentDecision[] | null): (HTMLElement | string)[] {
      if (decisions === null) {
        return [`${promptBuilderInterfaceText?.promptBuilderDeleteUsageCheckFailed}`];
      }
      if (decisions.length === 0) {
        return [`${promptBuilderInterfaceText?.promptBuilderDeleteNoUsage}`];
      }
      const decisionList = document.createElement('ul');
      decisions.forEach((decision) => {
        const decisionListItem = document.createElement('li');
        const decisionLink = document.createElement('a');
        decisionLink.href = `${VIYA}/SASDecisionManager/decisions/${decision.id}`;
        decisionLink.setAttribute('target', '_blank');
        decisionLink.setAttribute('rel', 'noopener noreferrer');
        decisionLink.textContent = decision.name;
        decisionLink.onclick = (event) => openModelManagerLink(event, decisionLink, promptBuilderInterfaceText);
        decisionListItem.appendChild(decisionLink);
        decisionList.appendChild(decisionListItem);
      });
      return [
        `${decisions.length} ${promptBuilderInterfaceText?.promptBuilderDeleteUsageFound}`,
        decisionList,
      ];
    }

    // Check whether any decisions use the model; null signals that the check
    // failed (e.g. the relationships service is unavailable) rather than that
    // no usage was found.
    async function checkModelDecisionUsage(modelID: string): Promise<DependentDecision[] | null> {
      try {
        return await getModelDependentDecisions(modelID);
      } catch (error) {
        console.error('Failed to check decision usage for the prompt.', error);
        return null;
      }
    }

    // Function to call when deleting the selected prompt
    async function promptBuilderDeletePrompt(): Promise<void> {
      const promptSelectedIndex = promptBuilderPromptSelectorDropdown.selectedIndex;
      const promptModelID = promptBuilderPromptSelectorDropdown.value;
      if (promptModelID === `${promptBuilderInterfaceText?.promptSelect}`) return;
      const promptModelName = promptBuilderPromptSelectorDropdown.options[promptSelectedIndex].text;
      deletePromptButton.disabled = true;
      deletePromptButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText?.promptBuilderDeletePromptButton}`;
      try {
        const decisions = await checkModelDecisionUsage(promptModelID);
        const confirmed = await showConfirmModal({
          title: `${promptBuilderInterfaceText?.promptBuilderDeletePromptTitle} ${promptModelName}`,
          body: buildUsageBody(decisions),
          confirmText: `${promptBuilderInterfaceText?.promptBuilderDeleteConfirmButton}`,
          cancelText: `${promptBuilderInterfaceText?.promptBuilderDeleteCancelButton}`,
        });
        if (!confirmed) return;
        const deleteStatus = await deleteModel(promptModelID);
        if (deleteStatus === 204) {
          promptBuilderPromptSelectorDropdown.remove(promptSelectedIndex);
          promptBuilderPromptSelectorDropdown.value = `${promptBuilderInterfaceText?.promptSelect}`;
          resetExperimentTrackerState();
        } else {
          promptExperimentResultContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`;
        }
      } finally {
        deletePromptButton.innerText = `${promptBuilderInterfaceText?.promptBuilderDeletePromptButton}`;
        deletePromptButton.disabled =
          promptBuilderPromptSelectorDropdown.value === `${promptBuilderInterfaceText?.promptSelect}`;
      }
    }

    // Function to call when deleting the selected project
    async function promptBuilderDeleteProject(): Promise<void> {
      const projectSelectedIndex = promptBuilderProjectSelectorDropdown.selectedIndex;
      const projectID = promptBuilderProjectSelectorDropdown.value;
      if (projectID === `${promptBuilderInterfaceText?.projectSelect}`) return;
      const projectName = promptBuilderProjectSelectorDropdown.options[projectSelectedIndex].text;
      deleteProjectButton.disabled = true;
      deleteProjectButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText?.promptBuilderDeleteProjectButton}`;
      try {
        let projectPrompts: Awaited<ReturnType<typeof getModelProjectModels>> = [];
        try {
          projectPrompts = await getModelProjectModels(projectID);
        } catch (error) {
          console.error('Failed to load the prompts of the selected project.', error);
          promptExperimentResultContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`;
          return;
        }
        // Confirm every contained prompt (with its decision usage) one by one
        // BEFORE deleting anything, so a single cancel aborts the whole
        // operation without leaving a partially deleted project behind.
        if (projectPrompts.length === 0) {
          const confirmed = await showConfirmModal({
            title: `${promptBuilderInterfaceText?.promptBuilderDeleteProjectButton}: ${projectName}`,
            body: [`${promptBuilderInterfaceText?.promptBuilderDeleteProjectEmptyNote}`],
            confirmText: `${promptBuilderInterfaceText?.promptBuilderDeleteConfirmButton}`,
            cancelText: `${promptBuilderInterfaceText?.promptBuilderDeleteCancelButton}`,
          });
          if (!confirmed) return;
        }
        for (let i = 0; i < projectPrompts.length; i++) {
          const decisions = await checkModelDecisionUsage(projectPrompts[i].value);
          const confirmed = await showConfirmModal({
            title: `${promptBuilderInterfaceText?.promptBuilderDeleteProjectTitle} ${projectPrompts[i].innerHTML} (${i + 1}/${projectPrompts.length})`,
            body: buildUsageBody(decisions),
            confirmText: `${promptBuilderInterfaceText?.promptBuilderDeleteConfirmButton}`,
            cancelText: `${promptBuilderInterfaceText?.promptBuilderDeleteCancelButton}`,
          });
          if (!confirmed) return;
        }
        // Delete the confirmed prompts explicitly before the project itself —
        // whether a project DELETE cascades to its models varies by SAS Viya
        // release, deleting them one by one is deterministic.
        for (const projectPrompt of projectPrompts) {
          const modelDeleteStatus = await deleteModel(projectPrompt.value);
          if (modelDeleteStatus !== 204) {
            promptExperimentResultContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`;
            return;
          }
        }
        const projectDeleteStatus = await deleteModelProject(projectID);
        if (projectDeleteStatus === 204) {
          promptBuilderProjectSelectorDropdown.remove(projectSelectedIndex);
          promptBuilderProjectSelectorDropdown.value = `${promptBuilderInterfaceText?.projectSelect}`;
          // Reset the prompt selector to the placeholder-only state
          promptBuilderPromptSelectorDropdown.innerHTML = '';
          const tmpPromptBuilderPromptSelectorItem = document.createElement('option');
          tmpPromptBuilderPromptSelectorItem.value = `${promptBuilderInterfaceText?.promptSelect}`;
          tmpPromptBuilderPromptSelectorItem.innerHTML = `${promptBuilderInterfaceText?.promptSelect}`;
          promptBuilderPromptSelectorDropdown.append(tmpPromptBuilderPromptSelectorItem);
          resetExperimentTrackerState();
        } else {
          promptExperimentResultContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`;
        }
      } finally {
        deleteProjectButton.innerText = `${promptBuilderInterfaceText?.promptBuilderDeleteProjectButton}`;
        deleteProjectButton.disabled =
          promptBuilderProjectSelectorDropdown.value === `${promptBuilderInterfaceText?.projectSelect}`;
      }
    }

    function promptBuilderCreateModal(
      tmpModalContainer: HTMLElement,
      tmpPrefix: string,
      tmpModalText: ModalText,
      tmpActionFunction: () => void
    ): void {
      // Create the button that triggers the modal
      const createModalButtonToggle = document.createElement('button');
      createModalButtonToggle.type = 'button';
      createModalButtonToggle.classList.add('btn', 'btn-primary');
      createModalButtonToggle.setAttribute('data-bs-toggle', 'modal');
      createModalButtonToggle.setAttribute('data-bs-target', `#${tmpPrefix}Modal`);
      createModalButtonToggle.innerHTML = tmpModalText?.modalTitle ?? '';
      // Create the modal wrapper
      const createModalWrapper = document.createElement('div');
      createModalWrapper.classList.add('modal', 'fade');
      createModalWrapper.setAttribute('id', `${tmpPrefix}Modal`);
      createModalWrapper.setAttribute('tabindex', '-1');
      // Create the modal dialog
      const createModalModalDialog = document.createElement('div');
      createModalModalDialog.classList.add('modal-dialog');
      // Create the modal content
      const createModalModalContent = document.createElement('div');
      createModalModalContent.classList.add('modal-content');
      // Create the modal header
      const createModalModalHeader = document.createElement('div');
      createModalModalHeader.classList.add('modal-header');
      // Create the modal title
      const createModalModalTitle = document.createElement('h1');
      createModalModalTitle.classList.add('modal-title');
      createModalModalTitle.innerHTML = tmpModalText?.modalTitle ?? '';
      // Create the modal close button
      const createModalModalCloseButton = document.createElement('button');
      createModalModalCloseButton.type = 'button';
      createModalModalCloseButton.classList.add('btn-close');
      createModalModalCloseButton.setAttribute('data-bs-dismiss', 'modal');
      createModalModalCloseButton.setAttribute('aria-label', 'Close');
      // Create the modal body
      const createModalModalBody = document.createElement('div');
      createModalModalBody.classList.add('modal-body');
      // Create the first modal input
      const createModalBodyInput1Text = document.createElement('span');
      createModalBodyInput1Text.innerHTML = `${tmpModalText?.nameLabel}:`;
      const createModalBodyInput1 = document.createElement('input');
      createModalBodyInput1.setAttribute('type', 'text');
      createModalBodyInput1.setAttribute('placeholder', tmpModalText?.nameLabel ?? '');
      createModalBodyInput1.setAttribute('id', `${tmpPrefix}Name`);
      // Create the second modal input
      const createModalBodyInput2Text = document.createElement('span');
      createModalBodyInput2Text.innerHTML = `${tmpModalText?.descriptionLabel}:`;
      const createModalBodyInput2 = document.createElement('input');
      createModalBodyInput2.setAttribute('type', 'text');
      createModalBodyInput2.setAttribute('placeholder', tmpModalText?.descriptionLabel ?? '');
      createModalBodyInput2.setAttribute('id', `${tmpPrefix}Description`);
      // Create the modal footer
      const createModalModalFooter = document.createElement('div');
      createModalModalFooter.classList.add('modal-footer');
      // Create the modal footer close button
      const createModalModalFooterButton = document.createElement('button');
      createModalModalFooterButton.type = 'button';
      createModalModalFooterButton.classList.add('btn', 'btn-secondary');
      createModalModalFooterButton.setAttribute('data-bs-dismiss', 'modal');
      createModalModalFooterButton.innerHTML = tmpModalText?.closeButtonText ?? '';
      // Create the modal footer save button
      const createModalModalFooterButton2 = document.createElement('button');
      createModalModalFooterButton2.type = 'button';
      createModalModalFooterButton2.classList.add('btn', 'btn-primary');
      createModalModalFooterButton2.innerHTML = tmpModalText?.saveButtonText ?? '';
      createModalModalFooterButton2.onclick = () => {
        tmpActionFunction();
      };
      // Append elements together
      createModalModalHeader.appendChild(createModalModalTitle);
      createModalModalHeader.appendChild(createModalModalCloseButton);
      createModalModalContent.appendChild(createModalModalHeader);
      createModalModalBody.appendChild(createModalBodyInput1Text);
      createModalModalBody.appendChild(createModalBodyInput1);
      createModalModalBody.appendChild(document.createElement('br'));
      createModalModalBody.appendChild(createModalBodyInput2Text);
      createModalModalBody.appendChild(createModalBodyInput2);
      createModalModalContent.appendChild(createModalModalBody);
      createModalModalFooter.appendChild(createModalModalFooterButton);
      createModalModalFooter.appendChild(createModalModalFooterButton2);
      createModalModalContent.appendChild(createModalModalFooter);
      createModalModalDialog.appendChild(createModalModalContent);
      createModalWrapper.appendChild(createModalModalDialog);

      // Add to the modal container
      tmpModalContainer.appendChild(createModalButtonToggle);
      tmpModalContainer.appendChild(createModalWrapper);
    }

    // Create the modals for project/prompt creation
    promptBuilderCreateModal(
      promptBuilderModalButtonContainer,
      'promptBuilderCreateProject',
      promptBuilderInterfaceText?.promptBuilderCreateProject as unknown as ModalText,
      promptBuilderCreateProject
    );
    promptBuilderCreateModal(
      promptBuilderModalButtonContainer,
      'promptBuilderCreatePrompt',
      promptBuilderInterfaceText?.promptBuilderCreatePrompt as unknown as ModalText,
      promptBuilderCreatePrompt
    );

    // Add link to SAS Model Manager. This is an <a> (not a <button>) so that,
    // inside VA's sandboxed DDC iframe, the browser's native "Open link in new
    // tab" (right-click / context menu) works. A plain click is handled by
    // openModelManagerLink(), which opens a new tab where the sandbox allows it
    // and otherwise copies the link to the clipboard.
    const openInMMButton = document.createElement('a');
    openInMMButton.id = `${promptBuilderObject?.id}-openInMMButton`;
    openInMMButton.setAttribute('role', 'button');
    openInMMButton.setAttribute('target', '_blank');
    openInMMButton.setAttribute('rel', 'noopener noreferrer');
    openInMMButton.classList.add('btn', 'btn-primary', 'disabled');
    openInMMButton.setAttribute('aria-disabled', 'true');
    openInMMButton.innerHTML = promptBuilderInterfaceText?.promptBuilderOpenInMMButton as string;
    promptBuilderModalButtonContainer.appendChild(openInMMButton);

    // Delete the selected prompt / project. Both stay disabled until a real
    // selection exists in the corresponding dropdown.
    const deletePromptButton = document.createElement('button');
    deletePromptButton.type = 'button';
    deletePromptButton.id = `${promptBuilderObject?.id}-delete-prompt-button`;
    deletePromptButton.classList.add('btn', 'btn-danger');
    deletePromptButton.disabled = true;
    deletePromptButton.innerText = `${promptBuilderInterfaceText?.promptBuilderDeletePromptButton}`;
    deletePromptButton.onclick = async function () {
      promptBuilderDeletePrompt();
    };
    promptBuilderModalButtonContainer.appendChild(deletePromptButton);

    const deleteProjectButton = document.createElement('button');
    deleteProjectButton.type = 'button';
    deleteProjectButton.id = `${promptBuilderObject?.id}-delete-project-button`;
    deleteProjectButton.classList.add('btn', 'btn-danger');
    deleteProjectButton.disabled = true;
    deleteProjectButton.innerText = `${promptBuilderInterfaceText?.promptBuilderDeleteProjectButton}`;
    deleteProjectButton.onclick = async function () {
      promptBuilderDeleteProject();
    };
    promptBuilderModalButtonContainer.appendChild(deleteProjectButton);

    function generateModelSelection(availableModels: AvailableLLM[]): void {
      availableModels.forEach((model, index) => {
        const modelDiv = document.createElement('div');
        modelDiv.className = 'form-check';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `model${index}`;
        checkbox.className = 'form-check-input';
        checkbox.value = model?.name;
        checkbox.addEventListener('change', () => {
          const optionsDiv = document.getElementById(`options${index}`);
          if (optionsDiv) optionsDiv.style.display = checkbox.checked ? 'flex' : 'none';
        });

        const label = document.createElement('label');
        label.className = 'form-check-label';
        label.htmlFor = `model${index}`;
        label.innerText = model?.name;

        const optionsDiv = document.createElement('div');
        optionsDiv.classList.add('model-options');
        optionsDiv.id = `options${index}`;

        if (model?.options?.temperature) {
          const temperatureInput = document.createElement('input');
          temperatureInput.type = 'number';
          temperatureInput.id = `temperature${index}`;
          temperatureInput.value = String(model.options.temperature.default);
          temperatureInput.step = '0.1';
          temperatureInput.min = '0';
          temperatureInput.max = '1';
          const temperatureInformationContainer = document.createElement('div');
          temperatureInformationContainer.className = 'info-container';
          temperatureInformationContainer.innerHTML = `Temperature: <span class="info-icon">&#x2139;&#xFE0F;</span><span class="info-content">${promptBuilderInterfaceText?.promptBuilderTemperatureInfo}</span>`;
          optionsDiv.appendChild(temperatureInformationContainer);
          optionsDiv.appendChild(temperatureInput);
        }

        if (model?.options?.top_p) {
          const topPInput = document.createElement('input');
          topPInput.type = 'number';
          topPInput.id = `top_p${index}`;
          topPInput.value = String(model.options.top_p.default);
          topPInput.step = '0.1';
          topPInput.min = '0';
          topPInput.max = '1';
          const topPInformationContainer = document.createElement('div');
          topPInformationContainer.className = 'info-container';
          topPInformationContainer.innerHTML = `Top P: <span class="info-icon">&#x2139;&#xFE0F;</span><span class="info-content">${promptBuilderInterfaceText?.promptBuilderTop_PInfo}</span>`;
          optionsDiv.appendChild(topPInformationContainer);
          optionsDiv.appendChild(topPInput);
        }

        if (model?.options?.top_k) {
          const topKInput = document.createElement('input');
          topKInput.type = 'number';
          topKInput.id = `top_k${index}`;
          topKInput.value = String(model.options.top_k.default);
          topKInput.step = '1';
          topKInput.min = '1';
          topKInput.max = '100';
          const topKInformationContainer = document.createElement('div');
          topKInformationContainer.className = 'info-container';
          topKInformationContainer.innerHTML = `Top K: <span class="info-icon">&#x2139;&#xFE0F;</span><span class="info-content">${promptBuilderInterfaceText?.promptBuilderTop_KInfo}</span>`;
          optionsDiv.appendChild(topKInformationContainer);
          optionsDiv.appendChild(topKInput);
        }

        if (model?.options?.max_length) {
          const maxLengthInput = document.createElement('input');
          maxLengthInput.type = 'number';
          maxLengthInput.id = `max_length${index}`;
          maxLengthInput.value = String(model.options.max_length.default);
          maxLengthInput.step = '1';
          maxLengthInput.min = '0';
          maxLengthInput.max = '1000000';
          const maxLengthInformationContainer = document.createElement('div');
          maxLengthInformationContainer.className = 'info-container';
          maxLengthInformationContainer.innerHTML = `Max Length: <span class="info-icon">&#x2139;&#xFE0F;</span><span class="info-content">${promptBuilderInterfaceText?.promptBuilderMax_LengthInfo}</span>`;
          optionsDiv.appendChild(maxLengthInformationContainer);
          optionsDiv.appendChild(maxLengthInput);
        }

        if (model?.options?.max_tokens) {
          const maxTokensInput = document.createElement('input');
          maxTokensInput.type = 'number';
          maxTokensInput.id = `max_tokens${index}`;
          maxTokensInput.value = String(model.options.max_tokens.default);
          maxTokensInput.step = '1';
          maxTokensInput.min = '0';
          maxTokensInput.max = '1000000';
          const maxTokensInformationContainer = document.createElement('div');
          maxTokensInformationContainer.className = 'info-container';
          maxTokensInformationContainer.innerHTML = `Max Tokens: <span class="info-icon">&#x2139;&#xFE0F;</span><span class="info-content">${promptBuilderInterfaceText?.promptBuilderMax_LengthInfo}</span>`;
          optionsDiv.appendChild(maxTokensInformationContainer);
          optionsDiv.appendChild(maxTokensInput);
        }

        if (model?.options?.max_new_tokens) {
          const maxNewTokensInput = document.createElement('input');
          maxNewTokensInput.type = 'number';
          maxNewTokensInput.id = `max_new_tokens${index}`;
          maxNewTokensInput.value = String(model.options.max_new_tokens.default);
          maxNewTokensInput.step = '1';
          maxNewTokensInput.min = '0';
          maxNewTokensInput.max = '1000000';
          const maxNewTokensInformationContainer = document.createElement('div');
          maxNewTokensInformationContainer.className = 'info-container';
          maxNewTokensInformationContainer.innerHTML = `Max New Tokens: <span class="info-icon">&#x2139;&#xFE0F;</span><span class="info-content">${promptBuilderInterfaceText?.promptBuilderMax_LengthInfo}</span>`;
          optionsDiv.appendChild(maxNewTokensInformationContainer);
          optionsDiv.appendChild(maxNewTokensInput);
        }

        modelDiv.appendChild(checkbox);
        modelDiv.appendChild(label);
        modelDiv.appendChild(optionsDiv);
        promptBuilderModelSelectorContainer.appendChild(modelDiv);
      });
    }

    // Model Selector
    const promptBuilderModelSelectorHeader = document.createElement('h1');
    promptBuilderModelSelectorHeader.innerText = promptBuilderInterfaceText?.promptBuilderModelSelectorHeading as string;
    const promptBuilderModelSelectorContainer = document.createElement('div');
    promptBuilderModelSelectorContainer.setAttribute('id', `${promptBuilderObject?.id}-model-selector-container`);
    let promptBuilderAvailableLLMs: AvailableLLM[] = (await getModelProjectModels(promptBuilderObject?.llmProjectID as string)).map(o => ({ ...o, id: o.value, name: o.innerHTML }));
    const promptBuilderDeprecatedLLMs: AvailableLLM[] = (await getModelProjectModels(promptBuilderObject?.llmProjectID as string, "eq(tags,'deprecated')")).map(o => ({ ...o, id: o.value, name: o.innerHTML }));
    promptBuilderAvailableLLMs = promptBuilderAvailableLLMs.filter(
      (obj1) => !promptBuilderDeprecatedLLMs.some((obj2) => obj1.id === obj2.id)
    );
    for (const promptBuilderAvailableLLM in promptBuilderAvailableLLMs) {
      const promptBuilderAvailableLLMContents = await getModelContents(promptBuilderAvailableLLMs[promptBuilderAvailableLLM]?.id);
      for (const promptBuilderAvailableLLMContent in promptBuilderAvailableLLMContents) {
        if (promptBuilderAvailableLLMContents[promptBuilderAvailableLLMContent]?.name === 'options.json') {
          promptBuilderAvailableLLMs[promptBuilderAvailableLLM].fileURI =
            promptBuilderAvailableLLMContents[promptBuilderAvailableLLMContent]?.fileUri;
          const promptBuilderCurrentOptions = await getFileContent(
            promptBuilderAvailableLLMs[promptBuilderAvailableLLM].fileURI!
          );
          const promptBuilderCurrentOptionsContent = await promptBuilderCurrentOptions.json();
          promptBuilderAvailableLLMs[promptBuilderAvailableLLM].options = promptBuilderCurrentOptionsContent;
        }
      }
    }
    generateModelSelection(promptBuilderAvailableLLMs);

    // Add the prompting inputs
    const promptBuilderPromptingHeader = document.createElement('h1');
    promptBuilderPromptingHeader.innerText = promptBuilderInterfaceText?.promptBuilderPromptingHeader as string;
    const promptBulderPromptingExplainer = document.createElement('p');
    promptBulderPromptingExplainer.innerHTML = promptBuilderInterfaceText?.promptBulderPromptingExplainer as string;

    // Variables manager: define name/description/type/value rows whose values
    // are substituted into the prompts via the {{variableName}} syntax.
    const promptBuilderVariablesHeader = document.createElement('h2');
    promptBuilderVariablesHeader.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesHeading}`;
    const promptBuilderVariablesDescription = document.createElement('p');
    promptBuilderVariablesDescription.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesDescription}`;
    const promptBuilderVariablesContainer = document.createElement('div');
    promptBuilderVariablesContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-variables`;
    const promptBuilderVariablesAddButton = document.createElement('button');
    promptBuilderVariablesAddButton.type = 'button';
    promptBuilderVariablesAddButton.classList.add('btn', 'btn-secondary');
    promptBuilderVariablesAddButton.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesAddButton}`;
    promptBuilderVariablesAddButton.onclick = () => createPromptVariableRow();

    function createPromptVariableRow(variable?: PromptVariable): void {
      const variableRow = document.createElement('div');
      variableRow.classList.add('row', 'g-2', 'align-items-start', 'mb-2', 'pb-variable-row');
      // Name
      const nameColumn = document.createElement('div');
      nameColumn.classList.add('col-md-3');
      const nameInput = document.createElement('input');
      nameInput.type = 'text';
      nameInput.maxLength = 32;
      nameInput.classList.add('form-control', 'pb-var-name');
      nameInput.placeholder = `${promptBuilderInterfaceText?.promptBuilderVariablesNameLabel}`;
      nameInput.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesNameLabel}`);
      nameInput.value = variable?.name ?? '';
      nameInput.oninput = () => validatePromptVariableRows();
      const nameFeedback = document.createElement('div');
      nameFeedback.classList.add('invalid-feedback');
      nameColumn.appendChild(nameInput);
      nameColumn.appendChild(nameFeedback);
      // Description
      const descriptionColumn = document.createElement('div');
      descriptionColumn.classList.add('col-md-4');
      const descriptionInput = document.createElement('input');
      descriptionInput.type = 'text';
      descriptionInput.maxLength = 500;
      descriptionInput.classList.add('form-control', 'pb-var-description');
      descriptionInput.placeholder = `${promptBuilderInterfaceText?.promptBuilderVariablesDescriptionLabel}`;
      descriptionInput.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesDescriptionLabel}`);
      descriptionInput.value = variable?.description ?? '';
      descriptionColumn.appendChild(descriptionInput);
      // Data type (the 128000-character default string length stays internal)
      const typeColumn = document.createElement('div');
      typeColumn.classList.add('col-md-2');
      const typeSelect = document.createElement('select');
      typeSelect.classList.add('form-select', 'pb-var-type');
      typeSelect.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesTypeLabel}`);
      const stringOption = document.createElement('option');
      stringOption.value = 'string';
      stringOption.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesTypeString}`;
      const decimalOption = document.createElement('option');
      decimalOption.value = 'decimal';
      decimalOption.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesTypeDecimal}`;
      typeSelect.appendChild(stringOption);
      typeSelect.appendChild(decimalOption);
      typeSelect.value = variable?.type === 'decimal' ? 'decimal' : 'string';
      typeSelect.onchange = () => validatePromptVariableRows();
      typeColumn.appendChild(typeSelect);
      // Value
      const valueColumn = document.createElement('div');
      valueColumn.classList.add('col-md-2');
      const valueInput = document.createElement('input');
      valueInput.type = 'text';
      valueInput.classList.add('form-control', 'pb-var-value');
      valueInput.placeholder = `${promptBuilderInterfaceText?.promptBuilderVariablesValueLabel}`;
      valueInput.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesValueLabel}`);
      valueInput.value = variable?.value ?? '';
      valueInput.oninput = () => validatePromptVariableRows();
      const valueFeedback = document.createElement('div');
      valueFeedback.classList.add('invalid-feedback');
      valueFeedback.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesValueNotNumeric}`;
      valueColumn.appendChild(valueInput);
      valueColumn.appendChild(valueFeedback);
      // Remove
      const removeColumn = document.createElement('div');
      removeColumn.classList.add('col-md-1');
      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.classList.add('btn', 'btn-outline-danger', 'pb-var-remove');
      removeButton.innerHTML = '&times;';
      removeButton.title = `${promptBuilderInterfaceText?.promptBuilderVariablesRemoveButton}`;
      removeButton.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesRemoveButton}`);
      removeButton.onclick = () => {
        variableRow.remove();
        validatePromptVariableRows();
      };
      removeColumn.appendChild(removeButton);

      variableRow.appendChild(nameColumn);
      variableRow.appendChild(descriptionColumn);
      variableRow.appendChild(typeColumn);
      variableRow.appendChild(valueColumn);
      variableRow.appendChild(removeColumn);
      promptBuilderVariablesContainer.appendChild(variableRow);
    }

    // Flag invalid/duplicate names and non-numeric decimal values on the rows.
    function validatePromptVariableRows(): void {
      const seenNames = new Set<string>();
      promptBuilderVariablesContainer.querySelectorAll('.pb-variable-row').forEach((row) => {
        const nameInput = row.querySelector('.pb-var-name') as HTMLInputElement;
        const nameFeedback = nameInput.nextElementSibling as HTMLElement;
        const typeSelect = row.querySelector('.pb-var-type') as HTMLSelectElement;
        const valueInput = row.querySelector('.pb-var-value') as HTMLInputElement;
        const name = nameInput.value.trim();
        let nameInvalidText = '';
        if (name !== '' && !isValidDS2VariableName(name)) {
          nameInvalidText = `${promptBuilderInterfaceText?.promptBuilderVariablesNameInvalid}`;
        } else if (name !== '' && seenNames.has(name)) {
          nameInvalidText = `${promptBuilderInterfaceText?.promptBuilderVariablesNameDuplicate}`;
        } else if (name !== '') {
          seenNames.add(name);
        }
        nameFeedback.innerText = nameInvalidText;
        nameInput.classList.toggle('is-invalid', nameInvalidText !== '');
        const valueInvalid =
          typeSelect.value === 'decimal' && valueInput.value.trim() !== '' && isNaN(Number(valueInput.value));
        valueInput.classList.toggle('is-invalid', valueInvalid);
      });
    }

    // Collect the currently valid variable definitions (rows with an invalid,
    // empty or duplicate name are highlighted by validation and skipped here).
    function collectPromptVariables(): PromptVariable[] {
      validatePromptVariableRows();
      const variables: PromptVariable[] = [];
      const seenNames = new Set<string>();
      promptBuilderVariablesContainer.querySelectorAll('.pb-variable-row').forEach((row) => {
        const name = (row.querySelector('.pb-var-name') as HTMLInputElement).value.trim();
        if (!isValidDS2VariableName(name) || seenNames.has(name)) return;
        seenNames.add(name);
        variables.push({
          name,
          description: (row.querySelector('.pb-var-description') as HTMLInputElement).value.trim(),
          type: (row.querySelector('.pb-var-type') as HTMLSelectElement).value === 'decimal' ? 'decimal' : 'string',
          value: (row.querySelector('.pb-var-value') as HTMLInputElement).value,
        });
      });
      return variables;
    }

    function setPromptVariables(variables: PromptVariable[]): void {
      promptBuilderVariablesContainer.innerHTML = '';
      variables.forEach((variable) => createPromptVariableRow(variable));
      validatePromptVariableRows();
    }

    // Replace {{variableName}} tokens with the variable values. Tokens that do
    // not match a defined variable are left as literal text.
    function substitutePromptVariables(text: string, variables: PromptVariable[]): string {
      let result = text;
      variables.forEach((variable) => {
        result = result.replace(
          new RegExp(`\\{\\{\\s*${variable.name}\\s*\\}\\}`, 'g'),
          () => variable.value
        );
      });
      return result;
    }

    // Right-click menu on the prompt fields to insert a {{variable}} at the
    // cursor. Falls back to the browser menu when no variables are defined.
    let promptVariableInsertMenu: HTMLDivElement | null = null;
    function hidePromptVariableInsertMenu(): void {
      promptVariableInsertMenu?.remove();
      promptVariableInsertMenu = null;
    }
    document.addEventListener('click', hidePromptVariableInsertMenu);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') hidePromptVariableInsertMenu();
    });
    function attachPromptVariableInsertMenu(promptTextarea: HTMLTextAreaElement): void {
      promptTextarea.addEventListener('contextmenu', (event) => {
        const variables = collectPromptVariables();
        if (variables.length === 0) return;
        event.preventDefault();
        hidePromptVariableInsertMenu();
        const insertMenu = document.createElement('div');
        insertMenu.classList.add('dropdown-menu', 'show', 'pb-variable-menu');
        insertMenu.style.left = `${event.clientX}px`;
        insertMenu.style.top = `${event.clientY}px`;
        const insertMenuHeader = document.createElement('h6');
        insertMenuHeader.classList.add('dropdown-header');
        insertMenuHeader.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesInsertMenuHeader}`;
        insertMenu.appendChild(insertMenuHeader);
        variables.forEach((variable) => {
          const insertMenuItem = document.createElement('button');
          insertMenuItem.type = 'button';
          insertMenuItem.classList.add('dropdown-item');
          insertMenuItem.innerText = variable.name;
          if (variable.description) insertMenuItem.title = variable.description;
          insertMenuItem.onclick = () => {
            const selectionStart = promptTextarea.selectionStart ?? promptTextarea.value.length;
            const selectionEnd = promptTextarea.selectionEnd ?? selectionStart;
            promptTextarea.setRangeText(`{{${variable.name}}}`, selectionStart, selectionEnd, 'end');
            promptTextarea.focus();
            hidePromptVariableInsertMenu();
          };
          insertMenu.appendChild(insertMenuItem);
        });
        document.body.appendChild(insertMenu);
        promptVariableInsertMenu = insertMenu;
      });
    }

    const promptBuilderPromptingContainer = document.createElement('div');
    promptBuilderPromptingContainer.style.gap = '20px';
    promptBuilderPromptingContainer.style.display = 'flex';
    const promptBuilderSystemPrompt = document.createElement('textarea');
    promptBuilderSystemPrompt.id = `${paneID}-obj-${promptBuilderObject?.id}-system-prompt`;
    promptBuilderSystemPrompt.placeholder = promptBuilderInterfaceText?.promptBuilderSystemPromptPlaceholder as string;
    promptBuilderSystemPrompt.style.width = '100%';
    promptBuilderSystemPrompt.style.height = '200px';
    const promptBuilderUserPrompt = document.createElement('textarea');
    promptBuilderUserPrompt.id = `${paneID}-obj-${promptBuilderObject?.id}-user-prompt`;
    promptBuilderUserPrompt.placeholder = promptBuilderInterfaceText?.promptBuilderUserPromptPlaceholder as string;
    promptBuilderUserPrompt.style.width = '100%';
    promptBuilderUserPrompt.style.height = '200px';
    promptBuilderPromptingContainer.appendChild(promptBuilderSystemPrompt);
    promptBuilderPromptingContainer.appendChild(promptBuilderUserPrompt);
    attachPromptVariableInsertMenu(promptBuilderSystemPrompt);
    attachPromptVariableInsertMenu(promptBuilderUserPrompt);

    // Start running experiments
    const promptBuilderRunExperimentsButton = document.createElement('button');
    promptBuilderRunExperimentsButton.setAttribute('type', 'button');
    promptBuilderRunExperimentsButton.setAttribute('class', 'btn btn-primary');
    promptBuilderRunExperimentsButton.id = `${paneID}-obj-${promptBuilderObject?.id}-run-experiment`;
    promptBuilderRunExperimentsButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
    promptBuilderRunExperimentsButton.onclick = async function () {
      promptBuilderRunExperiment();
    };

    const promptBuilderRunExperimentError = document.createElement('p');
    promptBuilderRunExperimentError.style.color = 'red';
    promptBuilderRunExperimentError.id = `${paneID}-obj-${promptBuilderObject?.id}-run-error`;
    let promptExperimentTrackerRunID = 0;
    let promptExperimentTracker: ExperimentTrackerEntry[] = [];
    // Set when a run was deleted since the last save/load, so an emptied
    // tracker can still be saved.
    let experimentsModified = false;
    // Blocks run deletion while an experiment is in flight (the run indices
    // would shift under the running experiment otherwise).
    let experimentRunning = false;

    // Add prompt evaluations here
    function annotatePrompts(arr: ExperimentResult[]): void {
      if (!Array.isArray(arr) || arr.length === 0) return;

      let fastestIndex = 0;
      let fewestTokensIndex = 0;
      let minRunTime = arr[0]?.data?.run_time;
      let minOutputLength = arr[0]?.data?.output_length;

      for (let i = 1; i < arr.length; i++) {
        const { run_time, output_length } = arr[i]?.data;

        if (run_time < minRunTime) {
          minRunTime = run_time;
          fastestIndex = i;
        }
        if (output_length < minOutputLength) {
          minOutputLength = output_length;
          fewestTokensIndex = i;
        }
      }

      for (let i = 0; i < arr.length; i++) {
        arr[i].data.fastest_prompt = i === fastestIndex;
        arr[i].data.fewest_tokens_prompt = i === fewestTokensIndex;
      }
    }

    async function promptBuilderRunExperiment(): Promise<void> {
      // Add a spinner to the button
      const promptBuilderRunExperimentTargetButton = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-run-experiment`
      ) as HTMLButtonElement;
      promptBuilderRunExperimentTargetButton.disabled = true;
      promptBuilderRunExperimentTargetButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText.promptBuilderRunExperimentsButtonRunStatus}`;
      experimentRunning = true;
      // Reset error message
      const promptBuilderRunExperimentErrorText = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-run-error`
      );
      if (promptBuilderRunExperimentErrorText) promptBuilderRunExperimentErrorText.innerText = '';
      const promptBuilderSelectedModels: { currentlySelectedModel: { name: string; options: Record<string, unknown> } }[] = [];
      promptBuilderAvailableLLMs.forEach((promptBuilderCurrentLLM, index) => {
        const promptBuilderCheckbox = document.getElementById(`model${index}`) as HTMLInputElement;
        if (promptBuilderCheckbox.checked) {
          const currentlySelectedModel: { name: string; options: Record<string, unknown> } = {
            name: promptBuilderCurrentLLM.name,
            options: {},
          };
          Object.keys(promptBuilderCurrentLLM.options ?? {}).forEach((key) => {
            if (key !== 'API_KEY') {
              try {
                currentlySelectedModel.options[`${key}`] = parseFloat(
                  (document.getElementById(`${key}${index}`) as HTMLInputElement).value
                );
              } catch {
                promptBuilderRunExperimentTargetButton.disabled = false;
                console.log(
                  `The Error was caused by the ${currentlySelectedModel} and the following option which couldn't be resolved ${currentlySelectedModel.options[`${key}`]}`
                );
                promptBuilderRunExperimentTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderModelCallFailed}`;
              }
            } else if (key === 'API_KEY') {
              const apiKeys = promptBuilderObject?.API_KEYS as Record<string, string> | undefined;
              currentlySelectedModel.options[`${key}`] =
                apiKeys?.[promptBuilderCurrentLLM.options![key]?.default as string] ?? '';
            }
          });
          promptBuilderSelectedModels.push({ currentlySelectedModel });
        }
      });

      // Catch if the user hasn't selected any LLM
      if (promptBuilderSelectedModels.length === 0) {
        alert(promptBuilderInterfaceText.promptExperimentSelectModelsAlert);
        promptBuilderRunExperimentTargetButton.disabled = false;
        promptBuilderRunExperimentTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
        experimentRunning = false;
        return;
      }

      const systemPrompt = (
        document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-system-prompt`) as HTMLTextAreaElement
      ).value;
      const userPrompt = (
        document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-user-prompt`) as HTMLTextAreaElement
      ).value;
      // The tracker stores the templates plus a snapshot of the variables; the
      // LLMs receive the prompts with the {{variable}} values filled in.
      const promptVariables = collectPromptVariables();
      const resolvedSystemPrompt = substitutePromptVariables(systemPrompt, promptVariables);
      const resolvedUserPrompt = substitutePromptVariables(userPrompt, promptVariables);
      promptExperimentTracker.push({ systemPrompt: systemPrompt, userPrompt: userPrompt, variables: promptVariables });

      const allPromises: Promise<ExperimentResult>[] = [];

      for (const modelObj of promptBuilderSelectedModels) {
        const modelName = modelObj.currentlySelectedModel.name;
        const options = modelObj.currentlySelectedModel.options ?? {};

        allPromises.push(
          callSCRLLM(
            promptBuilderObject.SCREndpoint as string,
            modelName,
            resolvedSystemPrompt,
            resolvedUserPrompt,
            options,
            (promptBuilderObject.deploymentType as string) ?? 'k8s'
          ).then((data) => ({ modelName, data: data as ExperimentResult['data'], options }))
        );
      }

      const results = await Promise.all(allPromises);
      // Identify fastest prompt and fewest tokens used prompt
      annotatePrompts(results);
      for (const { modelName, data, options } of results) {
        if (data?.error) {
          if (promptBuilderRunExperimentErrorText) {
            promptBuilderRunExperimentErrorText.innerText = data.error;
          }
          promptBuilderRunExperimentTargetButton.disabled = false;
          promptBuilderRunExperimentTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
          break;
        } else {
          try {
            const trackerEntry = promptExperimentTracker[promptExperimentTrackerRunID] as Record<string, unknown>;
            trackerEntry[`${modelName}`] = {
              best_prompt: null,
              fastest_prompt: data?.fastest_prompt,
              fewest_tokens_prompt: data?.fewest_tokens_prompt,
              output_length: data?.output_length,
              prompt_length: data?.prompt_length,
              run_time: data?.run_time,
              options: options,
              response: data?.response,
            } as ModelExperimentData;
          } catch {
            const trackerEntry = promptExperimentTracker[promptExperimentTrackerRunID] as Record<string, unknown>;
            trackerEntry[`${modelName}`] = {
              best_prompt: null,
              fastest_prompt: null,
              fewest_tokens_prompt: null,
              output_length: null,
              prompt_length: null,
              run_time: null,
              options: null,
              response: promptBuilderInterfaceText?.promptBuilderModelInferenceFailed as string,
            } as ModelExperimentData;
          }
        }
      }

      createPromptExperimentTracker(promptExperimentTracker, systemPrompt, userPrompt);

      promptBuilderRunExperimentTargetButton.disabled = false;
      promptBuilderRunExperimentTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
      experimentRunning = false;
    }

    const promptExperimentContainer = document.createElement('div');
    promptExperimentContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet`;

    // Add a prompt experiment tracker to the UI
    function createPromptExperimentTracker(
      tracker: ExperimentTrackerEntry[],
      systemPrompt = '',
      userPrompt = ''
    ): void {
      tracker.forEach((promptExperimentTrackerRunResult, index) => {
        if (index === promptExperimentTrackerRunID) {
          if (systemPrompt === '') {
            systemPrompt = promptExperimentTrackerRunResult.systemPrompt;
          }
          if (userPrompt === '') {
            userPrompt = promptExperimentTrackerRunResult.userPrompt;
          }
          // Add Run Container
          const promptExperimentRunContainer = document.createElement('div');
          promptExperimentRunContainer.className = 'accordion';
          promptExperimentRunContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}`;
          // Add the accordion main item
          createAccordionItem(
            promptExperimentRunContainer,
            `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}`,
            'run',
            `${promptBuilderInterfaceText.promptExperimentTrackerRunHeader}${index + 1}`
          );
          // Add a delete button for the run as a sibling of the accordion
          // toggle (a button nested inside a button would be invalid HTML)
          const promptExperimentRunHeader = promptExperimentRunContainer.querySelector('.accordion-header') as HTMLElement | null;
          if (promptExperimentRunHeader) {
            promptExperimentRunHeader.classList.add('d-flex', 'align-items-center');
            const loadRunButton = document.createElement('button');
            loadRunButton.type = 'button';
            loadRunButton.classList.add('btn', 'btn-outline-primary', 'btn-sm', 'pet-run-load');
            loadRunButton.title = `${promptBuilderInterfaceText.promptExperimentLoadRunButton}`;
            loadRunButton.setAttribute(
              'aria-label',
              `${promptBuilderInterfaceText.promptExperimentLoadRunButton} ${index + 1}`
            );
            loadRunButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="currentColor"><title>${promptBuilderInterfaceText.promptExperimentLoadRunButton}</title><path d="M440-320v-326L336-542l-56-58 200-200 200 200-56 58-104-104v326h-80ZM240-160q-33 0-56.5-23.5T160-240v-120h80v120h480v-120h80v120q0 33-23.5 56.5T680-160H240Z"/></svg>`;
            loadRunButton.onclick = () => loadExperimentRun(index);
            promptExperimentRunHeader.appendChild(loadRunButton);
            const deleteRunButton = document.createElement('button');
            deleteRunButton.type = 'button';
            deleteRunButton.classList.add('btn', 'btn-outline-danger', 'btn-sm', 'pet-run-delete');
            deleteRunButton.title = `${promptBuilderInterfaceText.promptExperimentDeleteRunButton}`;
            deleteRunButton.setAttribute(
              'aria-label',
              `${promptBuilderInterfaceText.promptExperimentDeleteRunButton} ${index + 1}`
            );
            deleteRunButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="currentColor"><title>${promptBuilderInterfaceText.promptExperimentDeleteRunButton}</title><path d="M280-120q-33 0-56.5-23.5T200-200v-520h-40v-80h200v-40h240v40h200v80h-40v520q0 33-23.5 56.5T680-120H280Zm400-600H280v520h400v-520ZM360-280h80v-360h-80v360Zm160 0h80v-360h-80v360ZM280-720v520-520Z"/></svg>`;
            deleteRunButton.onclick = () => deleteExperimentRun(index);
            promptExperimentRunHeader.appendChild(deleteRunButton);
          }
          const promptExperimentRunContainerItemBody = document.createElement('div');
          promptExperimentRunContainerItemBody.className = 'accordion-body';
          // Add the System Prompt to the main run body
          const promptExperimentRunContainerItemBodySystemPrompt = document.createElement('p');
          promptExperimentRunContainerItemBodySystemPrompt.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-systenPrompt`;
          promptExperimentRunContainerItemBodySystemPrompt.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentTrackerSystemPrompt}</b> ${systemPrompt}`;
          // Add the User Prompt to the main run body
          const promptExperimentRunContainerItemBodyUserPrompt = document.createElement('p');
          promptExperimentRunContainerItemBodyUserPrompt.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-userPrompt`;
          promptExperimentRunContainerItemBodyUserPrompt.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentTrackerUserPrompt}</b> ${userPrompt}`;
          // Append to the container
          promptExperimentRunContainerItemBody.appendChild(promptExperimentRunContainerItemBodySystemPrompt);
          promptExperimentRunContainerItemBody.appendChild(promptExperimentRunContainerItemBodyUserPrompt);
          // List the variable definitions used by the run, if any
          const promptExperimentRunVariables = promptExperimentTrackerRunResult.variables;
          if (Array.isArray(promptExperimentRunVariables) && promptExperimentRunVariables.length > 0) {
            const variablesLine = document.createElement('p');
            variablesLine.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-variables`;
            const variablesLabel = document.createElement('b');
            variablesLabel.innerText = `${promptBuilderInterfaceText.promptExperimentTrackerVariables}`;
            variablesLine.appendChild(variablesLabel);
            const variablesList = document.createElement('ul');
            (promptExperimentRunVariables as PromptVariable[]).forEach((variable) => {
              const variableItem = document.createElement('li');
              variableItem.textContent = `${variable.name} (${variable.type}): ${variable.value}`;
              if (variable.description) variableItem.title = variable.description;
              variablesList.appendChild(variableItem);
            });
            variablesLine.appendChild(variablesList);
            promptExperimentRunContainerItemBody.appendChild(variablesLine);
          }
          (promptExperimentRunContainer.lastChild as HTMLElement)!.lastChild!.appendChild(promptExperimentRunContainerItemBody);
          // Iterate over the models used in the run
          const promptExperimentContainerModelContainer = document.createElement('div');
          promptExperimentContainerModelContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested`;
          for (const promptExperimentRunModelKey in promptExperimentTrackerRunResult) {
            if (!TRACKER_META_KEYS.includes(promptExperimentRunModelKey)) {
              const modelData = promptExperimentTrackerRunResult[promptExperimentRunModelKey] as ModelExperimentData;
              // Create the accordion
              const promptExperimentContainerModelContainerAccordion = document.createElement('div');
              promptExperimentContainerModelContainerAccordion.className = 'accordion nested-accordion mt-3';
              promptExperimentContainerModelContainerAccordion.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}`;
              // Create the accordion item
              const promptExperimentContainerModelContainerAccordionItem = document.createElement('div');
              promptExperimentContainerModelContainerAccordionItem.className = 'accordion-item';
              // Create the accordion item header
              const promptExperimentContainerModelContainerAccordionItemHeader = document.createElement('h2');
              promptExperimentContainerModelContainerAccordionItemHeader.className = 'accordion-header';
              // Create the accordion button
              const promptExperimentContainerModelContainerAccordionItemButton = document.createElement('button');
              promptExperimentContainerModelContainerAccordionItemButton.className = 'accordion-button collapsed';
              promptExperimentContainerModelContainerAccordionItemButton.type = 'button';
              promptExperimentContainerModelContainerAccordionItemButton.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}-header`;
              promptExperimentContainerModelContainerAccordionItemButton.setAttribute('data-bs-toggle', 'collapse');
              promptExperimentContainerModelContainerAccordionItemButton.setAttribute(
                'data-bs-target',
                `#${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}-body`
              );
              // Add fastest and fewest token prompt icons if applicable
              if (modelData?.best_prompt) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML = `<svg class="bestPrompt" xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderBestPrompt}</title><path d="M200-160v-80h560v80H200Zm0-140-51-321q-2 0-4.5.5t-4.5.5q-25 0-42.5-17.5T80-680q0-25 17.5-42.5T140-740q25 0 42.5 17.5T200-680q0 7-1.5 13t-3.5 11l125 56 125-171q-11-8-18-21t-7-28q0-25 17.5-42.5T480-880q25 0 42.5 17.5T540-820q0 15-7 28t-18 21l125 171 125-56q-2-5-3.5-11t-1.5-13q0-25 17.5-42.5T820-740q25 0 42.5 17.5T880-680q0 25-17.5 42.5T820-620q-2 0-4.5-.5t-4.5-.5l-51 321H200Zm68-80h424l26-167-105 46-133-183-133 183-105-46 26 167Zm212 0Z"/></svg> `;
              }
              if (modelData?.fastest_prompt) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `<svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderFastestPrompt}</title><path d="m422-232 207-248H469l29-227-185 267h139l-30 208ZM320-80l40-280H160l360-520h80l-40 320h240L400-80h-80Zm151-390Z"/></svg> `;
              }
              if (modelData?.fewest_tokens_prompt) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `<svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderFewestTokensPrompt}</title><path d="M480-83 240-323l56-56 184 183 184-183 56 56L480-83Zm0-238L240-561l56-56 184 183 184-183 56 56-240 240Zm0-238L240-799l56-56 184 183 184-183 56 56-240 240Z"/></svg> `;
              }
              promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `${promptBuilderInterfaceText.promptExperimentModel} ${promptExperimentRunModelKey}`;
              // Create the accordion body container
              const promptExperimentContainerModelContainerAccordionItemBodyContainer = document.createElement('div');
              promptExperimentContainerModelContainerAccordionItemBodyContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}-body`;
              promptExperimentContainerModelContainerAccordionItemBodyContainer.className = 'accordion-collapse collapse';
              promptExperimentContainerModelContainerAccordionItemBodyContainer.setAttribute(
                'data-bs-parent',
                `#${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}`
              );
              // Create the accordion body
              const promptExperimentContainerModelContainerAccordionItemBodyContainerBody = document.createElement('div');
              promptExperimentContainerModelContainerAccordionItemBodyContainerBody.className = 'accordion-body';
              // Iterate over the model contents
              for (const promptExperimentRunModelKeyAttribute in modelData) {
                const promptExperimentRunModelKeyValue = (modelData as unknown as Record<string, unknown>)[promptExperimentRunModelKeyAttribute];
                const promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine = document.createElement('p');
                if (promptExperimentRunModelKeyAttribute === 'best_prompt') {
                  const bestPromptDiv = document.createElement('div');
                  bestPromptDiv.className = 'form-check';
                  const bestPromptCheckbox = document.createElement('input');
                  if (promptExperimentRunModelKeyValue) {
                    bestPromptCheckbox.checked = true;
                  }
                  bestPromptCheckbox.type = 'checkbox';
                  bestPromptCheckbox.id = `best-prompt-${index}-${promptExperimentRunModelKey}`;
                  bestPromptCheckbox.className = 'form-check-input';
                  bestPromptCheckbox.addEventListener('change', () => {
                    const currentHeader = document.getElementById(
                      `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}-header`
                    );
                    if (currentHeader) {
                      const hasBestPrompt = currentHeader.querySelector('.bestPrompt');
                      if (bestPromptCheckbox.checked && !hasBestPrompt) {
                        currentHeader.insertAdjacentHTML(
                          'afterbegin',
                          `<svg class="bestPrompt" xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderBestPrompt}</title><path d="M200-160v-80h560v80H200Zm0-140-51-321q-2 0-4.5.5t-4.5.5q-25 0-42.5-17.5T80-680q0-25 17.5-42.5T140-740q25 0 42.5 17.5T200-680q0 7-1.5 13t-3.5 11l125 56 125-171q-11-8-18-21t-7-28q0-25 17.5-42.5T480-880q25 0 42.5 17.5T540-820q0 15-7 28t-18 21l125 171 125-56q-2-5-3.5-11t-1.5-13q0-25 17.5-42.5T820-740q25 0 42.5 17.5T880-680q0 25-17.5 42.5T820-620q-2 0-4.5-.5t-4.5-.5l-51 321H200Zm68-80h424l26-167-105 46-133-183-133 183-105-46 26 167Zm212 0Z"/></svg> `
                        );
                      } else if (!bestPromptCheckbox.checked && hasBestPrompt) {
                        hasBestPrompt.remove();
                      }
                    }
                    petRows.forEach((obj) => {
                      if (obj.runId === index + 1 && obj.model === promptExperimentRunModelKey) {
                        obj.best_prompt = bestPromptCheckbox.checked ? 1 : 0;
                      }
                    });
                    // Keep the tracker in sync so the selection survives a
                    // re-render (e.g. after a run was deleted)
                    modelData.best_prompt = bestPromptCheckbox.checked;
                  });

                  const bestPromptLabel = document.createElement('label');
                  bestPromptLabel.className = 'form-check-label';
                  bestPromptLabel.htmlFor = `best-prompt-${index}-${promptExperimentRunModelKey}`;
                  bestPromptLabel.innerText = promptBuilderInterfaceText.promptExperimentModelPromptBest as string;
                  bestPromptDiv.appendChild(bestPromptCheckbox);
                  bestPromptDiv.appendChild(bestPromptLabel);
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(bestPromptDiv);
                } else if (promptExperimentRunModelKeyAttribute === 'prompt_length') {
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentModelPromptLength}</b> ${escapeHtml(promptExperimentRunModelKeyValue)}`;
                } else if (promptExperimentRunModelKeyAttribute === 'output_length') {
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentModelOutputLength}</b> ${escapeHtml(promptExperimentRunModelKeyValue)}`;
                } else if (promptExperimentRunModelKeyAttribute === 'run_time') {
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentModelRunTime}</b> ${escapeHtml(promptExperimentRunModelKeyValue)}`;
                } else if (promptExperimentRunModelKeyAttribute === 'options') {
                  const optionsVal = promptExperimentRunModelKeyValue as Record<string, unknown> | null;
                  if (optionsVal?.API_KEY !== undefined) {
                    const apiKeyDefault = promptBuilderAvailableLLMs.find(
                      (obj) => obj['name'] === promptExperimentRunModelKey
                    )?.options?.API_KEY?.default;
                    (modelData as unknown as Record<string, unknown>)[promptExperimentRunModelKeyAttribute] = {
                      ...(optionsVal as Record<string, unknown>),
                      API_KEY: apiKeyDefault,
                    };
                    (optionsVal as Record<string, unknown>)['API_KEY'] = apiKeyDefault;
                  }
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentModelOptions}</b> ${escapeHtml(JSON.stringify(promptExperimentRunModelKeyValue))}`;
                } else if (promptExperimentRunModelKeyAttribute === 'response') {
                  // Render the LLM markdown response through marked + DOMPurify so
                  // a response containing raw HTML/scripts is sanitized and cannot
                  // execute (previously handled by the <zero-md> web component).
                  const responseLabel = document.createElement('b');
                  responseLabel.innerText = promptBuilderInterfaceText.promptExperimentModelResponse as string;
                  const responseMarkdown = renderMarkdown(String(promptExperimentRunModelKeyValue ?? ''));
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(responseLabel);
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(document.createTextNode(' '));
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(responseMarkdown);
                }
                promptExperimentContainerModelContainerAccordionItemBodyContainerBody.appendChild(
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine
                );
              }

              promptExperimentContainerModelContainerAccordionItemHeader.appendChild(
                promptExperimentContainerModelContainerAccordionItemButton
              );
              promptExperimentContainerModelContainerAccordionItem.appendChild(
                promptExperimentContainerModelContainerAccordionItemHeader
              );
              promptExperimentContainerModelContainerAccordionItemBodyContainer.appendChild(
                promptExperimentContainerModelContainerAccordionItemBodyContainerBody
              );
              promptExperimentContainerModelContainerAccordionItem.appendChild(
                promptExperimentContainerModelContainerAccordionItemBodyContainer
              );
              promptExperimentContainerModelContainerAccordion.appendChild(
                promptExperimentContainerModelContainerAccordionItem
              );
              promptExperimentContainerModelContainer.appendChild(
                promptExperimentContainerModelContainerAccordion
              );
            }
          }
          // Add the model tracker
          (promptExperimentRunContainer.lastChild as HTMLElement)!.lastChild!.lastChild!.appendChild(
            promptExperimentContainerModelContainer
          );
          // Add the finished run tracker
          const prommpExperimentTargetContainer = document.getElementById(
            `${paneID}-obj-${promptBuilderObject?.id}-pet`
          );
          if (prommpExperimentTargetContainer) {
            prommpExperimentTargetContainer.prepend(promptExperimentRunContainer);
          }
          // Reset the prompts for the next loop
          systemPrompt = '';
          userPrompt = '';
          // Increment the run tracker
          promptExperimentTrackerRunID++;
        }
      });
      petRows = promptExperimentTransformData(promptExperimentTracker);
    }

    // Delete one experiment run and renumber the remaining ones. The runId is
    // positional (index + 1), so re-rendering from the spliced tracker keeps
    // the headers, checkbox wiring and the persisted rows contiguous at 1..N.
    function deleteExperimentRun(index: number): void {
      if (experimentRunning) return;
      promptExperimentTracker.splice(index, 1);
      experimentsModified = true;
      renderAllExperimentRuns();
    }

    function renderAllExperimentRuns(): void {
      const prommpExperimentTargetContainer = document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-pet`);
      if (prommpExperimentTargetContainer) prommpExperimentTargetContainer.innerHTML = '';
      // createPromptExperimentTracker only renders the entry whose index equals
      // the run counter and then increments it, so start from 0 to render all
      promptExperimentTrackerRunID = 0;
      createPromptExperimentTracker(promptExperimentTracker);
    }

    // Restore an experiment run into the workbench: prompts, variables, LLM
    // selection and each selected LLM's option values. LLMs of the run that
    // are no longer available are reported in a toast.
    function loadExperimentRun(index: number): void {
      const trackerEntry = promptExperimentTracker[index];
      if (!trackerEntry) return;
      const systemPromptInput = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-system-prompt`
      ) as HTMLTextAreaElement | null;
      const userPromptInput = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-user-prompt`
      ) as HTMLTextAreaElement | null;
      if (systemPromptInput) systemPromptInput.value = trackerEntry.systemPrompt ?? '';
      if (userPromptInput) userPromptInput.value = trackerEntry.userPrompt ?? '';
      setPromptVariables(Array.isArray(trackerEntry.variables) ? trackerEntry.variables : []);
      // Reselect the run's LLMs and restore their option values
      const runModels = Object.keys(trackerEntry).filter((key) => !TRACKER_META_KEYS.includes(key));
      promptBuilderAvailableLLMs.forEach((availableLLM, llmIndex) => {
        const llmCheckbox = document.getElementById(`model${llmIndex}`) as HTMLInputElement | null;
        if (!llmCheckbox) return;
        const selected = runModels.includes(availableLLM.name);
        if (llmCheckbox.checked !== selected) {
          llmCheckbox.checked = selected;
          // Fires the listener that shows/hides the option inputs
          llmCheckbox.dispatchEvent(new Event('change'));
        }
        if (selected) {
          const modelData = trackerEntry[availableLLM.name] as ModelExperimentData;
          Object.entries(modelData?.options ?? {}).forEach(([optionKey, optionValue]) => {
            if (optionKey === 'API_KEY') return;
            const optionInput = document.getElementById(`${optionKey}${llmIndex}`) as HTMLInputElement | null;
            if (optionInput) optionInput.value = String(optionValue);
          });
        }
      });
      const missingLLMs = runModels.filter(
        (modelName) => !promptBuilderAvailableLLMs.some((availableLLM) => availableLLM.name === modelName)
      );
      if (missingLLMs.length > 0) {
        showToast(`${promptBuilderInterfaceText?.promptBuilderLoadMissingLLMs} ${missingLLMs.join(', ')}`);
      }
    }

    // Load the most recent run that has a best response selected. Runs
    // automatically after a prompt's tracker is loaded, so it stays silent
    // when no best response has been selected yet.
    function loadMostRecentBestRun(): void {
      for (let index = promptExperimentTracker.length - 1; index >= 0; index--) {
        const trackerEntry = promptExperimentTracker[index];
        const hasBestPrompt = Object.keys(trackerEntry).some(
          (key) =>
            !TRACKER_META_KEYS.includes(key) && (trackerEntry[key] as ModelExperimentData)?.best_prompt
        );
        if (hasBestPrompt) {
          loadExperimentRun(index);
          return;
        }
      }
    }

    // Transform the data structure to be saved in SAS Model Manager
    function promptExperimentTransformData(inputArray: ExperimentTrackerEntry[]): PETRow[] {
      return inputArray
        .map((entry, index) => {
          const MODELKEYS = Object.keys(entry).filter(
            (key) => !TRACKER_META_KEYS.includes(key)
          );
          const responseForModel: PETRow[] = [];
          MODELKEYS.forEach((MODELKEY, MODELINDEX) => {
            if (MODELINDEX === 0) {
              responseForModel.push({
                runId: index + 1,
                systemPrompt: entry.systemPrompt,
                userPrompt: entry.userPrompt,
                variables: Array.isArray(entry.variables) ? entry.variables : null,
                model: '',
                options: '',
                response: '',
                run_time: null,
                prompt_length: null,
                output_length: null,
                best_prompt: null,
                fastest_prompt: null,
                fewest_tokens_prompt: null,
              });
            }

            const modelEntry = entry[MODELKEY] as ModelExperimentData;
            responseForModel.push({
              runId: index + 1,
              systemPrompt: '',
              userPrompt: '',
              model: MODELKEY,
              options: JSON.stringify(modelEntry.options).replace(/"/g, ''),
              response: modelEntry.response,
              run_time: modelEntry.run_time,
              prompt_length: modelEntry.prompt_length,
              output_length: modelEntry.output_length,
              best_prompt: modelEntry.best_prompt,
              fastest_prompt: modelEntry?.fastest_prompt,
              fewest_tokens_prompt: modelEntry?.fewest_tokens_prompt,
            });
          });

          return responseForModel;
        })
        .flat();
    }

    // Save the prompt run to the prompt
    const promptExperimentSaveButton = document.createElement('div');
    promptExperimentSaveButton.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-save-button`;
    promptExperimentSaveButton.innerText = `${promptBuilderInterfaceText?.promptBuilderSaveExperimentsButton}`;
    promptExperimentSaveButton.setAttribute('type', 'button');
    promptExperimentSaveButton.setAttribute('class', 'btn btn-primary');
    promptExperimentSaveButton.onclick = async function () {
      promptBuilderSaveExperiments();
    };

    // Save the prompt run and turn the best prompt into a model
    const promptExperimentCreateModelButton = document.createElement('div');
    promptExperimentCreateModelButton.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-create-model-button`;
    promptExperimentCreateModelButton.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateModelButton}`;
    promptExperimentCreateModelButton.setAttribute('type', 'button');
    promptExperimentCreateModelButton.setAttribute('class', 'btn btn-primary');
    promptExperimentCreateModelButton.style.marginLeft = '4px';
    promptExperimentCreateModelButton.onclick = async function () {
      await promptBuilderSaveExperiments();
      await promptBulderCreateBestPromptModel();
    };
    // Choose whether the manifested model performs the LLM call itself
    // (returning the same outputs as the LLM models) or returns the
    // llmBody/llmURL pair for the Call LLM node in SAS Intelligent Decisioning.
    const promptExperimentIntegratedCallDiv = document.createElement('div');
    promptExperimentIntegratedCallDiv.classList.add('form-check', 'form-check-inline', 'pet-manifest-integrated');
    promptExperimentIntegratedCallDiv.title = `${promptBuilderInterfaceText?.promptBuilderManifestIntegratedInfo}`;
    const promptExperimentIntegratedCallCheckbox = document.createElement('input');
    promptExperimentIntegratedCallCheckbox.type = 'checkbox';
    promptExperimentIntegratedCallCheckbox.classList.add('form-check-input');
    promptExperimentIntegratedCallCheckbox.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-manifest-integrated`;
    const promptExperimentIntegratedCallLabel = document.createElement('label');
    promptExperimentIntegratedCallLabel.classList.add('form-check-label');
    promptExperimentIntegratedCallLabel.htmlFor = promptExperimentIntegratedCallCheckbox.id;
    promptExperimentIntegratedCallLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestIntegratedLabel}`;
    promptExperimentIntegratedCallDiv.appendChild(promptExperimentIntegratedCallCheckbox);
    promptExperimentIntegratedCallDiv.appendChild(promptExperimentIntegratedCallLabel);

    // Response for the user about saving
    const promptExperimentResultContainer = document.createElement('div');
    promptExperimentResultContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-save-result`;

    // Save the experiments to the SAS Model Manager
    async function promptBuilderSaveExperiments(): Promise<void> {
      // Add spinner to save button
      const promptExperimentSaveTargetButton = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-pet-save-button`
      ) as HTMLButtonElement;
      promptExperimentSaveTargetButton.disabled = true;
      promptExperimentSaveTargetButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText.promptBuilderSaveExperimentsButtonStatus}`;
      const promptExperimentRunModel = (
        document.getElementById(`${promptBuilderObject?.id}-prompt-dropdown`) as HTMLSelectElement
      ).value;
      // Check if an experiment was run (deleting runs also counts as a change,
      // so an emptied tracker can still be saved)
      if (petRows.length === 0 && !experimentsModified) {
        promptExperimentSaveTargetButton.disabled = false;
        promptExperimentSaveTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderSaveExperimentsButton}`;
        alert(promptBuilderInterfaceText.promptExperimentSaveModelsExperimentAlert);
        return;
      }
      // Check if a prompt test was selected
      if (promptExperimentRunModel === promptBuilderInterfaceText.promptSelect) {
        promptExperimentSaveTargetButton.disabled = false;
        promptExperimentSaveTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderSaveExperimentsButton}`;
        alert(promptBuilderInterfaceText.promptExperimentSaveModelsPromptAlert);
        return;
      } else {
        // Get the ID of a previously created Prompt Experiment Tracker and delete it
        const promptBuilderAvailablePTE = await getModelContents(promptExperimentRunModel);
        for (const promptBuilderAvailablepte in promptBuilderAvailablePTE) {
          if (promptBuilderAvailablePTE[promptBuilderAvailablepte]?.name === 'Prompt-Experiment-Tracker.json') {
            await createModelVersion(promptExperimentRunModel);
            await deleteModelContent(promptExperimentRunModel, promptBuilderAvailablePTE[promptBuilderAvailablepte]?.id ?? '');
          }
        }
      }
      // Create the new Prompt Experiment Tracker
      const promptExperimentPromptResponseObject = await createModelContent(
        promptExperimentRunModel,
        petRows,
        'Prompt-Experiment-Tracker.json'
      );
      if (promptExperimentPromptResponseObject.status_code === 201) {
        experimentsModified = false;
        promptExperimentResultContainer.innerHTML = `<p>${promptBuilderInterfaceText.promptExperimentSaveSucessResponse} <a target="_blank" rel="noopener noreferrer" href="${VIYA}/SASModelManager/models/${promptExperimentRunModel}">${VIYA}/SASModelManager/models/${promptExperimentRunModel}</a></p>`;
      } else {
        promptExperimentResultContainer.innerHTML = `<p>${promptBuilderInterfaceText.promptExperimentSaveFailureResponse}</p>`;
      }

      // Re-enable the save button
      promptExperimentSaveTargetButton.disabled = false;
      promptExperimentSaveTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderSaveExperimentsButton}`;
    }

    // Turn the best prompt into a model
    async function promptBulderCreateBestPromptModel(): Promise<void> {
      // Disable the create model button
      const promptExperimentCreateModelTargetButton = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-pet-create-model-button`
      ) as HTMLButtonElement;
      promptExperimentCreateModelTargetButton.disabled = true;
      promptExperimentCreateModelTargetButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText.promptBuilderSaveExperimentsButtonStatus}`;
      // Get target container to display a message to the user
      const promptExperimentResultTargetContainer = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-pet-save-result`
      );
      // Get the selected model ID & model name
      const promptDropdown = document.getElementById(`${promptBuilderObject?.id}-prompt-dropdown`) as HTMLSelectElement;
      const promptExperimentRunModel = promptDropdown.value;
      const promptExperimentRunModelName = promptDropdown.options[promptDropdown.selectedIndex].text
        .toLowerCase()
        .replace(/[\s-]+/g, '_');
      // Get the latest Prompt with a Best Prompt selected
      let bestPromptItem: PETRow | null = null;
      petRows.forEach((item) => {
        if (item.best_prompt) {
          if (bestPromptItem === null || item.runId > bestPromptItem.runId) {
            bestPromptItem = item;
          }
        }
      });
      // Get the system & user Prompt for the Best Prompt (its run header row)
      let basePrompt: PETRow | null = null;
      if (bestPromptItem !== null) {
        basePrompt = petRows.find(
          (item) => item.runId === (bestPromptItem as PETRow).runId && item.model === ''
        ) ?? null;
        const promptInputs: {
          name: string;
          description: string;
          level: string;
          type: string;
          length: number;
        }[] = [];
        // Runs created with the variables manager carry their definitions: the
        // model inputs are the variables referenced as {{name}} in either
        // prompt template. Runs without stored variables fall back to the
        // legacy variableName:variableValue;... parsing of the user prompt.
        const runVariables: PromptVariable[] | null = Array.isArray(basePrompt?.variables)
          ? (basePrompt!.variables as PromptVariable[])
          : null;
        const referencedVariables: PromptVariable[] = [];
        if (runVariables) {
          runVariables.forEach((variable) => {
            const variableToken = new RegExp(`\\{\\{\\s*${variable.name}\\s*\\}\\}`);
            if (variableToken.test(basePrompt!.systemPrompt) || variableToken.test(basePrompt!.userPrompt)) {
              referencedVariables.push(variable);
              promptInputs.push({
                name: variable.name,
                description: variable.description,
                level: variable.type === 'decimal' ? 'interval' : 'nominal',
                type: variable.type === 'decimal' ? 'decimal' : 'string',
                length: variable.type === 'decimal' ? 8 : 10000000,
              });
            }
          });
        } else {
          let parsedUserPrompt = basePrompt!.userPrompt.trim().split(';');
          // Remove empty items, if the user closed with a semi-colon
          parsedUserPrompt = parsedUserPrompt.filter(Boolean);
          // Parse the input and create the input signature
          if (parsedUserPrompt.length >= 1) {
            parsedUserPrompt.forEach((item) => {
              // Check that the variable name doesn't contain blanks
              const tempInputVar = item.split(':');
              if (tempInputVar.length > 1 && isValidDS2VariableName(tempInputVar[0])) {
                const varType =
                  String(tempInputVar[1]).trim() === '' || isNaN(Number(tempInputVar[1]))
                    ? 'string'
                    : 'decimal';
                const varLevel = varType === 'string' ? 'nominal' : 'interval';
                promptInputs.push({
                  name: tempInputVar[0],
                  description: '',
                  level: varLevel,
                  type: varType,
                  length: varType === 'string' ? 128000 : 8,
                });
              } else {
                if (!promptInputs.some((pi) => pi.name === 'userPrompt')) {
                  promptInputs.push({
                    name: 'userPrompt',
                    description: 'Captures any non-structured inputs for the prompt template',
                    level: 'nominal',
                    type: 'string',
                    length: 128000,
                  });
                }
              }
            });
          } else {
            promptInputs.push({
              name: 'userPrompt',
              description: 'Captures any non-structured inputs for the prompt template',
              level: 'nominal',
              type: 'string',
              length: 128000,
            });
          }
        }
        // Check if the options contains an API-Key
        let requiresAPIKey = false;
        const bestPromptOptionsList = (bestPromptItem as PETRow).options
          .replace(/[{}]/g, '')
          .split(',')
          .map((str) => {
            const idx = str.indexOf('API_KEY');
            if (idx !== -1) {
              requiresAPIKey = true;
              promptInputs.push({
                name: 'API_KEY',
                description: 'This LLM call requires you to input an API-Key',
                level: 'nominal',
                type: 'string',
                length: 256,
              });
            }
            return idx !== -1 ? str.substring(0, idx) : str;
          })
          .filter((str) => str.trim() !== '');

        // Create the input and user input strings for the score code
        let scoreCodeInput = '';
        let scoreCodeUserPrompt = '';
        for (let i = 0; i < promptInputs.length; i++) {
          if (i !== 0) {
            scoreCodeInput += ', ';
            scoreCodeUserPrompt += '; ';
          }
          scoreCodeInput += promptInputs[i].name;
          if (promptInputs[i].name !== 'API_KEY') {
            scoreCodeUserPrompt += `${promptInputs[i].name}: {str(${promptInputs[i].name}).strip()}`;
          }
        }
        // For variable-based runs both prompts become Python f-strings built
        // from the stored templates: literal braces are escaped for the
        // f-string and each referenced {{variable}} becomes a score-function
        // input that is inserted at its position in the template.
        const promptTemplateToPythonFString = (template: string): string => {
          let fString = template.replace(/\{/g, '{{').replace(/\}/g, '}}');
          referencedVariables.forEach((variable) => {
            fString = fString.replace(
              new RegExp(`\\{\\{\\{\\{\\s*${variable.name}\\s*\\}\\}\\}\\}`, 'g'),
              `{str(${variable.name}).strip()}`
            );
          });
          return fString;
        };
        const scoreCodeSystemPromptLiteral = runVariables
          ? `f"""${promptTemplateToPythonFString(basePrompt!.systemPrompt)}"""`
          : `"""${basePrompt!.systemPrompt}"""`;
        const scoreCodeUserPromptLiteral = runVariables
          ? `f"""${promptTemplateToPythonFString(basePrompt!.userPrompt)}"""`
          : `f"${scoreCodeUserPrompt}"`;
        // Create the options string for the score code
        let scoreCodeOptions = '';
        for (let i = 0; i < bestPromptOptionsList.length; i++) {
          if (i !== 0) {
            scoreCodeOptions += ',';
          }
          scoreCodeOptions += bestPromptOptionsList[i];
        }
        if (requiresAPIKey) {
          scoreCodeOptions += scoreCodeOptions.length > 0 ? ',API_KEY:{API_KEY}' : 'API_KEY:{API_KEY}';
        }
        // With the integrated call the manifested model calls the LLM container
        // itself and returns the same outputs as the LLM models (response,
        // run_time, prompt_length, output_length — mirroring how the Prompt
        // Builder consumes the SCR responses); otherwise it returns the
        // llmBody/llmURL pair for the Call LLM node in SAS Intelligent Decisioning.
        const integratedLLMCall = promptExperimentIntegratedCallCheckbox.checked;
        // Create the output variables definition
        const outputVars = integratedLLMCall
          ? [
              {
                name: 'response',
                description: 'The response of the LLM to the manifested prompt',
                level: 'nominal',
                type: 'string',
                length: 1000000,
              },
              {
                name: 'run_time',
                description: 'Time in seconds the LLM call took',
                level: 'interval',
                type: 'decimal',
                length: 8,
              },
              {
                name: 'prompt_length',
                description: 'Number of input tokens',
                level: 'interval',
                type: 'decimal',
                length: 8,
              },
              {
                name: 'output_length',
                description: 'Number of output tokens',
                level: 'interval',
                type: 'decimal',
                length: 8,
              },
            ]
          : [
              {
                name: 'llmBody',
                description: 'Contains the structered input for the Call LLM node in SAS Intelligent Decisioning',
                level: 'nominal',
                type: 'string',
                length: 1000000,
              },
              {
                name: 'llmURL',
                description: 'The URL of the LLM container that will be called',
                level: 'nominal',
                type: 'string',
                length: 256,
              },
            ];
        // Handle the different LLM Container deployment types
        const deploymentTypeHandling = (promptBuilderObject.deploymentType as string) ?? 'k8s';
        let llmEndpoint = '';
        if (deploymentTypeHandling === 'k8s') {
          llmEndpoint = '{endpoint}/{llm}/{llm}';
        } else if (deploymentTypeHandling === 'aca') {
          llmEndpoint = 'https://{llm.replace("_", "-")}.{endpoint}/{llm}';
        }
        // The tail of the score code: either hand the prepared call over to the
        // Call LLM node (llmBody/llmURL) or perform it directly with requests,
        // unwrapping the SCR `data` envelope exactly like the Prompt Builder does.
        const scoreCodeReturn = integratedLLMCall
          ? `    # Call the LLM container and unwrap the SCR response envelope
    llmCall = requests.post(
        llmURL,
        data=llmBody.encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if llmCall.status_code != 200:
        return f"LLM call failed with status {llmCall.status_code}", None, None, None
    llmJson = llmCall.json()
    llmData = llmJson.get("data", llmJson) if isinstance(llmJson, dict) else {}
    response = llmData.get("response", "")
    run_time = llmData.get("run_time")
    prompt_length = llmData.get("prompt_length")
    output_length = llmData.get("output_length")
    return response, run_time, prompt_length, output_length`
          : `    return llmBody, llmURL`;
        const scoreCode = `import os
${integratedLLMCall ? 'import requests\n' : ''}
def scoreModel(${scoreCodeInput}):
    "Output: ${integratedLLMCall ? 'response, run_time, prompt_length, output_length' : 'llmBody, llmURL'}"
    # The llm and the target endpoint
    llm = "${(bestPromptItem as PETRow).model}"
    # Retrieves the endpoint where the LLM containers are hosted - e.g. https://example.com/llm
    # If an environment variable called LLMCONTAINERPATH is set, it will use that instead of the one stored in the prompt builder object
    endpoint = os.getenv("LLMCONTAINERPATH", "${promptBuilderObject?.SCREndpoint}")
    llmURL = f"""${llmEndpoint}"""
    # These are the options that were set for the best prompt
    options = f"{{${scoreCodeOptions}}}"
    # This is the system prompt that was selected as the best one by the prompt engineer
    systemPrompt = ${scoreCodeSystemPromptLiteral}.replace('\\n', "\\\\n").replace("'", '"').replace('"', '\\\\"')
    # Here the user prompt will be created from the inputs of the call
    userPrompt = ${scoreCodeUserPromptLiteral}.replace('\\n', "\\\\n").replace("'", '"').replace('"', '\\\\"')
    llmBody = '{"inputs":[{"name":"systemPrompt","value":"' + systemPrompt + '"},{"name":"userPrompt","value":"' + userPrompt + '"},{"name":"options","value":"' + options + '"}]}'
${scoreCodeReturn}`;
        const mainfestPromptScoreCodeBlob = new Blob([scoreCode], { type: 'text/x-python' });
        // Clean up previous variables first
        const modelVariables = await getModelVariables(promptExperimentRunModel);
        for (let i = 0; i < modelVariables.length; i++) {
          await deleteModelVariable(promptExperimentRunModel, modelVariables[i]!.id!);
        }
        const validatedModelName = validateAndCorrectPackageName(promptExperimentRunModelName);
        await createModelContent(promptExperimentRunModel, promptInputs, 'inputVar.json', 'inputVariables');
        await createModelContent(promptExperimentRunModel, outputVars, 'outputVar.json', 'outputVariables');
        await createModelContent(
          promptExperimentRunModel,
          mainfestPromptScoreCodeBlob,
          `${validatedModelName.correctedName}.py`,
          'score',
          'text/x-python'
        );
      } else {
        if (promptExperimentResultTargetContainer) {
          promptExperimentResultTargetContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateModelNoBestPrompt}`;
        }
      }

      // Re-enable the create model button
      promptExperimentCreateModelTargetButton.disabled = false;
      promptExperimentCreateModelTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateModelButton}`;
    }

    promptBuilderContainer.appendChild(promptBuilderHeader);
    promptBuilderContainer.appendChild(promptBuilderDescription);
    promptBuilderContainer.appendChild(promptBuilderProjectHeader);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptBuilderProjectSelectorHeader);
    promptBuilderContainer.appendChild(promptBuilderProjectSelectorDropdown);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptBuilderPromptHeader);
    promptBuilderContainer.appendChild(promptBuilderPromptSelectorDropdown);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptBuilderModalButtonContainer);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptBuilderModelSelectorHeader);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptBuilderModelSelectorContainer);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptBuilderPromptingHeader);
    promptBuilderContainer.appendChild(promptBulderPromptingExplainer);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptBuilderVariablesHeader);
    promptBuilderContainer.appendChild(promptBuilderVariablesDescription);
    promptBuilderContainer.appendChild(promptBuilderVariablesContainer);
    promptBuilderContainer.appendChild(promptBuilderVariablesAddButton);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptBuilderPromptingContainer);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptBuilderRunExperimentsButton);
    promptBuilderContainer.appendChild(promptBuilderRunExperimentError);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptExperimentContainer);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptExperimentSaveButton);
    promptBuilderContainer.appendChild(promptExperimentCreateModelButton);
    promptBuilderContainer.appendChild(promptExperimentIntegratedCallDiv);
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(document.createElement('br'));
    promptBuilderContainer.appendChild(promptExperimentResultContainer);

    return promptBuilderContainer;
}
