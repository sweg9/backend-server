FP = [0.0] * 383 + [1.0]  # a valid 384-number fingerprint


def sealed(pid="ph_h0001"):
    """A sealed photo as the hub sends it (wiki: Hub messages > Sealed photos)."""
    return {"photo_id": pid, "key": "AQID", "nonce": "BAUG", "data": "BwgJ", "w": 1280, "h": 960}
