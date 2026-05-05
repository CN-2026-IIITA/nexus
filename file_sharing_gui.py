"""
file_sharing_gui.py — Project Antigravity
Standalone PyQt6 panel for file upload and download over the DHT.
Added as a third tab in app.py — zero changes to gui.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (QObject, QRunnable, QThread, QThreadPool,
                           QTimer, Qt, pyqtSignal, pyqtSlot)
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPalette
from PyQt6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QLabel,
                              QLineEdit, QListWidget, QListWidgetItem,
                              QProgressBar, QPushButton, QSizePolicy,
                              QTextEdit, QVBoxLayout, QWidget)

logger = logging.getLogger("antigravity.gui.files")

# ── Colours ────────────────────────────────────────────────────────────────
C_BG     = "#0f172a"
C_PANEL  = "#1e293b"
C_BORDER = "#334155"
C_PURPLE = "#a855f7"
C_GREEN  = "#34d399"
C_YELLOW = "#fbbf24"
C_RED    = "#f43f5e"
C_TEXT   = "#e2e8f0"
C_DIM    = "#64748b"

_CARD_STYLE = f"""
    QFrame {{
        background:{C_PANEL}; border:1px solid {C_BORDER};
        border-radius:10px; padding:4px;
    }}
"""
_BTN_PURPLE = f"""
    QPushButton {{
        background:{C_PURPLE}; color:white; border:none;
        border-radius:8px; font-weight:700; font-size:12px; padding:8px 16px;
    }}
    QPushButton:hover  {{ background:#9333ea; }}
    QPushButton:pressed {{ background:#7c3aed; }}
    QPushButton:disabled {{ background:#4c1d95; color:#7c3aed; }}
"""
_BTN_OUTLINE = f"""
    QPushButton {{
        background:transparent; color:{C_GREEN};
        border:1px solid {C_GREEN}; border-radius:8px;
        font-weight:700; font-size:11px; padding:6px 12px;
    }}
    QPushButton:hover  {{ background:rgba(52,211,153,0.08); }}
    QPushButton:disabled {{ color:{C_DIM}; border-color:{C_DIM}; }}
"""


# ── Worker that runs async coroutines from a thread ───────────────────────

class _AsyncWorker(QObject):
    progress = pyqtSignal(int, int, str)
    done     = pyqtSignal(object)   # result (Path | str | None)
    error    = pyqtSignal(str)

    def __init__(self, loop: asyncio.AbstractEventLoop,
                 coro_fn, *args, **kwargs):
        super().__init__()
        self._loop    = loop
        self._coro_fn = coro_fn
        self._args    = args
        self._kwargs  = kwargs

    def run(self) -> None:
        def _progress(done, total, msg):
            self.progress.emit(done, total, msg)

        async def _go():
            try:
                result = await self._coro_fn(
                    *self._args,
                    on_progress=_progress,
                    **self._kwargs,
                )
                self.done.emit(result)
            except Exception as e:
                self.error.emit(str(e))

        asyncio.run_coroutine_threadsafe(_go(), self._loop).result(timeout=600)


class _WorkerThread(QThread):
    def __init__(self, worker: _AsyncWorker):
        super().__init__()
        self._worker = worker

    def run(self):
        self._worker.run()


# ── Drop Zone ─────────────────────────────────────────────────────────────

class _DropZone(QFrame):
    file_dropped = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setStyleSheet(f"""
            QFrame {{
                background: rgba(168,85,247,0.06);
                border: 2px dashed {C_PURPLE};
                border-radius: 12px; min-height: 80px;
            }}
            QFrame:hover {{
                background: rgba(168,85,247,0.12);
            }}
        """)
        lbl = QLabel("⬆  Drag & drop a file here  or  click Choose File")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color:{C_DIM}; font-size:12px; border:none; background:transparent;")
        lay = QVBoxLayout(self)
        lay.addWidget(lbl)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.setStyleSheet(self.styleSheet().replace(C_PURPLE, C_GREEN))

    def dragLeaveEvent(self, e):
        self.setStyleSheet(self.styleSheet().replace(C_GREEN, C_PURPLE))

    def dropEvent(self, e: QDropEvent):
        self.setStyleSheet(self.styleSheet().replace(C_GREEN, C_PURPLE))
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.exists(path):
                self.file_dropped.emit(path)


# ── Main panel ─────────────────────────────────────────────────────────────

class FileSharePanel(QWidget):
    """
    File Sharing panel.
    Usage (in app.py after window.attach_node):
        from file_sharing_gui import FileSharePanel
        panel = FileSharePanel(node=dht_node, loop=loop)
        window.tab_widget.addTab(panel, "📁 File Sharing")
    """

    def __init__(self, node, loop: asyncio.AbstractEventLoop,
                 parent=None):
        super().__init__(parent)
        self._node   = node
        self._loop   = loop
        self._fm     = None   # FileManager, created lazily after node is ready

        self.setStyleSheet(f"background:{C_BG}; color:{C_TEXT}; font-family:Inter,sans-serif;")
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        root.addWidget(self._build_left(), stretch=3)
        root.addWidget(self._build_right(), stretch=2)

    # ── Layout builders ────────────────────────────────────────────────────

    def _build_left(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # ── Upload section ──────────────────────────────────────────────────
        upload_card = QFrame()
        upload_card.setStyleSheet(_CARD_STYLE)
        uc_lay = QVBoxLayout(upload_card)

        ul_title = QLabel("📤  Upload File")
        ul_title.setStyleSheet(f"color:{C_PURPLE}; font-size:11px; font-weight:700;"
                               f"text-transform:uppercase; letter-spacing:2px; border:none;")
        uc_lay.addWidget(ul_title)

        self._drop_zone = _DropZone()
        self._drop_zone.file_dropped.connect(self._on_file_dropped)
        uc_lay.addWidget(self._drop_zone)

        self._selected_file_lbl = QLabel("No file selected")
        self._selected_file_lbl.setStyleSheet(f"color:{C_DIM}; font-size:11px; border:none;")
        uc_lay.addWidget(self._selected_file_lbl)

        btn_row = QHBoxLayout()
        self._choose_btn = QPushButton("Choose File")
        self._choose_btn.setStyleSheet(_BTN_OUTLINE)
        self._choose_btn.clicked.connect(self._choose_file)
        btn_row.addWidget(self._choose_btn)

        self._choose_folder_btn = QPushButton("Choose Folder")
        self._choose_folder_btn.setStyleSheet(_BTN_OUTLINE)
        self._choose_folder_btn.clicked.connect(self._choose_folder)
        btn_row.addWidget(self._choose_folder_btn)

        self._upload_btn = QPushButton("⬆  Upload to DHT")
        self._upload_btn.setStyleSheet(_BTN_PURPLE)
        self._upload_btn.setEnabled(False)
        self._upload_btn.clicked.connect(self._do_upload)
        btn_row.addWidget(self._upload_btn)
        uc_lay.addLayout(btn_row)

        self._upload_progress = QProgressBar()
        self._upload_progress.setRange(0, 100)
        self._upload_progress.setValue(0)
        self._upload_progress.setStyleSheet(f"""
            QProgressBar {{ border:none; border-radius:4px;
                            background:{C_BG}; height:8px; }}
            QProgressBar::chunk {{ background:{C_PURPLE}; border-radius:4px; }}
        """)
        uc_lay.addWidget(self._upload_progress)

        self._upload_status = QLabel("")
        self._upload_status.setStyleSheet(f"color:{C_GREEN}; font-size:10px; border:none;")
        self._upload_status.setWordWrap(True)
        uc_lay.addWidget(self._upload_status)
        lay.addWidget(upload_card)

        # ── Download section ────────────────────────────────────────────────
        dl_card = QFrame()
        dl_card.setStyleSheet(_CARD_STYLE)
        dc_lay = QVBoxLayout(dl_card)

        dl_title = QLabel("📥  Download File")
        dl_title.setStyleSheet(f"color:{C_GREEN}; font-size:11px; font-weight:700;"
                               f"text-transform:uppercase; letter-spacing:2px; border:none;")
        dc_lay.addWidget(dl_title)

        key_row = QHBoxLayout()
        self._file_key_input = QLineEdit()
        self._file_key_input.setPlaceholderText("Paste file key here…")
        self._file_key_input.setStyleSheet(f"""
            QLineEdit {{ background:{C_BG}; border:1px solid {C_BORDER};
                        border-radius:8px; padding:6px 10px;
                        color:{C_TEXT}; font-family:monospace; font-size:11px; }}
            QLineEdit:focus {{ border-color:{C_GREEN}; }}
        """)
        key_row.addWidget(self._file_key_input)

        self._dl_btn = QPushButton("⬇  Download")
        self._dl_btn.setStyleSheet(_BTN_OUTLINE)
        self._dl_btn.clicked.connect(self._do_download)
        key_row.addWidget(self._dl_btn)
        dc_lay.addLayout(key_row)

        self._dl_progress = QProgressBar()
        self._dl_progress.setRange(0, 100)
        self._dl_progress.setValue(0)
        self._dl_progress.setStyleSheet(f"""
            QProgressBar {{ border:none; border-radius:4px;
                            background:{C_BG}; height:8px; }}
            QProgressBar::chunk {{ background:{C_GREEN}; border-radius:4px; }}
        """)
        dc_lay.addWidget(self._dl_progress)

        self._dl_status = QLabel("")
        self._dl_status.setStyleSheet(f"color:{C_GREEN}; font-size:10px; border:none;")
        self._dl_status.setWordWrap(True)
        dc_lay.addWidget(self._dl_status)
        lay.addWidget(dl_card)

        lay.addStretch()
        return w

    def _build_right(self) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # Shared files list
        list_card = QFrame()
        list_card.setStyleSheet(_CARD_STYLE)
        lc_lay = QVBoxLayout(list_card)

        hdr = QHBoxLayout()
        t   = QLabel("📁  Shared Files")
        t.setStyleSheet(f"color:{C_YELLOW}; font-size:11px; font-weight:700;"
                        f"text-transform:uppercase; letter-spacing:2px; border:none;")
        hdr.addWidget(t)
        hdr.addStretch()
        self._copy_key_btn = QPushButton("Copy Key")
        self._copy_key_btn.setStyleSheet(_BTN_OUTLINE)
        self._copy_key_btn.clicked.connect(self._copy_selected_key)
        hdr.addWidget(self._copy_key_btn)
        lc_lay.addLayout(hdr)

        self._files_list = QListWidget()
        self._files_list.setStyleSheet(f"""
            QListWidget {{ background:{C_BG}; border:none; border-radius:6px;
                           color:{C_TEXT}; font-size:11px; }}
            QListWidget::item {{ padding:6px; border-bottom:1px solid {C_BORDER}; }}
            QListWidget::item:selected {{ background:rgba(168,85,247,0.2); }}
        """)
        lc_lay.addWidget(self._files_list)
        lay.addWidget(list_card, stretch=1)

        # Log strip
        log_card = QFrame()
        log_card.setStyleSheet(_CARD_STYLE)
        ll_lay = QVBoxLayout(log_card)
        lt = QLabel("Event Log")
        lt.setStyleSheet(f"color:{C_PURPLE}; font-size:10px; font-weight:700; border:none;")
        ll_lay.addWidget(lt)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        self._log.setStyleSheet(f"""
            QTextEdit {{ background:#000; color:{C_GREEN}; border:none;
                         font-family:monospace; font-size:10px; border-radius:6px; }}
        """)
        ll_lay.addWidget(self._log)
        lay.addWidget(log_card, stretch=0)

        return w

    # ── Slots ──────────────────────────────────────────────────────────────

    @pyqtSlot()
    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose file to share")
        if path:
            self._on_file_dropped(path)

    @pyqtSlot()
    def _choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Choose folder to share")
        if path:
            self._on_file_dropped(path)

    @pyqtSlot(str)
    def _on_file_dropped(self, path: str):
        self._selected_path = path
        name = os.path.basename(path)
        if os.path.isdir(path):
            size = sum(f.stat().st_size for f in Path(path).rglob('*') if f.is_file())
            name += " (Folder)"
        else:
            size = os.path.getsize(path)
        self._selected_file_lbl.setText(f"{name}  ({size // 1024} KB)")
        self._selected_file_lbl.setStyleSheet(
            f"color:{C_TEXT}; font-size:11px; border:none;")
        self._upload_btn.setEnabled(True)

    @pyqtSlot()
    def _do_upload(self):
        if not hasattr(self, "_selected_path"):
            return
        self._ensure_fm()
        self._upload_btn.setEnabled(False)
        self._upload_progress.setValue(0)
        self._upload_status.setText("Uploading…")

        worker = _AsyncWorker(self._loop, self._fm.upload, self._selected_path)
        worker.progress.connect(self._on_upload_progress)
        worker.done.connect(self._on_upload_done)
        worker.error.connect(lambda e: self._log_line(f"[ERROR] {e}", C_RED))

        self._upload_thread = _WorkerThread(worker)
        self._upload_thread.start()

    @pyqtSlot(int, int, str)
    def _on_upload_progress(self, done: int, total: int, msg: str):
        pct = int(done / max(total, 1) * 100)
        self._upload_progress.setValue(pct)
        self._upload_status.setText(msg)
        self._log_line(msg, C_PURPLE)

    @pyqtSlot(object)
    def _on_upload_done(self, file_key):
        self._upload_btn.setEnabled(True)
        if file_key:
            self._upload_status.setText(f"✅ Done! Key: {file_key[:16]}…")
            self._log_line(f"[UPLOAD] file_key={file_key}", C_GREEN)
            self._refresh_files_list()
        else:
            self._upload_status.setText("Upload failed.")

    @pyqtSlot()
    def _do_download(self):
        file_key = self._file_key_input.text().strip()
        if not file_key:
            self._dl_status.setText("Enter a file key first.")
            return
        self._ensure_fm()
        self._dl_btn.setEnabled(False)
        self._dl_progress.setValue(0)
        self._dl_status.setText("Searching DHT…")

        worker = _AsyncWorker(self._loop, self._fm.download, file_key)
        worker.progress.connect(self._on_dl_progress)
        worker.done.connect(self._on_dl_done)
        worker.error.connect(lambda e: self._log_line(f"[ERROR] {e}", C_RED))

        self._dl_thread = _WorkerThread(worker)
        self._dl_thread.start()

    @pyqtSlot(int, int, str)
    def _on_dl_progress(self, done: int, total: int, msg: str):
        pct = int(done / max(total, 1) * 100)
        self._dl_progress.setValue(pct)
        self._dl_status.setText(msg)
        self._log_line(msg, C_GREEN)

    @pyqtSlot(object)
    def _on_dl_done(self, path):
        self._dl_btn.setEnabled(True)
        if path:
            self._dl_status.setText(f"✅ Saved to: {path}")
            self._log_line(f"[DOWNLOAD] Saved {path}", C_GREEN)
            self._refresh_files_list()
            #exception handling
        else:
            self._dl_status.setText("❌ File not found in DHT.")
            self._log_line("[DOWNLOAD] Not found.", C_RED)

    @pyqtSlot()

    def _copy_selected_key(self):
        item = self._files_list.currentItem()
        if item:
            key = item.data(Qt.ItemDataRole.UserRole)
            if key:
                from PyQt6.QtWidgets import QApplication
                QApplication.clipboard().setText(key)
                self._log_line(f"[COPY] {key[:16]}… copied to clipboard", C_YELLOW)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _ensure_fm(self):
        if self._fm is None:
            from file_manager import FileManager
            self._fm = FileManager(self._node)

    def _refresh_files_list(self):
        if self._fm is None:
            return
        self._files_list.clear()
        for f in self._fm.list_local_files():
            size_kb = f["file_size"] // 1024
            txt     = f"📄 {f['file_name']}  •  {size_kb} KB  •  {f['chunks']} chunks"
            item    = QListWidgetItem(txt)
            item.setData(Qt.ItemDataRole.UserRole, f["file_key"])
            self._files_list.addItem(item)

    def _log_line(self, msg: str, color: str = C_GREEN):
        import time as _time
        ts  = _time.strftime("%H:%M:%S")
        self._log.append(
            f'<span style="color:{C_DIM}">[{ts}]</span> '
            f'<span style="color:{color}">{msg}</span>'
        )
