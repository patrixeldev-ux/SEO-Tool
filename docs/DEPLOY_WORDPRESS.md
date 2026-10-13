# Deploying SEO changes to a WordPress site

For website owners who are not developers. About 10 minutes, once per website. You need:

- a WordPress site (WordPress 6.0 or newer, PHP 7.4 or newer) that opens with `https://`;
- a WordPress user with the **Editor** role or higher (an Administrator also works);
- your Search OS account (role: admin or owner), with the website's **ownership already verified** (Settings > Deploy).

## What this does (and does not do)

You install a small plugin, the *SearchOS Connector*. When you approve changes in Search OS and press
**Deploy approved**, Search OS sends them to the plugin, and the plugin prints them in the page's HTML on the
server: the **title**, the **meta description**, the **canonical address**, extra **structured data (JSON-LD)** and **image
descriptions (alt text)**. Search engines and AI crawlers that do not run JavaScript see the change.

- Your posts and pages are **not edited**. The plugin keeps each change next to the page (as a small private setting). Rolling back removes the setting and the page shows exactly what your theme or SEO plugin produced before.
- **Yoast SEO and Rank Math** are supported: their own title, description and canonical are replaced through their official filters, so no tag appears twice. Without an SEO plugin the connector prints the tags itself and removes duplicates.
- Search OS never sees your WordPress login password, only an **Application Password** that you can delete at any time.
- Internal links are not inserted through WordPress yet; those actions stay in the developer hand-off.

## Step 1 - Install the plugin

1. Get `searchos-connector.zip` (from your Search OS contact, or build it with `scripts\build-wp-plugin.ps1` on Windows or `scripts/build-wp-plugin.sh`).
2. In WordPress: **Plugins > Add New > Upload Plugin**, choose the zip, press **Install Now**, then **Activate**.

## Step 2 - Create an Application Password

1. In WordPress open **Users > Profile** (your own profile).
2. Scroll to **Application Passwords**, type `Search OS` as the name and press **Add New Application Password**.
3. Copy the password that is shown (like `abcd efgh ijkl mnop qrst uvwx`). It is shown only once.

> If you do not see "Application Passwords", your site is not on https, a security plugin disabled them, or the user lacks permission.

## Step 3 - Connect in Search OS

1. **Settings > Deploy**, pick your website, choose **WordPress**.
2. Enter the site address (`https://example.com`), your WordPress username and the Application Password. Press **Connect**.
3. Search OS tests the connection and shows the WordPress version and whether Yoast SEO or Rank Math was found.

## Day to day

- Approve actions, press **Deploy approved**. Search OS then reads the page the way a crawler does (no JavaScript, with a cache-busting address) and shows **Verified** when the change is in the HTML. If the change is **not** there after 2 minutes (for example a page cache plugin keeps serving the old copy), the change is rolled back automatically and you are told why. Purge your page cache and try again.
- **Roll back** undoes one change or a whole batch. The original title, description, canonical and image text come back.
- To stop everything at once: deactivate the plugin (all changes switch off immediately) or delete the Application Password.
- Deleting the plugin removes everything it stored.

## Things to know

- Page cache plugins (WP Rocket, LiteSpeed Cache, W3 Total Cache, a CDN) may keep serving old copies for a while. Clear the cache after deploying if verification fails.
- Images are changed when WordPress prints them (post content, featured images). Images drawn directly by a theme file are not.
- Only posts, pages and the home page can be changed. Archives, categories and custom listing pages are not supported yet.

## For developers

- Plugin: `wp-plugin/searchos-connector` (PHP, no dependencies). REST namespace `searchos/v1`:
  - `GET /status` - plugin version, WordPress version, detected SEO plugin, current user.
  - `GET /overrides?url=` - stored overrides of a page. `PUT /overrides` - body `{url, clear:[...], set:{title, meta_description, canonical}, jsonld:{put:[{id,value,replace}], remove:[ids]}, alt:{put:[{src,value}], remove:[src]}}`; `clear` is applied before `set`.
  - Authentication: Application Passwords (HTTP Basic); permission `edit_posts`, and `edit_post` for the page (`edit_others_pages` for the home page).
- Storage: post meta `_searchos_overrides` (JSON) on the post that `url_to_postid` resolves to; the home page uses the static front page's post, or the option `searchos_home_overrides`.
- Output: `pre_get_document_title`, `wp_title`, `wpseo_title`, `rank_math/frontend/title`; `wpseo_metadesc`, `rank_math/frontend/description`; `get_canonical_url`, `wpseo_canonical`, `rank_math/frontend/canonical`; `wp_head` is buffered only when needed (JSON-LD, or meta tags without an SEO plugin) and edited as text; images through `wp_get_attachment_image_attributes` and the `the_content` / `post_thumbnail_html` filters. JSON-LD replacement removes matching nodes of a `@graph` block only.
- Tests: CI job `wordpress` starts the official WordPress and MariaDB images, installs the plugin with wp-cli, and runs `backend/tests/test_deploy_wordpress_live.py` (deploy, verify raw HTML, rollback, original restored), once without an SEO plugin and once with a Yoast test double (`wp-plugin/tests/fake-seo-plugin.php`). On a machine without Docker: `wp-plugin/tests/local-wp.sh start`, then `eval "$(wp-plugin/tests/local-wp.sh env)"` and run the same pytest file.
