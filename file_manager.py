"""
file_manager.py — Project Antigravity
File chunking, manifest management, upload and download over the DHT.

Upload flow:
  1. Split file into 256 KB chunks
  2. SHA-256 hash each chunk → chunk_key
  3. Store chunk locally + replicate via dht_store
  4. Build FileManifest (JSON) → hash file_name → file_key
  5. Store manifest via dht_store → return file_key

Download flow:
  1. file_key = SHA-256(file_name)
  2. find_value(file_key) → manifest bytes
  3. Parse manifest → chunk_keys[]
  4. For each chunk_key: find_value → bytes (or TCP fetch if pointer)
  5. Verify integrity, reassemble, save to downloads/
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from dht_storage import DEFAULT_DOWNLOAD_DIR
from rpc_extensions import DHTNode, fetch_chunk_tcp

logger = logging.getLogger("antigravity.files")

CHUNK_SIZE    = 256 * 1024   # 256 KB
REPLICATION   = 3            # replicate to this many nodes
MAX_RETRIES   = 3            # per-chunk download retries
RETRY_DELAY   = 1.0          # seconds between retries


# ── File manifest ──────────────────────────────────────────────────────────

@dataclass
class FileManifest:
    """
    Describes a file stored in the DHT.
    Serialised to JSON and stored under file_key.
    """
    file_name: str
    file_size: int
    chunk_size: int
    chunks: List[str]            # ordered SHA-256 hex chunk keys
    uploader_node_id: str = ""   # for attribution / debug
    created_at: float = field(default_factory=time.time)

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "FileManifest":
        return cls(**json.loads(raw.decode()))

    @staticmethod
    def file_key(file_name: str) -> str:
        """Deterministic DHT key for a file (SHA-256 of the file name)."""
        return hashlib.sha256(file_name.encode()).hexdigest()

    @staticmethod
    def chunk_key(chunk_bytes: bytes) -> str:
        """DHT key for a chunk (SHA-256 of the content)."""
        return hashlib.sha256(chunk_bytes).hexdigest()


# ── Progress callback type ─────────────────────────────────────────────────
# on_progress(done: int, total: int, message: str)
ProgressCb = Optional[Callable[[int, int, str], None]]


# ── FileManager ────────────────────────────────────────────────────────────

class FileManager:
    """
    High-level file upload / download over the DHT.

    Parameters
    ----------
    node         : DHTNode instance (must be started)
    downloads_dir: directory where downloaded files are saved
    """

    def __init__(self, node: DHTNode,
                 downloads_dir: str = DEFAULT_DOWNLOAD_DIR):
        self.node     = node
        self.dl_dir   = Path(downloads_dir)
        self.dl_dir.mkdir(parents=True, exist_ok=True)
        # Local manifest registry: file_key → FileManifest
        self._manifests: Dict[str, FileManifest] = {}

    # ── Upload ─────────────────────────────────────────────────────────────

    async def upload(self, path: str,
                     on_progress: ProgressCb = None) -> str:
        """
        Upload a file to the DHT.

        Returns
        -------
        file_key : hex string — share this so others can download the file
        """
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"File not found: {path}")

        temp_zip_path = None
        if src.is_dir():
            logger.info(f"[UPLOAD] Zipping directory {src.name}...")
            if on_progress:
                on_progress(0, 1, f"Zipping folder {src.name}...")
            
            fd, temp_zip = tempfile.mkstemp(suffix=".zip")
            os.close(fd)
            zip_out = shutil.make_archive(temp_zip[:-4], 'zip', src)
            os.remove(temp_zip)
            
            src_to_read = Path(zip_out)
            temp_zip_path = zip_out
            file_name = src.name + ".zip"
            file_size = src_to_read.stat().st_size
        else:
            src_to_read = src
            file_name = src.name
            file_size = src.stat().st_size

        logger.info(f"[UPLOAD] Starting: {file_name} ({file_size} bytes)")

        # 1. Chunk the file
        chunks = self._read_chunks(src_to_read)
        total  = len(chunks)
        logger.info(f"[UPLOAD] {total} chunk(s) of {CHUNK_SIZE // 1024} KB each")

        # 2. Store each chunk
        chunk_keys: List[str] = []
        for i, chunk_bytes in enumerate(chunks):
            key = FileManifest.chunk_key(chunk_bytes)
            chunk_keys.append(key)
            await self.node.dht_store(key, chunk_bytes, replication=REPLICATION)
            if on_progress:
                on_progress(i + 1, total,
                            f"Uploading chunk {i+1}/{total} ({key[:8]}…)")
            logger.info(f"[UPLOAD] Chunk {i+1}/{total} stored ({key[:8]}…)")

        # 3. Build and store manifest
        manifest = FileManifest(
            file_name=file_name,
            file_size=file_size,
            chunk_size=CHUNK_SIZE,
            chunks=chunk_keys,
            uploader_node_id=self.node.anr.node_id,
        )
        file_key = FileManifest.file_key(file_name)
        await self.node.dht_store(file_key, manifest.to_bytes(),
                                  replication=REPLICATION)
        self._manifests[file_key] = manifest

        if temp_zip_path and os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)

        logger.info(f"[UPLOAD] Complete. file_key={file_key[:16]}…")
        if on_progress:
            on_progress(total, total,
                        f"Upload complete! Key: {file_key[:16]}…")
        return file_key

    # ── Download ───────────────────────────────────────────────────────────

    async def download(self, file_key: str,
                       on_progress: ProgressCb = None) -> Optional[Path]:
        """
        Download a file by its file_key.

        Returns
        -------
        Path to the saved file, or None if the manifest was not found.
        """
        logger.info(f"[DOWNLOAD] Looking up manifest for {file_key[:16]}…")

        # 1. Fetch manifest
        raw_manifest = await self.node.find_value(file_key)
        if raw_manifest is None:
            logger.warning(f"[DOWNLOAD] Manifest not found: {file_key[:8]}…")
            return None

        # Handle TCP pointer (if manifest itself was too large for UDP — unlikely
        # but handle defensively)
        raw_manifest = await self._resolve_value(raw_manifest, key=file_key)
        if raw_manifest is None:
            logger.warning("[DOWNLOAD] Could not resolve manifest value")
            return None

        manifest = FileManifest.from_bytes(raw_manifest)
        self._manifests[file_key] = manifest
        total = len(manifest.chunks)
        logger.info(f"[DOWNLOAD] Manifest OK. {total} chunk(s) for '{manifest.file_name}'")

        # 2. Fetch each chunk
        chunk_data: List[Optional[bytes]] = [None] * total
        for i, chunk_key in enumerate(manifest.chunks):
            data = await self._fetch_chunk_with_retry(chunk_key, i, total)
            if data is None:
                logger.error(f"[DOWNLOAD] Failed to fetch chunk {i+1}/{total}")
                return None
            # Integrity check
            if FileManifest.chunk_key(data) != chunk_key:
                logger.error(f"[DOWNLOAD] Chunk {i+1} integrity check FAILED")
                return None
            chunk_data[i] = data
            if on_progress:
                on_progress(i + 1, total,
                            f"Downloaded chunk {i+1}/{total} ({chunk_key[:8]}…)")

        # 3. Reassemble
        dest = self.dl_dir / manifest.file_name
        with open(dest, "wb") as fh:
            for chunk in chunk_data:
                fh.write(chunk)
        logger.info(f"[DOWNLOAD] Saved to {dest} ({dest.stat().st_size} bytes)")
        if on_progress:
            on_progress(total, total, f"Saved to {dest}")
        return dest

    # ── Helpers ────────────────────────────────────────────────────────────

    def _read_chunks(self, path: Path) -> List[bytes]:
        chunks = []
        with open(path, "rb") as fh:
            while True:
                buf = fh.read(CHUNK_SIZE)
                if not buf:
                    break
                chunks.append(buf)
        return chunks

    async def _resolve_value(self, value: bytes, key: str = "") -> Optional[bytes]:
        """
        If *value* is a TCP pointer `{"tcp_host": ..., "tcp_port": ...}`,
        fetch the actual bytes over TCP. Otherwise return *value* as-is.
        """
        try:
            d = json.loads(value.decode())
            if "tcp_host" in d and "tcp_port" in d:
                return await fetch_chunk_tcp(d["tcp_host"], d["tcp_port"], key)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        return value

    async def _fetch_chunk_with_retry(
        self, chunk_key: str, idx: int, total: int,
    ) -> Optional[bytes]:
        """Try to get a chunk up to MAX_RETRIES times."""
        for attempt in range(1, MAX_RETRIES + 1):
            raw = await self.node.find_value(chunk_key)
            if raw is None:
                logger.debug(f"[DOWNLOAD] Chunk {idx+1}/{total} attempt {attempt} → not found")
                await asyncio.sleep(RETRY_DELAY * attempt)
                continue

            # Resolve TCP pointer if needed
            resolved = await self._resolve_value(raw, key=chunk_key)
            if resolved is not None:
                return resolved

            await asyncio.sleep(RETRY_DELAY * attempt)

        return None

    # ── Utilities ──────────────────────────────────────────────────────────

    def list_local_files(self) -> List[dict]:
        """Return summaries of all locally-known manifests."""
        result = []
        for fk, m in self._manifests.items():
            result.append({
                "file_key":  fk,
                "file_name": m.file_name,
                "file_size": m.file_size,
                "chunks":    len(m.chunks),
                "created_at": m.created_at,
            })
        return result

    @staticmethod
    def make_file_key(file_name: str) -> str:
        return FileManifest.file_key(file_name)
