/** Zero is a fresh server's baseline, not a bookmark recall. A lower positive
 * revision can also be a recall after a restart that happened between polls.
 */
export function observeBookmarkNavigation(previous, next) {
  if (!Number.isInteger(next) || next < 0) return {revision: previous, recall: false};
  return {revision: next, recall: previous !== undefined && next !== previous && next !== 0};
}
