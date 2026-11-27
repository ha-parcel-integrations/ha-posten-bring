# Posten Bring Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-posten-bring.svg)](https://github.com/ha-parcel-integrations/ha-posten-bring/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-posten-bring/total.svg)](https://github.com/ha-parcel-integrations/ha-posten-bring/releases)
[![Marketplace](https://img.shields.io/badge/Marketplace-Default-41BDF5.svg)](https://hacs.xyz)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 📦 Part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/), a family of package-tracking integrations for Home Assistant that all publish the same parcel format, statuses and events, so they plug into cross-carrier automations and the optional [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator). Browse the other carriers on the website.
>
> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your **Posten** or **Bring**
parcels via your own consumer account — both incoming and outgoing. No
tracking codes to enter: every parcel on your account shows up automatically.
Confirmed working for **Norway, Sweden and Denmark**.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Auto-discovers every parcel on your Posten/Bring account — incoming and outgoing, no tracking codes to enter
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `at_pickup_point` / `delivered` / `returning` / …), the carrier's own raw status, weight, dimensions, the expected delivery window and a public tracking link
- Summary sensors: incoming parcels, next delivery, delivered parcels, outgoing parcels, outgoing delivered parcels, awaiting pickup, en route to pickup point
- Read-only **Deliveries** calendar with the expected delivery windows
- Events + device triggers for no-code automations (registered, status changed, delivered, delivery time changed — plus the outgoing pair)
- Opt-in per-parcel status history
- A note on what is in a parcel, set with `posten_bring.update_contents`; it titles the calendar event and shows on the next-delivery sensor
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- Your own **Posten** or **Bring** consumer account (Vipps or phone-number login) — Posten Bring is active in **Norway, Sweden and Denmark**. Not Finland: Bring is no longer active there.
- A desktop browser with developer tools, to complete the one-time sign-in (see [Configuration](#configuration))

## Installation

### Marketplace (recommended)

1. Open the Marketplace and search for **Posten Bring**.
2. Install it and restart Home Assistant if it asks you to.

Or click the button below to open it directly in the Marketplace:

[![Open your Home Assistant instance and open a repository inside the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-parcel-integrations&repository=ha-posten-bring&category=integration)

### Manual

Copy `custom_components/posten_bring` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=posten_bring)

Or set it up by hand: add the integration via **Settings → Devices & Services → Add Integration → Posten Bring**.

1. Pick which brand's login you use — **Posten** or **Bring**. Either works for parcels in Norway, Sweden and Denmark alike; this only picks which login page opens. Don't add the integration twice with the other brand for the same account — both read the same inbox, so every parcel would show up twice.
2. Open the shown authorization URL in a browser and sign in (Vipps or phone number).
3. Your browser cannot open the address it's redirected to afterwards — that's expected. Copy that complete address from your browser's developer tools (or history) and paste it into the Home Assistant form. See [docs/finding-the-redirect-url.md](docs/finding-the-redirect-url.md) for a step-by-step guide per browser.

Home Assistant only stores the resulting refresh token — never your Vipps/phone credentials, and never the one-time authorization code.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensors (incoming and outgoing). |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet — in practice this tier never engages for Posten
  Bring, whose status vocabulary has no `out_for_delivery` state at all).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Never fully stops** — with nothing hot or in transit, polling keeps
  running at the normal cadence, since that's also how a new shipment on your
  account gets discovered.
- A small, fixed per-hub offset is added on top, so not every Posten Bring
  hub out there polls at exactly the same second.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Posten Bring → ⋮ → Delete**. This only removes the local refresh token; nothing is changed on your Posten/Bring account.

## Sensors

| Entity | Description |
|---|---|
| `sensor.posten_bring_<account>_incoming_parcels` | Number of active incoming parcels, full list under the `parcels` attribute |
| `sensor.posten_bring_<account>_parcel_<barcode>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.posten_bring_<account>_next_delivery` | Earliest expected delivery moment across all active incoming parcels |
| `sensor.posten_bring_<account>_delivered_parcels` | Recently delivered incoming parcels (see the retention option) |
| `sensor.posten_bring_<account>_outgoing_parcels` | Number of active outgoing parcels (`direction: send`) |
| `sensor.posten_bring_<account>_outgoing_delivered_parcels` | Recently delivered outgoing parcels |
| `sensor.posten_bring_<account>_awaiting_pickup` | Parcels ready for collection at a pickup point |
| `sensor.posten_bring_<account>_en_route_to_pickup_point` | Incoming parcels on their way to a pickup point that have not arrived yet |
| `calendar.posten_bring_<account>_deliveries` | Read-only calendar of expected deliveries, no extra API calls |
| `sensor.posten_bring_<account>_last_successful_update` | Diagnostic: when the account was last polled successfully |

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Announced, not yet handed to the carrier (`notified`) |
| `in_transit` | In the sorting network (`underway`) |
| `at_pickup_point` | Waiting for you at a pickup location (`collectable`) |
| `delivered` | Delivered — derived from the parcel's event history, since Posten Bring's own item status has no dedicated "delivered" value |
| `returning` | Going back to the sender (`return_underway` / `return_collectable`, or an archived parcel whose latest event is a return) |
| `unknown` | Not yet scanned, or a status we have not mapped yet |

`out_for_delivery` and `problem` are part of the shared enum but Posten
Bring's own status vocabulary has no mechanism that reaches either — this
integration never reports them.

The carrier's own raw status is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Posten Bring device):

| Event | When |
|---|---|
| `posten_bring_parcel_registered` | A new incoming parcel appears |
| `posten_bring_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `posten_bring_parcel_delivered` | A parcel is delivered |
| `posten_bring_parcel_delivery_time_changed` | The expected delivery window changes |
| `posten_bring_outgoing_parcel_status_changed` | An outgoing parcel's canonical status changes |
| `posten_bring_outgoing_parcel_delivered` | An outgoing parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `posten_bring.update_contents` | `tracking_code`, `contents`, `config_entry_id` (optional) | Set, replace or clear (empty text) a note on what is in a parcel |

Parcels come from your account, so there is no service to track one. With several accounts the note goes to every account that lists the code; set `config_entry_id` to pick one, which is also needed for a code no account lists yet.

The `contents` text is stored locally, never sent to Posten Bring, and fills the parcel's `contents` field; it titles the parcel's calendar event and shows on the next-delivery sensor.

## Examples

Ready-to-paste automations live in [`examples/`](examples/).

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/ha-parcel-card](https://github.com/jonisnet/ha-parcel-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.posten_bring: debug
```

## Troubleshooting

- **The setup form never shows a working redirect.** Make sure you complete the sign-in in a desktop browser with developer tools open and "preserve log" enabled *before* submitting the login form — see [docs/finding-the-redirect-url.md](docs/finding-the-redirect-url.md).
- **"That doesn't look like a valid redirect URL".** Paste the complete address, including `?code=…&state=…`. The code is single-use and short-lived — if it's been a few minutes, redo the sign-in.
- **A status logs "Unrecognised Posten Bring status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-posten-bring/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended.
- **The two-factor page says an SMS was sent, but none arrives.** Older versions of this integration opened an older Posten Bring login page whose SMS step fails for some accounts. Update the integration and start the setup again.
- **After the "Confirm e-mail address" step nothing seems to happen.** The sign-in has already finished — click **Continue** once and look for the redirect in the Network tab. A second click fails with "Authorization code has been issued before".

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Posten Bring, Home Assistant, or any other third party referenced in this project. Please don't contact Posten Bring for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to Posten Bring's terms. Data is sent only to Posten Bring's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration signs in to your own Posten/Bring account and reads your parcel inbox.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
