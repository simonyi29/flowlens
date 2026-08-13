"""Persistent Ed25519 identity used by an outbound FlowLens worker."""
from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IDENTITY_PATH = ROOT / "data" / "flowlens" / "worker" / "identity.pem"


class WorkerIdentityManager:
    def __init__(self, path: Path = DEFAULT_IDENTITY_PATH):
        self.path = path
        self._key: Ed25519PrivateKey | None = None

    def load_or_create(self) -> str:
        if self.path.exists():
            self._key = serialization.load_pem_private_key(self.path.read_bytes(), password=None)
            if not isinstance(self._key, Ed25519PrivateKey):
                raise ValueError("worker identity is not an Ed25519 key")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._key = Ed25519PrivateKey.generate()
            data = self._key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            self.path.write_bytes(data)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        return self.public_key()

    def public_key(self) -> str:
        if self._key is None:
            raise RuntimeError("worker identity is not loaded")
        raw = self._key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return base64.urlsafe_b64encode(raw).decode("ascii")

    def sign(self, challenge: bytes) -> str:
        if self._key is None:
            self.load_or_create()
        assert self._key is not None
        return base64.urlsafe_b64encode(self._key.sign(challenge)).decode("ascii")


def verify_worker_signature(public_key: str, challenge: bytes, signature: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(public_key.encode("ascii")))
        key.verify(base64.urlsafe_b64decode(signature.encode("ascii")), challenge)
        return True
    except (ValueError, InvalidSignature):
        return False

