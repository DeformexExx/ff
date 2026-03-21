# -*- coding: utf-8 -*-
import asyncio
import logging

logger = logging.getLogger("BashUtils")

async def run_bash(cmd: str) -> tuple[int, str, str]:
    """Выполняет bash команду и возвращает (код_возврата, stdout, stderr)"""
    try:
        process = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return process.returncode, stdout.decode().strip(), stderr.decode().strip()
    except Exception as e:
        logger.error(f"Bash Execution Error: {e} | CMD: {cmd}")
        return -1, "", str(e)
