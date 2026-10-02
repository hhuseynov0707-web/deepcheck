// A full reload starts a new behaviour session: the SDK registers afresh on
// the next page load. Its own module so tests can replace it (jsdom does not
// implement navigation).
export function reloadPage() {
  window.location.reload();
}
