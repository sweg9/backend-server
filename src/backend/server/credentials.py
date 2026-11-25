"""The temporary AWS credentials the server hands the enclave for KMS Decrypt (C-05, C-10).

On EC2 they come from the instance role through IMDSv2 (the instance needs a hop limit of 2 for a
pod to reach it). They are kept only in memory and never logged. Outside AWS there are none, and
sealed photos then do not open in the enclave: the hub handles that on the safe side.
"""

import asyncio
import json
import urllib.request
from datetime import UTC, datetime, timedelta

from backend.common.link import Credentials

IMDS = "http://169.254.169.254"


class NoCredentials:
    async def get(self) -> Credentials | None:
        return None


class InstanceRole:
    """IMDSv2: a session token, then the role's credentials. A new set is fetched once fewer than
    `refresh_before_s` seconds are left."""

    def __init__(self, refresh_before_s: float, timeout_s: float = 2, base: str = IMDS):
        self.refresh_before = timedelta(seconds=refresh_before_s)
        self.timeout_s = timeout_s
        self.base = base
        self._cached: Credentials | None = None

    def stale(self, c: Credentials | None) -> bool:
        return c is None or c.expires_at - datetime.now(UTC) < self.refresh_before

    async def get(self) -> Credentials | None:
        if self.stale(self._cached):
            self._cached = await asyncio.to_thread(self._fetch)
        return self._cached

    def _call(self, path: str, method: str = "GET", headers: dict[str, str] | None = None) -> str:
        req = urllib.request.Request(self.base + path, method=method, headers=headers or {})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:  # noqa: S310 (fixed http URL)
            return r.read().decode()

    def _fetch(self) -> Credentials:
        token = self._call("/latest/api/token", "PUT", {"X-aws-ec2-metadata-token-ttl-seconds": "21600"})
        h = {"X-aws-ec2-metadata-token": token}
        role = self._call("/latest/meta-data/iam/security-credentials/", headers=h).split()[0]
        c = json.loads(self._call(f"/latest/meta-data/iam/security-credentials/{role}", headers=h))
        return Credentials(
            access_key_id=c["AccessKeyId"],
            secret_access_key=c["SecretAccessKey"],
            session_token=c["Token"],
            expires_at=datetime.fromisoformat(c["Expiration"].replace("Z", "+00:00")),
        )
