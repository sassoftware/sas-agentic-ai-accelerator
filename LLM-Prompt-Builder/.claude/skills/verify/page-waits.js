/**
 * Waits on the thing itself, for the browser suites.
 *
 * A wait of a fixed time, or on a signal that can come before the thing it
 * stands for, gives two answers for one bundle on a loaded machine: the
 * Save button of a create dialog was clicked while the dialog still moved in,
 * and a save was given up on while it was still out. The suites wait on what
 * they look at instead - a request answered (in the log of the mock, or as
 * the page saw it), a state of the page - and `verify-waits.js` holds every
 * fixed-time wait that is left in the harness to the few kinds that guard no
 * state.
 *
 * Not a suite: run-all.cjs copies it beside the suites as page-waits.cjs.
 */
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * The requests of a page as the page sees them - also one a route of the
 * suite holds back, which the log of the mock does not hold yet. Call it
 * right after the page is created.
 */
function trackRequests(page) {
  if (page.__verifyRequests) return page.__verifyRequests;
  const tracked = { open: new Set(), sent: 0 };
  page.on('request', (request) => {
    tracked.open.add(request);
    tracked.sent += 1;
  });
  const ended = (request) => tracked.open.delete(request);
  page.on('requestfinished', ended);
  page.on('requestfailed', ended);
  page.__verifyRequests = tracked;
  return tracked;
}

/** Every page the browser opens from now on has its requests tracked (trackRequests). */
function trackPagesOf(browser) {
  const newPage = browser.newPage.bind(browser);
  browser.newPage = async (...args) => {
    const page = await newPage(...args);
    trackRequests(page);
    return page;
  };
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async (...args) => {
    const context = await newContext(...args);
    context.on('page', (page) => trackRequests(page));
    return context;
  };
  return browser;
}

/**
 * Until no request of the page is out and none was sent between two looks:
 * what a click or a choice sent has been answered. For a check that nothing
 * MORE is sent, after the state the action leads to has been waited for.
 */
async function quiet(page, label = 'the page has no request out', timeout = 120000) {
  const tracked = trackRequests(page);
  const start = Date.now();
  let seen = -1;
  while (Date.now() - start < timeout) {
    if (tracked.open.size === 0 && tracked.sent === seen) return;
    seen = tracked.open.size === 0 ? tracked.sent : -1;
    await sleep(150);
  }
  throw new Error(`timeout waiting for: ${label} (${tracked.open.size} out)`);
}

/**
 * A Bootstrap dialog fully shown: it has faded in, nothing of it moves any
 * more, and Bootstrap has put the focus into it - only then does a click on
 * its buttons land, and Escape close it.
 */
async function dialogShown(page, selector, timeout = 60000) {
  await page.waitForFunction(
    (wanted) => {
      const modal = document.querySelector(wanted);
      return (
        Boolean(modal) &&
        modal.classList.contains('show') &&
        getComputedStyle(modal).display !== 'none' &&
        modal.getAnimations({ subtree: true }).every((animation) => animation.playState !== 'running') &&
        modal.contains(document.activeElement)
      );
    },
    selector,
    { timeout }
  );
}

/**
 * Click a button of the dialog that is shown, and wait until that dialog is
 * gone - a confirmation dialog of the page is removed once it has faded out.
 * Where one dialog follows another (one per item of a Delete project), a wait
 * on every dialog being gone can miss the moment between the two.
 */
async function clickInDialog(page, button, timeout = 60000) {
  await dialogShown(page, '.modal.show', timeout);
  const dialog = await page.$('.modal.show');
  await page.click(`.modal.show ${button}`);
  await dialog.waitForElementState('hidden', { timeout });
}

/**
 * No Bootstrap dialog on the page any more: every one has faded out and is
 * hidden, its backdrop is gone, and the page behind it can be used - which
 * is when a dialog hands its answer on (`hidden.bs.modal`).
 */
async function dialogsGone(page, timeout = 60000) {
  await page.waitForFunction(
    () =>
      !document.querySelector('.modal.show, .modal-backdrop') &&
      !document.body.classList.contains('modal-open') &&
      [...document.querySelectorAll('.modal')].every(
        (modal) => getComputedStyle(modal).display === 'none' && modal.getAnimations({ subtree: true }).every((animation) => animation.playState !== 'running')
      ),
    null,
    { timeout }
  );
}

/**
 * A gate a route holds answers at until the suite opens it - once it has
 * looked at the page while those requests are out. A hold of a fixed time
 * ends on its own, on a loaded machine before the look. `held` counts the
 * requests that came to the gate; `close` makes it hold again.
 */
function gate(timeout = 180000) {
  const state = { opened: false, held: 0 };
  return {
    get held() {
      return state.held;
    },
    open() {
      state.opened = true;
    },
    close() {
      state.opened = false;
    },
    async pass() {
      state.held += 1;
      const start = Date.now();
      while (!state.opened && Date.now() - start < timeout) await sleep(50);
    },
  };
}

module.exports = { trackRequests, trackPagesOf, quiet, dialogShown, clickInDialog, dialogsGone, gate };
