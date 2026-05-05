"""
graph_widget.py — Project Antigravity (Enhanced)
Premium Kademlia network visualization:
  • All nodes on a circular ring (local node highlighted)
  • Edges colored by XOR distance (close=teal, far=purple)
  • Animated packet dots with motion trails
  • K-bucket side panel showing bucket index + peer chips
  • 30fps QPainter rendering, zero external deps
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore    import Qt, QTimer, QRect, QRectF, QPointF
from PyQt6.QtGui     import (
    QColor, QPainter, QPen, QBrush, QFont,
    QLinearGradient, QRadialGradient, QPainterPath, QFontMetrics,
)
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QSplitter

# ── Palette ────────────────────────────────────────────────────────────────
BG        = "#0a0c18"
SURFACE   = "#111427"
SURFACE2  = "#181d32"
BORDER    = "#1e2540"
ACCENT    = "#6c63ff"
ACCENT2   = "#00d4aa"
TEXT      = "#e8eaf6"
TEXT_DIM  = "#4a5180"

# XOR distance → edge colour (bucket-index buckets, closer=brighter)
BUCKET_COLORS = [
    "#ef5350",  # 0–63    far        red
    "#ff9800",  # 64–127  medium     orange
    "#ffeb3b",  # 128–191 close      yellow
    "#00e5ff",  # 192–223 very close cyan
    "#00d4aa",  # 224–255 nearest    teal
]

PACKET_COLORS = {
    "PING":      "#ce93d8",
    "PONG":      "#80deea",
    "FIND_NODE": "#ffb74d",
    "NEIGHBORS": "#69f0ae",
}


def _bucket_color(bucket_index: int) -> QColor:
    """Map a 0-255 bucket index to a colour (higher = closer = teal)."""
    if   bucket_index >= 224: c = BUCKET_COLORS[4]
    elif bucket_index >= 192: c = BUCKET_COLORS[3]
    elif bucket_index >= 128: c = BUCKET_COLORS[2]
    elif bucket_index >= 64:  c = BUCKET_COLORS[1]
    else:                     c = BUCKET_COLORS[0]
    return QColor(c)


# ── Internal data ──────────────────────────────────────────────────────────

class _Node:
    def __init__(self, short_id: str, addr: str, is_local: bool):
        self.short_id  = short_id
        self.addr      = addr
        self.is_local  = is_local
        self.angle     = 0.0          # radians on the ring
        self.bucket    = 0            # bucket index vs local
        self.glow      = 2.0          # decays over time
        # Computed screen pos (updated in paintEvent)
        self.sx = 0.0
        self.sy = 0.0


class _Packet:
    def __init__(self, src: str, dst: str, msg_type: str):
        self.src      = src
        self.dst      = dst
        self.color    = QColor(PACKET_COLORS.get(msg_type, "#ffffff"))
        self.label    = msg_type
        self.progress = 0.0
        self.speed    = 0.020
        self.trail: List[Tuple[int,int]] = []


# ── Ring canvas (left pane) ────────────────────────────────────────────────

class _RingCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(380)
        self._nodes:   Dict[str, _Node]  = {}
        self._edges:   List[Tuple]       = []   # (id_a, id_b)
        self._packets: List[_Packet]     = []
        self._local_id: Optional[str]    = None
        self._hovered: Optional[str]     = None
        self.setMouseTracking(True)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    # ── Layout helpers ─────────────────────────────────────────────────

    def _ring_center(self) -> Tuple[float, float]:
        return self.width() / 2, self.height() / 2

    def _ring_radius(self) -> float:
        return min(self.width(), self.height()) * 0.34

    def _recompute_angles(self) -> None:
        """Arrange non-local nodes evenly on the ring; local node stays at 9 o'clock."""
        peers = [n for n in self._nodes.values() if not n.is_local]
        n = len(peers)
        for i, peer in enumerate(peers):
            peer.angle = -math.pi / 2 + (2 * math.pi * i / max(n, 1))
        if self._local_id and self._local_id in self._nodes:
            self._nodes[self._local_id].angle = 0.0   # 3 o'clock (overridden below)

    def _screen_pos(self, node: _Node) -> Tuple[float, float]:
        cx, cy = self._ring_center()
        r      = self._ring_radius()
        if node.is_local:
            return cx, cy          # local node stays in center
        return (cx + r * math.cos(node.angle),
                cy + r * math.sin(node.angle))

    def _node_at(self, x: int, y: int) -> Optional[str]:
        for nid, n in self._nodes.items():
            sx, sy = self._screen_pos(n)
            if (x - sx)**2 + (y - sy)**2 < 30**2:
                return nid
        return None

    # ── Public API ─────────────────────────────────────────────────────

    def set_local_node(self, short_id: str, addr: str) -> None:
        self._local_id = short_id
        self._nodes[short_id] = _Node(short_id, addr, True)
        self._recompute_angles()
        self.update()

    def add_peer(self, short_id: str, addr: str, bucket: int = 0) -> None:
        if short_id in self._nodes:
            self._nodes[short_id].bucket = bucket
            return
        n = _Node(short_id, addr, False)
        n.bucket = bucket
        self._nodes[short_id] = n
        self._recompute_angles()
        self.update()

    def add_edge(self, id_a: str, id_b: str) -> None:
        pair = tuple(sorted([id_a, id_b]))
        if pair not in self._edges:
            self._edges.append(pair)
            self.update()

    def send_packet(self, from_id: str, to_id: str, msg_type: str) -> None:
        if from_id in self._nodes and to_id in self._nodes:
            self._packets.append(_Packet(from_id, to_id, msg_type))

    # ── Animation ──────────────────────────────────────────────────────

    #this code by written by team nexus

    def _tick(self) -> None:
        changed = bool(self._packets)
        for p in self._packets[:]:
            p.progress = min(1.0, p.progress + p.speed)
            if p.progress >= 1.0:
                self._packets.remove(p)
        for n in self._nodes.values():
            if n.glow > 0:
                n.glow = max(0.0, n.glow - 0.02)
                changed = True
        if changed:
            self.update()

    # ── Mouse ──────────────────────────────────────────────────────────

    def mouseMoveEvent(self, e):
        self._hovered = self._node_at(e.pos().x(), e.pos().y())
        self.update()

    # ── Drawing ────────────────────────────────────────────────────────

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background gradient
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0, QColor("#0a0c18"))
        bg.setColorAt(1, QColor("#060810"))
        p.fillRect(self.rect(), QBrush(bg))

        # Grid dots
        p.setPen(QPen(QColor(BORDER), 1))
        for gx in range(0, w, 32):
            for gy in range(0, h, 32):
                p.drawPoint(gx, gy)

        if not self._nodes:
            p.setPen(QPen(QColor(TEXT_DIM)))
            p.setFont(QFont("Segoe UI", 12))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Waiting for peers…\nBootstrap a node to visualise the network.")
            p.end(); return

        cx, cy = self._ring_center()
        r = self._ring_radius()

        # Ring circle (background guide)
        ring_pen = QPen(QColor(BORDER), 1, Qt.PenStyle.DashLine)
        p.setPen(ring_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # ── Compute screen positions
        positions: Dict[str, Tuple[float, float]] = {}
        for nid, node in self._nodes.items():
            positions[nid] = self._screen_pos(node)

        # ── Edges (colored by bucket index)
        for id_a, id_b in self._edges:
            if id_a not in positions or id_b not in positions:
                continue
            x1, y1 = positions[id_a]
            x2, y2 = positions[id_b]

            # Determine bucket from non-local node
            peer_id = id_b if id_a == self._local_id else id_a
            bucket  = self._nodes[peer_id].bucket if peer_id in self._nodes else 0
            edge_col = _bucket_color(bucket)

            # Glowing line (multiple widths)
            for lw, alpha in [(8, 12), (4, 25), (1.5, 160)]:
                c = QColor(edge_col); c.setAlpha(alpha)
                p.setPen(QPen(c, lw))
                p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # ── Packets with trail
        for pkt in self._packets:
            if pkt.src not in positions or pkt.dst not in positions:
                continue
            x1, y1 = positions[pkt.src]
            x2, y2 = positions[pkt.dst]
            t = pkt.progress
            px = x1 + (x2 - x1) * t
            py = y1 + (y2 - y1) * t

            # Trail (ghost dots behind the packet)
            for i, trail_t in enumerate([t - 0.06, t - 0.03]):
                if trail_t < 0: continue
                tx_ = x1 + (x2 - x1) * trail_t
                ty_ = y1 + (y2 - y1) * trail_t
                tc  = QColor(pkt.color); tc.setAlpha(60 - i * 25)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(tc))
                r_ = 5 - i * 1.5
                p.drawEllipse(QRectF(tx_ - r_, ty_ - r_, r_ * 2, r_ * 2))

            # Glow halos
            for rad, alpha in [(18, 20), (11, 50), (6, 120)]:
                hc = QColor(pkt.color); hc.setAlpha(alpha)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(hc))
                p.drawEllipse(QRectF(px - rad, py - rad, rad * 2, rad * 2))

            # Label
            p.setPen(QPen(pkt.color))
            p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            p.drawText(int(px) + 10, int(py) - 4, pkt.label)

        # ── Nodes
        for nid, node in self._nodes.items():
            sx, sy = positions[nid]
            is_local   = node.is_local
            is_hovered = (nid == self._hovered)
            base_col   = QColor(ACCENT) if is_local else _bucket_color(node.bucket)
            radius     = 36 if is_local else (28 if is_hovered else 24)

            # Glow rings
            rings = 6 if is_local else 4
            for ring in range(rings, 0, -1):
                gc = QColor(base_col)
                gc.setAlpha(int((15 + node.glow * 30) * ring / rings))
                off = ring * 7
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(gc))
                p.drawEllipse(QRectF(sx - radius - off, sy - radius - off,
                                     (radius + off) * 2, (radius + off) * 2))

            # Circle body with radial gradient
            grad = QRadialGradient(sx - radius * 0.3, sy - radius * 0.3, radius * 1.3)
            lighter = QColor(base_col).lighter(140)
            grad.setColorAt(0.0, lighter)
            grad.setColorAt(1.0, base_col)
            p.setPen(QPen(QColor(255, 255, 255, 60 if is_local else 40), 2))
            p.setBrush(QBrush(grad))
            p.drawEllipse(QRectF(sx - radius, sy - radius, radius * 2, radius * 2))

            # Inner gloss
            gloss = QColor(255, 255, 255, 35)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(gloss))
            p.drawEllipse(QRectF(sx - radius * 0.55, sy - radius * 0.65,
                                  radius * 0.9, radius * 0.55))

            # Short ID label
            p.setPen(QPen(QColor("#ffffff")))
            p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            fm  = p.fontMetrics()
            lbl = node.short_id[:8]
            p.drawText(int(sx) - fm.horizontalAdvance(lbl) // 2, int(sy) + 4, lbl)

            # Tag below
            tag = "LOCAL" if is_local else node.addr
            tag_col = QColor(ACCENT) if is_local else base_col
            p.setPen(QPen(tag_col))
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            fm2 = p.fontMetrics()
            p.drawText(int(sx) - fm2.horizontalAdvance(tag) // 2,
                       int(sy) + radius + 16, tag)

            # Bucket badge (for peers)
            if not is_local and node.bucket > 0:
                bx, by = int(sx) + radius - 8, int(sy) - radius + 8
                p.setBrush(QBrush(base_col))
                p.setPen(QPen(QColor(BG), 1))
                p.drawEllipse(bx - 8, by - 8, 16, 16)
                p.setPen(QPen(QColor("#fff")))
                p.setFont(QFont("Segoe UI", 6, QFont.Weight.Bold))
                bi_str = str(node.bucket)
                p.drawText(bx - 5, by + 4, bi_str)

        # ── Packet legend (bottom left)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        lx, ly = 14, h - 14 - len(PACKET_COLORS) * 18
        for mtype, color in PACKET_COLORS.items():
            ly_curr = ly + list(PACKET_COLORS).index(mtype) * 18
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(color)))
            p.drawEllipse(lx, ly_curr - 5, 9, 9)
            p.setPen(QPen(QColor(TEXT_DIM)))
            p.drawText(lx + 14, ly_curr + 3, mtype)

        # ── Stats bar (top right)
        stats = (f"Nodes: {len(self._nodes)}   "
                 f"Edges: {len(self._edges)}   "
                 f"Live packets: {len(self._packets)}")
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QPen(QColor(TEXT_DIM)))
        sw = p.fontMetrics().horizontalAdvance(stats)
        p.drawText(w - sw - 12, 18, stats)

        p.end()


# ── K-Bucket side panel (right pane) ──────────────────────────────────────

class _BucketPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(200)
        self.setMaximumWidth(280)
        self._buckets: Dict[int, List[str]] = {}   # bucket_idx -> [short_ids]
        self._peer_addrs: Dict[str, str]    = {}

    def update_peer(self, short_id: str, addr: str, bucket: int) -> None:
        self._peer_addrs[short_id] = addr
        if bucket not in self._buckets:
            self._buckets[bucket] = []
        if short_id not in self._buckets[bucket]:
            self._buckets[bucket].append(short_id)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        p.fillRect(self.rect(), QColor(SURFACE))

        # Title
        p.setPen(QPen(QColor(ACCENT)))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        p.drawText(14, 28, "K-BUCKET ASSIGNMENTS")

        # Divider
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawLine(10, 36, w - 10, 36)

        if not self._buckets:
            p.setPen(QPen(QColor(TEXT_DIM)))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(QRect(10, 50, w - 20, 60),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                       "No peers yet")
            p.end(); return

        y = 50
        for bucket_idx in sorted(self._buckets.keys(), reverse=True):
            peers = self._buckets[bucket_idx]
            col   = _bucket_color(bucket_idx)

            # Bucket header
            p.setPen(QPen(col))
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            p.drawText(14, y + 12, f"Bucket {bucket_idx}")

            # Bucket bar
            bar_w = int((bucket_idx / 255) * (w - 90))
            bar_rect = QRect(w - bar_w - 14, y + 2, bar_w, 10)
            bc = QColor(col); bc.setAlpha(60)
            p.setBrush(QBrush(bc))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(bar_rect, 3, 3)
            p.setPen(QPen(col, 1))
            p.drawRoundedRect(bar_rect, 3, 3)

            y += 20

            # Peer chips
            for sid in peers:
                addr = self._peer_addrs.get(sid, "")
                chip_rect = QRect(14, y, w - 28, 26)

                # Chip background
                cc = QColor(col); cc.setAlpha(25)
                p.setBrush(QBrush(cc))
                p.setPen(QPen(col, 1))
                p.drawRoundedRect(chip_rect, 6, 6)

                # Node ID
                p.setPen(QPen(col))
                p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                p.drawText(22, y + 11, sid[:8])

                # Address
                p.setPen(QPen(QColor(TEXT_DIM)))
                p.setFont(QFont("Segoe UI", 7))
                p.drawText(22, y + 22, addr)

                y += 32

            # Divider
            p.setPen(QPen(QColor(BORDER), 1))
            p.drawLine(10, y + 4, w - 10, y + 4)
            y += 14

            if y > h - 20:
                break

        p.end()


# ── Public composite widget ────────────────────────────────────────────────

class NetworkGraphWidget(QWidget):
    """
    Composite widget:  _RingCanvas (left)  |  _BucketPanel (right)
    Drop-in replacement for the original NetworkGraphWidget.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._canvas = _RingCanvas()
        self._panel  = _BucketPanel()
        splitter.addWidget(self._canvas)
        splitter.addWidget(self._panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setStyleSheet("QSplitter::handle { background: #1e2540; width: 1px; }")
        layout.addWidget(splitter)

        self._local_id: Optional[str] = None

    # ── Bucket index helper ────────────────────────────────────────────
    def _compute_bucket(self, peer_id: str) -> int:
        """Approx bucket index: count matching leading bits of short IDs."""
        if not self._local_id or len(peer_id) < 8 or len(self._local_id) < 8:
            return 128
        try:
            a = int(self._local_id[:8], 16)
            b = int(peer_id[:8], 16)
            xor = a ^ b
            if xor == 0: return 255
            return max(0, 31 - xor.bit_length())  # 0-31 for 8-hex-char IDs
        except Exception:
            return 128

    # ── Public API (mirrors original) ──────────────────────────────────

    def set_local_node(self, short_id: str, addr: str) -> None:
        self._local_id = short_id
        self._canvas.set_local_node(short_id, addr)

    def add_peer(self, short_id: str, addr: str) -> None:
        bucket = self._compute_bucket(short_id)
        self._canvas.add_peer(short_id, addr, bucket)
        self._panel.update_peer(short_id, addr, bucket)

    def add_edge(self, id_a: str, id_b: str) -> None:
        self._canvas.add_edge(id_a, id_b)

    def send_packet(self, from_id: str, to_id: str, msg_type: str) -> None:
        self._canvas.send_packet(from_id, to_id, msg_type)

    # Keep _nodes accessible for gui.py peer existence check
    @property
    def _nodes(self):
        return self._canvas._nodes
