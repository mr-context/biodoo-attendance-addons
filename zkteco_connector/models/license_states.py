# -*- coding: utf-8 -*-
"""Rules tying a device's Odoo state to its licence slot on the bridge.

Kept free of any Odoo import so the rules can be unit-tested on their own.

`offline` is NOT a licence state: it only says the device has not been heard
from lately (`_cron_mark_offline`). A device that goes offline — power cut,
network down — still owns the slot the operator gave it. Forgetting that made
the 15-minute reconciliation drop offline devices from the list sent to the
bridge, which released their slot and, on a full licence, handed it to a
device waiting in `held`.
"""

# States in which a device owns a licence slot, or is queued for one. Every
# device in one of these states must be re-asserted to the bridge and listed
# in the declarative `sync`.
SLOT_STATES = ('approved', 'held', 'offline')


def state_after_verdict(current_state, granted):
    """Return the device state to store after a bridge verdict.

    - refused → `held`, whatever the device was doing;
    - granted → `approved`, except for a device currently `offline`: the
      verdict confirms its slot, it does not prove the device is reachable.
      Flipping it to `approved` would only have `_cron_mark_offline` put it
      back a minute later, on every reconciliation.
    """
    if not granted:
        return 'held'
    if current_state == 'offline':
        return 'offline'
    return 'approved'
