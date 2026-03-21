# -*- coding: utf-8 -*-
import asyncio
import logging
import time
from bash_utils import run_bash

logger = logging.getLogger("InjectionEngine")

class InjectionEngine:
    @staticmethod
    async def inject_and_launch(clone_name: str, cookie: str, place_id: str, status_msg=None) -> bool:
        """
        The strictly ordered, pure-bash injection mechanism.
        Возвращает True если запуск успешен, иначе False.
        Обновляет статус через status_msg.edit_text(text) если передан.
        """
        async def update_status(text: str):
            logger.info(text)
            if status_msg:
                try:
                    await status_msg.edit_text(text)
                except Exception:
                    pass

        try:
            # 1. Skip Cleanup (V5.0 SAFE MODE)
            # await run_bash(f"su -c 'am force-stop com.roblox.{clone_name}'")

            # 2. SQLite Injection (STRICT BASH)
            await update_status(f"⏳ ({clone_name}) 2/4: Инъекция Cookie (BASH)...")
            
            sqlite_bin = "/data/data/com.termux/files/usr/bin/sqlite3"
            db_path = f"/data/data/com.roblox.{clone_name}/app_webview/Default/Cookies"
            
            # Calculate Timestamp in microseconds
            current_time = int(time.time() * 1000000)
            
            sql_del = "DELETE FROM cookies;"
            sql_ins = (
                f"INSERT INTO cookies ("
                f"creation_utc, host_key, top_frame_site_key, name, value, "
                f"path, expires_utc, is_secure, is_httponly, last_access_utc, "
                f"has_expires, is_persistent, samesite, source_port"
                f") VALUES ("
                f"{current_time}, '.roblox.com', '', '.ROBLOSECURITY', '{cookie}', "
                f"'/', 253402300799000000, 1, 1, {current_time}, "
                f"1, 1, -1, -1"
                f");"
            )
            
            # Form the full su command with escaped quotes for sqlite
            inj_cmd = f"su -c \"{sqlite_bin} {db_path} \\\"{sql_del} {sql_ins}\\\"\""
            ret, stdout, stderr = await run_bash(inj_cmd)
            
            if ret != 0:
                await update_status(f"❌ SQLite Ошибка ({clone_name}):\n{stderr}")
                return False

            # 3. Permissions Fix (CRITICAL)
            await update_status(f"⏳ ({clone_name}) 3/4: Восстановление прав...")
            chown_cmd = f"su -c \"chown \\$(stat -c %u:%g /data/data/com.roblox.{clone_name}) {db_path}\""
            ret, stdout, stderr = await run_bash(chown_cmd)
            
            if ret != 0:
                if "Permission denied" in stderr or "not found" in stderr:
                    await update_status(f"❌ Root Error ({clone_name}): Устройство без Root или tsu не установлен.\n{stderr}")
                else:
                    await update_status(f"❌ Chown Ошибка ({clone_name}):\n{stderr}")
                return False

            # 4. Launch (Monkey / Intent) (Golden Sequence)
            await update_status(f"⏳ ({clone_name}) 4/4: Запуск (Awaken)...")
            
            # Step 1: Force Stop (already done at step 1, but user requested it again in sequence)
            # await run_bash(f"su -c 'am force-stop com.roblox.{clone_name}'")
            
            # Step 3: Start the app (Awaken)
            ret, stdout, stderr = await run_bash(f"su -c 'monkey -p com.roblox.{clone_name} 1'")
            
            if ret != 0:
                await update_status(f"❌ Monkey Error ({clone_name}):\n{stderr}")
                return False

            if place_id:
                # Step 4: WAIT 6 SECONDS (ugPhone needs time)
                await asyncio.sleep(6)
                
                # Step 5: Send the Join command (Strike)
                import re
                share_code = None
                
                # Safe Extraction (Regex)
                match = re.search(r"code=([a-zA-Z0-9]+)", str(place_id))
                if match:
                    share_code = match.group(1)
                
                if share_code:
                    # Universal Intent Format (roblox://)
                    join_intent = f"roblox://navigation/share_links?code={share_code}&type=Server"
                    join_cmd = f"su -c 'am start -W -a android.intent.action.VIEW -d \"{join_intent}\" com.roblox.{clone_name}'"
                    ret, stdout, stderr = await run_bash(join_cmd)
                    if ret != 0:
                        logger.error(f"Join Intent fail for {clone_name}: {stderr}")
                else:
                    # Fallback to standard PlaceID or direct URL
                    if str(place_id).isdigit():
                        join_cmd = f"su -c 'am start -W -a android.intent.action.VIEW -d \"roblox://placeId={place_id}\" com.roblox.{clone_name}'"
                    else:
                        join_cmd = f"su -c 'am start -W -a android.intent.action.VIEW -d \"{place_id}\" com.roblox.{clone_name}'"
                    
                    ret, stdout, stderr = await run_bash(join_cmd)
                
            await update_status(f"✅ Запущено ({clone_name})")
            return True
                
            await update_status(f"✅ Запущено ({clone_name})")
            return True
            
        except Exception as e:
            logger.error(f"Launch Sequence Error for {clone_name}: {e}")
            await update_status(f"❌ Критическая ошибка ({clone_name}): {str(e)}")
            return False

    @staticmethod
    async def stop(clone_name: str) -> bool:
        ret, stdout, stderr = await run_bash(f"su -c 'am force-stop com.roblox.{clone_name}'")
        return ret == 0

    @staticmethod
    async def clean(clone_name: str) -> bool:
        await InjectionEngine.stop(clone_name)
        ret, stdout, stderr = await run_bash(f"su -c 'rm -rf /data/data/com.roblox.{clone_name}/cache/*'")
        return ret == 0
