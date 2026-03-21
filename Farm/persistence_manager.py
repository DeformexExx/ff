# -*- coding: utf-8 -*-
# persistence_manager.py — Project Aegis V4.0
import json
import os
import logging

logger = logging.getLogger("PersistenceManager")


class PersistenceManager:
    def __init__(self, farm_dir: str):
        self.path = os.path.join(farm_dir, "persistence.json")

        # ── V4.0 ATOMIC INITIALISATION ──────────────────────────────────────
        self.targets: dict        = {}   # {clone_name: True}
        self.active_clones: list  = []   # [clone_name, …]  — expected-running
        self.target_states: dict  = {}   # {clone_name: "RUNNING"/"STOPPED"}
        self.auto_restore: bool   = True
        self.console_mode: bool   = False
        # ────────────────────────────────────────────────────────────────────

        self.load()

    # ──────────────────────────────────────────────────────────────────────
    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)

            self.auto_restore  = d.get("auto_restore", True)
            self.console_mode  = d.get("console_mode", False)
            self.target_states = d.get("target_states", {})

            # active_clones list
            ac = d.get("active_clones", [])
            self.active_clones = ac if isinstance(ac, list) else []

            # targets: migrate legacy list → dict
            raw = d.get("targets", d.get("target_clones", []))
            if isinstance(raw, list):
                self.targets = {n: True for n in raw}
            elif isinstance(raw, dict):
                self.targets = raw
            else:
                self.targets = {}

            # Sync active_clones → targets
            for n in self.active_clones:
                if n not in self.targets:
                    self.targets[n] = True

        except Exception as e:
            logger.error(f"PersistenceManager.load(): {e}")

    # ──────────────────────────────────────────────────────────────────────
    def save(self):
        try:
            # V5.7: Final check to ensure all states are strings (UPPER)
            for k, v in list(self.target_states.items()):
                self.target_states[k] = str(v.value if hasattr(v, 'value') else v).upper()

            payload = {
                "auto_restore":  self.auto_restore,
                "console_mode":  self.console_mode,
                "active_clones": self.active_clones,
                "targets":       self.targets,
                "target_states": self.target_states,
                "target_clones": list(self.targets.keys()),  # legacy compat
            }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"PersistenceManager.save(): {e}")

    # ──────────────────────────────────────────────────────────────────────


    def add_target(self, name: str, target_state: str = "RUNNING"):
        self.targets[name] = True
        self.target_states[name] = target_state
        if name not in self.active_clones:
            self.active_clones.append(name)
        self.save()

    def remove_target(self, name: str):
        self.targets.pop(name, None)
        self.target_states.pop(name, None)
        if name in self.active_clones:
            self.active_clones.remove(name)
        self.save()

    def get_target_state(self, name: str) -> str:
        return self.target_states.get(name, "STOPPED")

    def force_sync_status(self, states_dict: dict):
        """V5.3 Hotfix: Convert any Enum objects in the state dict to strings before saving."""
        clean_states = {}
        for k, v in states_dict.items():
            # If it's an Enum, take .value, otherwise str
            clean_states[k] = str(v.value if hasattr(v, 'value') else v)
        self.target_states.update(clean_states)
        self.save()
