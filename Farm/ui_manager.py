# -*- coding: utf-8 -*-
# ui_manager.py — Project Aegis V4.0 Dark Premium
import re
from typing import Any
from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup


def _fmt_uptime(seconds: float) -> str:
    """Format elapsed seconds as Xh Ym or Ym Zs."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


class UIManager:

    # ── WELCOME ───────────────────────────────────────────────────────────
    @staticmethod
    def get_welcome_text(device_id: str) -> str:
        return (
            "💎 *AEGIS OVERLORD V6.0*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡️ SYSTEM : `[💠 ONLINE (SAFE)]`\n"
            f"📱 DEVICE : `{device_id}`\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )

    # ── MAIN KEYBOARD ─────────────────────────────────────────────────────
    @staticmethod
    def get_main_keyboard() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup([
            [KeyboardButton("📱 DEVICE"), KeyboardButton("🤖 CLONES")],
            [KeyboardButton("⚙️ SYSTEM"), KeyboardButton("⚙️ Maintenance")],
        ], resize_keyboard=True)

    @staticmethod
    def _make_bar(percent_str: str, length: int = 10) -> str:
        try:
            val = float(percent_str.replace('%', ''))
            filled = int(round((val / 100.0) * length))
            filled = max(0, min(length, filled))
            return '▰' * filled + '▱' * (length - filled)
        except:
            return '▱' * length

    @staticmethod
    def format_dashboard(device_id: str, ram: str, cpu: str, temp: str) -> str:
        ram_bar = UIManager._make_bar(ram)
        cpu_bar = UIManager._make_bar(cpu)
        return (
            "💎 *AEGIS V6.0 — STABLE*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 DEVICE  : `{device_id}`\n"
            f"🐕 WATCHDOG: `[DEEP MONITOR 🔒]`\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 RAM: `[{ram_bar}]` {ram}\n"
            f"🚀 CPU: `[{cpu_bar}]` {cpu}\n"
            f"🌡 TEMP: `{temp}`\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✨ _Advanced Telemetry Active_"
        )

    @staticmethod
    def get_device_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("♻️ GIT SYNC",   callback_data="sys_sync")],
            [InlineKeyboardButton("🖼 SCREENSHOT", callback_data="sys_screenshot")],
            [InlineKeyboardButton("🏠 BACK",       callback_data="nav_home")],
        ])

    # ── SYSTEM ────────────────────────────────────────────────────────────
    @staticmethod
    def get_system_keyboard(console_on: bool, restore_on: bool) -> InlineKeyboardMarkup:
        c = "🟢 ON" if console_on  else "🔴 OFF"
        r = "🟢 ON" if restore_on else "🔴 OFF"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"📟 CONSOLE: {c}",      callback_data="toggle_console")],
            [InlineKeyboardButton(f"🔄 AUTO-RESTORE: {r}", callback_data="toggle_restore")],
            [InlineKeyboardButton("❓ HELP",                callback_data="sys_help")],
            [InlineKeyboardButton("🏠 BACK",               callback_data="nav_home")],
        ])

    @staticmethod
    def get_maintenance_keyboard(enabled: bool, minutes: int) -> InlineKeyboardMarkup:
        state = "🟢 ON" if enabled else "🔴 OFF"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Toggle: [{state}]", callback_data="maint_toggle")],
            [InlineKeyboardButton(f"⏱ Set Timer: {minutes}m", callback_data="maint_set_timer")],
            [InlineKeyboardButton("🚀 Run Now", callback_data="maint_run_now")],
            [InlineKeyboardButton("🏠 BACK", callback_data="nav_home")],
        ])

    # ── CLONE HUB TEXT — V5.2 Liquid Glass Card ─────────────────────────
    @staticmethod
    def format_clones_hub(clones_data: list, state_map: dict, uptime_map: dict) -> str:
        """
        state_map:  {clone_name: CloneState (str value)}
        uptime_map: {clone_name: start_timestamp (float) or None}
        """
        msg = "💎 *AEGIS OVERLORD V6.0*\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"

        if not clones_data:
            msg += "_No clones configured._"
            return msg

        import time as _time

        STATE_ICONS = {
            "STOPPED":  "🌑 STOPPED",
            "STARTING": "⏳ STARTING",
            "RUNNING":  "🟢 RUNNING",
        }

        for clone in clones_data:
            name  = clone.get("name", "Unknown")
            raw_s = state_map.get(name, "STOPPED")
            state = str(raw_s.value if hasattr(raw_s, 'value') else raw_s).upper()
            icon  = STATE_ICONS.get(state, "❓ UNKNOWN")

            # Uptime
            ts = uptime_map.get(name)
            if state == "RUNNING" and ts:
                uptime = _fmt_uptime(_time.time() - ts)
            else:
                uptime = "—"

            # Thread info
            thr_info = state_map.get(f"{name}:threads", "")
            thr_line = f"🧵 Threads: `{thr_info}`" if thr_info else "🧵 Threads: `—`"
            
            # Status badge
            status_text = state_map.get(f"{name}:status", "Stable")
            if state == "STOPPED":
                status_text = "Offline"
            elif state == "STARTING":
                status_text = "Injecting"

            msg += (
                f"[🎮 *{name.upper()}*]\n"
                f"State: {icon} | 📊 {status_text}\n"
                f"{thr_line} | ⏱ Uptime: `{uptime}`\n"
                "╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"
            )

        return msg.rstrip()

    # ── CLONE HUB KEYBOARD ────────────────────────────────────────────────
    @staticmethod
    def get_clones_hub_keyboard(clones_data: list) -> InlineKeyboardMarkup:
        """
        Row 0: [⚡️ Mass Start] [❄️ Mass Stop]
        Rows 1-N: [⚙️ CloneA] [⚙️ CloneB]  (2 per row)
        Last: [🏠 HOME]
        """
        rows = []
        rows.append([
            InlineKeyboardButton("⚡️ Mass Start", callback_data="mass_start"),
            InlineKeyboardButton("❄️ Mass Stop",  callback_data="mass_stop"),
        ])
        names = [c.get("name", "?") for c in clones_data]
        for i in range(0, len(names), 2):
            row = [
                InlineKeyboardButton(f"⚙️ {n.upper()}", callback_data=f"clone_{n}")
                for n in names[i:i+2]
            ]
            rows.append(row)
        rows.append([InlineKeyboardButton("🏠 HOME", callback_data="nav_home")])
        return InlineKeyboardMarkup(rows)

    # ── CLONE SUB-MENU ────────────────────────────────────────────────────
    @staticmethod
    def get_clone_submenu(name: str, state: Any) -> InlineKeyboardMarkup:
        """Individual clone control keyboard."""
        # Fix Enum vs str bug (V5.7 Reconstruction)
        state_str = str(state.value if hasattr(state, 'value') else state).upper()
        
        rows = []
        if state_str in ("STOPPED", "COOLDOWN", "OFFLINE"):
            rows.append([InlineKeyboardButton("⚡️ Start",    callback_data=f"start_{name}")])
        elif state_str == "RUNNING":
            rows.append([InlineKeyboardButton("❄️ Stop",     callback_data=f"stop_{name}")])
            rows.append([InlineKeyboardButton("♻️ Relaunch", callback_data=f"start_{name}")])
        else:
            # STARTING — show abort
            rows.append([InlineKeyboardButton("❌ Abort",    callback_data=f"stop_{name}")])
        rows.append([InlineKeyboardButton("📸 Screenshot",   callback_data=f"shot_{name}")])
        rows.append([InlineKeyboardButton("🏠 Back to Hub",  callback_data="nav_home")])
        return InlineKeyboardMarkup(rows)

    # ── HELP ──────────────────────────────────────────────────────────────
    @staticmethod
    def get_help_text() -> str:
        return (
            "🛡 *AEGIS V5.0 SAFE MODE*\n\n"
            "• Watchdog: *Silent* for 10 mins after boot\n"
            "• Startup: Set Identity -> Inject -> Launch (No Cleanup)\n"
            "• UI Refresh: Throttled to 60s gap\n"
            "• Locking: Serialized startup active\n\n"
            "Stable logic: No aggressive kills or background interference."
        )
