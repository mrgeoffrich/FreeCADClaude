// SPDX-License-Identifier: LGPL-2.1-or-later
// The pairing token: how it gets off the URL and where it lives afterwards.
//
// device_server.py mints one token per start and accepts it either as `?t=` on
// the page load or as an `X-FC-Token` header. The device arrives via the first
// form (a scanned QR code, or a typed URL), so the page has to lift it out of
// its own location once and keep it -- the query string is gone the moment the
// user reloads from a bookmark, and every later fetch needs the header form.
//
// sessionStorage, not localStorage: the token is regenerated every time the
// server starts, so persisting it past the browser tab would only ever hand
// back a stale one.

const STORAGE_KEY = "fc-device-token";

/** The token carried by a page URL's query string, or null.
 *
 * Pure and takes the search string rather than reading `location`, so the
 * parsing is testable without a DOM.
 */
export function tokenFromSearch(search: string): string | null {
  const value = new URLSearchParams(search).get("t");
  return value ? value : null;
}

/** Resolve the token for this page load: the URL wins, then whatever a
 * previous load in this tab stashed. Returns null if we have neither, which is
 * the "unpaired -- rescan the code" state. */
export function resolveToken(
  search: string,
  storage: Pick<Storage, "getItem" | "setItem">,
): string | null {
  const fromUrl = tokenFromSearch(search);
  if (fromUrl) {
    storage.setItem(STORAGE_KEY, fromUrl);
    return fromUrl;
  }
  return storage.getItem(STORAGE_KEY);
}
