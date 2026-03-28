# -*- coding: utf-8 -*-
# config_manager.py — Aegis V12.2 (HWID support)
import json
import os
import logging

from hwid_manager import ensure_fingerprint

logger = logging.getLogger("ConfigManager")


class ConfigManager:
    """Loads config.json + DEV_{id}.json, provides clone data & admin IDs."""

    def __init__(self, device_id: str, farm_dir: str):
        self.device_id = device_id
        self.farm_dir = farm_dir
        self._cfg_path = os.path.join(farm_dir, "config.json")
        self._dev_path = os.path.join(farm_dir, f"{device_id}.json")

        self.bot_token: str = ""
        self.admin_ids: list = []
        self.clones_data: list = []
        self._raw_cfg: dict = {}
        self._raw_dev: dict = {}

        self.reload()

    # ──────────────────────────────────────────────────────────────────────
    def reload(self):
        """(Re)load both config.json and DEV_{id}.json."""
        # ── config.json ──
        try:
            with open(self._cfg_path, "r", encoding="utf-8") as f:
                self._raw_cfg = json.load(f)
            self.bot_token = self._raw_cfg.get("bot_token", "")
            self.admin_ids = self._raw_cfg.get("admin_ids", [])
        except Exception as e:
            logger.error(f"config.json load error: {e}")

        # ── DEV_{id}.json ──
        if os.path.exists(self._dev_path):
            try:
                with open(self._dev_path, "r", encoding="utf-8") as f:
                    self._raw_dev = json.load(f)
                self.clones_data = self._raw_dev.get("clones", [])
                # V12.2: Auto-generate HWID fingerprints for clones missing them
                hwid_changed = False
                for clone in self.clones_data:
                    if ensure_fingerprint(clone):
                        hwid_changed = True
                if hwid_changed:
                    self._save_dev()
                    logger.info("HWID: auto-generated missing fingerprints, saved to DEV json.")
            except Exception as e:
                logger.error(f"DEV json load error: {e}")
        else:
            logger.warning(f"DEV file not found: {self._dev_path}")

    # ──────────────────────────────────────────────────────────────────────
    def get_clone(self, name: str) -> dict | None:
        for c in self.clones_data:
            if c.get("name") == name:
                return c
        return None

    # ──────────────────────────────────────────────────────────────────────
    def update_clone_status(self, name: str, status: str):
        """Write status into DEV_{id}.json for the given clone."""
        changed = False
        for c in self.clones_data:
            if c.get("name") == name:
                c["status"] = status.lower()
                changed = True
                break
        if changed:
            self._save_dev()

    # ──────────────────────────────────────────────────────────────────────
    def _save_dev(self):
        try:
            self._raw_dev["clones"] = self.clones_data
            with open(self._dev_path, "w", encoding="utf-8") as f:
                json.dump(self._raw_dev, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logger.error(f"DEV json save error: {e}")
