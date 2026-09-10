# Working in this repository

Home Assistant custom integration for **Posten Bring** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Structure, options flow, dynamic polling and module layout are suite-wide**
and identical in every carrier — the authoritative spec is
[`ha-carrier-template/scaffold/CLAUDE.md`](https://github.com/ha-parcel-integrations/ha-carrier-template/blob/main/scaffold/CLAUDE.md).
This repo follows it exactly.

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).
- **If this carrier can reach `ParcelStatus.AT_PICKUP_POINT` from a real raw
  status/code**, it needs an `awaiting_pickup` sensor — see *Parcel contract*
  in `CONVENTIONS.md`. Say "pickup point", not "ServicePoint"/"parcel
  shop"/"locker", for the generic concept. `ha-dhl-nl`, `ha-dpd`, `ha-gls`,
  `ha-inpost` are reference implementations; `posten_bring`'s `collectable`
  status reaches it too, so `PostenBringAwaitingPickupSensor` ships here from
  the first release (see *Carrier-specific notes* below).

## Carrier-specific notes

**API mechanics live in `carrier-research/posten-bring/api/` (private research
repo)** — the OAuth token endpoints, the `parcel-api/v1/parcel` inbox request
shape, pagination, and the status/event vocabularies. Do not duplicate them
here; this file is HA-integration decisions only.

**OAuth, not a form login.** The config flow builds an authorization URL
(`api.py`'s `build_authorization_url`) with a fresh, per-flow `state`, shows
it, and asks the user to paste back the complete `posten://…`/`bring://…`
redirect their browser could not open — the same browser-paste shape as
`ha-dhl`'s DE flow, adapted for this carrier's specifics:

- **No PKCE.** The live gate (research doc, 2026-09-01) confirmed the token
  endpoint rejects a public-client (no-secret) request regardless of PKCE,
  and the official app never sends `code_verifier`. Every token-endpoint
  call (initial exchange and refresh) sends `Authorization: Basic
  base64(client_id:client_secret)` instead, using the brand's own embedded
  pair (`const.py`'s `BRANDS`) — the maintainer has explicitly approved
  shipping this baked-in secret for this carrier only; do not extract this
  pattern into the template or reuse it for another carrier without an
  equivalent override.
- **Brand picker, not a country picker.** Posten and Bring are one backend
  API with two registered OAuth clients and two redirect schemes; the first
  config-flow step only picks which client/redirect scheme to use
  (`CONF_BRAND`), not a market — either brand's account works against the
  shared `api.posten.no` inbox regardless of the user's actual country.
- **No stable account identifier is available to key a `unique_id` on.** The
  token response carries only opaque `access_token`/`refresh_token`/
  `expires_in` fields — no ID token, and the build plan rules out any API
  call beyond the inbox (so no profile-endpoint lookup either). Unlike every
  other account-based carrier in the suite, this config flow does **not**
  call `async_set_unique_id`/`_abort_if_unique_id_configured` for new
  entries — a deliberate, documented gap: nothing stops a user from adding
  the same account twice. Device names disambiguate multiple entries of the
  *same* brand only by `entry.title`, which is just the brand name — a
  second same-brand account looks identical in the UI. If this API is ever
  found to expose a stable subject/profile identifier, revisit this. The same
  gap applies **across** brands, not just within one: Posten and Bring are two
  OAuth front doors onto the same shared inbox, so logging into both brands
  with the same underlying account produces two config entries polling
  identical data — every parcel doubled, not two different parcel sets. The
  `user` step's description warns against this; there is no technical way to
  detect or block it.
- **A rotated refresh token is persisted from inside the refresh itself.**
  Every refresh burns the token that was sent, and ordinary polling — not
  just reauth — rotates it. `PostenBringSession` therefore takes a
  `token_updater` callback (wired in `__init__.py` to
  `async_update_entry`) and calls it the moment a rotated token arrives,
  before the call it was refreshed for runs. Persisting after that call
  instead would leave the entry holding a burned token whenever anything in
  between failed, turning one transient 500 into a full browser-paste
  reauth on the next restart.
- **A 401/403 from the inbox is not an auth failure.** Only the token
  endpoint can raise `PostenBringAuthError`. `api.py` retries the inbox once
  after a forced refresh; if that refreshed token is *still* refused, the
  credential is demonstrably fine and the refusal is server-side, so it
  surfaces as `PostenBringApiError`. The coordinator retries those and only
  raises `ConfigEntryAuthFailed` after `MAX_UNAUTHORIZED_POLLS` consecutive
  ones — this carrier's reauth costs the user a devtools paste, so it must
  never be triggered by a blip.

**Status mapping is case-insensitive by design.** The wire format is
lowercase `snake_case` (`status: "archived"`), not the `SCREAMING_SNAKE_CASE`
the APK's enum member names implied — `parcels.py`'s `map_parcel_status`
lowercases every raw value before comparing. Never hardcode an uppercase
spelling anywhere in this codebase.

**No item-level `DELIVERED` or `RETURNING` — both are event-derived.**
`ARCHIVED`/`ARCHIVED_BY_USER` only means "left active tracking"; the mapping
walks `events[]` for the most recent (by timestamp, not list order) event
type: `delivered`/`delivered_bag_on_door` → `delivered`,
`return`/`delivered_sender` → `returning`, anything else → `unknown` plus a
one-shot warning naming the barcode. This mapping is unconfirmed until a real
payload shows an archived, delivered item — see the research doc.

**`out_for_delivery` and `problem` are unreachable from a real Posten Bring
status.** Per CONVENTIONS.md's status-vocabulary section, this is the
explicit exemption: the item-level vocabulary
(`notified`/`underway`/`collectable`/`return_underway`/`return_collectable`/
`archived[_by_user]`/`unknown`) has no mechanism that maps to either. This
carrier therefore never fires `parcel_status_changed` with
`new_status: out_for_delivery`, and the example automation
(`examples/automations/notify_when_ready_for_pickup.yaml`) triggers on
`at_pickup_point` instead, the closest "act on this today" equivalent.

**`awaiting_pickup` sensor is present** — `collectable` does reach
`ParcelStatus.AT_PICKUP_POINT` from a real raw status, so this carrier ships
the suite's standard sensor (`PostenBringAwaitingPickupSensor`), unlike
`out_for_delivery`/`problem` above.

**Incoming and outgoing come from the same inbox call.** Unlike `ha-dhl-nl`
(a second sent-shipments endpoint) this carrier's single
`POST parcel-api/v1/parcel` response already carries both directions via the
`direction` field (`receive`/`send`); `coordinator.py` splits one fetched
list into `data`/`delivered` (incoming) and `outgoing`/`delivered_outgoing`
rather than needing two coordinators. An unrecognised `direction` value
defaults to incoming with a one-shot warning rather than being dropped.
Outgoing events are deliberately narrower than incoming's — only
`outgoing_parcel_status_changed`/`_delivered`, no `registered`/
`delivery_time_changed` — matching the rest of the suite's account-based
outgoing model.

**`ProductName` is preserved raw, never validated.** A live capture returned
a value (`pickup_parcel_bulk`) outside the previously-assumed closed set —
treat it as free text everywhere, including diagnostics.

**Delivery window over date-only value.** `normalize_parcel` prefers
`delivery.deliveryTime.deliveryWindow.{start,end}` and only falls back to the
bare `delivery.deliveryTime.date` when no window is present — a date-only
value can be local midnight encoded as UTC.

**Public tracking `url`** points at the keyless `sporing.posten.no/sporing/
{parcelNumber}` page (never called by this integration itself, just linked)
rather than a Posten/Bring app deep link — no source confirms an app-scheme
URL, and the public tracker is confirmed reachable without a key.

**Target market is NO/SE/DK, not NO/SE/DK/FI.** Bring is no longer active in
Finland (maintainer decision, research doc 2026-09-01) — nothing in this
repo (strings, docs, manifest) should suggest FI support.

**Diagnostics redact the refresh token, the OAuth `code`/`state`, and every
carrier PII field** (`recipient`, `name`, `address`, `postalCode`, `city`,
`street`, `email`, `phone`, `alias`, `userChosenName`) — see
`diagnostics.py`'s `TO_REDACT`. `client_id`/`client_secret` are never placed
in any dict that reaches diagnostics in the first place, but are listed
defensively in case a future field ever nests one.

## Running tests

```
python -m pytest tests/ --cov=custom_components.posten_bring
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in your own private research notes, never in
this repo.
