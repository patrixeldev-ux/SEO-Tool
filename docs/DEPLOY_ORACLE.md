# Put Search OS online on an Oracle Cloud free server

This guide takes you from "I have nothing" to "Search OS is running on the internet with HTTPS", without
needing a domain name. You need: an Oracle Cloud account, a Windows PC, and about 45 minutes (most of it waiting).

What you get: one small server (a virtual machine, "VM") that runs everything: the web app, the API, the
background worker, the database. The address will look like `https://152-67-1-2.sslip.io` (your IP address with
dashes). The padlock certificate is issued automatically and for free.

## 1. Create the server (Oracle Cloud console)

1. Sign in to <https://cloud.oracle.com>. Check which **home region** your account has (top right); the free
   server must be created there.
2. Open the menu (three lines, top left) > **Compute** > **Instances** > **Create instance**.
3. **Name:** `searchos`.
4. **Image and shape** > **Edit**:
   - **Image** > *Change image* > **Canonical Ubuntu** 24.04 (22.04 also works).
   - **Shape** > *Change shape* > choose **Ampere** > tick **VM.Standard.A1.Flex**. Set **2 OCPU** and
     **12 GB memory**. This is the largest size Oracle's documentation lists for the free allowance of a normal free
     account (Always Free: 2 OCPUs and 12 GB in total across A1 instances). The shape should show the
     *Always Free-eligible* label.
5. **Networking:** keep the defaults, and make sure **Automatically assign public IPv4 address** is selected.
6. **Add SSH keys:** choose **Generate a key pair for me** and click **Save private key**. Save the file in a safe
   place, for example `C:\Users\<you>\.ssh\oracle.key`. You cannot download it again. Without this file you cannot
   get into the server.
7. **Boot volume:** the default (about 50 GB) is fine.
8. Click **Create**. Wait until the status says **Running**. Copy the **Public IP address** from the instance
   page.

> If Oracle says **"Out of capacity for shape VM.Standard.A1.Flex"**, that is common for free servers. Try again a
> few hours later, or choose a different *availability domain* in the *Placement* section.

## 2. Open the web ports (Oracle security list)

Oracle blocks everything except SSH (port 22) until you allow it. This is the step people forget.

1. On the instance page, under **Primary VNIC**, click the **Subnet** link.
2. Click the **Default Security List for ...** (under *Security*).
3. Open the **Security rules** tab > **Add Ingress Rules**.
4. Fill in, then **Add Ingress Rules**:
   - **Source Type:** CIDR
   - **Source CIDR:** `0.0.0.0/0`
   - **IP Protocol:** TCP
   - **Destination Port Range:** `80`
5. Do it again for port `443`. (Port 22 should already be in the list.)

(The same screen is also reachable from the menu: **Networking** > **Virtual cloud networks** > your network >
**Security** tab > the security list.)

Search OS's installer also opens ports 80 and 443 inside the server's own firewall; the Oracle rule above is the
other half and can only be done in the console.

## 3. Install Search OS (from your Windows PC)

Open **PowerShell** (not as administrator), go to your copy of the repository and run:

```powershell
cd "D:\Patrixel Projects\SEO-Tool"
.\scripts\deploy-oracle.ps1 -Ip 152.67.1.2 -KeyPath "$env:USERPROFILE\.ssh\oracle.key"
```

Replace the IP and the key path. What happens: the script logs in to the server, installs Docker, opens the
firewall, adds a little swap memory if the server has less than 8 GB, downloads Search OS, creates your secret
settings file (`.env.prod`) with random passwords, builds everything and starts it. **The first run takes
10 to 20 minutes** on the free ARM server. Keep the window open. At the end it prints your address.

Good to know:

- If you see *"UNPROTECTED PRIVATE KEY FILE"*, Windows thinks other users can read your key. Run the two `icacls`
  commands the script prints, then run the script again.
- If you see a *timeout*, port 22/80/443 is not open: redo step 2, and check the instance is **Running**.
- The script can be run again at any time. It never overwrites your settings or your data.
- If the repository is **private**, add a GitHub token with read access to the code:
  `-GithubToken ghp_xxxxx` (create one at GitHub > Settings > Developer settings > Fine-grained tokens,
  repository access: only `seo-tool`, permission: *Contents: Read-only*).
- If another program already uses port 80 or 443 on the server (for example another project), the installer stops
  before changing anything and tells you. See "Sharing the VM with another project" below.

## 4. Create your account and sign in

Public sign-up is switched off on the server (so strangers cannot create accounts). Create yours with:

```powershell
ssh -i "$env:USERPROFILE\.ssh\oracle.key" -t ubuntu@152.67.1.2 "cd ~/seo-tool && deploy/oracle/create-admin.sh"
```

It asks for your email, a name for your organisation and a password (at least 12 characters; typing is hidden).
Then open `https://152-67-1-2.sslip.io/` (your IP with dashes) and sign in.

The first time you open the address, the certificate may take up to a minute to appear. A browser warning that
does not go away after a few minutes means port 80 or 443 is still blocked (step 2).

## 5. Optional: Google Search Console and AI keys

All keys live in the file `~/seo-tool/.env.prod` on the server. Edit it with:

```powershell
ssh -i "$env:USERPROFILE\.ssh\oracle.key" -t ubuntu@152.67.1.2 "nano ~/seo-tool/.env.prod"
```

(Save with `Ctrl+O`, `Enter`, exit with `Ctrl+X`.) Remove the `#` in front of a line to switch it on.

- **Google Search Console:** follow [GOOGLE_SETUP.md](GOOGLE_SETUP.md). In Google Cloud, the *Authorized redirect
  URI* is **`https://152-67-1-2.sslip.io/api/integrations/google/callback`** (your own address; note the
  `/api`). Then put `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env.prod`.
- **AI suggestions:** set `ANTHROPIC_API_KEY` (or `GEMINI_API_KEY` and `GEMINI_MODEL`).
- Apply changes: `cd ~/seo-tool && deploy/oracle/update.sh --no-pull`.

Never put `.env.prod` in an email or chat; it holds every secret. Keep a private copy of it (a password manager)
because the encryption key inside it is needed to read stored Google connections after a restore.

## 6. Everyday commands

Run these on the server (`ssh -i <key> ubuntu@<ip>`, then `cd ~/seo-tool`), or put them after the `ssh` command
as above.

| What | Command |
|---|---|
| Update to the latest version (backs up first, keeps all data) | `deploy/oracle/update.sh` |
| Apply `.env.prod` changes only | `deploy/oracle/update.sh --no-pull` |
| See logs (all, then just one: `caddy api worker beat postgres redis`) | `deploy/oracle/logs.sh` / `deploy/oracle/logs.sh api` |
| Back up the database now | `deploy/oracle/backup.sh` |
| Another user / organisation | `deploy/oracle/create-admin.sh` |
| Is everything running? | `docker compose --env-file .env.prod -f docker-compose.prod.yml ps` |

Or run the Windows script again to update: `.\scripts\deploy-oracle.ps1 -Ip ... -KeyPath ...` pulls the latest
code, but `update.sh` is the better choice because it makes a backup first.

### Backups

Backups go to `~/searchos-backups` (the newest 14 are kept). For a **daily backup**, run `crontab -e` on the server
and add this line (03:17 every night):

```
17 3 * * * /home/ubuntu/seo-tool/deploy/oracle/backup.sh >> /home/ubuntu/searchos-backups/backup.log 2>&1
```

These files live on the same server. Copy them off now and then (from your PC):
`scp -i "$env:USERPROFILE\.ssh\oracle.key" "ubuntu@152.67.1.2:searchos-backups/*.gz" .`

**Restore** (replaces the current data; use the file name you want):

```bash
cd ~/seo-tool
gunzip -c ~/searchos-backups/searchos-20261013-031700.sql.gz | docker compose --env-file .env.prod -f docker-compose.prod.yml exec -T postgres psql -U searchos searchos
```

For a restore into a brand-new empty database, run the installer first, then the command above.

## 7. Cost, limits and keeping the server

- Cost: **0** while you stay inside Oracle's *Always Free* allowance: A1 Ampere compute of 2 OCPU / 12 GB in total
  for a free account, 200 GB of disk in total (this setup uses about 50 GB), one reserved IP and 10 TB of traffic per month. Oracle
  can change these limits; check [Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm).
- **Idle servers can be reclaimed.** Oracle treats an instance as idle if, over 7 days, CPU (95th percentile),
  network and (for A1) memory use are all below 20%, and may stop and later delete it. Search OS uses real memory
  while crawling, but a quiet week can still look idle. Keep it in use (run audits regularly), or upgrade the
  account to *Pay As You Go*: you are only charged for things above the free allowance, and Oracle documents
  idle reclamation for Always Free accounts. Make backups regardless, and copy them off the server.
- **Do not delete the instance** to "restart" it: its IP address changes. Stopping and starting it is fine.
- Memory: the stack is capped as follows (all can be changed in `.env.prod`, see `.env.prod.example`):

| Part | Cap | Notes |
|---|---|---|
| worker (crawling, Chromium, 2 jobs at once) | 3 GB | raise to 6-8 GB on a 24 GB server |
| API (2 workers) | 1.5 GB | |
| Postgres | 1.5 GB | |
| Redis | 384 MB | job queue; set to never drop jobs |
| beat, Caddy | 384 MB + 256 MB | |

A server with 6 GB is enough to run it, 12 GB is comfortable, 24 GB leaves room for other projects. Logs rotate
automatically (3 files of 10 MB per part).

## 8. Move to your own domain later

1. Buy a domain and create an **A record** (`seo.yourdomain.com`) pointing to the server's IP address.
2. On the server, edit `.env.prod`: `DOMAIN=seo.yourdomain.com`.
3. `cd ~/seo-tool && deploy/oracle/update.sh --no-pull`. Caddy gets a new certificate for the new name.
4. In Google Cloud, change the redirect URI to `https://seo.yourdomain.com/api/integrations/google/callback`.
5. Everyone signs in again once (the sign-in cookie belongs to the old address). If you deployed Cloudflare
   rules, regenerate them in Settings > Deploy so they point to the new address.

## Sharing the VM with another project

The installer checks ports 80 and 443 first. If another project (for example Agency OS) already listens there,
nothing is changed and you get a message. Two ways forward:

- **Simplest:** stop the other project's web server (or move it to other ports), run the installer, then add the
  other project to Search OS's Caddy. In `deploy/caddy/Caddyfile`, add a block after the existing one, e.g.
  `other-152-67-1-2.sslip.io { reverse_proxy host.docker.internal:3000 }` (the port where the other project
  listens on the host; the container can reach it as `host.docker.internal`), and run `deploy/oracle/update.sh --no-pull`.
- **Or** keep the other project's web server in front, and send `/` and `/api` to Search OS's Caddy after you
  move Search OS to other ports (this needs a change to `docker-compose.prod.yml`; ask your developer).

Make sure the two projects together stay inside the VM's memory (see the table above).

## If something does not work

| Symptom | What to check |
|---|---|
| Browser says the connection timed out | Oracle security list has no ingress rule for 80/443 (step 2) |
| Certificate error that does not go away | Same cause; then `deploy/oracle/logs.sh caddy` |
| "Sign in" fails with a network error | `deploy/oracle/logs.sh api` |
| Installer says ports are in use | Another program runs on 80/443 (see above) |
| Installer: ".env.prod is missing but a database exists" | You deleted `.env.prod`. Restore it from your copy |
| After a reboot nothing runs | It starts by itself (`restart: unless-stopped`); wait 2 minutes, then check `ps` |
