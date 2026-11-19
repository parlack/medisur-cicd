#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y nginx certbot python3-certbot-nginx acl
id medisur-webhook >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin medisur-webhook
install -d -m 755 /opt/medisur-webhook
install -m 644 "$root/server.py" /opt/medisur-webhook/server.py
install -d -m 700 /etc/medisur/secrets
if [[ ! -s /etc/medisur/secrets/github_webhook_secret ]]; then
    umask 077
    openssl rand -hex 32 > /etc/medisur/secrets/github_webhook_secret
fi
setfacl -m u:medisur-webhook:x /etc/medisur /etc/medisur/secrets
setfacl -m u:medisur-webhook:r /etc/medisur/secrets/github_webhook_secret
install -m 644 "$root/medisur-webhook.service" /etc/systemd/system/medisur-webhook.service
if [[ ! -f /etc/nginx/sites-available/medisur-webhook ]]; then
    install -m 644 "$root/nginx.conf" /etc/nginx/sites-available/medisur-webhook
fi
ln -sfn /etc/nginx/sites-available/medisur-webhook /etc/nginx/sites-enabled/medisur-webhook
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl enable --now medisur-webhook nginx
systemctl reload nginx
certbot --nginx -d medisur-sergio-cicd.eastus.cloudapp.azure.com --non-interactive --agree-tos --register-unsafely-without-email --redirect