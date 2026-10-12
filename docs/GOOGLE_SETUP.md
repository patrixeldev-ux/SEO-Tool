# Connecting Google Search Console, PageSpeed and Bing

This guide is for someone who has never used Google Cloud. It takes about 15 minutes and you only do it once.
You need: a Google account that can see your website in [Search Console](https://search.google.com/search-console),
and Search OS running on your Windows PC (see `docs/LOCAL_TESTING.md`).

Nothing here costs money. Google asks for no credit card for these steps.

> Google changes its screens from time to time. The names below follow Google's and Microsoft's current screens as described in public guides
> (October 2026), but they could not be clicked through on a live account while writing this. If a button has
> moved, look for the same words nearby.

## Part 1. Create a Google Cloud project

1. Open [console.cloud.google.com](https://console.cloud.google.com) and sign in with the Google account that has access to your Search Console property.
2. If Google shows a welcome screen with terms of service, tick the box and click **Agree and continue**.
3. At the top left, click the **project selector** (it shows the name of the current project or "Select a project").
4. In the window that opens, click **New project**.
5. Type a name such as `Search OS` and click **Create**.
6. Wait a few seconds, then open the project selector again and **choose the new project**. Everything below must be done inside it.

## Part 2. Switch on the two Google services

1. In the left menu, open **APIs & Services**, then **Library**. (You can also type "API Library" in the search bar at the top.)
2. Type `Google Search Console API` in the search box, click the result, then click **Enable**.
3. Go back to **Library**, search for `PageSpeed Insights API`, click the result, then click **Enable**.

## Part 3. Set up the sign-in screen (OAuth consent screen)

This is the screen Google shows when you click "Connect Google Search Console" in Search OS. Only you will see it.

1. In the left menu, open **APIs & Services**, then **OAuth consent screen**. Google may call this area **Google Auth Platform**. Click **Get started**.
2. **App Information**: type `Search OS` as the app name, choose your email under **User support email**, then click **Next**.
3. **Audience**: choose **External**, then click **Next**.
4. **Contact Information**: type your email address, then click **Next**.
5. **Finish**: tick the box that agrees to the Google API Services User Data Policy, click **Continue**, then click **Create**.
6. In the left menu of the Google Auth Platform, click **Audience**. Under **Test users**, click **Add users**, type the Google email address you will sign in with, and click **Save**.
7. Optional: click **Data Access**, then **Add or remove scopes**, and add the scope `https://www.googleapis.com/auth/webmasters.readonly` (read-only Search Console access). If you skip this, Google still asks for the permission when you connect.

> **Important: the "Testing" status.** While the app is in **Testing**, Google makes the connection expire after
> 7 days, and Search OS will then show "Needs reconnecting". Reconnecting takes one click. To stop this, open
> **Audience** and click **Publish app**, then **Confirm**. For an app only you use, this is fine: when you connect,
> Google shows "Google hasn't verified this app". Click **Advanced**, then **Go to Search OS (unsafe)**. You are
> trusting your own app, so this is expected.

## Part 4. Create the sign-in key (OAuth client)

1. In the left menu of the Google Auth Platform, click **Clients**, then **Create client**. (In the older layout: **APIs & Services**, **Credentials**, **Create credentials**, **OAuth client ID**.)
2. For **Application type**, choose **Web application**.
3. For **Name**, type `Search OS local`.
4. Under **Authorized redirect URIs**, click **Add URI** and paste exactly this, with nothing before or after it:

   `http://localhost:8000/integrations/google/callback`

5. Leave **Authorized JavaScript origins** empty. Click **Create**.
6. A window titled **OAuth client created** shows a **Client ID** and a **Client secret**. Keep it open, or click **Download JSON**. Treat the secret like a password.

## Part 5. Give the keys to Search OS

1. Open the folder where Search OS lives (for example `D:\Patrixel Projects\SEO-Tool`).
2. Open the file named `.env` in Notepad. If it does not exist, copy `.env.example` and rename the copy to `.env`.
3. Add these two lines at the end, pasting your own values after the equals sign (no quotes, no spaces):

   `GOOGLE_CLIENT_ID=` then your Client ID, which ends in `.apps.googleusercontent.com`

   `GOOGLE_CLIENT_SECRET=` then your Client secret

4. Save the file.
5. Open PowerShell in the same folder and run `powershell -ExecutionPolicy Bypass -File scripts\dev-up.ps1`. This restarts everything with the new settings and opens the app.

You do not need to change anything else for local use. The redirect address and the app address already match the defaults.

## Part 6. Connect your site

1. In Search OS, open **Settings** and scroll to **Integrations**.
2. Under your site, click **Connect Google Search Console** and sign in with the Google account you added as a test user.
3. Click **Continue**, then **Allow** on the permission screen. This gives read-only access to Search Console.
4. You come back to Settings. Pick the Search Console property for this site (it suggests the best match) and click **Use this property**.
5. Search OS now imports up to 16 months of history in the background. A progress line shows how many days are stored. Open **Search Console** in the left menu to watch the charts fill in.

To remove access later, click **Disconnect** next to Google. Search OS asks Google to revoke the permission and deletes the stored login. Data already imported stays.

## Part 7 (optional). A PageSpeed Insights key

Search OS tests your 20 busiest pages for loading speed during each audit. This works without a key but Google then allows only a few tests per day. A free key raises the limit.

1. In the Google Cloud project, open **APIs & Services**, then **Credentials**.
2. Click **Create credentials**, then **API key**. Copy the key shown.
3. Optional but wise: click **Edit API key**, under **API restrictions** choose **Restrict key**, tick **PageSpeed Insights API**, and click **OK**, then **Save**.
4. Add this line to `.env`, then run `scripts\dev-up.ps1` again:

   `PSI_API_KEY=` then your key

## Part 8 (optional). Bing Webmaster Tools

Bing gives you a second source of search data and the AI Performance report.

1. Sign in at [bing.com/webmasters](https://www.bing.com/webmasters) and make sure your website is added and verified there.
2. Click **Settings** at the top right, then **API Access**.
3. The first time, read and accept the terms, then click **API Key**.
4. Click **Generate API Key** and copy it. One key works for all of your verified sites. If you lose it, delete it on the same page and generate a new one.
5. In Search OS, open **Settings**, **Integrations**, find your site, paste the key under **Bing Webmaster API key**, and click **Save and check key**. Search OS tests the key with Bing before keeping it, and stores it encrypted.

Bing reports weekly, not daily, so its numbers are shown by week.

## Part 9 (optional). The AI reports

Google's Search Console "generative AI" report and Bing's "AI Performance" report can only be downloaded as files. In
Search OS open **Search Console**, choose your site, then **AI reports**, and use **Upload CSV** under the matching report.
If a file is refused, the message lists the columns Search OS found. Send that message to whoever maintains Search OS.

## If something goes wrong

| What you see | What it means |
|---|---|
| "Google isn't set up yet" | `GOOGLE_CLIENT_ID` or `GOOGLE_CLIENT_SECRET` is missing in `.env`, or you did not restart with `scripts\dev-up.ps1`. |
| Google says `redirect_uri_mismatch` | The redirect address in Part 4 differs from `http://localhost:8000/integrations/google/callback`. Edit the client in **Clients** and fix it. |
| Google says "Access blocked" or "app not verified" and no Advanced link | Your email is not in **Test users** (Part 3, step 6). Add it, or publish the app. |
| Back in Search OS: "Google did not give long-term access" | Go to [myaccount.google.com/permissions](https://myaccount.google.com/permissions), remove Search OS, and connect again. |
| The property list is empty | The Google account you signed in with cannot see the website in Search Console. Add or verify the site there first. |
| "Needs reconnecting" | The permission expired or was removed (see the Testing note in Part 3). Click **Reconnect Google**. |
| Numbers differ a little from Search Console | Search Console's own totals include searches Google hides for privacy. Search OS charts use those totals, and its query tables cannot, so the tables add up to less. |
