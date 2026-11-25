"""Seals each photo before it leaves the home: a new data key from KMS (GenerateDataKey), AES-256-GCM,
and the KMS-encrypted key sent along. Only the attested enclave opens it (C-05; wiki: Hub messages >
Sealed photos). The format both sides share is in common/sealing.py.

The hub never sends a photo unsealed: without `kms_key_id` in settings/hub.yaml, `data_keys` refuses,
and the step that needed the photo goes on the safe side.

Status: sealing is built and tested with a fake KMS. `KmsDataKeys` (boto3) has not run against an AWS
account yet. The Reader (camera.py) that calls `seal` is not built.
"""

import base64
import os
from typing import Any, Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.common.messages import Sealed
from backend.common.sealing import KEY_BYTES, NONCE_BYTES, SealError, associated_data
from backend.common.settings import HubSettings

JPEG_START = b"\xff\xd8"


class DataKeys(Protocol):
    """Hands out a new data key per photo: (the key, the key as KMS encrypted it)."""

    def new_key(self) -> tuple[bytes, bytes]: ...


class KmsDataKeys:
    """AWS KMS GenerateDataKey with the key named in settings (`kms_key_id`). The key policy lets only
    the attested enclave image decrypt what this returns."""

    def __init__(self, key_id: str, client: Any = None):
        if client is None:
            import boto3  # only the hub on a real home network needs it

            client = boto3.client("kms")
        self.key_id = key_id
        self.client = client

    def new_key(self) -> tuple[bytes, bytes]:
        r = self.client.generate_data_key(KeyId=self.key_id, KeySpec="AES_256")
        return r["Plaintext"], r["CiphertextBlob"]


def data_keys(settings: HubSettings, client: Any = None) -> KmsDataKeys:
    """The hub's data keys, or SealError while no KMS key is set: then no photo leaves the home (C-05)."""
    if not settings.kms_key_id:
        raise SealError("kms_key_id is not set in settings/hub.yaml, so photos stay at home")
    return KmsDataKeys(settings.kms_key_id, client)


def seal(jpeg: bytes, *, photo_id: str, w: int, h: int, keys: DataKeys) -> Sealed:
    """One JPEG photo, `w` by `h` px, as the `sealed` field of a hub message."""
    if not jpeg.startswith(JPEG_START):
        raise SealError("not a JPEG photo")
    key, wrapped = keys.new_key()
    if len(key) != KEY_BYTES or not wrapped:
        raise SealError("KMS gave no 256-bit data key")
    nonce = os.urandom(NONCE_BYTES)
    data = AESGCM(key).encrypt(nonce, jpeg, associated_data(photo_id, w, h))
    del key  # the plain key is not kept; Python cannot wipe it, so it is only dropped
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return Sealed(photo_id=photo_id, key=b64(wrapped), nonce=b64(nonce), data=b64(data), w=w, h=h)
