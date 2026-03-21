import re
import html
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
    def get_welcome_text(device_id: str, version: str) -> str:
        d_esc = html.escape(device_id.replace('_', '-'))
        v_esc = html.escape(version)
        return (
            f"💎 <b>AEGIS V{v_esc} — DIAMOND CUT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡️ SYSTEM : <code>[💠 ONLINE (SAFE)]</code>\n"
            f"📱 DEVICE : <code>{d_esc}</code>\n"
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
    def format_dashboard(device_id: str, ram: str, cpu: str, temp: str, version: str) -> str:
        d_esc = html.escape(device_id.replace('_', '-'))
        v_esc = html.escape(version)
        r_esc = html.escape(ram)
        c_esc = html.escape(cpu)
        t_esc = html.escape(temp)
        return (
            f"💎 <b>AEGIS V{v_esc} — DIAMOND CUT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 DEVICE  : <code>{d_esc}</code>\n"
            f"🐕 WATCHDOG: <code>[DEEP MONITOR 🔒]</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 RAM: {r_esc} | 🚀 CPU: {c_esc} | 🌡 TEMP: {t_esc}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "✨ <i>Advanced Telemetry Active</i>"
        )

    @staticmethod
    def get_device_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
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
    def format_clones_hub(clones_data: list, state_map: dict, version: str) -> str:
        """
        state_map:  {clone_name: CloneState (str value)}
        """
        v_esc = html.escape(version)
        msg = f"💎 <b>AEGIS V{v_esc} — DIAMOND CUT</b>\n"
        msg += "━━━━━━━━━━━━━━━━━━━━\n"

        if not clones_data:
            msg += "<i>No clones configured.</i>"
            return msg

        # Compact Tabular format
        for clone in clones_data:
            name = "Unknown"
            name = clone.get("name", "Unknown").replace('_', '-')
            raw_s = state_map.get(name.replace('-', '_'), "STOPPED")
            state = str(raw_s).upper()
            
            # Suffix logic: clienb -> B
            suffix = name[-1].upper() if name.lower().startswith("clien") else name.upper()

            # Thread info (name already has '-' instead of '_')
            thr_info = str(state_map.get(f"{name}:threads", "0"))
            
            thr_val = 0
            try:
                thr_val = int(thr_info)
            except:
                pass

            if state == "STARTING":
                msg += f"⏳ {suffix} | Loading....\n"
            elif thr_val > 130:
                msg += f"🟢 {suffix} | {thr_val} th\n"
            elif thr_val > 0:
                msg += f"🟡 {suffix} | Freeze ({thr_val})\n"
            else:
                msg += f"🔴 {suffix} | Offline\n"

        return msg.rstrip()

    # ── CLONE HUB KEYBOARD ────────────────────────────────────────────────
    @staticmethod
    def get_clones_hub_keyboard(clones_data: list) -> InlineKeyboardMarkup:
        """
        Rows 1-N: [⚙️ CloneA] [⚙️ CloneB]  (2 per row)
        Last: [🏠 HOME]
        """
        rows = []
        names = [c.get("name", "?") for c in clones_data]
        for i in range(0, len(names), 2):
            row = [
                InlineKeyboardButton(f"⚙️ {n.upper().replace('_', '-')}", callback_data=f"clone_{n}")
                for n in names[i:i+2]
            ]
            rows.append(row)
        rows.append([
            InlineKeyboardButton("🚀 МАСС СТАРТ", callback_data="mass_start"),
            InlineKeyboardButton("🛑 МАСС СТОП", callback_data="mass_stop")
        ])
        rows.append([InlineKeyboardButton("🏠 HOME", callback_data="nav_home")])
        return InlineKeyboardMarkup(rows)

    # ── CLONE SUB-MENU ────────────────────────────────────────────────────
    @staticmethod
    def get_clone_submenu(name: str, state: Any) -> InlineKeyboardMarkup:
        """Individual clone control keyboard."""
        # Fix Enum vs str bug
        state_str = str(state).upper()
        
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
            "🛡 <b>AEGIS SAFE MODE</b>\n\n"
            "• Watchdog: <b>Active (V6.0 Stable)</b>\n"
            "• Startup: Set Identity -> Inject -> Launch\n"
            "• UI Refresh: Optimized for stability\n"
            "• Locking: Serialized startup active\n\n"
            "<i>Stable logic: Clean kernel detection.</i>"
        )
