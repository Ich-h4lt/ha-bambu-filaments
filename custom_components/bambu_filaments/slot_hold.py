"""Keep the slot binding of a manual spool when the cloud drops it.

Bambu's cloud sometimes clears the slot binding of spools without RFID
although nothing happened at the printer - observed live on 2026-10-21: an
AMS backup switch (slot 4 ran out, the print continued from slot 1) and
about 45 minutes later both bindings of that backup pair were gone from the
library while slot 1 still held the same spool. The library cannot tell a
lost binding from a deliberate unassign (no source, `updatedAt` unchanged),
so the printer side decides: ha-bambulab reports per AMS slot whether it is
empty and which profile, material and color is loaded.

A binding is remembered together with that slot fingerprint while the cloud
reports it. When the cloud drops it, it is kept (and flagged) as long as the
slot shows the same fingerprint and was not emptied in between. It is
released when the slot is emptied, its profile/material/color changes, the
cloud binds another spool to that slot or binds this spool elsewhere, or the
spool is deleted. Nothing is written to the cloud; the held binding is put
into the coordinator data, so location, card and usage booking use it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import logging
from typing import Any, Protocol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

# Slot states as derived from the ha-bambulab tray sensor.
SLOT_LOADED = "loaded"
SLOT_EMPTY = "empty"
SLOT_UNKNOWN = "unknown"  # "?" while the AMS reads a freshly inserted spool
SLOT_UNAVAILABLE = "unavailable"  # printer offline, HA start, integration down

EXTERNAL_SLOT_ID = "255"

Fingerprint = tuple[str, str, str]


class SlotLike(Protocol):
    """What reconcile() needs from a slot reading (see ams_register.SlotReading)."""

    status: str

    @property
    def fingerprint(self) -> Fingerprint: ...


def slot_key(dev_id: Any, ams_id: Any, slot_id: Any) -> str:
    """Printer serial (= cloud devId), AMS index and 0-based slot as one key."""
    return f"{dev_id}|{ams_id}|{slot_id}"


def make_fingerprint(filament_id: Any, material: Any, color: Any) -> Fingerprint:
    """Profile, material and 6-digit color - what identifies a manual spool in a slot."""
    hex6 = str(color or "").strip().lstrip("#").upper()[:6]
    return (str(filament_id or ""), str(material or "").upper(), hex6)


@dataclass
class HeldBinding:
    """A slot binding as last reported by the cloud, plus the slot fingerprint."""

    spool_id: int
    dev_id: str
    ams_id: int
    slot_id: str
    ams_type: Any = None
    device_name: str | None = None
    ams_sn: str | None = None
    fingerprint: Fingerprint | None = None
    # ISO timestamp once the cloud stopped reporting the binding.
    lost_since: str | None = None

    @property
    def key(self) -> str:
        return slot_key(self.dev_id, self.ams_id, self.slot_id)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fingerprint"] = list(self.fingerprint) if self.fingerprint else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HeldBinding | None:
        try:
            fp = data.get("fingerprint")
            return cls(
                spool_id=int(data["spool_id"]),
                dev_id=str(data["dev_id"]),
                ams_id=int(data["ams_id"]),
                slot_id=str(data["slot_id"]),
                ams_type=data.get("ams_type"),
                device_name=data.get("device_name"),
                ams_sn=data.get("ams_sn"),
                fingerprint=tuple(fp) if isinstance(fp, list) and len(fp) == 3 else None,
                lost_since=data.get("lost_since"),
            )
        except (KeyError, TypeError, ValueError):
            return None


def cloud_slot_bindings(spools: dict[int, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Manual spools the cloud currently reports in an AMS slot, by slot key.

    RFID spools are left out (the AMS identifies those itself), as is the
    external spool holder (no AMS slot sensor to compare with).
    """
    bound: dict[str, dict[str, Any]] = {}
    for spool in spools.values():
        if spool.get("RFID") or not spool.get("inPrinter") or not spool.get("devId"):
            continue
        slot_id, ams_id = spool.get("slotId"), spool.get("amsId")
        if slot_id in (None, "") or ams_id is None:
            continue
        if str(slot_id) == EXTERNAL_SLOT_ID or spool.get("amsType") == 0:
            continue
        try:
            key = slot_key(spool["devId"], int(ams_id), str(int(slot_id)))
        except (TypeError, ValueError):
            continue
        bound[key] = spool
    return bound


def reconcile(
    held: dict[str, HeldBinding],
    spools: dict[int, dict[str, Any]],
    slots: dict[str, SlotLike],
    emptied: set[str],
    now: str,
) -> tuple[dict[str, HeldBinding], list[tuple[str, HeldBinding, str]]]:
    """New set of held bindings plus (event, binding, detail) for logging.

    `held` is the previous result, `spools` the fresh cloud data, `slots` the
    current AMS slot readings by key and `emptied` the slot keys that
    reported empty at any moment since the previous run.
    Events: "lost", "released", "restored", "replaced".
    """
    result: dict[str, HeldBinding] = {}
    events: list[tuple[str, HeldBinding, str]] = []
    cloud = cloud_slot_bindings(spools)
    cloud_ids = {int(s["id"]): key for key, s in cloud.items()}

    # The cloud is authoritative while it reports a binding: follow it and
    # keep the slot fingerprint current (a profile change right before or
    # after the assignment is normal).
    for key, spool in cloud.items():
        prev = held.get(key)
        slot = slots.get(key)
        if slot is not None and slot.status == SLOT_LOADED:
            fingerprint: Fingerprint | None = slot.fingerprint
        elif slot is not None and slot.status == SLOT_EMPTY:
            fingerprint = None
        elif prev is not None and prev.spool_id == spool["id"]:
            fingerprint = prev.fingerprint
        else:
            fingerprint = None
        binding = HeldBinding(
            spool_id=int(spool["id"]),
            dev_id=str(spool["devId"]),
            ams_id=int(spool["amsId"]),
            slot_id=str(int(spool["slotId"])),
            ams_type=spool.get("amsType"),
            device_name=spool.get("deviceName"),
            ams_sn=spool.get("amsSn"),
            fingerprint=fingerprint,
        )
        result[key] = binding
        if prev is not None and prev.lost_since:
            if prev.spool_id == binding.spool_id:
                events.append(("restored", binding, "the cloud reports the binding again"))
            else:
                events.append(("replaced", prev, f"spool {binding.spool_id} is now bound to that slot"))

    for key, prev in held.items():
        if key in result:
            continue
        reason = None
        slot = slots.get(key)
        if prev.spool_id not in spools:
            reason = "spool deleted from the library"
        elif prev.spool_id in cloud_ids:
            reason = "spool is now bound to another slot"
        elif key in emptied or (slot is not None and slot.status == SLOT_EMPTY):
            reason = "slot was emptied"
        elif prev.fingerprint is None:
            reason = "slot content was never confirmed"
        elif slot is not None and slot.status == SLOT_LOADED and slot.fingerprint != prev.fingerprint:
            reason = (
                f"slot now holds {'/'.join(slot.fingerprint)} instead of "
                f"{'/'.join(prev.fingerprint)}"
            )
        if reason is not None:
            if prev.lost_since:
                events.append(("released", prev, reason))
            continue
        # Same spool still in the slot (or the slot cannot be read right
        # now - offline printer, HA start): keep the binding.
        if prev.lost_since:
            result[key] = prev
        else:
            result[key] = replace(prev, lost_since=now)
            events.append(("lost", result[key], "slot unchanged, binding kept"))
    return result, events


def apply_held(spools: dict[int, dict[str, Any]], held: dict[str, HeldBinding]) -> None:
    """Put lost bindings back into the coordinator data and flag them."""
    for binding in held.values():
        if not binding.lost_since:
            continue
        spool = spools.get(binding.spool_id)
        if spool is None or spool.get("inPrinter"):
            continue
        spool.update(
            inPrinter=True,
            devId=binding.dev_id,
            amsId=binding.ams_id,
            slotId=binding.slot_id,
            amsType=binding.ams_type,
            deviceName=binding.device_name,
            bindingLost=True,
            bindingLostSince=binding.lost_since,
        )
        if binding.ams_sn and not spool.get("amsSn"):
            spool["amsSn"] = binding.ams_sn


class SlotHoldStore:
    """Persists held bindings so a HA restart does not forget them."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.slot_hold_{entry_id}"
        )
        self.held: dict[str, HeldBinding] = {}
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        data = await self._store.async_load() or {}
        for raw in data.get("bindings", []):
            if isinstance(raw, dict) and (binding := HeldBinding.from_dict(raw)) is not None:
                self.held[binding.key] = binding
        self._loaded = True

    async def async_set(self, held: dict[str, HeldBinding]) -> None:
        if held == self.held:
            return
        self.held = held
        await self._store.async_save({"bindings": [b.to_dict() for b in held.values()]})

    async def async_clear(self) -> None:
        await self.async_load()
        if self.held:
            await self.async_set({})
