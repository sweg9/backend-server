"""The format of a sealed photo, shared by the hub that seals it and the enclave that opens it (C-05;
wiki: Hub messages > Sealed photos). The hub and the enclave never import each other, so what both
sides must agree on is here.

- Each photo has its own 256-bit data key from KMS (GenerateDataKey). `Sealed.key` is that key as KMS
  encrypted it; only an enclave whose attestation matches the published image can have KMS decrypt it.
- The photo is encrypted with AES-256-GCM under a random 96-bit nonce. Never reuse a nonce with a key;
  a new key per photo makes that sure.
- The associated data binds the ciphertext to its `photo_id`, `w` and `h`: a sealed photo moved under
  another id or size does not open.
"""

KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # 96 bits, the GCM standard size
TAG_BYTES = 16  # appended to the ciphertext by AES-GCM
FORMAT = "nova-seal-v1"  # changes when this format does; part of the associated data


def associated_data(photo_id: str, w: int, h: int) -> bytes:
    """What the GCM tag also covers, in the same bytes on both sides."""
    return f"{FORMAT}|{photo_id}|{w}x{h}".encode()


class SealError(Exception):
    """A photo could not be sealed or opened. The message names the reason, never the photo."""
