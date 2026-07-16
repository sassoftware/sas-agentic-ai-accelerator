/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Ambient module declarations for bundled vendor assets (Bootstrap sub-paths
 * and CSS side-effect imports) that do not ship their own type definitions.
 */

declare module 'bootstrap/js/dist/modal' {
  export default class Modal {
    constructor(element: Element, options?: Record<string, unknown>);
    show(): void;
    hide(): void;
    dispose(): void;
    static getInstance(element: Element): Modal | null;
    static getOrCreateInstance(element: Element): Modal;
  }
}

declare module 'bootstrap/js/dist/tooltip' {
  export default class Tooltip {
    constructor(element: Element, options?: Record<string, unknown>);
    show(): void;
    hide(): void;
    dispose(): void;
  }
}

declare module 'bootstrap/js/dist/toast' {
  export default class Toast {
    constructor(element: Element, options?: Record<string, unknown>);
    show(): void;
    hide(): void;
    dispose(): void;
  }
}

declare module 'bootstrap/js/dist/collapse';

declare module '*.css';
