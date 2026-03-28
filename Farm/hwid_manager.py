# -*- coding: utf-8 -*-
# hwid_manager.py — Aegis V12.2 HWID Fingerprint System
"""
Generates, stores, and applies unique digital fingerprints (android_id + device model)
for each Roblox clone account to avoid "Suspicious Activity" detection.

Each account gets:
  - android_id: 16-char hex string (generated once, stored forever)
  - device_model: random model from DEVICE_POOL
  - manufacturer: matched to the device_model brand

Fingerprints are stored in DEV_{id}.json alongside the clone data.
Applied via `settings put --user 0 secure android_id` before each launch.
Model/manufacturer stored for logs only (ro.* is read-only on ugPhone).
"""

import os
import secrets
import logging
from typing import Optional, Tuple

from bash_utils import run_bash

logger = logging.getLogger("HWIDManager")

# ── Device Pool: 15 popular Android models ────────────────────────────────────
# Format: (model_name, manufacturer)
DEVICE_POOL = [
    ("Pixel 6",              "Google"),
    ("Pixel 7 Pro",          "Google"),
    ("Pixel 8",              "Google"),
    ("SM-S901B",             "samsung"),      # Galaxy S22
    ("SM-S911B",             "samsung"),      # Galaxy S23
    ("SM-G998B",             "samsung"),      # Galaxy S21 Ultra
    ("SM-A546B",             "samsung"),      # Galaxy A54
    ("2201123G",             "Xiaomi"),       # Xiaomi 12
    ("23049RAD8C",           "Xiaomi"),       # Xiaomi 13T Pro
    ("22101316G",            "Xiaomi"),       # Redmi Note 12 Pro
    ("LE2125",               "OnePlus"),      # OnePlus 9 Pro
    ("CPH2449",              "OnePlus"),      # OnePlus Nord CE 3
    ("V2254A",               "vivo"),         # vivo X90 Pro
    ("CPH2529",              "OPPO"),         # OPPO Reno 10 Pro
    ("moto g84 5G",          "motorola"),     # Moto G84
]


def generate_android_id() -> str:
    """Generate a random 16-character hex android_id."""
    return secrets.token_hex(8)  # 8 bytes = 16 hex chars


def generate_fingerprint() -> dict:
    """
    Generate a complete fingerprint: android_id + random device from pool.
    Returns dict with keys: android_id, device_model, manufacturer.
    """
    import random
    model, manufacturer = random.choice(DEVICE_POOL)
    return {
        "android_id": generate_android_id(),
        "device_model": model,
        "manufacturer": manufacturer,
    }


def ensure_fingerprint(clone_data: dict) -> bool:
    """
    Check if clone_data has HWID fields. If missing, generate and inject them.
    Returns True if new fingerprint was generated, False if already existed.
    """
    needs_gen = (
        not clone_data.get("android_id")
        or not clone_data.get("device_model")
        or not clone_data.get("manufacturer")
    )
    if needs_gen:
        fp = generate_fingerprint()
        clone_data["android_id"] = fp["android_id"]
        clone_data["device_model"] = fp["device_model"]
        clone_data["manufacturer"] = fp["manufacturer"]
        logger.info(
            f"HWID generated for {clone_data.get('name', '?')}: "
            f"id={fp['android_id']}, model={fp['device_model']}, mfr={fp['manufacturer']}"
        )
        return True
    return False


def reset_fingerprint(clone_data: dict) -> dict:
    """
    Force-regenerate fingerprint for a single clone.
    Returns the new fingerprint dict.
    """
    fp = generate_fingerprint()
    clone_data["android_id"] = fp["android_id"]
    clone_data["device_model"] = fp["device_model"]
    clone_data["manufacturer"] = fp["manufacturer"]
    logger.info(
        f"HWID RESET for {clone_data.get('name', '?')}: "
        f"id={fp['android_id']}, model={fp['device_model']}, mfr={fp['manufacturer']}"
    )
    return fp


async def apply_fingerprint(clone_name: str, android_id: str, device_model: str, manufacturer: str) -> Tuple[bool, str]:
    """
    Apply HWID fingerprint to the Android system BEFORE launching Roblox.

    ugPhone constraints:
      - settings requires --user 0 and su
      - ro.product.model / ro.product.manufacturer are READ-ONLY (no resetprop)

    Execution:
      1. su -c "settings put --user 0 secure android_id <id>"
      2. asyncio.sleep(3) — let system update registry
      3. su -c "settings get --user 0 secure android_id" — VERIFY

    Model/manufacturer data is kept in JSON for logs/User-Agent but NOT applied.

    Returns (success: bool, error_msg: str).
    If android_id fails to apply or verify → returns False + error.
    """
    import asyncio

    # Step 1: Apply android_id
    ret, _, stderr = await run_bash(
        f'su -c "settings put --user 0 secure android_id {android_id}"'
    )
    if ret != 0:
        error_msg = f"settings put android_id failed: {stderr.strip()}"
        logger.error(f"HWID apply failed for {clone_name}: {error_msg}")
        return False, error_msg

    # Step 2: Wait for system to update registry
    await asyncio.sleep(3)

    # Step 3: Verify — read back and compare
    ret, stdout, _ = await run_bash(
        'su -c "settings get --user 0 secure android_id"'
    )
    actual_id = stdout.strip() if ret == 0 else ""
    if actual_id != android_id:
        error_msg = (
            f"android_id verification failed: expected={android_id}, got={actual_id}"
        )
        logger.error(f"HWID verify failed for {clone_name}: {error_msg}")
        return False, error_msg

    # Model spoofing disabled on this system (ro.* is read-only, no resetprop)
    logger.info(
        f"[{clone_name}] Identity: Android ID [{android_id}] Applied "
        f"(Model Spoofing: Disabled by System)"
    )
    return True, ""
