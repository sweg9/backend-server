# Dev instance on AWS

One `t3.small` in Seoul that runs the server and the hub side by side with the demo home, so the web app
can be developed against the real API before the hub at home and the enclave are ready. It is not the
deployment of the design (that is `deploy/README.md` and `deploy/k8s`). Kept **stopped** when nobody
uses it: then only its 12 GiB disk is charged.

```bash
deploy/dev-ec2/launch.sh          # once: create it; it sets itself up and ends stopped
deploy/dev-ec2/dev.sh start       # prints the API address and the SSH command
deploy/dev-ec2/dev.sh stop
```

Every start pulls `main` and syncs the packages before the services start, so a new start runs the newest code.

## Once, after the first start: the app password

The hub refuses every sign-in until it has APP_PASSWORD_HASH (C-10: it lives only on the instance).

```bash
ssh -i ~/Downloads/NOVA.pem ec2-user@<address>
sudo -u sweg9 /opt/sweg9/backend-server/.venv/bin/backend hash-password      # type the team's password
echo "APP_PASSWORD_HASH='<the printed hash>'" | sudo tee -a /etc/sweg9/backend.env >/dev/null
sudo systemctl restart sweg9-hub
journalctl -u sweg9-hub -u sweg9-server -f                                    # logs
```

## Using it from the web app

Point the dev server's proxy at `http://<address>` (Vite: `server.proxy` sends `/api` there and strips
`/api`). Going through the proxy keeps the `Secure`, `SameSite=Strict` session cookie on `localhost`.
SSH is open only to the owner's address in the security group `NOVA-sg`; port 80 is open to all and
every route but `/login` needs a session.

## Starting over and tearing down

- Demo data from scratch: `sudo systemctl stop sweg9-hub && sudo -u sweg9 rm /opt/sweg9/backend-server/data/hub/app.db`, then `cd /opt/sweg9/backend-server && sudo -u sweg9 -H /opt/sweg9/.local/bin/uv run --no-sync backend seed-demo && sudo systemctl start sweg9-hub`.
- After the presentation: terminate the instance (its disk goes with it) and delete `NOVA-sg`.
