"""vsock client to the Nitro Enclave (wiki: Modules > server).

Hands each request (`recognize`, `check`, `refind`, `people`, `decide`) to the enclave unopened and
returns its answer. The sealed photos are put in S3 on the way (store.py).

Not built yet: until it is, every request goes unanswered and the hub handles that on the safe side.
"""

from backend.common.messages import Envelope


class EnclaveUnavailable(Exception):
    pass


class VsockEnclave:
    def __init__(self, cid: int, port: int):
        self.cid = cid
        self.port = port

    async def ask(self, request: Envelope) -> Envelope:
        raise EnclaveUnavailable(f"the vsock link to CID {self.cid} port {self.port} is not built yet")
