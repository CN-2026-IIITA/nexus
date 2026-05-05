"""
protocols.py — Project Antigravity
RPC message types: PING, PONG, FIND_NODE, NEIGHBORS.
Every message is serialized to/from compact JSON over UDP.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional

from crypto import ANR


# ---------------------------------------------------------------------------
# Message type enum
# ---------------------------------------------------------------------------

class MsgType(str, Enum):
    PING        = "PING"
    PONG        = "PONG"
    FIND_NODE   = "FIND_NODE"
    NEIGHBORS   = "NEIGHBORS"


# ---------------------------------------------------------------------------
# Base message envelope
# ---------------------------------------------------------------------------

@dataclass
class _BaseMessage:
    msg_type: str
    msg_id: str = field(default_factory=lambda: os.urandom(8).hex())
    sender_anr: Optional[dict] = None      # always include sender's ANR

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "_BaseMessage":
        d = json.loads(raw.decode())
        msg_type = d.get("msg_type")
        dispatchers = {
            MsgType.PING:       PingMessage,
            MsgType.PONG:       PongMessage,
            MsgType.FIND_NODE:  FindNodeMessage,
            MsgType.NEIGHBORS:  NeighborsMessage,
        }
        klass = dispatchers.get(msg_type)
        if klass is None:
            raise ValueError(f"Unknown msg_type: {msg_type}")
        return klass(**{k: v for k, v in d.items() if k in klass.__dataclass_fields__})

    def get_sender_anr(self) -> Optional[ANR]:
        if self.sender_anr:
            return ANR.from_dict(self.sender_anr)
        return None


# ---------------------------------------------------------------------------
# PING
# ---------------------------------------------------------------------------

@dataclass
class PingMessage(_BaseMessage):
    """
    Sent to check liveness.  Carries the sender's ANR so the recipient
    can update its routing table even if it hasn't seen this node before.
    """
    msg_type: str = MsgType.PING
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def build(cls, sender_anr: ANR) -> "PingMessage":
        return cls(sender_anr=sender_anr.to_dict())


# ---------------------------------------------------------------------------
# PONG
# ---------------------------------------------------------------------------

@dataclass
class PongMessage(_BaseMessage):
    """Reply to a PING.  Echoes the original msg_id for correlation."""
    msg_type: str = MsgType.PONG
    ping_id: str = ""                      # msg_id of the PING being acknowledged
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def build(cls, ping: PingMessage, sender_anr: ANR) -> "PongMessage":
        return cls(ping_id=ping.msg_id, sender_anr=sender_anr.to_dict())


# ---------------------------------------------------------------------------
# FIND_NODE
# ---------------------------------------------------------------------------

@dataclass
class FindNodeMessage(_BaseMessage):
    """
    Request up to k nearest nodes to *target_id* known by the recipient.
    """
    msg_type: str = MsgType.FIND_NODE
    target_id: str = ""                    # hex-encoded 256-bit target Node ID

    @classmethod
    def build(cls, target_id: str, sender_anr: ANR) -> "FindNodeMessage":
        return cls(target_id=target_id, sender_anr=sender_anr.to_dict())


# ---------------------------------------------------------------------------
# NEIGHBORS
# ---------------------------------------------------------------------------

@dataclass
class NeighborsMessage(_BaseMessage):
    """
    Reply to FIND_NODE.  Contains a list of up to k ANR dicts closest
    to the requested target_id.
    """
    msg_type: str = MsgType.NEIGHBORS
    find_node_id: str = ""                 # msg_id of the originating FIND_NODE
    nodes: List[dict] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        find_node_msg: FindNodeMessage,
        closest_anrs: List[ANR],
        sender_anr: ANR,
    ) -> "NeighborsMessage":
        return cls(
            find_node_id=find_node_msg.msg_id,
            nodes=[a.to_dict() for a in closest_anrs],
            sender_anr=sender_anr.to_dict(),
        )

    def get_nodes(self) -> List[ANR]:
        return [ANR.from_dict(d) for d in self.nodes]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

MAX_UDP_PAYLOAD = 1280   # safe MTU limit in bytes

def encode_message(msg: _BaseMessage) -> bytes:
    data = msg.to_bytes()
    if len(data) > MAX_UDP_PAYLOAD:
        raise ValueError(
            f"Message of type {msg.msg_type} exceeds {MAX_UDP_PAYLOAD} bytes "
            f"({len(data)} bytes).  Consider splitting NEIGHBORS payload."
        )
    return data


def decode_message(raw: bytes) -> _BaseMessage:
    return _BaseMessage.from_bytes(raw)
