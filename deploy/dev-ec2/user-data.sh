#!/bin/bash
# First boot of the dev instance (Amazon Linux 2023): installs the server and the hub from this repo's
# main branch, adds the demo home, enables them as services, then powers off. The launch sets shutdown
# behavior to "stop", so the instance ends stopped and only its disk is charged. Every later start pulls
# main, then runs the server on port 80 and the hub beside it.
set -euxo pipefail

APP=/opt/sweg9/backend-server
UV=/opt/sweg9/.local/bin/uv
EXTRAS="--frozen --extra server --extra hub --no-dev"

dnf install -y git
id sweg9 || useradd --system --create-home --home-dir /opt/sweg9 sweg9
sudo -u sweg9 -H bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u sweg9 -H git clone https://github.com/sweg9/backend-server.git "$APP"
sudo -u sweg9 -H bash -c "cd $APP && $UV sync $EXTRAS && $UV run --no-sync backend seed-demo"

# Secrets live only on the instance (C-10). HUB_TOKEN is made here; APP_PASSWORD_HASH is added over SSH.
install -d -m 750 -g sweg9 /etc/sweg9
printf 'HUB_TOKEN=%s\nDEMO_SCENARIOS=1\n' "$(openssl rand -hex 32)" >/etc/sweg9/backend.env
chgrp sweg9 /etc/sweg9/backend.env
chmod 640 /etc/sweg9/backend.env

# `sudo /usr/local/bin/sweg9-set-password` over `ssh -t`: asks for the app password, keeps only its hash.
cat >/usr/local/bin/sweg9-set-password <<'SCRIPT'
#!/bin/bash
set -euo pipefail
H=$(sudo -u sweg9 /opt/sweg9/backend-server/.venv/bin/backend hash-password)
sed -i "/^APP_PASSWORD_HASH=/d" /etc/sweg9/backend.env
printf "APP_PASSWORD_HASH='%s'\n" "$H" >> /etc/sweg9/backend.env
systemctl restart sweg9-hub
echo "Saved. The hub restarted with the new password."
SCRIPT
chmod 755 /usr/local/bin/sweg9-set-password

cat >/etc/systemd/system/sweg9-update.service <<UNIT
[Unit]
Description=Pull main of backend-server and sync its packages
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
User=sweg9
WorkingDirectory=$APP
ExecStart=/bin/bash -c 'git pull --ff-only && $UV sync $EXTRAS'
TimeoutStartSec=600
[Install]
WantedBy=multi-user.target
UNIT

cat >/etc/systemd/system/sweg9-server.service <<UNIT
[Unit]
Description=backend-server, server mode, on port 80
Wants=sweg9-update.service
After=sweg9-update.service
[Service]
User=sweg9
WorkingDirectory=$APP
EnvironmentFile=/etc/sweg9/backend.env
AmbientCapabilities=CAP_NET_BIND_SERVICE
ExecStart=$APP/.venv/bin/backend server --host 0.0.0.0 --port 80
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

cat >/etc/systemd/system/sweg9-hub.service <<UNIT
[Unit]
Description=backend-server, hub mode, with the demo home
Wants=sweg9-server.service
After=sweg9-server.service
[Service]
User=sweg9
WorkingDirectory=$APP
EnvironmentFile=/etc/sweg9/backend.env
ExecStart=$APP/.venv/bin/backend hub --stdout --server-url ws://127.0.0.1/ws/hub
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable sweg9-update sweg9-server sweg9-hub
shutdown -h now
