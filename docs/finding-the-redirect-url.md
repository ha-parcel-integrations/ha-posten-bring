# Finding the `posten://…`/`bring://…` redirect URL

During setup, Posten Bring's sign-in page finishes by redirecting your
browser to a URL that starts with `posten://` (if you picked Posten) or
`bring://` (if you picked Bring). No desktop browser has an app registered
for that address, so **the sign-in will visibly fail or hang at the last
step** — you'll see a blank page, an error, or a spinner that never resolves.
That is expected, not a bug. Home Assistant needs that failed URL, not a
successful page load, so you have to catch it with your browser's developer
tools before it disappears.

This only has to be done once per login (or once per reauth, if it's ever
needed). The general idea is the same in every browser:

1. Open your browser's developer tools and switch to the **Network** tab
   *before* you finish signing in.
2. Turn on **"preserve log"** (the exact wording differs per browser — see
   below) so the failed request doesn't get cleared if the page reloads or
   redirects.
3. Complete the sign-in normally (Vipps or phone number, and any
   second-factor step you have enabled).
4. When the page fails to continue, look through the Network tab's request
   list for one whose **name/URL starts with `posten://` or `bring://`**
   (matching whichever brand you picked in Home Assistant).
5. Copy that request's **full URL** — not just its name as shown in the
   list, the complete address including everything after `?code=`.
6. Paste that whole address into the Home Assistant setup form.

The sections below show exactly where to click for each browser.

## Google Chrome

1. Press `F12` (Windows/Linux) or `Cmd+Option+I` (Mac) to open DevTools, or
   right-click anywhere on the page → **Inspect**.
2. Click the **Network** tab at the top of the DevTools panel.
3. Click the **circular record button** in the top-left of the Network panel
   if it isn't already red/active (it usually is by default).
4. Check the **"Preserve log"** checkbox, near the top of the Network panel.
5. Go back to the page and complete the sign-in.
6. When the flow stops/errors, look at the request list in the Network panel.
   The failing request's **Name** column shows the start of a URL — look for
   one beginning with `posten:` or `bring:`. You can also type that into the
   **Filter** box above the list to find it instantly.
7. Click that request to select it. In the panel that opens on the right,
   the **Headers** tab shows a **General** section with **Request URL** —
   that is the full address you need. Click it once to select the text (or
   use the small copy icon Chrome shows on hover) and copy it in full.

## Microsoft Edge

Edge uses the same Chromium DevTools as Chrome, so the steps are identical:

1. Press `F12` or right-click → **Inspect**.
2. Open the **Network** tab.
3. Enable **"Preserve log"**.
4. Complete the sign-in.
5. Filter for `posten` or `bring` in the request list, click the matching
   request.
6. Under **Headers → General → Request URL**, copy the full address.

## Mozilla Firefox

1. Press `F12` or right-click → **Inspect** (or **Inspect Element**).
2. Click the **Network** tab.
3. Click the **gear icon** (⚙) in the Network panel and enable
   **"Persist Logs"** — this is Firefox's equivalent of "preserve log".
4. Complete the sign-in.
5. Type `posten` or `bring` into the Network panel's filter box to find the
   request.
6. Click the request. In the details pane, the **Headers** tab shows the
   **URL** near the top — copy it in full.

## Safari

Safari's developer tools are hidden by default:

1. Open **Safari → Settings → Advanced** and turn on
   **"Show features for web developers"** (older Safari versions: enable the
   **Develop** menu the same way, worded slightly differently per version).
2. Open the **Develop** menu (in the menu bar) → **Show Web Inspector**, or
   press `Cmd+Option+I`.
3. Click the **Network** tab in the Web Inspector.
4. Look for a **"Preserve Log"** button/checkbox in the Network tab's toolbar
   and enable it.
5. Complete the sign-in.
6. Look through the request list for one starting with `posten:` or
   `bring:` — Safari's filter field at the top of the Network tab also
   accepts either as a search term.
7. Click the request and check the **Headers** section for the full request
   URL, then copy it.

## Mobile browsers (Chrome/Safari on a phone)

Mobile browsers don't have on-device developer tools with a Network tab.
Complete the sign-in on a **desktop/laptop browser** instead, using the steps
above — the redirect URL isn't tied to a specific device, so where you catch
it doesn't matter, only that Home Assistant is the one you paste it into
afterwards.

## Troubleshooting

- **I don't see any request starting with `posten:`/`bring:`.** Make sure
  "Preserve log"/"Persist Logs" was turned on *before* you submitted the
  sign-in form — otherwise the browser's normal page-navigation clears the
  network log the moment the redirect happens, and the failed request is
  gone before you can scroll to it.
- **The address I copied gets rejected by Home Assistant.** Paste the
  request URL exactly as shown, including the full query string
  (`?code=…&state=…`). A partial copy (e.g. missing the `code=` value) will
  not work. The code is single-use and short-lived — if it's been more than
  a couple of minutes, redo the sign-in and copy the new one rather than
  retrying the old value.
- **The request never appears at all, the page just shows a normal
  Posten/Bring error.** Confirm you're actually being redirected past the
  Vipps/phone-number step — an incomplete sign-in won't reach the
  `posten://`/`bring://` step at all, so there's nothing to catch yet.
