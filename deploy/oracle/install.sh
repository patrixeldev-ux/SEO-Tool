#!/usr/bin/env bash
# Search OS - one-command installer for an Oracle Cloud Always Free VM (Ubuntu 22.04 / 24.04, arm64 or x86_64).
#
#   bash install.sh [branch]            # default branch: main
#
# Run it as the normal 'ubuntu' user (it uses sudo). Safe to run again: every step checks first, and an existing
# .env.prod is never overwritten. Optional environment variables:
#   REPO_URL      git repository (default https://github.com/patrixeldev-ux/seo-tool.git)
#   INSTALL_DIR   where to put it (default ~/seo-tool)
#   DOMAIN        host name to serve (default <public-ip-with-dashes>.sslip.io)
#   PUBLIC_IP     skip public IP detection
#   GITHUB_TOKEN  read-only token, only needed if the repository is private
set -euo pipefail

BRANCH="${1:-main}"
REPO_URL="${REPO_URL:-https://github.com/patrixeldev-ux/seo-tool.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/seo-tool}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || die "sudo is required. Run as the 'ubuntu' user."
  SUDO="sudo"
  $SUDO -n true 2>/dev/null || $SUDO -v || die "This user cannot use sudo."
fi

docker_cmd() {
  if docker info >/dev/null 2>&1; then docker "$@"; else $SUDO docker "$@"; fi
}

# ---- 0. what are we running on ------------------------------------------------------------------------
command -v apt-get >/dev/null 2>&1 || die "This installer supports Ubuntu / Debian (apt) only."
ARCH="$(dpkg --print-architecture)"
case "$ARCH" in amd64 | arm64) ;; *) die "Unsupported CPU architecture: $ARCH (need amd64 or arm64)." ;; esac
MEM_KB="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
log "Machine: $ARCH, $((MEM_KB / 1024)) MB RAM"
if [ "$MEM_KB" -lt 3500000 ]; then
  warn "Less than 4 GB RAM. The free x86 micro shape is too small; use the Ampere A1 shape (VM.Standard.A1.Flex)."
fi

# ---- 1. ports 80 / 443 must be free (or already ours) -----------------------------------------------------
ours_running=0
if command -v docker >/dev/null 2>&1; then
  if [ -n "$(docker_cmd ps --filter "label=com.docker.compose.project=searchos" --filter "name=caddy" -q 2>/dev/null || true)" ]; then
    ours_running=1
  fi
fi
if [ "$ours_running" -eq 0 ]; then
  busy=""
  for port in 80 443; do
    if $SUDO ss -H -ltn "sport = :$port" 2>/dev/null | grep -q .; then busy="$busy $port"; fi
  done
  if [ -n "$busy" ]; then
    {
      echo
      echo "Port(s)$busy on this machine are already in use by another program (for example another project such as"
      echo "Agency OS, nginx or Apache). Nothing has been changed."
      echo
      echo "Who is using them:"
      $SUDO ss -ltnp "( sport = :80 or sport = :443 )" 2>/dev/null || true
      echo
      echo "Options: (1) stop that program, then run this installer again; (2) put Search OS behind the existing"
      echo "web server instead - see 'Sharing the VM with another project' in docs/DEPLOY_ORACLE.md."
    } >&2
    exit 1
  fi
fi

# ---- 2. base packages --------------------------------------------------------------------------------------
log "Installing base packages"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq ca-certificates curl git gnupg openssl iproute2 >/dev/null

# ---- 3. Docker Engine + compose plugin (Docker's own apt repository) -----------------------------------------
if docker_cmd compose version >/dev/null 2>&1; then
  log "Docker is already installed: $(docker_cmd --version)"
else
  log "Installing Docker Engine"
  # shellcheck disable=SC1091
  . /etc/os-release
  DOCKER_OS="$ID"
  case "$ID" in ubuntu | debian) ;; *) DOCKER_OS="ubuntu" ;; esac
  $SUDO install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$DOCKER_OS/gpg" | $SUDO gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$DOCKER_OS ${VERSION_CODENAME:-$UBUNTU_CODENAME} stable" |
    $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
  $SUDO systemctl enable --now docker
fi
if [ "$(id -u)" -ne 0 ] && ! id -nG | tr ' ' '\n' | grep -qx docker; then
  $SUDO usermod -aG docker "$USER" || true
  log "Added $USER to the docker group (takes effect the next time you log in)"
fi

# ---- 4. host firewall -------------------------------------------------------------------------------------
# Oracle's Ubuntu images ship an iptables INPUT chain that ends in a REJECT rule. Insert ACCEPT rules BEFORE it.
# Existing rules are never flushed.
open_ports() {
  local tool="$1" port pos
  command -v "$tool" >/dev/null 2>&1 || return 0
  $SUDO "$tool" -S INPUT >/dev/null 2>&1 || return 0
  for port in 80 443; do
    if $SUDO "$tool" -C INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null; then
      continue
    fi
    pos="$($SUDO "$tool" -L INPUT --line-numbers -n 2>/dev/null | awk '$2 == "REJECT" || $2 == "DROP" {print $1; exit}')"
    if [ -n "$pos" ]; then
      $SUDO "$tool" -I INPUT "$pos" -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT
    else
      $SUDO "$tool" -A INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT
    fi
  done
}
log "Opening ports 80 and 443 in the host firewall"
if ! dpkg -s iptables-persistent >/dev/null 2>&1; then
  echo iptables-persistent iptables-persistent/autosave_v4 boolean false | $SUDO debconf-set-selections
  echo iptables-persistent iptables-persistent/autosave_v6 boolean false | $SUDO debconf-set-selections
  $SUDO apt-get install -y -qq iptables-persistent netfilter-persistent >/dev/null
fi
open_ports iptables
open_ports ip6tables
$SUDO netfilter-persistent save >/dev/null 2>&1 || warn "Could not save firewall rules with netfilter-persistent"
if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
  $SUDO ufw allow 80/tcp >/dev/null
  $SUDO ufw allow 443/tcp >/dev/null
fi

# ---- 5. swap on small machines ---------------------------------------------------------------------------
if [ "$MEM_KB" -lt 7500000 ] && [ -z "$($SUDO swapon --show --noheadings 2>/dev/null)" ]; then
  if [ ! -e /swapfile ]; then
    log "Less than 8 GB RAM: adding 2 GB of swap"
    $SUDO fallocate -l 2G /swapfile 2>/dev/null || $SUDO dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    $SUDO chmod 600 /swapfile
    $SUDO mkswap /swapfile >/dev/null
  fi
  $SUDO swapon /swapfile 2>/dev/null || true
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
fi

# ---- 6. the code -------------------------------------------------------------------------------------------
git_auth=()
if [ -n "$GITHUB_TOKEN" ]; then
  git_auth=(-c "http.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 -w0)")
fi
if [ -d "$INSTALL_DIR/.git" ]; then
  log "Updating the code in $INSTALL_DIR (branch $BRANCH)"
  git -C "$INSTALL_DIR" "${git_auth[@]}" fetch --quiet origin "$BRANCH" ||
    die "Could not fetch $REPO_URL. If the repository is private, run again with GITHUB_TOKEN=<token>."
  git -C "$INSTALL_DIR" checkout --quiet "$BRANCH" 2>/dev/null || git -C "$INSTALL_DIR" checkout --quiet -b "$BRANCH" "origin/$BRANCH"
  git -C "$INSTALL_DIR" merge --ff-only --quiet "origin/$BRANCH" ||
    die "$INSTALL_DIR has local changes that conflict with the update. Fix or remove them, then run again."
else
  log "Downloading the code to $INSTALL_DIR (branch $BRANCH)"
  git "${git_auth[@]}" clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR" ||
    die "Could not clone $REPO_URL (branch $BRANCH). If the repository is private, run again with GITHUB_TOKEN=<token>."
fi
if [ -n "$GITHUB_TOKEN" ]; then
  # Remember the access for update.sh (stored in the repo's own config, readable only by this user).
  git -C "$INSTALL_DIR" config http.extraheader "AUTHORIZATION: basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 -w0)"
fi
cd "$INSTALL_DIR"
GIT_SHA="$(git rev-parse --short HEAD)"

# ---- 7. public address -------------------------------------------------------------------------------------
detect_ip() {
  local ip url
  for url in https://ifconfig.me https://api.ipify.org https://icanhazip.com; do
    ip="$(curl -fsS -m 10 "$url" 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then echo "$ip"; return 0; fi
  done
  # Oracle's own metadata service (public address attached to this VM's network card).
  ip="$(curl -fsS -m 5 -H 'Authorization: Bearer Oracle' http://169.254.169.254/opc/v2/vnics/ 2>/dev/null |
    grep -o '"publicIp"[^"]*"[0-9.]*"' | head -n 1 | grep -o '[0-9][0-9.]*' || true)"
  if [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then echo "$ip"; return 0; fi
  return 1
}

# ---- 8. settings (.env.prod) -------------------------------------------------------------------------------
ENV_FILE="$INSTALL_DIR/.env.prod"
if [ -f "$ENV_FILE" ]; then
  log "Keeping your existing .env.prod"
else
  if docker_cmd volume inspect searchos_pgdata >/dev/null 2>&1; then
    die ".env.prod is missing but a database from an earlier install exists. A new random database password would not match it. Restore .env.prod from your backup, or remove the old data with: docker volume rm searchos_pgdata"
  fi
  if [ -n "${DOMAIN:-}" ]; then
    HOST="$DOMAIN"
  else
    PUBLIC_IP="${PUBLIC_IP:-$(detect_ip || true)}"
    [ -n "$PUBLIC_IP" ] || die "Could not detect this machine's public IP address. Run again with PUBLIC_IP=<address> (or DOMAIN=<host name>)."
    HOST="${PUBLIC_IP//./-}.sslip.io"
  fi
  log "Creating .env.prod (host name: $HOST)"
  umask 077
  pg_password="$(openssl rand -hex 24)"
  jwt_secret="$(openssl rand -hex 32)"
  fernet_key="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"
  sed -e "s|^DOMAIN=.*|DOMAIN=$HOST|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$pg_password|" \
    -e "s|^JWT_SECRET=.*|JWT_SECRET=$jwt_secret|" \
    -e "s|^TOKEN_ENCRYPTION_KEY=.*|TOKEN_ENCRYPTION_KEY=$fernet_key|" \
    "$INSTALL_DIR/.env.prod.example" >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
  unset pg_password jwt_secret fernet_key
fi
for key in DOMAIN POSTGRES_PASSWORD JWT_SECRET TOKEN_ENCRYPTION_KEY; do
  grep -Eq "^$key=.+" "$ENV_FILE" || die "$key is empty in $ENV_FILE. Fill it in (see .env.prod.example) and run again."
done
HOST="$(grep -E '^DOMAIN=' "$ENV_FILE" | tail -n 1 | cut -d= -f2-)"

# ---- 9. build and start ------------------------------------------------------------------------------------
dc() { GIT_SHA="$GIT_SHA" docker_cmd compose --env-file "$ENV_FILE" -f "$INSTALL_DIR/docker-compose.prod.yml" "$@"; }
log "Building and starting Search OS (the first build takes 10-20 minutes on an A1 VM)"
dc up -d --build

log "Waiting for the API to become healthy"
waited=0
until [ "$(dc ps api --format '{{.Health}}' 2>/dev/null | head -n 1)" = "healthy" ]; do
  if [ "$waited" -ge 600 ]; then
    dc logs --tail=40 api >&2 || true
    die "The API did not become healthy in 10 minutes. See the log above, or run deploy/oracle/logs.sh api."
  fi
  sleep 5
  waited=$((waited + 5))
done

log "Waiting for the HTTPS certificate"
waited=0
until curl -fsS -m 10 -o /dev/null --resolve "$HOST:443:127.0.0.1" "https://$HOST/api/health" 2>/dev/null; do
  if [ "$waited" -ge 180 ]; then
    dc logs --tail=30 caddy >&2 || true
    warn "HTTPS is not answering yet. The usual causes: ports 80/443 are not open in the Oracle VCN security list, or the host name does not point to this machine. The site may still come up in a minute; check with deploy/oracle/logs.sh caddy"
    break
  fi
  sleep 5
  waited=$((waited + 5))
done

cat <<DONE

------------------------------------------------------------------------------
 Search OS is running:   https://$HOST/
------------------------------------------------------------------------------
 Next steps
  1. Create your admin account:
       cd $INSTALL_DIR && deploy/oracle/create-admin.sh
  2. Open https://$HOST/ and sign in.
  3. Google Search Console (optional): add this redirect URI in Google Cloud
       https://$HOST/api/integrations/google/callback
     and put GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in $ENV_FILE
  4. AI suggestions (optional): put ANTHROPIC_API_KEY (or GEMINI_API_KEY) in $ENV_FILE
     then apply with:  cd $INSTALL_DIR && deploy/oracle/update.sh --no-pull
  5. Daily backup (optional), add with 'crontab -e':
       17 3 * * * $INSTALL_DIR/deploy/oracle/backup.sh >> \$HOME/searchos-backups/backup.log 2>&1
 Update later:  cd $INSTALL_DIR && deploy/oracle/update.sh
 Logs:          cd $INSTALL_DIR && deploy/oracle/logs.sh
DONE
if ! id -nG | tr ' ' '\n' | grep -qx docker && [ "$(id -u)" -ne 0 ]; then
  echo " (Log out and back in once so that 'docker' works without sudo.)"
fi
