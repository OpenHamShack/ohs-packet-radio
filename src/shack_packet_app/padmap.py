"""The operator's own mapping for the AKP03E (Settings › Control pad): which
action each key, button and knob press runs, on top of
data/pad-layout.json. No Qt.

shack_pad has the mechanism (shack_pad.mapping, CONTROLLER-PAD.md §5): the
change is one entry, controllerMaps.packet.ajazz-akp03e, every stored value
is checked again on read, and one this version cannot use leaves that
control on its default instead of misfiring. This is the editor's side, as
NB's ohscore/padmap.py is, with two differences:

- **No transmit actions to place.** Packet declares no `ptt` or
  `transmit` (padactions.py), so the "PTT only on a picture key" rule NB
  needs has nothing to act on here.
- **Stored in this app's settings** (settings.PadSettings.maps), in the
  family's controllerMaps shape so it can move into the shared station
  profile unchanged.

**A reset is written as an empty entry (None), never by removing it.** The
station profile a controller syncs never deletes a field, so a removed
entry brings the old map back on the next load (GUIDE §5); shack_pad's own
`mapping.with_entry(..., None)` removes it, which is why `maps()` does not
use it for a reset.
"""
from __future__ import annotations

from . import padactions

MODEL = padactions.MODEL
KIND = padactions.KIND


def _lib():
    from shack_pad import actions, mapping
    from shack_pad.drivers import by_model
    return actions, mapping, by_model(MODEL)


class PadMap:
    """One mapping being edited. `maps` is controllerMaps as stored;
    nothing is written from here (maps() says what to store)."""

    def __init__(self, maps: dict | None):
        self._actions, self._mapping, self.drv = _lib()
        self.decl, self.layout = padactions.declaration()
        self.acts, _notes = self._actions.load(self.decl)
        self.stored_maps = dict(maps or {})
        self._entry = self._mapping.entry_of(self.stored_maps, KIND, MODEL) or None

    def rows(self) -> list[tuple[str, str]]:
        """(control, "press") for each key, button and knob press, in the
        pad's order. Knob turns are left out: no Packet action turns."""
        return [(c["id"], "press") for c in self.drv.controls]

    def page(self) -> dict:
        return self._mapping.resolve(self.drv, self.layout, self._entry, self.acts)[0][0]

    def current(self, cid: str, slot: str = "press") -> str:
        return self.page().get(cid, {}).get(slot) or "none"

    def choices(self, cid: str, slot: str = "press") -> list[str]:
        """Nothing first, then this app's actions in the declared order."""
        return ["none"] + [aid for aid, a in self.acts.items()
                           if a.kind == slot and not a.library and aid not in ("none", "none-turn")]

    def set(self, cid: str, aid: str, slot: str = "press") -> None:
        """ValueError (from the library) for anything it cannot use."""
        self._entry = self._mapping.set_binding(self.drv, self.layout, self._entry, self.acts, 0, cid, slot, aid)

    def reset(self) -> None:
        self._entry = None

    def is_default(self) -> bool:
        return self.page() == self._mapping.resolve(self.drv, self.layout, None, self.acts)[0][0]

    def maps(self) -> dict:
        """controllerMaps to store: this app's entry replaced and every
        other app's left as it was. Back to the defaults is an explicit
        None (see the module's note), not a missing entry."""
        out = {k: dict(v) for k, v in self.stored_maps.items() if isinstance(v, dict)}
        entry = None if self._entry is None or self.is_default() else self._entry
        out.setdefault(KIND, {})[MODEL] = entry
        return out
