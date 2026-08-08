// SPDX-License-Identifier: LGPL-2.1-or-later
// Phase 1 placeholder: proves the page was served, and that the token survived
// the trip from the QR code / typed URL into sessionStorage.
//
// The real app (toolbar, canvas, ink) is phase 3 and the transport is phase 4;
// this file becomes the wiring that mounts them.
import { resolveToken } from "./token";

const status = document.getElementById("status");

if (status) {
  const token = resolveToken(window.location.search, window.sessionStorage);
  if (token) {
    status.textContent = "Connected — paired with FreeCAD.";
    status.className = "ok";
    // Drop the token from the visible URL now that it's stashed, so a
    // screenshot or a shared link doesn't carry it.
    if (window.location.search) {
      history.replaceState(null, "", window.location.pathname);
    }
  } else {
    status.textContent =
      "No pairing token — open the link from FreeCAD's Device button again.";
    status.className = "warn";
  }
}
