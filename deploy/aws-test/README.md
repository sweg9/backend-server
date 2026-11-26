# A short test of the AWS design

**Ran on 2026-11-26 and worked end to end:** a photo from the laptop camera was sealed with KMS on the hub,
passed through the server unopened, opened only inside the Nitro Enclave (KMS checked its PCR0), and the
enclave's YOLO found the person at the laptop, so the safety gate cancelled the move (C-04, C-05, FR-10).
About 1 hour of m6i.xlarge, then everything deleted. The run found and fixed: uvloop cannot use vsock (the
server now runs on the standard asyncio loop), a KMS failure killed the hub's task (now the photo stays home),
an enclave image over 2 GiB, PATH inside the enclave, and memory for linuxkit next to the allocator.

One `m6i.xlarge` in Seoul for a few hours, deleted right after: the server pod on k3s, the enclave program in a
real Nitro Enclave over vsock, and a photo sealed on the laptop with KMS and opened only inside the enclave
(C-05). About $0.25 an hour while the instance runs; nothing is left afterwards. The full deployment
(domain, TLS, Dashboard, logging, the ml-engine pod) is `deploy/README.md`.

What the test leaves out: TLS and a domain (plain HTTP on port 80), the Dashboard, Loki and Grafana, the Nitro
Enclaves device plugin (the enclave runs from systemd on the host). The control detector and the effect check
have no weights in a repo yet, so the enclave loads only the people detector (COCO `yolo11s`); the other model
steps answer on the safe side.

```bash
deploy/aws-test/launch.sh            # KMS key, instance role, the instance; prints its address
deploy/aws-test/setup.sh <address>   # builds kmstool, the enclave image, the EIF and the server there (30-45 min),
                                     # then lets only that enclave's PCR0 Decrypt with the key
deploy/aws-test/hub-test.sh <address>   # in your own terminal: the hub on this laptop against the test server
deploy/aws-test/teardown.sh          # deletes the instance, the role and the key (the key after 7 days)
```

`hub-test.sh` asks for the HUB_TOKEN `setup.sh` printed and a password for the test app. Then, in another
terminal, sign in and send the robot somewhere: the safety gate before the move takes a photo with the laptop
camera, seals it with KMS, and the enclave opens it and looks for people (FR-10):

```bash
curl -si http://<address>/api/login -H 'content-type: application/json' -d '{"password":"..."}' | grep -i set-cookie
curl -s http://<address>/api/robot/route -H 'content-type: application/json' -H 'Cookie: session=...' \
  -d '{"stops":["living_wall_1"]}'
```

On the instance: `sudo nitro-cli describe-enclaves`, `kubectl -n sweg9 logs deploy/server`,
`sudo nitro-cli console --enclave-name ml-engine` works only in debug mode, which the test does not use (its PCRs
would be zeros and KMS would refuse).
