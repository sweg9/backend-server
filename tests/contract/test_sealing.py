"""A photo sealed by the hub opens only in the enclave, and only as it was sealed (C-05; wiki: Hub
messages > Sealed photos). The hub and the enclave never import each other; this test uses both, with a
fake KMS in place of AWS."""

import base64
import os
import subprocess

import cv2
import numpy as np
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.common.ids import new_id
from backend.common.messages import Sealed
from backend.common.sealing import SealError
from backend.common.settings import EnclaveSettings, HubSettings, load
from backend.enclave.unseal import KmstoolKeys, opener, unseal
from backend.hub.seal import KmsDataKeys, data_keys, seal

W, H = 80, 60
ENCLAVE = load(EnclaveSettings, "enclave.yaml")
PID = new_id("ph")  # photo ids are ph_<ULID>


class FakeKms:
    """Wraps each data key under its own master key. Opens a key only for the attested enclave image,
    like the KMS key policy does."""

    def __init__(self, attested=True):
        self.master = AESGCM(os.urandom(32))
        self.attested = attested
        self.handed_out = 0

    def new_key(self):
        self.handed_out += 1
        key, nonce = os.urandom(32), os.urandom(12)
        return key, nonce + self.master.encrypt(nonce, key, b"kms")

    def open_key(self, wrapped):
        if not self.attested:
            raise SealError("KMS did not open the data key for this enclave")
        try:
            return self.master.decrypt(wrapped[:12], wrapped[12:], b"kms")
        except InvalidTag:
            raise SealError("KMS did not open the data key: another key") from None


def photo(w=W, h=H):
    frame = np.zeros((h, w, 3), np.uint8)
    frame[:, : w // 2] = (0, 0, 255)  # left half red, in BGR
    ok, jpeg = cv2.imencode(".jpg", frame)
    assert ok
    return jpeg.tobytes()


def sealed_photo(kms, pid=PID, w=W, h=H):
    return seal(photo(w, h), photo_id=pid, w=w, h=h, keys=kms)


def test_round_trip_hub_to_enclave():
    kms = FakeKms()
    s = sealed_photo(kms)
    frame = opener(kms)(s)
    assert frame.shape == (H, W, 3) and frame.dtype == np.uint8
    assert frame[H // 2, 5, 2] > 200 and frame[H // 2, W - 5, 2] < 50  # red on the left only


def test_what_leaves_the_home_is_only_ciphertext():
    kms = FakeKms()
    jpeg = photo()
    s = seal(jpeg, photo_id=PID, w=W, h=H, keys=kms)
    data = base64.b64decode(s.data)
    assert jpeg not in data and not data.startswith(b"\xff\xd8")
    assert "/9j/" not in s.model_dump_json()  # no base64 JPEG anywhere in the message


def test_every_photo_has_its_own_key_and_nonce():
    kms = FakeKms()
    a, b = sealed_photo(kms), sealed_photo(kms)
    assert kms.handed_out == 2
    assert a.key != b.key and a.nonce != b.nonce and a.data != b.data


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.model_copy(update={"photo_id": new_id("ph")}),  # moved under another id
        lambda s: s.model_copy(update={"w": W + 1}),  # claimed another size
        lambda s: s.model_copy(
            update={
                "data": base64.b64encode(
                    bytes([base64.b64decode(s.data)[0] ^ 1]) + base64.b64decode(s.data)[1:]
                ).decode()
            }
        ),
    ],
    ids=["other id", "other size", "changed bytes"],
)
def test_a_changed_or_moved_photo_does_not_open(change):
    kms = FakeKms()
    with pytest.raises(SealError, match="does not open"):
        unseal(change(sealed_photo(kms)), kms)


def test_another_key_or_another_enclave_image_cannot_open_it():
    with pytest.raises(SealError, match="another key"):
        unseal(sealed_photo(FakeKms()), FakeKms())
    kms = FakeKms()
    s = sealed_photo(kms)
    kms.attested = False  # an enclave image whose attestation the key policy does not allow
    with pytest.raises(SealError, match="did not open"):
        unseal(s, kms)


def test_the_size_must_match_the_opened_photo():
    kms = FakeKms()
    s = seal(photo(40, 30), photo_id=PID, w=W, h=H, keys=kms)  # sealed with a wrong size
    with pytest.raises(SealError, match="not the size"):
        opener(kms)(s)


def test_the_hub_seals_only_jpeg_and_only_with_a_256_bit_key():
    kms = FakeKms()
    with pytest.raises(SealError, match="not a JPEG"):
        seal(b"\x89PNG....", photo_id="p", w=W, h=H, keys=kms)

    class ShortKey:
        def new_key(self):
            return os.urandom(16), b"wrapped"

    with pytest.raises(SealError, match="256-bit"):
        seal(photo(), photo_id="p", w=W, h=H, keys=ShortKey())


def test_no_kms_key_means_no_photo_leaves_the_home():
    settings = load(HubSettings, "hub.yaml").model_copy(update={"kms_key_id": None})
    with pytest.raises(SealError, match="stay at home"):
        data_keys(settings)


def test_the_hub_asks_kms_for_an_aes_256_data_key():
    calls = []

    class Client:
        def generate_data_key(self, **kw):
            calls.append(kw)
            return {"Plaintext": b"k" * 32, "CiphertextBlob": b"wrapped"}

    settings = load(HubSettings, "hub.yaml").model_copy(update={"kms_key_id": "alias/nova-photos"})
    assert data_keys(settings, Client()).new_key() == (b"k" * 32, b"wrapped")
    assert calls == [{"KeyId": "alias/nova-photos", "KeySpec": "AES_256"}]
    assert isinstance(data_keys(settings, Client()), KmsDataKeys)


def test_unseal_refuses_what_is_not_a_sealed_photo():
    kms = FakeKms()
    good = sealed_photo(kms)
    for bad in [
        good.model_copy(update={"nonce": "!!"}),
        good.model_copy(update={"nonce": base64.b64encode(b"short").decode()}),
    ]:
        with pytest.raises(SealError):
            unseal(bad, kms)


def test_kmstool_reads_the_plain_key_and_reports_a_refusal(monkeypatch):
    seen = {}

    class Done:
        def __init__(self, code, out):
            self.returncode, self.stdout = code, out

    def run(cmd, **kw):
        seen["cmd"], seen["timeout"] = cmd, kw.get("timeout")
        return Done(0, "PLAINTEXT: " + base64.b64encode(b"k" * 32).decode() + "\n")

    monkeypatch.setattr("backend.enclave.unseal.subprocess.run", run)
    keys = KmstoolKeys("ap-northeast-2", 8000, lambda: ("AKIA", "secret", "token"), ENCLAVE.kmstool_timeout_s)
    assert keys.open_key(b"wrapped") == b"k" * 32
    assert seen["cmd"][1] == "decrypt" and "--ciphertext" in seen["cmd"]
    assert seen["timeout"] == ENCLAVE.kmstool_timeout_s  # C-12: from settings/enclave.yaml

    monkeypatch.setattr("backend.enclave.unseal.subprocess.run", lambda cmd, **kw: Done(1, ""))
    with pytest.raises(SealError, match="did not open"):
        keys.open_key(b"wrapped")


def test_sealed_is_the_hub_message_field():
    s = sealed_photo(FakeKms())
    assert Sealed.model_validate_json(s.model_dump_json()) == s


@pytest.mark.parametrize(
    ("raised", "message"),
    [
        (subprocess.TimeoutExpired(cmd="kmstool", timeout=10), "did not answer"),
        (FileNotFoundError("/app/kmstool_enclave_cli"), "kmstool is missing"),
        (PermissionError("not executable"), "could not run"),
    ],
    ids=["timeout", "no kmstool", "cannot run"],
)
def test_kmstool_failures_are_seal_errors(monkeypatch, raised, message):
    """The safe-side path relies on SealError: a slow KMS or a missing tool must not escape as another error."""

    def run(cmd, **kw):
        raise raised

    monkeypatch.setattr("backend.enclave.unseal.subprocess.run", run)
    keys = KmstoolKeys("ap-northeast-2", 8000, lambda: ("AKIA", "secret", "token"), ENCLAVE.kmstool_timeout_s)
    with pytest.raises(SealError, match=message):
        keys.open_key(b"wrapped")
