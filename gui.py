"""
gui.py — Project Antigravity
PyQt6 desktop GUI: Node Status dashboard, K-bucket routing table viewer,
bootstrap controls, and a real-time scrolling event log.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import (
    QObject, QThread, QTimer, Qt, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette, QTextCharFormat
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)
from graph_widget import NetworkGraphWidget

if TYPE_CHECKING:
    from network import AntigravityNode

# ── Palette ────────────────────────────────────────────────────────────────
BG          = "#0d0f1a"
SURFACE     = "#151829"
SURFACE2    = "#1c2035"
BORDER      = "#252a42"
ACCENT      = "#6c63ff"
ACCENT2     = "#00d4aa"
TEXT        = "#e8eaf6"
TEXT_DIM    = "#7c84a8"
SENT_COL    = "#6c63ff"
RECV_COL    = "#00d4aa"
WARN_COL    = "#ffb74d"
ERR_COL     = "#ef5350"
PING_COL    = "#ce93d8"
PONG_COL    = "#80cbc4"

STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 13px;
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 18px;
    padding: 12px 10px 10px 10px;
    background: {SURFACE};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    top: 2px;
    color: {ACCENT};
    font-weight: 700;
    font-size: 12px;
    letter-spacing: 1.2px;
    text-transform: uppercase;
}}
QLabel {{
    color: {TEXT};
}}
QLabel#dimLabel {{
    color: {TEXT_DIM};
    font-size: 11px;
}}
QLineEdit {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    color: {TEXT};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QPushButton#primary {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {ACCENT}, stop:1 #8b5cf6);
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 9px 22px;
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 0.5px;
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #7c73ff, stop:1 #9d70ff);
}}
QPushButton#primary:pressed {{
    background: {ACCENT};
}}
QPushButton#secondary {{
    background: {SURFACE2};
    color: {ACCENT2};
    border: 1px solid {ACCENT2};
    border-radius: 8px;
    padding: 7px 16px;
    font-weight: 600;
}}
QPushButton#secondary:hover {{
    background: rgba(0,212,170,0.12);
}}
QTableWidget {{
    background: {SURFACE};
    border: none;
    gridline-color: {BORDER};
    color: {TEXT};
    selection-background-color: rgba(108,99,255,0.25);
}}
QTableWidget::item {{
    padding: 4px 8px;
}}
QHeaderView::section {{
    background: {SURFACE2};
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.8px;
    text-transform: uppercase;
}}
QTextEdit {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: {TEXT};
    font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
    font-size: 12px;
    padding: 4px 8px;
}}
QScrollBar:vertical {{
    background: {SURFACE};
    width: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QSplitter::handle {{
    background: {BORDER};
    width: 1px;
}}
"""


# ── Stat card ──────────────────────────────────────────────────────────────

from PyQt6.QtGui import QPainter, QBrush, QPen
from PyQt6.QtCore import QRect

class StatCard(QFrame):
    """
    Stat card drawn with QPainter so it works on macOS PyQt6 regardless
    of stylesheet cascade quirks.  Labels use QPalette for colour — never
    setStyleSheet — which is the only thing that survives nested QGroupBox.
    """
    def __init__(self, title: str, value: str = "—", accent: str = ACCENT):
        super().__init__()
        self._accent   = QColor(accent)
        self._bg       = QColor(SURFACE2)
        self._border   = QColor(BORDER)
        self.setMinimumHeight(58)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        lay.setSpacing(3)

        # Title label — use QPalette, no stylesheet
        self._title_lbl = QLabel(title.upper())
        self._title_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._title_lbl.setAutoFillBackground(False)
        p1 = self._title_lbl.palette()
        p1.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_DIM))
        p1.setColor(QPalette.ColorRole.Window,     QColor(0, 0, 0, 0))
        self._title_lbl.setPalette(p1)

        # Value label
        self._value_lbl = QLabel(value)
        font = QFont("Segoe UI", 13, QFont.Weight.Bold)
        self._value_lbl.setFont(font)
        self._value_lbl.setWordWrap(True)
        self._value_lbl.setAutoFillBackground(False)
        p2 = self._value_lbl.palette()
        p2.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
        p2.setColor(QPalette.ColorRole.Window,     QColor(0, 0, 0, 0))
        self._value_lbl.setPalette(p2)

        lay.addWidget(self._title_lbl)
        lay.addWidget(self._value_lbl)

    # Draw the card background + borders ourselves
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)

        # Background fill
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._bg))
        painter.drawRoundedRect(r, 8, 8)

        # Outer border
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(r, 8, 8)

        # Accent left bar
        accent_bar = QRect(r.left(), r.top() + 6, 3, r.height() - 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self._accent))
        painter.drawRoundedRect(accent_bar, 2, 2)
        painter.end()

    def set_value(self, v: str) -> None:
        self._value_lbl.setText(v)


# ── Bridge: network events → Qt signals ────────────────────────────────────

class NetworkBridge(QObject):
    log_signal    = pyqtSignal(str, str)    # (event, message)
    graph_signal  = pyqtSignal(str, dict)   # (event, full_payload)
    refresh_signal = pyqtSignal()

    def handle_event(self, event: str, payload: dict) -> None:
        msg = payload.get("message", "")
        self.log_signal.emit(event, msg)
        self.graph_signal.emit(event, payload)
        self.refresh_signal.emit()


# ── Main Window ────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._loop = loop
        self._node: Optional[AntigravityNode] = None
        self._bridge = NetworkBridge()
        self._bridge.log_signal.connect(self._append_log)
        self._bridge.refresh_signal.connect(self._refresh_routing_table)

        self.setWindowTitle("Project Antigravity — P2P Node Discovery")
        self.setMinimumSize(1280, 820)
        self._build_ui()
        self.setStyleSheet(STYLESHEET)
        self._bridge.graph_signal.connect(self._handle_graph_event)

        # Uptime ticker
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._tick_uptime)
        self._uptime_timer.start(1000)

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Header
        root.addWidget(self._build_header())

        # Main splitter: left panel | right panel
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(2)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)
        root.addWidget(splitter)

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"""
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                stop:0 #1a1040, stop:0.5 {SURFACE}, stop:1 #0d1a2e);
            border: 1px solid {BORDER};
            border-radius: 12px;
            padding: 14px 20px;
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 14, 20, 14)

        # Logo + title
        logo_lbl = QLabel("⬡")
        logo_lbl.setStyleSheet(f"font-size:32px; color:{ACCENT}; margin-right:10px;")
        title_lbl = QLabel("Project <b>Antigravity</b>")
        title_lbl.setStyleSheet("font-size:22px; font-weight:800; color:#fff; letter-spacing:-0.5px;")
        sub_lbl = QLabel("Kademlia / discv5-inspired P2P Discovery Protocol")
        sub_lbl.setStyleSheet(f"font-size:12px; color:{TEXT_DIM};")

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_col.addWidget(title_lbl)
        title_col.addWidget(sub_lbl)

        self._status_dot = QLabel("⬤  INITIALISING")
        self._status_dot.setStyleSheet(f"color:{WARN_COL}; font-size:12px; font-weight:700;")

        lay.addWidget(logo_lbl)
        lay.addLayout(title_col)
        lay.addStretch()
        lay.addWidget(self._status_dot)
        return w

    # ── Left panel: status + bootstrap ────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(12)
        lay.addWidget(self._build_status_group())
        lay.addWidget(self._build_bootstrap_group())
        lay.addStretch()
        return w

    def _build_status_group(self) -> QGroupBox:
        grp = QGroupBox("Local Node Status")
        lay = QVBoxLayout(grp)
        lay.setSpacing(8)

        self._card_id      = StatCard("Node ID", "—", ACCENT)
        self._card_addr    = StatCard("Listen Address", "—", ACCENT2)
        self._card_uptime  = StatCard("Uptime", "0s", "#ffb74d")
        self._card_peers   = StatCard("Known Peers", "0", "#ef5350")
        self._card_buckets = StatCard("Active Buckets", "0 / 256", "#ce93d8")

        for c in [self._card_id, self._card_addr, self._card_uptime,
                  self._card_peers, self._card_buckets]:
            lay.addWidget(c)
        return grp

    def _build_bootstrap_group(self) -> QGroupBox:
        grp = QGroupBox("Bootstrap Controls")
        lay = QVBoxLayout(grp)
        lay.setSpacing(10)

        # IP field
        ip_lbl = QLabel("Peer IP Address")
        ip_lbl.setObjectName("dimLabel")
        self._ip_input = QLineEdit()
        self._ip_input.setText("127.0.0.1")   # pre-filled for local testing
        self._ip_input.setPlaceholderText("e.g. 127.0.0.1")

        # Port field
        port_lbl = QLabel("Peer UDP Port")
        port_lbl.setObjectName("dimLabel")
        self._port_input = QLineEdit()
        self._port_input.setPlaceholderText("e.g. 9000")

        # Buttons
        self._bootstrap_btn = QPushButton("⬢  Bootstrap Network")
        self._bootstrap_btn.setObjectName("primary")
        self._bootstrap_btn.clicked.connect(self._on_bootstrap)

        self._ping_btn = QPushButton("♥  Ping Peer")
        self._ping_btn.setObjectName("secondary")
        self._ping_btn.clicked.connect(self._on_ping)

        self._find_btn = QPushButton("⊕  Find Nodes (Self)")
        self._find_btn.setObjectName("secondary")
        self._find_btn.clicked.connect(self._on_find_self)

        lay.addWidget(ip_lbl)
        lay.addWidget(self._ip_input)
        lay.addWidget(port_lbl)
        lay.addWidget(self._port_input)
        lay.addSpacing(4)
        lay.addWidget(self._bootstrap_btn)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._ping_btn)
        btn_row.addWidget(self._find_btn)
        lay.addLayout(btn_row)
        return grp

    # ── Right panel: routing table + log ──────────────────────────────

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Tab widget: Routing Table | Network Graph ──────────────────
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabBar::tab {{
                background: {SURFACE}; color: {TEXT_DIM};
                border: 1px solid {BORDER}; border-bottom: none;
                padding: 6px 16px; border-radius: 6px 6px 0 0;
            }}
            QTabBar::tab:selected {{
                background: {SURFACE}; color: {ACCENT};
                border-bottom: 2px solid {ACCENT};
            }}
            QTabWidget::pane {{
                border: 1px solid {BORDER}; border-radius: 0 6px 6px 6px;
            }}
        """)

        # Tab 1 — Routing Table
        rt_widget = QWidget()
        rt_lay = QVBoxLayout(rt_widget)
        rt_lay.setContentsMargins(4, 4, 4, 4)
        self._rt_table = QTableWidget(0, 4)
        self._rt_table.setHorizontalHeaderLabels(
            ["Bucket #", "Node ID (short)", "IP : Port", "Seq"]
        )
        self._rt_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._rt_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._rt_table.setColumnWidth(0, 90)
        self._rt_table.verticalHeader().setVisible(False)
        self._rt_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._rt_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._rt_table.setAlternatingRowColors(True)
        self._rt_table.setStyleSheet(f"alternate-background-color: {SURFACE2};")
        rt_lay.addWidget(self._rt_table)
        tabs.addTab(rt_widget, "⊞  Routing Table")

        # Tab 2 — Network Graph
        self._graph = NetworkGraphWidget()
        tabs.addTab(self._graph, "⬡  Network Graph")
        tabs.setCurrentIndex(1)   # open on graph tab by default

        splitter.addWidget(tabs)
        splitter.addWidget(self._build_log_group())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 3)
        lay.addWidget(splitter)
        return w

    def _build_log_group(self) -> QGroupBox:
        grp = QGroupBox("Event Log")
        lay = QVBoxLayout(grp)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        btn_clear = QPushButton("Clear")
        btn_clear.setObjectName("secondary")
        btn_clear.setFixedWidth(80)
        btn_clear.clicked.connect(self._log_view.clear)

        top = QHBoxLayout()
        top.addWidget(QLabel("Protocol Activity"))
        top.addStretch()
        top.addWidget(btn_clear)
        lay.addLayout(top)
        lay.addWidget(self._log_view)
        return grp

    # ── Node attachment ────────────────────────────────────────────────

    def attach_node(self, node: "AntigravityNode") -> None:
        from network import event_bus
        self._node = node
        event_bus.subscribe(self._bridge.handle_event)

        self._card_id.set_value(node.anr.node_id[:32] + "…")
        self._card_addr.set_value(f"{node.host}:{node.port}")
        self._status_dot.setText("⬤  RUNNING")
        self._status_dot.setStyleSheet(f"color:{ACCENT2}; font-size:12px; font-weight:700;")
        self._append_log("node_ready", f"[NODE] Local ID: {node.anr.node_id}")

        # Register local node in the graph
        short_id = node.anr.node_id[:8]
        self._graph.set_local_node(short_id, f"{node.host}:{node.port}")
        self._local_graph_id = short_id

        # ── Fix race condition: bootstrap may have completed BEFORE the GUI
        # subscribed to events.  Immediately populate the graph from the
        # current routing table, then keep syncing every 2 seconds.
        self._sync_graph_from_routing_table()
        self._graph_sync_timer = QTimer(self)
        self._graph_sync_timer.timeout.connect(self._sync_graph_from_routing_table)
        self._graph_sync_timer.start(2000)

    # ── Slot handlers ──────────────────────────────────────────────────

    def _sync_graph_from_routing_table(self) -> None:
        """Populate the graph from the live routing table.
        Runs immediately on attach and every 2 s — fixes the race condition
        where bootstrap fires before the GUI event subscription is active."""
        if self._node is None:
            return
        local_id = getattr(self, "_local_graph_id", None)
        if not local_id:
            return
        # find_closest with our own ID returns ALL known peers sorted by distance
        all_peers = self._node.routing_table.find_closest(
            self._node.anr.node_id, count=200
        )
        for anr in all_peers:
            peer_short = anr.node_id[:8]
            addr       = f"{anr.ip}:{anr.udp_port}"
            self._graph.add_peer(peer_short, addr)
            self._graph.add_edge(local_id, peer_short)

    @pyqtSlot()
    def _tick_uptime(self) -> None:
        if self._node is None:
            return
        s = int(self._node.uptime_seconds)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        self._card_uptime.set_value(f"{h:02d}:{m:02d}:{sec:02d}")
        self._card_peers.set_value(str(self._node.routing_table.total_nodes))
        nb = len(self._node.routing_table.non_empty_buckets)
        self._card_buckets.set_value(f"{nb} / 256")

    @pyqtSlot(str, dict)
    def _handle_graph_event(self, event: str, payload: dict) -> None:
        """Drive the network graph widget from structured event payloads."""
        local_id = getattr(self, "_local_graph_id", None)
        if local_id is None:
            return

        remote_ip   = payload.get("remote_ip", "")
        remote_port = payload.get("remote_port", 0)
        remote_id   = payload.get("remote_id", "")[:8] if payload.get("remote_id") else ""
        msg_type    = payload.get("msg_type", "")
        addr_label  = f"{remote_ip}:{remote_port}" if remote_ip else ""

        # ── Peer discovered ──────────────────────────────────────────
        if event == "peer_connected":
            nid   = payload.get("node_id", "")[:8]
            nip   = payload.get("node_ip", "")
            nport = payload.get("node_port", 0)
            if nid:
                self._graph.add_peer(nid, f"{nip}:{nport}")
                self._graph.add_edge(local_id, nid)
            return

        # ── Packet animations (local → remote) ───────────────────────
        if not remote_ip:
            return
        peer_short = remote_id if remote_id else addr_label[:8]
        if not peer_short:
            return

        # Ensure peer node exists (best-effort from address)
        if peer_short not in self._graph._nodes:
            self._graph.add_peer(peer_short, addr_label)

        if event in ("sent_ping", "sent_find_node", "sent_neighbors"):
            self._graph.send_packet(local_id, peer_short, msg_type)

        elif event in ("recv_ping", "recv_pong_structured",
                       "recv_find_node", "recv_neighbors"):
            self._graph.send_packet(peer_short, local_id, msg_type)

        elif event == "sent_pong":
            self._graph.send_packet(local_id, peer_short, "PONG")

    @pyqtSlot(str, str)
    def _append_log(self, event: str, msg: str) -> None:
        color_map = {
            "sent_ping": SENT_COL, "recv_ping": RECV_COL,
            "sent_pong": PONG_COL, "recv_pong": PING_COL,
            "sent_find_node": SENT_COL, "recv_find_node": RECV_COL,
            "sent_neighbors": SENT_COL, "recv_neighbors": RECV_COL,
            "ping_timeout": WARN_COL, "find_node_timeout": WARN_COL,
            "bootstrap_start": ACCENT, "bootstrap_done": ACCENT2,
            "bootstrap_peer_ok": ACCENT2, "bootstrap_peer_fail": ERR_COL,
            "node_started": ACCENT2, "node_stopped": WARN_COL,
            "node_ready": ACCENT2,
        }
        color = color_map.get(event, TEXT)
        ts    = time.strftime("%H:%M:%S")
        self._log_view.append(
            f'<span style="color:{TEXT_DIM};">[{ts}]</span> '
            f'<span style="color:{color};">{msg}</span>'
        )
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    @pyqtSlot()
    def _refresh_routing_table(self) -> None:
        if self._node is None:
            return
        summary = self._node.routing_table.bucket_summary()
        rows: list[tuple] = []
        for bucket in summary:
            for node in bucket["nodes"]:
                rows.append((
                    str(bucket["index"]),
                    node["short_id"],
                    f"{node['ip']}:{node['port']}",
                    str(node["seq"]),
                ))

        self._rt_table.setRowCount(len(rows))
        for r, (bidx, short_id, addr, seq) in enumerate(rows):
            items = [bidx, short_id, addr, seq]
            for c, val in enumerate(items):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if c == 1:
                    item.setForeground(QColor(ACCENT2))
                self._rt_table.setItem(r, c, item)

    # ── Control button actions ─────────────────────────────────────────

    def _get_peer_addr(self) -> tuple[str, int] | None:
        ip   = self._ip_input.text().strip()
        port = self._port_input.text().strip()
        if not ip or not port:
            self._append_log("ui_error", "[UI] Enter both IP and Port first.")
            return None
        try:
            return ip, int(port)
        except ValueError:
            self._append_log("ui_error", "[UI] Invalid port number.")
            return None

    def _on_bootstrap(self) -> None:
        addr = self._get_peer_addr()
        if self._node and addr:
            asyncio.run_coroutine_threadsafe(
                self._node.bootstrap([addr]), self._loop
            )

    def _on_ping(self) -> None:
        addr = self._get_peer_addr()
        if self._node and addr:
            asyncio.run_coroutine_threadsafe(
                self._node.ping(*addr), self._loop
            )

    def _on_find_self(self) -> None:
        addr = self._get_peer_addr()
        if self._node and addr:
            asyncio.run_coroutine_threadsafe(
                self._node.find_node(*addr, target_id=self._node.node_id_hex),
                self._loop,
            )
