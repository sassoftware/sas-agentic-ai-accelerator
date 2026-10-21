/**
 * A function that does nothing while its previous call is still out. A poll a
 * timer starts every few seconds (setInterval) is not sent again while the
 * answer to the one before is awaited: an answer of SAS Viya can take longer
 * than the interval, and every poll that overlapped acted once more on the end
 * of the job it followed.
 */
export function oneAtATime(run: () => Promise<void>): () => Promise<void> {
  let out = false;
  return async () => {
    if (out) return;
    out = true;
    try {
      await run();
    } finally {
      out = false;
    }
  };
}
