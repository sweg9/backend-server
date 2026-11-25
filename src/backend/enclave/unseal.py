"""Opens a sealed photo: KMS decrypts its data key only for this enclave's attestation document, then
AES-256-GCM (C-05). The opened photo never leaves the enclave. The format is in common/sealing.py.

In the enclave the order is Decryption -> ML Engine -> Policy: `opener` turns each sealed photo into a
frame for handlers.py, which hands it to the `Engine`; only answers leave (A-07).

Status: opening is built and tested with a fake KMS. `KmstoolKeys` wraps AWS's kmstool_enclave_cli
(aws-nitro-enclaves-sdk-c), which sends the attestation document with the Decrypt call through the
parent's vsock proxy; it has not run in a Nitro Enclave yet.
"""

import base64
import binascii
import subprocess
from collections.abc import Callable
from typing import Protocol

import numpy as np

from backend.common.messages import Sealed
from backend.common.sealing import KEY_BYTES, NONCE_BYTES, TAG_BYTES, SealError, associated_data

KMSTOOL = "/app/kmstool_enclave_cli"  # where the enclave image puts it
KMSTOOL_TIMEOUT_S = 10  # one Decrypt through the vsock proxy; past it the step goes on the safe side


class KeyOpener(Protocol):
    """Decrypts a data key that KMS encrypted. Works only inside the attested enclave."""

    def open_key(self, wrapped: bytes) -> bytes: ...


class KmstoolKeys:
    """KMS Decrypt with the enclave's attestation, through kmstool_enclave_cli. `credentials` returns
    the temporary AWS credentials the parent hands over vsock: (access key id, secret, session token)."""

    def __init__(self, region: str, proxy_port: int, credentials: Callable[[], tuple[str, str, str]], tool=KMSTOOL):
        self.region = region
        self.proxy_port = proxy_port
        self.credentials = credentials
        self.tool = tool

    def open_key(self, wrapped: bytes) -> bytes:
        key_id, secret, token = self.credentials()
        # The temporary credentials go to kmstool as command-line arguments. That is fine only inside the
        # enclave, where nothing else runs and nobody can list processes; never copy this to the parent.
        cmd = [
            self.tool, "decrypt",
            "--region", self.region,
            "--proxy-port", str(self.proxy_port),
            "--aws-access-key-id", key_id,
            "--aws-secret-access-key", secret,
            "--aws-session-token", token,
            "--ciphertext", base64.b64encode(wrapped).decode(),
        ]  # fmt: skip
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=KMSTOOL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise SealError("KMS did not answer") from None
        except FileNotFoundError:
            raise SealError("kmstool is missing") from None
        except OSError:
            raise SealError("kmstool could not run") from None
        if out.returncode != 0:
            raise SealError("KMS did not open the data key for this enclave")
        for line in out.stdout.splitlines():
            if line.startswith("PLAINTEXT:"):
                return base64.b64decode(line.split(":", 1)[1].strip())
        raise SealError("kmstool gave no data key")


def _b64(field: str, value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise SealError(f"{field} is not base64") from None


def unseal(s: Sealed, keys: KeyOpener) -> bytes:
    """The JPEG bytes of a sealed photo. SealError if it was changed, moved to another id or size, or
    sealed for another key."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce, data, wrapped = _b64("nonce", s.nonce), _b64("data", s.data), _b64("key", s.key)
    if len(nonce) != NONCE_BYTES or len(data) <= TAG_BYTES or not wrapped:
        raise SealError("not a sealed photo")
    key = keys.open_key(wrapped)
    if len(key) != KEY_BYTES:
        raise SealError("the data key is not 256 bits")
    try:
        return AESGCM(key).decrypt(nonce, data, associated_data(s.photo_id, s.w, s.h))
    except InvalidTag:
        raise SealError("the sealed photo does not open: changed, moved or sealed for another key") from None
    finally:
        del key


def opener(keys: KeyOpener) -> Callable[[Sealed], np.ndarray]:
    """The `open_photo` of handlers.Handlers: a sealed photo -> uint8 BGR array of its `w` by `h`."""
    import cv2

    def open_photo(s: Sealed) -> np.ndarray:
        jpeg = unseal(s, keys)
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise SealError("the opened photo is not an image")
        if frame.shape[:2] != (s.h, s.w):
            raise SealError("the opened photo is not the size it was sealed with")
        return frame

    return open_photo
