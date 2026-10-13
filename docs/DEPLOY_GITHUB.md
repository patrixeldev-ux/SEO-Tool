# Deploying SEO changes through GitHub (pull requests)

For websites whose pages are **plain HTML files in a GitHub repository** (static sites). Search OS never changes your
live site directly and **never pushes to your main branch**: it opens a **pull request** with the edits. Nothing
changes on the site until someone merges it.

Pages that are produced by a framework or template (Next.js, Hugo, Jekyll, React ...) cannot be edited
automatically: Search OS recognises that, makes **no** edits, marks the action **Needs a developer**, and shows the
exact text for your developer.

## What you need

- the repository (`owner/name`, e.g. `acme/website`);
- a GitHub **fine-grained personal access token** limited to that one repository;
- your Search OS account (admin or owner), with the website's ownership verified (Settings > Deploy).

## Step 1 - Create the token

1. GitHub > your profile picture > **Settings** > **Developer settings** > **Personal access tokens** > **Fine-grained tokens** > **Generate new token**.
2. Name: `Search OS`. Expiration: your choice (the connection stops working when it expires; create a new one then).
3. **Repository access**: *Only select repositories* > pick your website repository.
4. **Permissions** > Repository permissions: **Contents: Read and write** and **Pull requests: Read and write**. Nothing else.
5. **Generate token** and copy it (shown once).

## Step 2 - Connect in Search OS

**Settings > Deploy** > your website > **GitHub**:

| Field | What to enter |
|---|---|
| Repository | `owner/name` |
| Token | the token from step 1 |
| Branch | leave empty to use the repository's default branch (changes are proposed *into* it as pull requests) |
| Folder with the HTML files | where the pages are, if not the top folder (e.g. `public` or `docs`) |
| Page-to-file rules | only when the pages are not at the usual place (see below) |

Press **Connect**. Search OS checks the token and the repository and reports the result.

### Where is a page's file?

By default `https://example.com/about` is looked for at `about.html` and `about/index.html` (inside the folder you gave), and `/` at `index.html`. Search OS edits a page only if **exactly one** of these files exists **and** it contains the page's current title / description text (otherwise the live page is built from somewhere else and editing the file would not change it).

If your files live elsewhere, add rules. `*` matches one folder name, `**` matches several; `{1}`, `{2}` ... insert what the stars matched:

| Page pattern | File |
|---|---|
| `/blog/*` | `content/blog/{1}.html` |
| `/docs/**` | `site/docs/{1}.html` |

## Day to day

1. Approve actions in Search OS and press **Deploy approved**. Search OS opens one pull request per batch (branch `searchos/<batch id>`), one commit per edited file. The action shows **Waiting for merge**.
2. Review and **merge** the pull request in GitHub as usual (your own checks and build run first).
3. Search OS notices the merge within about 10 minutes, waits for your site to be rebuilt and re-reads the page like a crawler (no JavaScript). When the change is in the HTML the action shows **Verified**.
4. **Roll back**: before the merge it closes the pull request and deletes the branch. After the merge it opens a second pull request that restores each file exactly as it was; merge it to undo. If someone edited the same file after the merge, Search OS does not guess: it tells you to undo by hand.

## Safety

- Writes happen only on branches named `searchos/...`; the default branch is never written to (this is enforced in code and covered by tests).
- The token is stored encrypted and is never shown again or written to logs. Delete it in GitHub to cut access at any time.
- Search OS edits `.html` files only, and only the title, meta description, canonical, structured data (JSON-LD), image text and inserted internal links of the page that was approved.

## Troubleshooting

| Message | What to do |
|---|---|
| "GitHub refused or could not find ..." | The token does not cover this repository or lacks a permission from step 1. |
| "The token can read this repository but not write" | Set *Contents* to **Read and write**. |
| Needs a developer: "No HTML file for this page was found" | The repository does not hold this page as an HTML file: add a rule, set the folder, or use the hand-off text. |
| Needs a developer: "built from somewhere else" | The file does not contain the live title/description: a template or build step produces the page. |
