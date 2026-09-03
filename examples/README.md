# Examples

Ready-to-paste Home Assistant snippets for the Posten Bring integration.

| Folder | Contents |
|---|---|
| [`automations/`](automations/) | YAML automations — copy them into your `automations.yaml` or paste them into the Automation editor in **raw editor** mode. |

Parcels come from your Posten/Bring account, so there is nothing to register
by hand: whatever the account knows about shows up automatically, both
incoming and outgoing.

All examples assume a single Posten Bring account. Adjust entity IDs to match
yours; with more than one account configured, every entity ID carries the
account name.

## Events used in the examples

The coordinator fires these on the HA event bus:

| Event | When | Payload |
|---|---|---|
| `posten_bring_parcel_registered` | A new incoming parcel appears | The full normalised parcel dict |
| `posten_bring_parcel_status_changed` | A parcel's canonical status changes | Same, plus `old_status` / `new_status` |
| `posten_bring_parcel_delivered` | A parcel reaches the delivered status | Same (fires *instead of* `status_changed` on that final hop) |
| `posten_bring_parcel_delivery_time_changed` | A parcel's expected delivery time changes | Same, plus `old_planned_from` / `new_planned_from` / `old_planned_to` / `new_planned_to` |
| `posten_bring_outgoing_parcel_status_changed` | An outgoing parcel's canonical status changes | Same, plus `old_status` / `new_status` |
| `posten_bring_outgoing_parcel_delivered` | An outgoing parcel is delivered | The full normalised parcel dict |

Every payload also carries the account's `device_id`, which is what device
triggers filter on. Events are suppressed on the first refresh after start-up.
