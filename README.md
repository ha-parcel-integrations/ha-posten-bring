# Posten Bring Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-posten-bring.svg)](https://github.com/ha-parcel-integrations/ha-posten-bring/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

A custom Home Assistant integration that tracks your **Posten** or **Bring**
parcels via your own consumer account — both incoming and outgoing. No
tracking codes to enter: every parcel on your account shows up automatically.
Confirmed working for **Norway and Sweden**; Denmark is expected to work the
same way (same shared account backend) but not yet independently confirmed —
see [Troubleshooting](#troubleshooting).

Part of the [ha-parcel-integrations](https://github.com/ha-parcel-integrations) family: it publishes the same canonical parcel format, statuses and events as the other carrier integrations, so it plugs straight into the [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) and cross-carrier automations.

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
- Summary sensors: incoming parcels, next delivery, delivered parcels, outgoing parcels, outgoing delivered parcels, awaiting pickup
- Read-only **Deliveries** calendar with the expected delivery windows
- Events + device triggers for no-code automations (registered, status changed, delivered, delivery time changed — plus the outgoing pair)
- Opt-in per-parcel status history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2024.12 or newer
- Your own **Posten** or **Bring** consumer account (Vipps or phone-number login) — Posten Bring is active in **Norway, Sweden and Denmark** (confirmed for Norway and Sweden; Denmark is expected but not yet independently confirmed). Not Finland: Bring is no longer active there.
- A desktop browser with developer tools, to complete the one-time sign-in (see [Configuration](#configuration))

## Installation

### HACS (recommended)

1. In HACS, choose the three-dot menu → **Custom repositories**.
2. Add `https://github.com/ha-parcel-integrations/ha-posten-bring` as an **Integration**.
3. Install **Posten Bring** and restart Home Assistant.

### Manual

Copy `custom_components/posten_bring` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

Add the integration via **Settings → Devices & Services → Add Integration → Posten Bring**.

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
status-driven schedule (quiet overnight window, faster when a parcel is out
for delivery). See [CLAUDE.md](CLAUDE.md) for the details.

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

## Examples

Ready-to-paste automations live in [`examples/`](examples/).

## Debugging

```yaml
logger:
  logs:
    custom_components.posten_bring: debug
```

## Troubleshooting

- **The setup form never shows a working redirect.** Make sure you complete the sign-in in a desktop browser with developer tools open and "preserve log" enabled *before* submitting the login form — see [docs/finding-the-redirect-url.md](docs/finding-the-redirect-url.md).
- **"That doesn't look like a valid redirect URL".** Paste the complete address, including `?code=…&state=…`. The code is single-use and short-lived — if it's been a few minutes, redo the sign-in.
- **A status logs "Unrecognised Posten Bring status"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-posten-bring/issues/new) with the logged line so the mapping can be extended.
- **Using a Danish account.** Posten Bring's shared inbox has been confirmed to return Norwegian and Swedish accounts' own parcels; Danish coverage hasn't been independently tested yet. If it doesn't show your parcels, please [open an issue](https://github.com/ha-parcel-integrations/ha-posten-bring/issues/new) — that's exactly the report this needs.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://github.com/ha-parcel-integrations) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://github.com/ha-parcel-integrations) for the current list of supported carriers.

## Disclaimer

This integration uses the official Posten/Bring native-app OAuth client to read your own account's parcel inbox — the same credential pair the official Android app itself uses, embedded here with the project maintainer's explicit approval. It is not affiliated with, endorsed by, or supported by Posten Bring AS.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
