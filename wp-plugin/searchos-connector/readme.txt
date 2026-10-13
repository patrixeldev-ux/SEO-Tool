=== SearchOS Connector ===
Requires at least: 6.0
Requires PHP: 7.4
Stable tag: 0.1.0
License: Proprietary

Lets your Search OS account apply approved SEO changes to your pages on the server: the page title, meta
description, canonical address, structured data (JSON-LD) and image descriptions (alt text).

== Installation ==
1. Upload the searchos-connector folder to wp-content/plugins (or upload the zip in Plugins > Add New) and activate it.
2. Users > Profile > Application Passwords: type "Search OS" and press "Add New Application Password". Copy the password.
3. In Search OS open Settings > Deploy > WordPress and enter your site address, your username and that password.

== Notes ==
* Works with Yoast SEO and Rank Math (their own title / description / canonical are overridden through their filters, so no tag is repeated) and without any SEO plugin.
* Deactivating the plugin switches every change off at once. Deleting it removes everything it stored.
* Images are changed when WordPress prints them (post content, featured images). Images drawn by the theme itself are not.
