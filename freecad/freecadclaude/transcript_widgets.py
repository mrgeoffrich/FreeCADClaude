# SPDX-License-Identifier: LGPL-2.1-or-later
"""A scrollable stack of collapsible entry widgets for the chat transcript.

Pure Qt -- no knowledge of AgentWorker, the CLI, or FreeCAD tools. Each
transcript entry (a user message, a streamed Claude reply, a thinking block,
a tool-use note, ...) is its own :class:`CollapsibleSection` -- a small
checkable-QToolButton header that shows/hides an auto-sizing content browser
-- stacked inside a :class:`TranscriptView` (a QScrollArea). This replaces
rendering the whole conversation as one big Markdown string on a single
QTextBrowser, so any entry can be collapsed independently of the rest.
"""

from PySide import QtCore, QtWidgets

#: Role label colours, reused for both the live widget headers and the
#: plain-Markdown reconstruction in TranscriptView.to_markdown().
YOU_COLOR = "#3478c6"      # blue
CLAUDE_COLOR = "#d97757"   # Claude coral
MUTED_COLOR = "#888888"    # de-emphasised grey (reasoning)


def _blockquote(text):
    """Render reasoning as a muted, de-emphasised blockquote."""
    return "\n".join("> " + line for line in text.splitlines())


def _header_label(kind, live, tool_name=""):
    return {
        "you": "You",
        "claude": "Claude",
        "thinking": "💭 thinking…" if live else "💭 thought",
        "tool": f"↪ used tool: {tool_name}",
        "warning": "⚠ Warning",
    }.get(kind, "Note")


def _header_color(kind):
    return {"you": YOU_COLOR, "claude": CLAUDE_COLOR, "thinking": MUTED_COLOR}.get(kind)


class _AutoHeightTextBrowser(QtWidgets.QTextBrowser):
    """A QTextBrowser that sizes itself to its document instead of scrolling
    internally, so many of these can stack inside one outer QScrollArea."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setOpenExternalLinks(True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._min_height = self.fontMetrics().height() + 8
        self.document().documentLayout().documentSizeChanged.connect(self._sync_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Re-wrap to the new width now (word-wrap height depends on width)
        # so the height measured below is current, not stale.
        self.document().setTextWidth(self.viewport().width())
        self._sync_height()

    def sync_height(self):
        """Call after un-hiding a previously-collapsed browser -- a hidden
        widget may never have received a real resizeEvent, so its wrapped
        height can be stale the first time it's expanded."""
        self.document().setTextWidth(self.viewport().width())
        self._sync_height()

    def _sync_height(self, *_args):
        h = max(int(self.document().size().height()) + 2 * self.frameWidth(), self._min_height)
        if h != self.height():  # guard against a resize -> layout -> resize loop
            self.setFixedHeight(h)


class CollapsibleSection(QtWidgets.QWidget):
    """One transcript entry: a checkable header that toggles an auto-height
    Markdown content browser."""

    _DEFAULT_COLLAPSED = {"thinking": True, "tool": True}  # else expanded
    _CONTENT_TRANSFORM = {"thinking": _blockquote}          # else identity
    #: Shown instead of a blank browser while a section is live but hasn't
    #: received any content yet -- e.g. the eager thinking placeholder created
    #: the moment a turn starts, before Claude has streamed any reasoning (or
    #: for a turn that produces none at all). Without this, expanding it out
    #: of curiosity mid-turn just looks broken.
    _LIVE_PLACEHOLDER = {"thinking": "*(thinking…)*"}

    def __init__(self, kind, text="", *, collapsed=None, live=False,
                 tool_name="", parent=None):
        super().__init__(parent)
        self.kind = kind
        self._tool_name = tool_name
        self._raw_text = ""
        self._live = live
        self._build_ui()
        self.set_collapsed(self._DEFAULT_COLLAPSED.get(kind, False) if collapsed is None else collapsed)
        self._refresh_header()
        if text:
            self.append_text(text)
            self.flush()
        elif self._live and kind in self._LIVE_PLACEHOLDER:
            self.flush()  # show the placeholder immediately, not just on the next render tick

    def _build_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._toggle = QtWidgets.QToolButton(self)
        self._toggle.setCheckable(True)
        self._toggle.setAutoRaise(True)
        self._toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self._toggle.clicked.connect(lambda checked: self.set_collapsed(not checked))
        outer.addWidget(self._toggle)
        self._browser = _AutoHeightTextBrowser(self)
        outer.addWidget(self._browser)

    # -- collapse/expand ---------------------------------------------------

    def set_collapsed(self, collapsed):
        self._toggle.setChecked(not collapsed)
        self._toggle.setArrowType(QtCore.Qt.RightArrow if collapsed else QtCore.Qt.DownArrow)
        self._browser.setVisible(not collapsed)
        if not collapsed:
            self._browser.sync_height()

    def is_collapsed(self):
        return not self._toggle.isChecked()

    # -- content -------------------------------------------------------

    def append_text(self, chunk):
        """Cheap accumulate only; call flush() to actually re-render."""
        self._raw_text += chunk

    def flush(self):
        if not self._raw_text and self._live and self.kind in self._LIVE_PLACEHOLDER:
            self._browser.setMarkdown(self._LIVE_PLACEHOLDER[self.kind])
            return
        transform = self._CONTENT_TRANSFORM.get(self.kind)
        self._browser.setMarkdown(transform(self._raw_text) if transform else self._raw_text)

    def update_content_markdown(self, text):
        self._raw_text = ""
        self.append_text(text)
        self.flush()

    # -- live/committed lifecycle -------------------------------------

    def commit(self, final_text=None):
        self._live = False
        if final_text is not None:
            self.update_content_markdown(final_text)
        self._refresh_header()

    @property
    def raw_text(self):
        return self._raw_text

    @property
    def is_live(self):
        return self._live

    def _refresh_header(self):
        self._toggle.setText(_header_label(self.kind, self._live, self._tool_name))
        color = _header_color(self.kind)
        style = "QToolButton { border: none; font-weight: bold; }"
        if color:
            style = f"QToolButton {{ border: none; font-weight: bold; color: {color}; }}"
        self._toggle.setStyleSheet(style)

    def to_markdown_fragment(self):
        """Plain-Markdown reconstruction of this entry, for ChatWidget._md."""
        if self.kind == "you":
            return f'<span style="color:{YOU_COLOR}"><b>You:</b></span> {self._raw_text}'
        if self.kind == "claude":
            return f'<span style="color:{CLAUDE_COLOR}"><b>Claude:</b></span>\n\n{self._raw_text}'
        if self.kind == "thinking":
            return f'<span style="color:{MUTED_COLOR}">💭 <i>thought</i></span>\n\n{_blockquote(self._raw_text)}'
        if self.kind == "tool":
            header = f"*↪ used tool: {self._tool_name}*"
            return f"{header}\n\n{self._raw_text}" if self._raw_text.strip() else header
        if self.kind == "warning":
            return f"**⚠ {self._raw_text}**"
        return self._raw_text  # "note" and anything else: already plain Markdown


class TranscriptView(QtWidgets.QScrollArea):
    """A vertical stack of CollapsibleSection entries in a scroll area."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        body = QtWidgets.QWidget(self)
        self._layout = QtWidgets.QVBoxLayout(body)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)  # keeps entries packed at the top
        self.setWidget(body)

        self._entries = []        # ordered CollapsibleSection list (incl. live ones)

        # Auto-follow the bottom while streaming. Driven by the scrollbar's own
        # signals rather than a "check pinned, resize, singleShot(0) rescroll"
        # dance around each content mutation: rangeChanged fires exactly when
        # Qt finishes recalculating the scrollable range (however many layout
        # passes a given change takes), so re-pinning there can't read a
        # stale bar.maximum() the way a fixed-delay timer could. It also
        # covers every content-mutating path uniformly (streamed text, a
        # newly-added entry, an expanded tool result, a collapse/expand)
        # without each call site needing its own pinned/rescroll bookkeeping.
        self._stick_to_bottom = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)

    # -- entries -------------------------------------------------------

    def add_entry(self, kind, text="", *, collapsed=None, live=False, tool_name=""):
        entry = CollapsibleSection(kind, text, collapsed=collapsed, live=live,
                                    tool_name=tool_name, parent=self.widget())
        self._layout.insertWidget(self._layout.count() - 1, entry)  # before the stretch
        self._entries.append(entry)
        return entry

    def start_live_entry(self, kind, **kw):
        return self.add_entry(kind, "", live=True, **kw)

    def remove_entry(self, entry):
        self._layout.removeWidget(entry)
        self._entries.remove(entry)
        entry.setParent(None)
        entry.deleteLater()

    def clear(self):
        for entry in list(self._entries):
            self.remove_entry(entry)

    # -- scroll pinning --------------------------------------------------

    def _on_range_changed(self, _minimum, maximum):
        if self._stick_to_bottom:
            self.verticalScrollBar().setValue(maximum)

    def _on_value_changed(self, value):
        # Recomputed on every value change regardless of cause (our own
        # re-pin above, a streaming resize nudging things, or the user
        # dragging the scrollbar) -- so scrolling back to the bottom by hand
        # mid-stream resumes auto-follow, and scrolling away stops it.
        bar = self.verticalScrollBar()
        self._stick_to_bottom = value >= bar.maximum() - 8

    # -- ChatWidget._md compatibility -----------------------------------

    def to_markdown(self):
        # NB: filter on the rendered fragment, not raw_text -- a "tool" entry's
        # content lives entirely in its header (tool_name), so raw_text is
        # always empty for it by design, yet it must still appear here.
        fragments = (e.to_markdown_fragment() for e in self._entries if not e.is_live)
        return "".join(f.rstrip() + "\n\n" for f in fragments if f.strip())
