"""
crypto.py — Project Antigravity
Cryptographic primitives: secp256k1 key-pair generation, Node ID derivation,
and Antigravity Node Record (ANR) signing/verification.

Dependency: `cryptography` (PyCA) — pure-Python + Rust, works on Python 3.9+
through 3.14+. Replaces coincurve for maximum version compatibility.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

try:
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.exceptions import InvalidSignature
except ImportError:
    raise ImportError(
        "cryptography is required.  Run:  pip install cryptography PyQt6"
    )


# ---------------------------------------------------------------------------
# Key pair
# ---------------------------------------------------------------------------

class NodeKeypair:
    """Wraps a secp256k1 private/public key pair and derives a 256-bit Node ID."""

    def __init__(self, private_key_bytes: Optional[bytes] = None):
        if private_key_bytes:
            private_int = int.from_bytes(private_key_bytes, "big")
            self._privkey = ec.derive_private_key(private_int, ec.SECP256K1())
        else:
            self._privkey = ec.generate_private_key(ec.SECP256K1())

        self._pubkey = self._privkey.public_key()

        # Node ID = SHA-256 of the compressed public key (33 bytes)
        compressed = self._pubkey.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.CompressedPoint,
        )
        self._node_id: bytes = hashlib.sha256(compressed).digest()

    # ------------------------------------------------------------------ #

    @property
    def private_key_bytes(self) -> bytes:
        """Raw 32-byte big-endian private scalar."""
        return self._privkey.private_numbers().private_value.to_bytes(32, "big")

    @property
    def public_key_bytes(self) -> bytes:
        """Compressed (33-byte) SEC-encoded public key."""
        return self._pubkey.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.CompressedPoint,
        )

    @property
    def node_id(self) -> bytes:
        """Raw 256-bit Node ID."""
        return self._node_id

    @property
    def node_id_hex(self) -> str:
        return self._node_id.hex()

    # ------------------------------------------------------------------ #

    def sign(self, data: bytes) -> bytes:
        """Return a DER-encoded ECDSA signature over SHA-256(data)."""
        return self._privkey.sign(data, ec.ECDSA(hashes.SHA256()))

    # ------------------------------------------------------------------ #

    @staticmethod
    def verify(public_key_bytes: bytes, data: bytes, signature: bytes) -> bool:
        """Verify a DER-encoded ECDSA signature produced by sign()."""
        try:
            pub = ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256K1(), public_key_bytes
            )
            pub.verify(signature, data, ec.ECDSA(hashes.SHA256()))
            return True
        except (InvalidSignature, Exception):
            return False

    # ------------------------------------------------------------------ #

    def save(self, path: str) -> None:
        """Persist the private key to a file (hex-encoded, 0600 permissions)."""
        import os
        with open(path, "w") as fh:
            fh.write(self.private_key_bytes.hex())
        os.chmod(path, 0o600)

    @classmethod
    def load(cls, path: str) -> "NodeKeypair":
        with open(path) as fh:
            return cls(bytes.fromhex(fh.read().strip()))


# ---------------------------------------------------------------------------
# XOR Distance metric (Kademlia)
# ---------------------------------------------------------------------------

def xor_distance(node_id_a: bytes, node_id_b: bytes) -> int:
    """Return the integer XOR distance between two 256-bit Node IDs."""
    if len(node_id_a) != 32 or len(node_id_b) != 32:
        raise ValueError("Node IDs must be 32 bytes (256 bits)")
    return int.from_bytes(node_id_a, "big") ^ int.from_bytes(node_id_b, "big")


def xor_distance_hex(a: str, b: str) -> int:
    """Convenience wrapper accepting hex strings."""
    return xor_distance(bytes.fromhex(a), bytes.fromhex(b))


def leading_zeros(distance: int) -> int:
    """Return number of leading zero bits in a 256-bit integer."""
    if distance == 0:
        return 256
    return 255 - distance.bit_length() + 1


def bucket_index(local_id: bytes, remote_id: bytes) -> int:
    """Map a remote node to its k-bucket index [0, 255]."""
    dist = xor_distance(local_id, remote_id)
    return 255 - leading_zeros(dist)


# ---------------------------------------------------------------------------
# Antigravity Node Record (ANR)
# ---------------------------------------------------------------------------

@dataclass
class ANR:
    """
    Antigravity Node Record — signed envelope carrying a node's reachability info.

    Fields
    ------
    node_id     : hex-encoded 256-bit Node ID
    public_key  : hex-encoded compressed secp256k1 public key (33 bytes)
    ip          : dotted-quad IPv4 (or IPv6 string)
    udp_port    : UDP listening port
    seq         : monotonically-increasing sequence number
    signature   : hex-encoded DER ECDSA signature over the canonical payload
    timestamp   : UNIX timestamp (float) when the record was created
    """

    node_id: str
    public_key: str
    ip: str
    udp_port: int
    seq: int
    signature: str = field(default="")
    timestamp: float = field(default_factory=time.time)

    # ------------------------------------------------------------------ #

    def _signing_payload(self) -> bytes:
        """Canonical, deterministic bytes that are signed."""
        payload = json.dumps(
            {
                "node_id":   self.node_id,
                "public_key": self.public_key,
                "ip":         self.ip,
                "udp_port":   self.udp_port,
                "seq":        self.seq,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return payload.encode()

    def sign(self, keypair: NodeKeypair) -> "ANR":
        """Sign the record in-place and return self."""
        self.signature = keypair.sign(self._signing_payload()).hex()
        return self

    def verify(self) -> bool:
        """Verify the embedded signature against the public key in the record."""
        try:
            pub_bytes = bytes.fromhex(self.public_key)
            sig_bytes = bytes.fromhex(self.signature)
            return NodeKeypair.verify(pub_bytes, self._signing_payload(), sig_bytes)
        except Exception:
            return False

    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ANR":
        return cls(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, raw: str) -> "ANR":
        return cls.from_dict(json.loads(raw))

    # ------------------------------------------------------------------ #

    @classmethod
    def create(cls, keypair: NodeKeypair, ip: str, udp_port: int,
               seq: int = 0) -> "ANR":
        """Factory: build and immediately sign a new ANR."""
        anr = cls(
            node_id=keypair.node_id_hex,
            public_key=keypair.public_key_bytes.hex(),
            ip=ip,
            udp_port=udp_port,
            seq=seq,
        )
        anr.sign(keypair)
        return anr

    def short_id(self, n: int = 8) -> str:
        """Return the first *n* hex chars of the Node ID for display."""
        return self.node_id[:n]

    def __repr__(self) -> str:
        return f"ANR(node={self.short_id()}, {self.ip}:{self.udp_port}, seq={self.seq})"
