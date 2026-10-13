# Deploying SEO changes through Cloudflare

This guide is for website owners who are not developers. It takes about 20 minutes, once per website. You need:

- a free Cloudflare account that already manages your website's domain (your domain's nameservers point to Cloudflare);
- a computer with [Node.js](https://nodejs.org) 20 or newer;
- your Search OS account (role: admin or owner), with the website's **ownership already verified** (Settings > Deploy).

## What this does (and does not do)

Search OS adds a small program, a *Worker*, to your Cloudflare account. It sits in front of your website. When a
visitor, Google, ChatGPT's or Claude's crawler asks for a page, the Worker takes the page your site normally
sends and changes only what you approved in Search OS: the title, the description, the canonical address, extra
structured data, image descriptions and internal links. Nothing runs in the visitor's browser (no tracking script),
so crawlers that do not run JavaScript see the changes too.

- Your website, your hosting and your content management system are **not** modified. Turn the Worker off and the site is exactly as before.
- If anything goes wrong inside the Worker (our server is down, the rules cannot be read, an error), the page is delivered **unchanged**. The Worker never breaks your site.
- The rules are **signed** with a secret only you and Search OS know. The Worker ignores rules that are unsigned, tampered with, or older than 24 hours.
- Search OS never gets access to your Cloudflare account. You do not give us an API token.

## Step 1 - Get your two values from Search OS

1. In Search OS open **Settings > Deploy**, pick your website, choose **Cloudflare**, and press **Create connection**.
2. Keep that page open. It shows:
   - **Site token** (not secret),
   - **Signing secret** (secret; treat it like a password),
   - **Rules address** (where the Worker will fetch its rules).

> The rules address must be reachable from the internet. If you run Search OS on your own computer it must be
> published (set `PUBLIC_API_URL` in the server settings); `localhost` does not work for Cloudflare.

## Step 2 - Get the Worker files

Download the `edge` folder of the Search OS project to your computer and open a terminal in it (on Windows: open the
folder, type `cmd` in the address bar and press Enter). Then run:

```
npm install -g pnpm
pnpm install
npx wrangler login
```

`wrangler login` opens your browser so you can allow it to use your Cloudflare account.

## Step 3 - Create the storage place for the rules

```
npx wrangler kv namespace create RULES
```

It prints a line with an `id = "..."`. Copy that id.

## Step 4 - Fill in `wrangler.toml`

Open `wrangler.toml` in Notepad and change:

| Setting | Put here |
|---|---|
| `routes` > `pattern` | your domain followed by `/*`, e.g. `example.com/*` |
| `routes` > `zone_name` | your domain, e.g. `example.com` |
| `kv_namespaces` > `id` | the id from step 3 |
| `API_BASE` | the part of the *Rules address* before `/edge/...` (e.g. `https://api.example.com`) |
| `SITE_TOKEN` | the *Site token* from Search OS |

Do **not** write the signing secret in this file. Run this instead and paste the secret when asked:

```
npx wrangler secret put SIGNING_SECRET
```

## Step 5 - Publish the Worker

```
npx wrangler deploy
```

When it finishes, your website is served through the Worker. Nothing changes on your pages yet, because there are no approved changes.

If your site is also reachable with `www.`, add a second route in `wrangler.toml` (`www.example.com/*`).

## Step 6 - Test the connection

Back in Search OS press **Test connection**. It opens your home page and looks for the Worker's marker. When it says
the Worker is installed, the connection becomes **Connected** and you can approve actions and press **Deploy approved**.

## Day to day

- Approved changes appear on your pages within about 5 minutes (the Worker keeps the rules for 5 minutes). Search OS then re-reads the page like a crawler does and shows **Verified**.
- Every change can be undone with **Roll back** (one change, or a whole batch). The page returns to exactly what it was.

## Emergency off switch

Either of these makes every page pass through unchanged:

- In Search OS: **Settings > Deploy > Turn the Worker off** (takes effect within 5 minutes).
- Immediately: in `wrangler.toml` set `SEARCHOS_DISABLED = "1"` and run `npx wrangler deploy`. Or delete the route in the Cloudflare dashboard (Workers & Pages > your Worker > Settings > Domains & Routes).

## Changing or rotating the secret

Press **Rotate secret** in Search OS, run `npx wrangler secret put SIGNING_SECRET` with the new value, then **Test connection** again. Until the Worker has the new secret it ignores the rules and your pages pass through unchanged.

## Troubleshooting

| Message in Search OS | What to check |
|---|---|
| "The SearchOS Worker was not seen on it" | The route in `wrangler.toml` must match your domain and be on the Cloudflare zone for it; run `npx wrangler deploy` again; the DNS record for your site must be "proxied" (orange cloud). |
| Test works but changes never show | The *Rules address* is not reachable from the internet, or `SITE_TOKEN` / `SIGNING_SECRET` differ from the ones in Search OS. Open the rules address in a browser: you should see JSON. |
| Verification says "failed" | The page was changed back by your site, or another plugin rewrites the same tag. Search OS rolls that change back automatically. |

## For developers

- Source: `edge/src` (TypeScript, `HTMLRewriter`), tests: `pnpm test` in `edge/` (runs inside the real Workers runtime).
- Request flow: origin response -> only `GET` + `200` + `text/html` -> rules from memory / KV (5 min) / API -> signature + 24 h check -> host and path lookup -> `HTMLRewriter`. Any other case returns the origin response (header `x-searchos-edge: pass|off|ready|error`).
- Page operations are defined in `backend/searchos/deploy/ops.py`; the Worker, the WordPress plugin and the GitHub editor implement the same list.
- Links are inserted by replacing the first exact occurrence of the anchor text inside text nodes of `p, li, td, dd, blockquote, figcaption` that are not inside a link, heading, navigation, code, form or script. If the text is not found nothing changes.
- A request carrying the query parameter `searchos_verify` (used by Search OS' verification) makes the Worker refresh rules older than 10 seconds instead of waiting for the 5-minute cache.
