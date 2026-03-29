# -*- coding: utf-8 -*-
# persistence_manager.py — Aegis V12.0 (Persistent State / AutoResume)
# Creates and manages session_state.json for crash-proof state recovery.
# State is written IMMEDIATELY on every Start/Stop action so even SIGKILL
# cannot lose the intended state.
# V11.0: All disk writes are async (non-blocking) to prevent UI freeze.
import json
import os
import logging
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger("PersistenceManager")

# Thread pool for non-blocking file I/O
_io_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="persist-io")


def _atomic_write_sync(path: str, data: dict) -> None:
    """Atomic write: write to .tmp then rename — survives SIGKILL mid-write."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX (Android/Linux)
    except Exception as e:
        logger.error(f"_atomic_write_sync({path}): {e}")
        # Fallback: direct write
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e2:
            logger.error(f"_atomic_write_sync fallback failed: {e2}")


async def _atomic_write_async(path: str, data: dict) -> None:
    """Non-blocking atomic write — offloads to thread pool so UI never freezes."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_io_pool, _atomic_write_sync, path, data)


def _atomic_write(path: str, data: dict) -> None:
    """
    Smart write: uses async if event loop is running, sync otherwise.
    This ensures writes work both during startup (no loop) and runtime.
    """
    try:
        loop = asyncio.get_running_loop()
        # Schedule async write — fire-and-forget (non-blocking)
        asyncio.ensure_future(_atomic_write_async(path, data))
    except RuntimeError:
        # No event loop — use sync write (startup / shutdown)
        _atomic_write_sync(path, data)


class PersistenceManager:
    def __init__(self, farm_dir: str):
        self.farm_dir = farm_dir
        self.path = os.path.join(farm_dir, "persistence.json")
        self.session_path = os.path.join(farm_dir, "session_state.json")

        # ── V11.0 State ──────────────────────────────────────────────────────
        self.targets: dict        = {}   # {clone_name: True}
        self.active_clones: list  = []   # [clone_name, …]  — expected-running
        self.target_states: dict  = {}   # {clone_name: "RUNNING"/"STOPPED"}
        self.auto_restore: bool        = True
        self.console_mode: bool        = False
        self.silent_mode:  bool        = True   # V12.2: suppress per-clone messages
        self.kill_oldest_enabled: bool = True   # V12.5: kill oldest clone at 90% RAM
        # session_state.json data: {clone_name: {"enabled": bool, "ts": epoch}}
        self.session_state: dict  = {}
        # ────────────────────────────────────────────────────────────────────

        self.load()
        self._load_session_state()

    # ──────────────────────────────────────────────────────────────────────
    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)

            self.auto_restore        = d.get("auto_restore", True)
            self.console_mode        = d.get("console_mode", False)
            self.silent_mode         = d.get("silent_mode", True)
            self.kill_oldest_enabled = d.get("kill_oldest_enabled", True)
            self.target_states       = d.get("target_states", {})

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
            # Ensure all states are strings (UPPER)
            for k, v in list(self.target_states.items()):
                self.target_states[k] = str(v.value if hasattr(v, 'value') else v).upper()

            payload = {
                "auto_restore":        self.auto_restore,
                "console_mode":        self.console_mode,
                "silent_mode":         self.silent_mode,
                "kill_oldest_enabled": self.kill_oldest_enabled,
                "active_clones":       self.active_clones,
                "targets":             self.targets,
                "target_states":       self.target_states,
                "target_clones":       list(self.targets.keys()),  # legacy compat
            }
            _atomic_write(self.path, payload)
        except Exception as e:
            logger.error(f"PersistenceManager.save(): {e}")

    # ══════════════════════════════════════════════════════════════════════
    #  SESSION STATE (session_state.json) — Неубиваемый AutoResume
    # ══════════════════════════════════════════════════════════════════════

    def _load_session_state(self):
        """Load session_state.json — maps clone names to enabled/disabled."""
        if not os.path.exists(self.session_path):
            self.session_state = {}
            return
        try:
            with open(self.session_path, "r", encoding="utf-8") as f:
                self.session_state = json.load(f)
        except Exception as e:
            logger.error(f"session_state.json load error: {e}")
            self.session_state = {}

    def _save_session_state(self):
        """Atomic write of session_state.json — survives SIGKILL. Non-blocking."""
        _atomic_write(self.session_path, self.session_state)

    def set_clone_enabled(self, name: str, enabled: bool):
        """
        Called IMMEDIATELY when user presses Start or Stop.
        Writes enabled state to session_state.json atomically.
        Non-blocking: offloads I/O to thread pool.
        """
        self.session_state[name] = {
            "enabled": enabled,
            "ts": time.time(),
        }
        self._save_session_state()
        logger.info(f"SessionState [{name}]: enabled={enabled}")

    def is_clone_enabled(self, name: str) -> bool:
        """Check if clone was marked as enabled (should be running)."""
        entry = self.session_state.get(name, {})
        if isinstance(entry, dict):
            return entry.get("enabled", False)
        # Legacy: if just a bool
        return bool(entry)

    def get_enabled_clones(self) -> list:
        """Return list of clone names that have enabled=True in session state."""
        result = []
        for name, entry in self.session_state.items():
            if isinstance(entry, dict) and entry.get("enabled", False):
                result.append(name)
            elif isinstance(entry, bool) and entry:
                result.append(name)
        return result

    # ──────────────────────────────────────────────────────────────────────
    # Legacy API (kept for compatibility with existing main.py calls)
    # ──────────────────────────────────────────────────────────────────────

    def add_target(self, name: str, target_state: str = "RUNNING"):
        self.targets[name] = True
        self.target_states[name] = target_state
        if name not in self.active_clones:
            self.active_clones.append(name)
        self.save()
        # Also update session_state
        self.set_clone_enabled(name, True)

    def remove_target(self, name: str):
        self.targets.pop(name, None)
        self.target_states.pop(name, None)
        if name in self.active_clones:
            self.active_clones.remove(name)
        self.save()
        # Also update session_state
        self.set_clone_enabled(name, False)

    def get_target_state(self, name: str) -> str:
        return self.target_states.get(name, "STOPPED")

    def force_sync_status(self, states_dict: dict):
        """Convert any Enum objects in the state dict to strings before saving."""
        clean_states = {}
        for k, v in states_dict.items():
            clean_states[k] = str(v.value if hasattr(v, 'value') else v)
        self.target_states.update(clean_states)
        self.save()
