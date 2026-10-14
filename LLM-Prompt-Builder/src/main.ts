/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Entry point of the Prompt Builder page: prompt templates, decision
 * templates and RAG setups in one bundle.
 *
 * This build targets a SAS Visual Analytics embed (served through SAS Job
 * Execution). It runs inside an already-authenticated SAS Viya session and
 * relies on the ambient session cookies — there is no login flow and no
 * dependency on the SAS Auth Browser SDK.
 */

// Bundle Bootstrap CSS + the two components the builders use declaratively.
// Importing the component modules also registers their data-api
// (data-bs-toggle) click handlers, so accordions and modals work without
// further wiring. The builders import what they drive from code themselves.
import 'bootstrap/dist/css/bootstrap.min.css';
import 'bootstrap/js/dist/modal';
import 'bootstrap/js/dist/collapse';
import './styles.css';

import { mountBuilderShell } from './objects/builder-shell';
import { refuseClicksOnDisabledControls } from './ui/disabled-guard';

// Before any listener of the page: a click a script sends to a control that
// is off reaches none of them.
refuseClicksOnDisabledControls();

const root = document.getElementById('app');
// The shell reads the configuration, answers the Options pane, checks that
// the object is configured and only then builds what it offers.
if (root) void mountBuilderShell(root);
