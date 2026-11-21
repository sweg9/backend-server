"""Seals each photo before it leaves the home: a new data key from KMS (GenerateDataKey), AES-256-GCM,
and the KMS-encrypted key sent along. Only the attested enclave can open it (C-05).

Not built yet."""
