"""
chat_gui.py — Project Antigravity
P2P Chat panel styled after the Nexus P2P Network UI (dark sidebar,
channel list, message bubbles, broadcast input bar).

Integration (zero changes to existing files):
  - Broadcasts AGCHAT| datagrams to all routing-table peers.
  - Receives via monkey-patched _handle_datagram → event_bus "chat_recv".
  - Added as a tab in app.py exactly like FileSharePanel.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import types
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Set

from PyQt6.QtCore import (
    QObject, Qt, QTimer, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QColor, QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

logger = logging.getLogger("antigravity.chat")

# ── Palette (matches Nexus screenshots exactly) ────────────────────────────
BG        = "#0d1117"
SIDEBAR   = "#161b22"
SURFACE   = "#1c2333"
SURFACE2  = "#21262d"
BORDER    = "#30363d"
ACCENT    = "#4f8ef7"     # blue  — active btn / send / my bubbles
ACCENT2   = "#3fb950"     # green — online dots
TEXT      = "#e6edf3"
TEXT_DIM  = "#8b949e"
BUBBLE_BG = "#1c2333"
BUBBLE_BD = "#30363d"
MY_BG     = "#1f3a5c"
MY_BD     = "#4f8ef7"


# ── Chat message dataclass ─────────────────────────────────────────────────

@dataclass
class ChatMessage:
    msg_id:  str
    node_id: str
    nick:    str
    text:    str
    ts:      float = field(default_factory=time.time)
    target:  str   = "everyone"   # "everyone" or a node_id hex

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "ChatMessage":
        d = json.loads(raw.decode())
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})

    @classmethod
    def make(cls, node_id: str, nick: str, text: str,
             target: str = "everyone") -> "ChatMessage":
        mid = hashlib.sha256(
            f"{node_id}{text}{time.time()}".encode()
        ).hexdigest()[:16]
        return cls(msg_id=mid, node_id=node_id, nick=nick,
                   text=text, target=target)


# ── UDP datagram integration ───────────────────────────────────────────────

CHAT_MAGIC = b"AGCHAT|"


def install_chat_handler(node) -> None:
    """
    Monkey-patch node._handle_datagram (idempotent) so AGCHAT| datagrams
    are decoded and published on the event_bus as "chat_recv" events.
    """
    if getattr(node, "_chat_handler_installed", False):
        return
    original = node._handle_datagram.__func__

    async def _patched(self, data: bytes, addr) -> None:
        if data.startswith(CHAT_MAGIC):
            try:
                msg = ChatMessage.from_bytes(data[len(CHAT_MAGIC):])
                from network import event_bus
                event_bus.publish("chat_recv", {
                    "message": f"[CHAT] {msg.nick}: {msg.text}",
                    "chat_msg": msg,
                })
            except Exception as exc:
                logger.debug(f"[CHAT] bad datagram {addr}: {exc}")
            return
        await original(self, data, addr)

    node._handle_datagram = types.MethodType(_patched, node)
    node._chat_handler_installed = True


async def _broadcast(node, msg: ChatMessage) -> int:
    """Send a CHAT datagram to every known peer. Returns # sent."""
    payload = CHAT_MAGIC + msg.to_bytes()
    if len(payload) > 1400:
        return 0
    peers = node.routing_table.find_closest(node.anr.node_id, count=200)
    sent = 0
    for anr in peers:
        try:
            node._transport.sendto(payload, (anr.ip, anr.udp_port))
            sent += 1
        except Exception:
            pass
    return sent


# ── Qt event bridge (network thread → GUI thread) ─────────────────────────

class _ChatBridge(QObject):
    msg_signal = pyqtSignal(object)   # ChatMessage

    def handle_event(self, event: str, payload: dict) -> None:
        if event == "chat_recv":
            cm = payload.get("chat_msg")
            if cm is not None:
                self.msg_signal.emit(cm)


# ── Line edit that fires on Enter ──────────────────────────────────────────

class _ChatInput(QLineEdit):
    enter_pressed = pyqtSignal()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.enter_pressed.emit()
        else:
            super().keyPressEvent(e)


# ── Single message row ─────────────────────────────────────────────────────

class _MsgRow(QWidget):
    def __init__(self, msg: ChatMessage, mine: bool, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background:{BG};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 6, 20, 6)
        lay.setSpacing(4)

        # Header: nick + timestamp
        hdr = QHBoxLayout()
        hdr.setSpacing(8)

        nick = QLabel(msg.nick)
        nick.setStyleSheet(
            f"color:{'#e6edf3' if mine else TEXT}; font-size:12px; "
            f"font-weight:600; background:transparent;"
        )
        ts_str = time.strftime("%H:%M", time.localtime(msg.ts))
        ts   = QLabel(ts_str)
        ts.setStyleSheet(f"color:{TEXT_DIM}; font-size:10px; background:transparent;")

        hdr.addWidget(nick)
        hdr.addWidget(ts)
        hdr.addStretch()
        lay.addLayout(hdr)

        # Bubble
        bubble = QLabel(msg.text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        bg = MY_BG     if mine else BUBBLE_BG
        bd = MY_BD     if mine else BUBBLE_BD
        bubble.setStyleSheet(f"""
            QLabel {{
                background:{bg};
                border:1px solid {bd};
                border-radius:8px;
                color:{TEXT};
                font-size:13px;
                padding:10px 14px;
            }}
        """)
        lay.addWidget(bubble)


# ── System / info row ──────────────────────────────────────────────────────

class _SysRow(QWidget):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{BG};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 4, 20, 4)
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{TEXT_DIM}; font-size:11px; background:transparent;")
        lay.addWidget(lbl)
        lay.addStretch()


# ── Scrollable message area ────────────────────────────────────────────────

class _MsgArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setStyleSheet(f"QScrollArea {{ border:none; background:{BG}; }}")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().setStyleSheet(f"""
            QScrollBar:vertical {{ background:{SURFACE}; width:6px; border-radius:3px; }}
            QScrollBar::handle:vertical {{ background:{BORDER}; border-radius:3px; min-height:24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)
        self._inner = QWidget()
        self._inner.setStyleSheet(f"background:{BG};")
        self._vlay  = QVBoxLayout(self._inner)
        self._vlay.setContentsMargins(0, 8, 0, 8)
        self._vlay.setSpacing(0)
        self._vlay.addStretch()
        self.setWidget(self._inner)

    def add(self, w: QWidget) -> None:
        self._vlay.addWidget(w)
        QTimer.singleShot(30, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()
        ))

    def clear_messages(self) -> None:
        while self._vlay.count() > 1:
            item = self._vlay.takeAt(1)
            if item and item.widget():
                item.widget().deleteLater()


# ── Sidebar channel / peer button ──────────────────────────────────────────

class _ChanBtn(QPushButton):
    """Checkable sidebar button — matches Nexus "Everyone" / peer row style."""

    BASE_STYLE = f"""
        QPushButton {{
            background: transparent;
            color: {TEXT_DIM};
            border: none;
            border-radius: 6px;
            font-size: 13px;
            padding: 7px 14px;
            text-align: left;
        }}
        QPushButton:hover {{
            background: {SURFACE2};
            color: {TEXT};
        }}
        QPushButton:checked {{
            background: rgba(79,142,247,0.20);
            color: {TEXT};
            font-weight: 600;
        }}
    """

    def __init__(self, label: str, icon: str = "●", active: bool = False):
        super().__init__(f"  {icon}  {label}")
        self.setCheckable(True)
        self.setChecked(active)
        self.setStyleSheet(self.BASE_STYLE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


# ── Main Chat Panel ────────────────────────────────────────────────────────

class ChatPanel(QWidget):
    """
    Drop-in P2P chat tab.

    Usage in app.py:
        from chat_gui import ChatPanel
        _chat = ChatPanel()
        tab_widget.addTab(_chat, "💬 Chat")
        ...
        _chat.attach(node, loop)   # call after node is running
    """

    def __init__(self, node=None, loop=None, parent=None):
        super().__init__(parent)
        self._node     = node
        self._loop     = loop
        self._my_id    = ""
        self._nick     = "Anonymous"
        self._channel  = "everyone"
        self._seen:     Set[str]         = set()
        self._nicks:    Dict[str, str]   = {}   # node_id → nick
        self._chan_btns: Dict[str, _ChanBtn] = {}

        self._bridge = _ChatBridge()
        self._bridge.msg_signal.connect(self._on_incoming)

        self.setStyleSheet(
            f"QWidget {{ background:{BG}; color:{TEXT}; "
            f"font-family:'Inter','Segoe UI',sans-serif; }}"
        )
        self._build_ui()

        self._peer_timer = QTimer(self)
        self._peer_timer.timeout.connect(self._refresh_peers)
        self._peer_timer.start(4000)

    # ── Public ────────────────────────────────────────────────────────────

    def attach(self, node, loop: asyncio.AbstractEventLoop) -> None:
        self._node  = node
        self._loop  = loop
        self._my_id = node.anr.node_id
        self._nick  = f"Node-{node.anr.node_id[:8]}"

        install_chat_handler(node)

        from network import event_bus
        event_bus.subscribe(self._bridge.handle_event)

        self._sys(f"Connected  ·  {node.host}:{node.port}")
        self._refresh_peers()

    # ── UI builders ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Left sidebar
        sidebar = self._build_sidebar()
        sidebar.setFixedWidth(220)
        root.addWidget(sidebar)

        # Thin separator line
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setMaximumWidth(1)
        div.setStyleSheet(f"background:{BORDER}; border:none;")
        root.addWidget(div)

        # Main chat area
        root.addWidget(self._build_chat_area(), stretch=1)

    # ── Sidebar ───────────────────────────────────────────────────────────

    def _build_sidebar(self) -> QWidget:
        w   = QWidget()
        w.setStyleSheet(f"background:{SIDEBAR};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Title block
        title_w = QWidget()
        title_w.setStyleSheet(f"background:{SIDEBAR}; border-bottom:1px solid {BORDER};")
        tl = QVBoxLayout(title_w)
        tl.setContentsMargins(16, 14, 16, 14)
        tl.setSpacing(3)

        lbl_title = QLabel("💬  P2P Chat")
        lbl_title.setStyleSheet(
            f"color:{TEXT}; font-size:15px; font-weight:700; background:transparent;"
        )
        lbl_sub = QLabel("Select a channel — direct message\na peer or broadcast to everyone")
        lbl_sub.setWordWrap(True)
        lbl_sub.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:10px; line-height:1.5; background:transparent;"
        )
        tl.addWidget(lbl_title)
        tl.addWidget(lbl_sub)
        lay.addWidget(title_w)

        # "CHANNELS" section header
        ch_label = QLabel("CHANNELS")
        ch_label.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:9px; font-weight:700; "
            f"letter-spacing:1.5px; padding:12px 16px 4px 16px; background:transparent;"
        )
        lay.addWidget(ch_label)

        # Channel + peer scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.verticalScrollBar().setStyleSheet(f"""
            QScrollBar:vertical {{ background:transparent; width:4px; }}
            QScrollBar::handle:vertical {{ background:{BORDER}; border-radius:2px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)

        chan_w = QWidget()
        chan_w.setStyleSheet("background:transparent;")
        self._chan_lay = QVBoxLayout(chan_w)
        self._chan_lay.setContentsMargins(8, 0, 8, 8)
        self._chan_lay.setSpacing(2)

        # "Everyone" broadcast button
        everyone = _ChanBtn("Everyone", "🌐", active=True)
        everyone.clicked.connect(lambda: self._switch_channel("everyone"))
        self._chan_btns["everyone"] = everyone
        self._chan_lay.addWidget(everyone)
        self._chan_lay.addStretch()

        scroll.setWidget(chan_w)
        lay.addWidget(scroll, stretch=1)
        return w

    # ── Chat area ─────────────────────────────────────────────────────────

    def _build_chat_area(self) -> QWidget:
        w   = QWidget()
        w.setStyleSheet(f"background:{BG};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Channel header
        self._chan_header = QLabel("🌐  Everyone — Broadcast")
        self._chan_header.setStyleSheet(
            f"color:{ACCENT}; font-size:14px; font-weight:600; "
            f"padding:13px 20px; border-bottom:1px solid {BORDER}; background:{BG};"
        )
        lay.addWidget(self._chan_header)

        # Messages
        self._msg_area = _MsgArea()
        lay.addWidget(self._msg_area, stretch=1)

        # Input bar
        lay.addWidget(self._build_input_bar())
        return w

    def _build_input_bar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet(
            f"background:{BG}; border-top:1px solid {BORDER}; padding:0;"
        )
        b_lay = QHBoxLayout(bar)
        b_lay.setContentsMargins(16, 10, 16, 14)
        b_lay.setSpacing(10)

        # Channel / target selector
        self._combo = QComboBox()
        self._combo.addItem("🌐  Everyone (Broadcast)", "everyone")
        self._combo.setFixedWidth(220)
        self._combo.setStyleSheet(f"""
            QComboBox {{
                background:{SURFACE2};
                border:1px solid {BORDER};
                border-radius:8px;
                color:{TEXT};
                font-size:12px;
                padding:8px 10px;
            }}
            QComboBox::drop-down {{ border:none; width:22px; }}
            QComboBox::down-arrow {{
                border-left:4px solid transparent;
                border-right:4px solid transparent;
                border-top:5px solid {TEXT_DIM};
                margin-right:8px;
            }}
            QComboBox QAbstractItemView {{
                background:{SURFACE2};
                border:1px solid {BORDER};
                color:{TEXT};
                selection-background-color:{SURFACE};
                outline:none;
            }}
        """)

        # Message text field
        self._input = _ChatInput()
        self._input.setPlaceholderText("Type a message...")
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background:{SURFACE2};
                border:1px solid {BORDER};
                border-radius:8px;
                color:{TEXT};
                font-size:13px;
                padding:8px 14px;
            }}
            QLineEdit:focus {{ border-color:{ACCENT}; }}
        """)
        self._input.enter_pressed.connect(self._send)

        # Send button
        btn = QPushButton("Send ▶")
        btn.setFixedWidth(100)
        btn.setStyleSheet(f"""
            QPushButton {{
                background:{ACCENT};
                color:white;
                border:none;
                border-radius:8px;
                font-size:13px;
                font-weight:700;
                padding:8px 16px;
            }}
            QPushButton:hover   {{ background:#3d7de0; }}
            QPushButton:pressed {{ background:#2d6abf; }}
        """)
        btn.clicked.connect(self._send)

        b_lay.addWidget(self._combo)
        b_lay.addWidget(self._input, stretch=1)
        b_lay.addWidget(btn)
        return bar

    # ── Peer list refresh ─────────────────────────────────────────────────

    def _refresh_peers(self) -> None:
        if self._node is None:
            return
        peers = self._node.routing_table.find_closest(
            self._node.anr.node_id, count=100
        )
        current_ids = {p.node_id for p in peers if p.node_id != self._my_id}

        # Remove departed peers
        stale = [pid for pid in self._chan_btns
                 if pid != "everyone" and pid not in current_ids]
        for pid in stale:
            btn = self._chan_btns.pop(pid)
            self._chan_lay.removeWidget(btn)
            btn.deleteLater()

        # Add new peers
        for anr in peers:
            pid = anr.node_id
            if pid == self._my_id or pid in self._chan_btns:
                continue
            nick = self._nicks.get(pid, f"{pid[:8]}…")
            btn  = _ChanBtn(nick, "●")
            # Green dot colour override
            btn.setStyleSheet(
                btn.BASE_STYLE +
                f"\nQPushButton {{ color:{ACCENT2}; }}"
                f"\nQPushButton:checked {{ color:{TEXT}; }}"
            )
            btn.clicked.connect(
                lambda _checked, p=pid, n=nick: self._switch_channel(p, label=n)
            )
            self._chan_btns[pid] = btn
            # Insert before the trailing stretch
            self._chan_lay.insertWidget(self._chan_lay.count() - 1, btn)

        # Sync combo box
        self._combo.blockSignals(True)
        saved = self._combo.currentData()
        self._combo.clear()
        self._combo.addItem("🌐  Everyone (Broadcast)", "everyone")
        for anr in peers:
            pid = anr.node_id
            if pid == self._my_id:
                continue
            nick = self._nicks.get(pid, f"{pid[:8]}…")
            self._combo.addItem(f"●  {nick}", pid)
        # Restore selection
        for i in range(self._combo.count()):
            if self._combo.itemData(i) == saved:
                self._combo.setCurrentIndex(i)
                break
        self._combo.blockSignals(False)

    def _switch_channel(self, channel_id: str, label: str = "") -> None:
        for pid, btn in self._chan_btns.items():
            btn.setChecked(pid == channel_id)
        self._channel = channel_id
        if channel_id == "everyone":
            self._chan_header.setText("🌐  Everyone — Broadcast")
        else:
            disp = label or self._nicks.get(channel_id, f"{channel_id[:8]}…")
            self._chan_header.setText(f"●  {disp}  — Direct Message")
        self._msg_area.clear_messages()
        self._sys(f"Switched channel")

    # ── Send ──────────────────────────────────────────────────────────────

    @pyqtSlot()
    def _send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        if self._node is None:
            self._sys("⚠  Not connected yet.")
            return

        idx    = self._combo.currentIndex()
        target = self._combo.itemData(idx) or "everyone"
        msg    = ChatMessage.make(self._my_id, self._nick, text, target=target)
        self._input.clear()

        self._render(msg, mine=True)
        self._seen.add(msg.msg_id)

        asyncio.run_coroutine_threadsafe(
            _broadcast(self._node, msg), self._loop
        )

    # ── Receive ───────────────────────────────────────────────────────────

    @pyqtSlot(object)
    def _on_incoming(self, msg: ChatMessage) -> None:
        if msg.msg_id in self._seen:
            return
        self._seen.add(msg.msg_id)
        self._nicks[msg.node_id] = msg.nick

        # Only display if directed to everyone or to us
        if msg.target == "everyone" or msg.target == self._my_id:
            self._render(msg, mine=False)

    # ── Render helpers ────────────────────────────────────────────────────

    def _render(self, msg: ChatMessage, mine: bool) -> None:
        self._msg_area.add(_MsgRow(msg, mine))

    def _sys(self, text: str) -> None:
        self._msg_area.add(_SysRow(text))