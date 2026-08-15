// SPDX-License-Identifier: LGPL-2.1-or-later
// The chrome around the canvas: the toolbar, the status bar, the caption/send
// bar, the image-source sheet, and the transient banner/toast.
//
// The markup itself lives in index.html -- five buttons and a canvas are
// easier to read, and easier to diff against docs/device-annotation-mockup.html,
// as HTML than as DOM-building code. This module finds those elements, owns
// their behaviour, and hands `main.ts` a small typed surface. No component
// framework: there is no application state here that would benefit from one.

/** Which drawing tool is armed: freehand ink, a two-point dimension, or the
 * eraser (which rubs out whole strokes -- ink is stored as vectors). */
export type Tool = "pen" | "dimension" | "eraser";

const WELCOME_KEY = "fc-welcome-seen";
const MSGBAR_KEY = "fc-msgbar-collapsed";

/** Whether the first-run help has been dismissed on this device.
 *
 * localStorage, not sessionStorage: the page is reopened from a fresh QR scan
 * every time FreeCAD restarts the server, and someone who has read the help
 * once shouldn't meet it again on every pairing. Storage is passed in so the
 * rule is testable without a DOM.
 */
export function welcomeSeen(storage: Pick<Storage, "getItem">): boolean {
  return storage.getItem(WELCOME_KEY) === "1";
}

export function markWelcomeSeen(storage: Pick<Storage, "setItem">): void {
  storage.setItem(WELCOME_KEY, "1");
}

/** Whether the message bar was left collapsed. Remembered across reloads for
 * the same reason the welcome is: the page is reopened from a fresh QR scan
 * every time FreeCAD restarts the server, and someone who folded the bar away
 * on a small screen should not have to fold it again each pairing. Expanded is
 * the default -- a message nobody can see is worse than a bar in the way. */
export function msgbarCollapsed(storage: Pick<Storage, "getItem">): boolean {
  return storage.getItem(MSGBAR_KEY) === "1";
}

export function setMsgbarCollapsed(
  storage: Pick<Storage, "setItem">,
  collapsed: boolean,
): void {
  storage.setItem(MSGBAR_KEY, collapsed ? "1" : "0");
}

export type SourceChoice = "freecad" | "camera" | "library";

/** What the value sheet opens with. `measured` is pre-formatted by `scale`,
 * because whether it reads "24.3 mm" or "348 px" is that module's rule, not
 * this one's. */
export interface ValueSheetInfo {
  readonly measured: string;
  readonly targetMm: number | null;
  readonly note: string;
}

/** One row in the gallery sheet -- every capture FreeCAD has published this
 * session, as main.ts has it, formatted for display. `isNew` is "not yet
 * loaded", not "recently published": it's what tells the user which one they
 * haven't actually looked at. */
export interface GalleryItem {
  readonly id: string;
  readonly title: string;
  readonly time: string;
  readonly isNew: boolean;
}

/** What the status bar shows about the current image. */
export interface StatusInfo {
  /** Document or file name. */
  readonly title: string;
  /** The view, or where the image came from. */
  readonly subtitle: string;
  readonly confidence: "exact" | "approximate" | "none";
  /** e.g. "0.084 mm/px". Blank hides it. */
  readonly scaleText?: string;
  /** Green when an image is loaded and we're paired. */
  readonly connected: boolean;
}

export interface Ui {
  readonly canvas: HTMLCanvasElement;
  /** Posted alongside the flattened PNG, as the annotation document's
   * `caption`. Cleared by main.ts once a send succeeds. */
  readonly caption: HTMLInputElement;

  /** Assigned by main.ts. Defaults are no-ops so a half-wired UI is inert
   * rather than broken. */
  onSource: (choice: SourceChoice) => void;
  onTool: (tool: Tool) => void;
  onUndo: () => void;
  onClear: () => void;
  onSend: () => void;
  /** Put the image back on the contain-fit after a pinch. */
  onFit: () => void;
  /** What the incoming-capture banner's Load button does. */
  onLoadIncoming: () => void;
  /** The gallery button was tapped -- main.ts responds with `openGallery`. */
  onOpenGallery: () => void;
  /** A row in the gallery sheet was tapped, with its capture's id. */
  onGallerySelect: (id: string) => void;
  /** The value sheet's Done, with the target the user typed (null if they left
   * it blank -- "just point it out" is a legitimate answer) and their note. */
  onValueDone: (targetMm: number | null, note: string) => void;
  onValueDelete: () => void;
  /** The calibration sheet's Set scale, with the real distance in mm. */
  onCalibrate: (mm: number) => void;
  /** ...and its Skip, which means "I'll mark it up without millimetres". */
  onCalibrateSkip: () => void;
  /** The first-run help's Got it, so the caller can record that it's been read. */
  onWelcomeDone: () => void;
  /** The message bar was folded or unfolded, so the caller can remember it. */
  onMessageToggle: (collapsed: boolean) => void;

  setStatus(status: StatusInfo): void;
  /** The message bar. `note` is Claude's line for the current image (null when
   * it sent none); `hint` is what the armed tool is about to do. The bar hides
   * itself when it has neither. */
  setMessage(note: string | null, hint: string): void;
  /** Unfold the bar. Called when a new note arrives -- a message the user has
   * not read yet is not something to leave hidden behind a chevron. */
  expandMessage(): void;
  /** Fold it, or not, to match what was remembered. */
  setMessageCollapsed(collapsed: boolean): void;
  setSendEnabled(enabled: boolean): void;
  /** Reflect the armed tool in the toolbar. */
  setTool(tool: Tool): void;
  /** Show the fit button only once there is something to fit back. */
  setZoomed(zoomed: boolean): void;
  openValue(info: ValueSheetInfo): void;
  openCalibrate(): void;
  /** Enables the "Latest view from FreeCAD" source once /api/latest has
   * something to offer, with its document/view as the subtitle. */
  setFreecadSource(available: boolean, subtitle: string): void;
  /** Enables the gallery button once there's at least one capture to list. */
  setGalleryEnabled(enabled: boolean): void;
  /** The gallery holds a capture that hasn't been loaded yet. */
  setGalleryBadge(hasNew: boolean): void;
  /** Render the gallery sheet's rows and show it. */
  openGallery(items: readonly GalleryItem[]): void;
  openSources(): void;
  openWelcome(): void;
  closeSheets(): void;
  /** A capture arrived mid-drawing. Notify, don't clobber. */
  showBanner(text: string): void;
  hideBanner(): void;
  toast(text: string, kind?: "ok" | "bad"): void;
}

function need<T extends HTMLElement>(doc: Document, id: string): T {
  const el = doc.getElementById(id);
  if (!el) throw new Error(`missing element #${id}`);
  return el as T;
}

const CONFIDENCE_CLASS: Record<StatusInfo["confidence"], string> = {
  exact: "badge exact",
  approximate: "badge approx",
  none: "badge",
};

const CONFIDENCE_LABEL: Record<StatusInfo["confidence"], string> = {
  exact: "exact scale",
  approximate: "approximate scale",
  none: "no scale",
};

export function mountUi(doc: Document = document): Ui {
  const canvas = need<HTMLCanvasElement>(doc, "sheet");
  const caption = need<HTMLInputElement>(doc, "caption");
  const sendButton = need<HTMLButtonElement>(doc, "send");

  const dot = need(doc, "dot");
  const docName = need(doc, "docname");
  const viewName = need(doc, "viewname");
  const conf = need(doc, "conf");
  const scaleText = need(doc, "scaletext");

  const msgbar = need<HTMLButtonElement>(doc, "msgbar");
  const msgNote = need(doc, "msg-note");
  const msgHint = need(doc, "msg-hint");

  const penButton = need<HTMLButtonElement>(doc, "t-pen");
  const dimButton = need<HTMLButtonElement>(doc, "t-dim");
  const eraseButton = need<HTMLButtonElement>(doc, "t-erase");
  const sourceButton = need<HTMLButtonElement>(doc, "t-source");
  const undoButton = need<HTMLButtonElement>(doc, "t-undo");
  const clearButton = need<HTMLButtonElement>(doc, "t-clear");
  const fitButton = need<HTMLButtonElement>(doc, "t-fit");

  const valueSheet = need(doc, "sheet-value");
  const valueMeasured = need(doc, "val-measured");
  const valueTarget = need<HTMLInputElement>(doc, "val-target");
  const valueNote = need<HTMLInputElement>(doc, "val-note");
  const valueDelete = need<HTMLButtonElement>(doc, "val-delete");
  const valueDone = need<HTMLButtonElement>(doc, "val-done");

  const calibrateSheet = need(doc, "sheet-calibrate");
  const calibrateMm = need<HTMLInputElement>(doc, "cal-mm");
  const calibrateSkip = need<HTMLButtonElement>(doc, "cal-skip");
  const calibrateSet = need<HTMLButtonElement>(doc, "cal-set");

  const sourcesSheet = need(doc, "sheet-sources");
  const freecadChoice = need<HTMLButtonElement>(doc, "src-freecad");
  const freecadSub = need(doc, "src-freecad-sub");
  const cameraChoice = need<HTMLButtonElement>(doc, "src-camera");
  const libraryChoice = need<HTMLButtonElement>(doc, "src-library");
  const cancelChoice = need<HTMLButtonElement>(doc, "src-cancel");

  const galleryButton = need<HTMLButtonElement>(doc, "t-gallery");
  const gallerySheet = need(doc, "sheet-gallery");
  const galleryList = need(doc, "gallery-list");
  const galleryCancel = need<HTMLButtonElement>(doc, "gallery-cancel");

  const welcomeSheet = need(doc, "sheet-welcome");
  const welcomeDone = need<HTMLButtonElement>(doc, "welcome-done");
  const helpButton = need<HTMLButtonElement>(doc, "t-help");

  const banner = need(doc, "banner");
  const bannerText = need(doc, "banner-text");
  const bannerLoad = need<HTMLButtonElement>(doc, "banner-load");
  const toastEl = need(doc, "toast");

  let toastTimer: number | null = null;

  const ui: Ui = {
    canvas,
    caption,

    onSource: () => {},
    onTool: () => {},
    onUndo: () => {},
    onClear: () => {},
    onSend: () => {},
    onFit: () => {},
    onLoadIncoming: () => {},
    onOpenGallery: () => {},
    onGallerySelect: () => {},
    onValueDone: () => {},
    onValueDelete: () => {},
    onCalibrate: () => {},
    onCalibrateSkip: () => {},
    onWelcomeDone: () => {},
    onMessageToggle: () => {},

    setStatus(status) {
      docName.textContent = status.title;
      viewName.textContent = status.subtitle;
      conf.className = CONFIDENCE_CLASS[status.confidence];
      conf.textContent = CONFIDENCE_LABEL[status.confidence];
      scaleText.textContent = status.scaleText ?? "";
      dot.className = status.connected ? "dot" : "dot off";
    },

    setMessage(note, hint) {
      msgNote.textContent = note ?? "";
      msgNote.hidden = !note;
      msgHint.textContent = hint;
      msgHint.hidden = hint === "";
      // Drives which line the collapsed bar previews.
      msgbar.classList.toggle("has-note", Boolean(note));
      msgbar.hidden = !note && hint === "";
    },

    expandMessage() {
      msgbar.setAttribute("aria-expanded", "true");
    },

    setMessageCollapsed(collapsed) {
      msgbar.setAttribute("aria-expanded", String(!collapsed));
    },

    setSendEnabled(enabled) {
      sendButton.disabled = !enabled;
    },

    setTool(tool) {
      penButton.setAttribute("aria-pressed", String(tool === "pen"));
      dimButton.setAttribute("aria-pressed", String(tool === "dimension"));
      eraseButton.setAttribute("aria-pressed", String(tool === "eraser"));
    },

    setZoomed(zoomed) {
      fitButton.hidden = !zoomed;
    },

    openValue(info) {
      valueMeasured.textContent = info.measured;
      valueTarget.value = info.targetMm === null ? "" : String(info.targetMm);
      valueNote.value = info.note;
      valueSheet.hidden = false;
      // Not focused on purpose: raising the tablet keyboard covers the sheet
      // and the dimension the user is looking at, and most dimensions are
      // placed to point at something rather than to set a number.
    },

    openCalibrate() {
      calibrateMm.value = "";
      calibrateSheet.hidden = false;
    },

    setFreecadSource(available, subtitle) {
      freecadChoice.disabled = !available;
      freecadSub.textContent = subtitle;
    },

    setGalleryEnabled(enabled) {
      galleryButton.disabled = !enabled;
    },

    setGalleryBadge(hasNew) {
      galleryButton.classList.toggle("has-new", hasNew);
    },

    openGallery(items) {
      galleryList.replaceChildren();
      if (items.length === 0) {
        const empty = doc.createElement("p");
        empty.className = "gallery-empty";
        empty.textContent = "Nothing sent yet.";
        galleryList.append(empty);
      }
      for (const item of items) {
        const row = doc.createElement("button");
        row.type = "button";
        row.className = "gallery-item";
        const meta = doc.createElement("span");
        meta.className = "meta";
        const title = doc.createElement("strong");
        title.textContent = item.title;
        const time = doc.createElement("small");
        time.textContent = item.time;
        meta.append(title, time);
        row.append(meta);
        if (item.isNew) {
          const badge = doc.createElement("span");
          badge.className = "new";
          badge.textContent = "NEW";
          row.append(badge);
        }
        row.addEventListener("click", () => {
          ui.closeSheets();
          ui.onGallerySelect(item.id);
        });
        galleryList.append(row);
      }
      gallerySheet.hidden = false;
    },

    openSources() {
      sourcesSheet.hidden = false;
    },

    openWelcome() {
      welcomeSheet.hidden = false;
    },

    closeSheets() {
      sourcesSheet.hidden = true;
      valueSheet.hidden = true;
      calibrateSheet.hidden = true;
      welcomeSheet.hidden = true;
      gallerySheet.hidden = true;
    },

    showBanner(text) {
      bannerText.textContent = text;
      banner.hidden = false;
    },

    hideBanner() {
      banner.hidden = true;
    },

    toast(text, kind = "ok") {
      toastEl.textContent = text;
      toastEl.className = kind === "bad" ? "toast bad" : "toast";
      toastEl.hidden = false;
      if (toastTimer !== null) window.clearTimeout(toastTimer);
      toastTimer = window.setTimeout(() => {
        toastEl.hidden = true;
        toastTimer = null;
      }, 2600);
    },
  };

  sourceButton.addEventListener("click", () => ui.openSources());
  cancelChoice.addEventListener("click", () => ui.closeSheets());
  freecadChoice.addEventListener("click", () => {
    ui.closeSheets();
    ui.onSource("freecad");
  });
  cameraChoice.addEventListener("click", () => {
    ui.closeSheets();
    ui.onSource("camera");
  });
  libraryChoice.addEventListener("click", () => {
    ui.closeSheets();
    ui.onSource("library");
  });
  // Tapping the scrim outside the sheet dismisses it, as a bottom sheet should.
  // The value sheet is NOT in this list: dismissing it by a stray tap would
  // silently discard a typed target, and the sheet has both a Done and a
  // Delete for the two things the user might mean.
  for (const scrim of [sourcesSheet, calibrateSheet, gallerySheet]) {
    scrim.addEventListener("click", (e) => {
      if (e.target === scrim) ui.closeSheets();
    });
  }

  for (const [button, tool] of [
    [penButton, "pen"],
    [dimButton, "dimension"],
    [eraseButton, "eraser"],
  ] as const) {
    button.addEventListener("click", () => {
      ui.setTool(tool);
      ui.onTool(tool);
    });
  }

  /** A blank input means "no target", not zero -- the sheet's own text says so
   * ("leave it blank to just point it out"), and Number("") is 0. */
  const typedNumber = (input: HTMLInputElement): number | null => {
    const text = input.value.trim();
    if (!text) return null;
    const value = Number(text);
    return Number.isFinite(value) ? value : null;
  };

  valueDone.addEventListener("click", () => {
    ui.closeSheets();
    ui.onValueDone(typedNumber(valueTarget), valueNote.value.trim());
  });
  valueDelete.addEventListener("click", () => {
    ui.closeSheets();
    ui.onValueDelete();
  });
  calibrateSet.addEventListener("click", () => {
    const mm = typedNumber(calibrateMm);
    // A missing or nonsense length leaves the sheet open rather than setting a
    // wrong scale: everything downstream trusts this number.
    if (mm === null || mm <= 0) return;
    ui.closeSheets();
    ui.onCalibrate(mm);
  });
  calibrateSkip.addEventListener("click", () => {
    ui.closeSheets();
    ui.onCalibrateSkip();
  });

  msgbar.addEventListener("click", () => {
    const collapsed = msgbar.getAttribute("aria-expanded") === "true";
    ui.setMessageCollapsed(collapsed);
    ui.onMessageToggle(collapsed);
  });

  helpButton.addEventListener("click", () => ui.openWelcome());
  welcomeDone.addEventListener("click", () => {
    ui.closeSheets();
    ui.onWelcomeDone();
  });

  galleryButton.addEventListener("click", () => ui.onOpenGallery());
  galleryCancel.addEventListener("click", () => ui.closeSheets());

  undoButton.addEventListener("click", () => ui.onUndo());
  clearButton.addEventListener("click", () => ui.onClear());
  fitButton.addEventListener("click", () => ui.onFit());
  sendButton.addEventListener("click", () => ui.onSend());
  bannerLoad.addEventListener("click", () => {
    ui.hideBanner();
    ui.onLoadIncoming();
  });

  return ui;
}
