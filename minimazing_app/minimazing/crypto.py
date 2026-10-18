"""Keybag parsing and decryption for encrypted iOS backups (iOS 10.2+).

This mirrors what iMazing's binary shows it implements itself (its own
"Keybag unlock succeeded / unwrap failed / invalid data protection class"
messages and a Manifest.plist "ManifestKey" parser), using the publicly
documented format:

  Manifest.plist
    BackupKeyBag  -> TLV keybag (SALT, ITER, DPSL, DPIC + one block per class)
    ManifestKey   -> 4-byte little-endian protection class + 40-byte wrapped key

  password --PBKDF2-SHA256(DPSL, DPIC)--> --PBKDF2-SHA1(SALT, ITER)--> passcode key
  passcode key --AES key unwrap (RFC 3394)--> class keys
  class key   --AES key unwrap--> per-file key (and the Manifest.db key)
  file bytes  --AES-256-CBC, IV = 0--> plaintext (truncate to recorded size)
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import (
    InvalidUnwrap,
    aes_key_unwrap,
    aes_key_wrap,
)

WRAP_DEVICE = 1
WRAP_PASSCODE = 2

# Tags that belong to a per-protection-class block once one has started.
# NB: CLAS is deliberately excluded here - it's handled as the block
# delimiter itself (see parse()), not stored via this set.
_CLASS_TAGS = {b"WRAP", b"WPKY", b"KTYP", b"PBKY", b"PBYT"}


class BackupPasswordError(Exception):
    """Raised when the backup password is wrong."""


def _tlv(blob: bytes):
    i = 0
    while i + 8 <= len(blob):
        tag = blob[i : i + 4]
        (length,) = struct.unpack(">I", blob[i + 4 : i + 8])
        yield tag, blob[i + 8 : i + 8 + length]
        i += 8 + length


@dataclass
class Keybag:
    attrs: dict = field(default_factory=dict)
    classes: dict = field(default_factory=dict)  # class number -> dict of tags
    class_keys: dict = field(default_factory=dict)  # class number -> 32-byte key

    @classmethod
    def parse(cls, blob: bytes) -> "Keybag":
        """A keybag is a flat TLV stream: a handful of global attributes
        (VERS, TYPE, UUID, SALT, ITER, DPSL, DPIC, ...) followed by one
        block per protection class. Blocks are NOT delimited by their own
        UUID tag (every block - and the header - carries one, so that
        would misgroup things); the real delimiter is CLAS, which starts
        a new block and whose value is that class's number."""
        kb = cls()
        current_class: int | None = None
        for tag, value in _tlv(blob):
            if tag == b"CLAS":
                (current_class,) = struct.unpack(">I", value)
                kb.classes[current_class] = {}
                continue
            if current_class is not None and tag in _CLASS_TAGS:
                kb.classes[current_class][tag] = value
            else:
                kb.attrs[tag] = value
        return kb

    def _int(self, tag: bytes) -> int:
        return struct.unpack(">I", self.attrs[tag])[0]

    def unlock(self, password: str | bytes) -> None:
        if isinstance(password, str):
            password = password.encode("utf-8")
        if b"DPSL" in self.attrs:  # iOS 10.2+ double derivation
            password = hashlib.pbkdf2_hmac(
                "sha256", password, self.attrs[b"DPSL"], self._int(b"DPIC"), 32
            )
        passcode_key = hashlib.pbkdf2_hmac(
            "sha1", password, self.attrs[b"SALT"], self._int(b"ITER"), 32
        )
        for clas, block in self.classes.items():
            if b"WPKY" not in block:
                continue
            (wrap,) = struct.unpack(">I", block[b"WRAP"])
            if wrap & WRAP_PASSCODE:
                try:
                    self.class_keys[clas] = aes_key_unwrap(passcode_key, block[b"WPKY"])
                except InvalidUnwrap as exc:
                    raise BackupPasswordError("Wrong backup password") from exc
        if not self.class_keys:
            raise BackupPasswordError("No class keys could be unwrapped")

    def unwrap_key(self, protection_class: int, wrapped: bytes) -> bytes:
        key = self.class_keys.get(protection_class)
        if key is None:
            raise KeyError(f"No key for protection class {protection_class}")
        return aes_key_unwrap(key, wrapped)


def aes_cbc_decrypt(key: bytes, data: bytes, iv: bytes = b"\0" * 16) -> bytes:
    usable = len(data) - (len(data) % 16)
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data[:usable]) + dec.finalize()


def aes_cbc_encrypt(key: bytes, data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    data = data + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(b"\0" * 16)).encryptor()
    return enc.update(data) + enc.finalize()


def strip_pkcs7(data: bytes) -> bytes:
    if data and 1 <= data[-1] <= 16 and data.endswith(bytes([data[-1]]) * data[-1]):
        return data[: -data[-1]]
    return data


__all__ = [
    "Keybag",
    "BackupPasswordError",
    "aes_cbc_decrypt",
    "aes_cbc_encrypt",
    "aes_key_wrap",
    "strip_pkcs7",
]
