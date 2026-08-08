// SPDX-License-Identifier: LGPL-2.1-or-later
// The chrome around the canvas: the toolbar, the status bar, the caption/send
// bar, the image-source sheet, and the transient banner/toast.
//
// The markup itself lives in index.html -- five buttons and a canvas are
// easier to read, and easier to diff against docs/device-annotation-mockup.html,
// as HTML than as DOM-building code. This module finds those elements, owns
// their behaviour, and hands `main.ts` a small typed surface. No component
// framework: there is no application state here that would benefit from one.

/** Which drawing tool is armed. `dimension` is phase 5; its button exists in
 * the toolbar as a disabled placeholder so the layout and the tap targets are
 * the real ones. */
export type Tool = "pen";

export type SourceChoice = "freecad" | "camera" | "library";

/** What the status bar shows about the current image. `scale` and
 * `confidence` are phase 5's -- until then every image is "no scale". */
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
  /** What the incoming-capture banner's Load button does. */
  onLoadIncoming: () => void;

  setStatus(status: StatusInfo): void;
  setHint(text: string): void;
  setSendEnabled(enabled: boolean): void;
  /** Enables the "Latest view from FreeCAD" source once /api/latest has
   * something to offer, with its document/view as the subtitle. */
  setFreecadSource(available: boolean, subtitle: string): void;
  openSources(): void;
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
  const hint = need(doc, "hint");

  const penButton = need<HTMLButtonElement>(doc, "t-pen");
  const sourceButton = need<HTMLButtonElement>(doc, "t-source");
  const undoButton = need<HTMLButtonElement>(doc, "t-undo");
  const clearButton = need<HTMLButtonElement>(doc, "t-clear");

  const sourcesSheet = need(doc, "sheet-sources");
  const freecadChoice = need<HTMLButtonElement>(doc, "src-freecad");
  const freecadSub = need(doc, "src-freecad-sub");
  const cameraChoice = need<HTMLButtonElement>(doc, "src-camera");
  const libraryChoice = need<HTMLButtonElement>(doc, "src-library");
  const cancelChoice = need<HTMLButtonElement>(doc, "src-cancel");

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
    onLoadIncoming: () => {},

    setStatus(status) {
      docName.textContent = status.title;
      viewName.textContent = status.subtitle;
      conf.className = CONFIDENCE_CLASS[status.confidence];
      conf.textContent = CONFIDENCE_LABEL[status.confidence];
      scaleText.textContent = status.scaleText ?? "";
      dot.className = status.connected ? "dot" : "dot off";
    },

    setHint(text) {
      hint.textContent = text;
      hint.hidden = text === "";
    },

    setSendEnabled(enabled) {
      sendButton.disabled = !enabled;
    },

    setFreecadSource(available, subtitle) {
      freecadChoice.disabled = !available;
      freecadSub.textContent = subtitle;
    },

    openSources() {
      sourcesSheet.hidden = false;
    },

    closeSheets() {
      sourcesSheet.hidden = true;
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
  sourcesSheet.addEventListener("click", (e) => {
    if (e.target === sourcesSheet) ui.closeSheets();
  });

  penButton.addEventListener("click", () => {
    penButton.setAttribute("aria-pressed", "true");
    ui.onTool("pen");
  });
  undoButton.addEventListener("click", () => ui.onUndo());
  clearButton.addEventListener("click", () => ui.onClear());
  sendButton.addEventListener("click", () => ui.onSend());
  bannerLoad.addEventListener("click", () => {
    ui.hideBanner();
    ui.onLoadIncoming();
  });

  return ui;
}
