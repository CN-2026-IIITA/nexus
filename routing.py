"""
routing.py — Project Antigravity
In-memory Kademlia routing table using 256 k-buckets (k = 20).
Thread/coroutine-safe via asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Tuple

from crypto import ANR, bucket_index, xor_distance


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

K = 20           # bucket capacity (Kademlia constant)
NUM_BUCKETS = 256


# ---------------------------------------------------------------------------
# Single k-bucket
# ---------------------------------------------------------------------------

class KBucket:
    """
    A single Kademlia k-bucket, implemented as an ordered dict
    (insertion-order == LRU order; most-recently-seen at the end).

    Capacity: *k* entries.  When full, the least-recently-seen entry
    (bucket[0]) should be PING-verified before eviction.
    """

    def __init__(self, index: int, k: int = K):
        self.index = index
        self.k = k
        # node_id_hex -> ANR
        self._nodes: OrderedDict[str, ANR] = OrderedDict()
        self._replacement_cache: OrderedDict[str, ANR] = OrderedDict()

    # ------------------------------------------------------------------ #

    @property
    def nodes(self) -> List[ANR]:
        return list(self._nodes.values())

    @property
    def size(self) -> int:
        return len(self._nodes)

    @property
    def is_full(self) -> bool:
        return self.size >= self.k

    # ------------------------------------------------------------------ #

    def touch(self, anr: ANR) -> Optional[ANR]:
        """
        Insert or update *anr*.

        Returns
        -------
        None     — if the node was inserted/updated successfully.
        ANR      — the least-recently-seen (head) node that should be
                   PING-verified; if it fails, evict it and re-try.
        """
        nid = anr.node_id

        if nid in self._nodes:
            # Refresh: move to tail (most-recently-seen)
            self._nodes.move_to_end(nid)
            self._nodes[nid] = anr
            return None

        if not self.is_full:
            self._nodes[nid] = anr
            return None

        # Bucket full → add to replacement cache and return LRS for verification
        #thala for a reason
        self._replacement_cache[nid] = anr
        # Trim replacement cache
        while len(self._replacement_cache) > self.k:
            self._replacement_cache.popitem(last=False)

        # Return head (LRS) to caller for PING
        lrs_id, lrs_anr = next(iter(self._nodes.items()))
        return lrs_anr

    def evict(self, node_id_hex: str) -> None:
        """Remove a dead node and promote from replacement cache if available."""
        if node_id_hex in self._nodes:
            del self._nodes[node_id_hex]
        # Promote one from replacement cache
        if self._replacement_cache:
            new_id, new_anr = self._replacement_cache.popitem(last=True)
            self._nodes[new_id] = new_anr

    def get(self, node_id_hex: str) -> Optional[ANR]:
        return self._nodes.get(node_id_hex)

    def contains(self, node_id_hex: str) -> bool:
        return node_id_hex in self._nodes

    def __repr__(self) -> str:
        return f"KBucket(index={self.index}, size={self.size}/{self.k})"


# ---------------------------------------------------------------------------
# Routing table
# ---------------------------------------------------------------------------

class RoutingTable:
    """
    Full 256-bucket Kademlia routing table.

    Usage
    -----
    >>> table = RoutingTable(local_anr)
    >>> eviction_candidate = await table.add(remote_anr)
    >>> closest = table.find_closest(target_id_hex, count=K)
    """

    def __init__(self, local_anr: ANR, k: int = K):
        self.local_anr = local_anr
        self.local_id = bytes.fromhex(local_anr.node_id)
        self.k = k
        self._buckets: List[KBucket] = [KBucket(i, k) for i in range(NUM_BUCKETS)]
        self._lock = asyncio.Lock()
        self._eviction_callbacks: List[Callable[[ANR, int], None]] = []

    # ------------------------------------------------------------------ #

    def _bucket_for(self, anr: ANR) -> KBucket:
        idx = bucket_index(self.local_id, bytes.fromhex(anr.node_id))
        return self._buckets[idx]

    # ------------------------------------------------------------------ #

    async def add(self, anr: ANR) -> Optional[ANR]:
        """
        Add or refresh a node.

        Returns
        -------
        None     — success / already known.
        ANR      — least-recently-seen node in the target bucket that
                   the caller should PING before deciding to evict.
        """
        if anr.node_id == self.local_anr.node_id:
            return None                         # never add ourselves

        async with self._lock:
            bucket = self._bucket_for(anr)
            return bucket.touch(anr)

    async def remove(self, node_id_hex: str) -> None:
        """Evict a dead node (call after a failed PING verification)."""
        async with self._lock:
            for bucket in self._buckets:
                if bucket.contains(node_id_hex):
                    bucket.evict(node_id_hex)
                    break

    # ------------------------------------------------------------------ #

    def find_closest(self, target_id_hex: str, count: int = K) -> List[ANR]:
        """Return up to *count* nodes sorted by XOR distance to *target_id_hex*."""
        target = bytes.fromhex(target_id_hex)
        all_nodes: List[ANR] = []
        for bucket in self._buckets:
            all_nodes.extend(bucket.nodes)

        all_nodes.sort(key=lambda anr: xor_distance(bytes.fromhex(anr.node_id), target))
        return all_nodes[:count]

    def get_node(self, node_id_hex: str) -> Optional[ANR]:
        for bucket in self._buckets:
            anr = bucket.get(node_id_hex)
            if anr:
                return anr
        return None

    # ------------------------------------------------------------------ #

    @property
    def total_nodes(self) -> int:
        return sum(b.size for b in self._buckets)

    @property
    def non_empty_buckets(self) -> List[KBucket]:
        return [b for b in self._buckets if b.size > 0]

    def bucket_summary(self) -> List[Dict]:
        """Return a JSON-serializable summary of non-empty buckets."""
        result = []
        for bucket in self.non_empty_buckets:
            result.append(
                {
                    "index": bucket.index,
                    "count": bucket.size,
                    "nodes": [
                        {
                            "node_id": anr.node_id,
                            "short_id": anr.short_id(8),
                            "ip": anr.ip,
                            "port": anr.udp_port,
                            "seq": anr.seq,
                        }
                        for anr in bucket.nodes
                    ],
                }
            )
        return result

    def __repr__(self) -> str:
        return (
            f"RoutingTable(local={self.local_anr.short_id()}, "
            f"nodes={self.total_nodes}, buckets={len(self.non_empty_buckets)})"
        )
