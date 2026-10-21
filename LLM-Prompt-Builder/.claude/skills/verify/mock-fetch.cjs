/**
 * `fetch` for the requests a suite sends to its own mock from Node.
 *
 * On a loaded machine such a request can get no answer at all - Node's fetch
 * then throws "fetch failed" - and a suite stopped there on a request that
 * checks nothing of the page. A request that got no answer is sent again, up
 * to three times in all; an answer of any status is handed back as it is.
 * The control routes of the mock set a state and its reads change nothing,
 * so a request that is sent twice does what it did once (the poll of a job
 * from Node is counted once more, and a job found completed stays so).
 *
 * Every suite that sends a request to the mock takes `fetch` from here
 * (`const fetch = require('./mock-fetch.cjs');`); run-all.cjs refuses to run
 * one that does not.
 */
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

module.exports = async function mockFetch(url, options) {
  for (let attempt = 1; ; attempt++) {
    try {
      return await fetch(url, options);
    } catch (error) {
      if (attempt >= 3) throw error;
      await sleep(1000 * attempt);
    }
  }
};
