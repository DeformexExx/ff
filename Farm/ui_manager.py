import html
from typing import Any
from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup


def _clone_letter(name: str) -> str:
    n = (name or "?").lower()
    if n.startswith("clien") and len(n) >= 1:
        return n[-1].upper()
    return name.replace("_", "-").upper()[:4]


def _hub_emoji(state: str, healthy: bool) -> str:
    s = str(state).upper()
    if s == "STARTING":
        return "⏳"
    if s == "RUNNING" and healthy:
        return "🟢"
    if s == "RUNNING":
        return "🟡"
    return "🔴"


class UIManager:

    @staticmethod
    def get_welcome_text(device_id: str, version: str) -> str:
        d_esc = html.escape(device_id.replace("_", "-"))
        v_esc = html.escape(version)
        return f"<b>Aegis</b> <code>{d_esc}</code> · v{v_esc}"

    @staticmethod
    def get_main_keyboard() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("Клоны"), KeyboardButton("Устройство")],
                [KeyboardButton("Система")],
            ],
            resize_keyboard=True,
        )

    @staticmethod
    def format_dashboard(device_id: str, ram: str, cpu: str, temp: str, version: str) -> str:
        d_esc = html.escape(device_id.replace("_", "-"))
        return (
            f"<b>Устройство</b> <code>{d_esc}</code>\n"
            f"RAM {html.escape(ram)} · CPU {html.escape(cpu)} · {html.escape(temp)}"
        )

    @staticmethod
    def get_device_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📷 Снимок", callback_data="sys_screenshot")],
                [InlineKeyboardButton("⬅️ В меню", callback_data="nav_home")],
            ]
        )

    @staticmethod
    def get_system_keyboard(console_on: bool, restore_on: bool) -> InlineKeyboardMarkup:
        c = "вкл" if console_on else "выкл"
        r = "вкл" if restore_on else "выкл"
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"Консоль: {c}", callback_data="toggle_console")],
                [InlineKeyboardButton(f"Автовосстановление: {r}", callback_data="toggle_restore")],
                [InlineKeyboardButton("⬅️ В меню", callback_data="nav_home")],
            ]
        )

    @staticmethod
    def format_clones_hub(clones_data: list, state_map: dict, _version: str) -> str:
        if not clones_data:
            return "<i>Нет клонов в конфиге.</i>"

        SEP = "━━━━━━━━━━━━━━━━━━━━"
        ram_free = state_map.get("__ram_free__", "N/A")
        last_sync = state_map.get("__last_sync__", "—")

        header = (
            f"💎 <b>AEGIS OS V{_version}</b> | 🧠 RAM: <b>{html.escape(ram_free)}</b>\n"
            f"♻️ Last Sync: <code>{html.escape(last_sync)}</code>\n"
            f"{SEP}\n"
            f"<code>ID  STATUS    ACCOUNT      TH    CON</code>"
        )

        lines = [header]
        for clone in clones_data:
            raw_name = clone.get("name", "?")
            name_disp = raw_name.replace("_", "-")
            nick = (clone.get("nickname") or clone.get("name") or "?").replace("_", "-")
            # Truncate nick to 12 chars for alignment
            nick_short = nick[:12] if len(nick) > 12 else nick
            nick_esc = html.escape(nick_short)
            letter = _clone_letter(raw_name)
            thr_raw = state_map.get(f"{name_disp}:threads", "0")
            cpu_raw = state_map.get(f"{name_disp}:cpu", "—")
            healthy = state_map.get(f"{name_disp}:healthy", "0") == "1"
            state = str(state_map.get(name_disp, "STOPPED")).upper()
            try:
                thr_val = int(thr_raw)
            except (ValueError, TypeError):
                thr_val = 0
            emoji = _hub_emoji(state, healthy)
            # Status label short
            if state == "RUNNING" and healthy:
                st_label = "ACTIVE"
            elif state == "RUNNING":
                st_label = "STUCK"
            elif state == "STARTING":
                st_label = "START…"
            else:
                st_label = "IDLE"
            cpu_display = f"{cpu_raw}%" if cpu_raw != "—" else "0%"
            # cpu_raw field now stores TCP connection count (watchdog writes it via ":cpu" key)
            try:
                con_val = int(cpu_raw) if cpu_raw not in ("—", "") else 0
            except (ValueError, TypeError):
                con_val = 0
            lines.append(
                f"<code>[{letter}] {emoji} {st_label:<6}  {nick_esc:<12}  {thr_val:<5}  {con_val}</code>"
            )

        lines.append(SEP)
        return "\n".join(lines)

    @staticmethod
    def get_clones_hub_keyboard(clones_data: list, state_map: dict) -> InlineKeyboardMarkup:
        rows = []
        chunk: list = []
        for clone in clones_data:
            raw_name = clone.get("name")
            if not raw_name:
                continue
            name_disp = raw_name.replace("_", "-")
            nick = (clone.get("nickname") or raw_name).replace("_", "-")
            # Keep nick short to fit button (max ~10 chars)
            nick_short = nick[:10] if len(nick) > 10 else nick
            healthy = state_map.get(f"{name_disp}:healthy", "0") == "1"
            state = str(state_map.get(name_disp, "STOPPED")).upper()
            letter = _clone_letter(raw_name)
            emoji = _hub_emoji(state, healthy)
            thr_raw = state_map.get(f"{name_disp}:threads", "0")
            cpu_raw = state_map.get(f"{name_disp}:cpu", "—")
            try:
                thr_val = int(thr_raw)
            except (ValueError, TypeError):
                thr_val = 0
            # Format: [B] 🟢 Nick | 145th | 12con
            try:
                con_val_btn = int(cpu_raw) if cpu_raw not in ("—", "") else 0
            except (ValueError, TypeError):
                con_val_btn = 0
            label = f"[{letter}] {emoji} {nick_short} | {thr_val}th | {con_val_btn}con"
            chunk.append(
                InlineKeyboardButton(label, callback_data=f"clone_{raw_name}")
            )
            if len(chunk) >= 4:
                rows.append(chunk)
                chunk = []
        if chunk:
            rows.append(chunk)
        rows.append(
            [
                InlineKeyboardButton("🚀 START ALL", callback_data="mass_start"),
                InlineKeyboardButton("🛑 STOP ALL", callback_data="mass_stop"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton("♻️ REFRESH", callback_data="hub_refresh"),
                InlineKeyboardButton("🧹 DEEP CLEAN", callback_data="deep_clean"),
            ]
        )
        rows.append([InlineKeyboardButton("⬅️ В меню", callback_data="nav_home")])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def get_clone_submenu(name: str, _state: Any, _threads: int = 0) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("▶️ СТАРТ", callback_data=f"start_single_{name}"),
                    InlineKeyboardButton("🛑 СТОП", callback_data=f"stop_single_{name}"),
                ],
                [
                    InlineKeyboardButton("🧹 ОЧИСТКА", callback_data=f"purge_cache_{name}"),
                    InlineKeyboardButton("⬅️ НАЗАД", callback_data="hub_clones"),
                ],
            ]
        )

    @staticmethod
    def get_help_text() -> str:
        return "Команды: /start, /console, /exec, /update, /mass_start, /mass_stop"
