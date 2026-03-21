# -*- coding: utf-8 -*-
import os
import json
import logging

logger = logging.getLogger("ConfigManager")

class ConfigManager:
    def __init__(self, device_id: str, farm_dir: str):
        self.device_id = device_id
        self.farm_dir = farm_dir
        self.config_file = os.path.join(farm_dir, f"{device_id}.json")
        self.bot_token_file = os.path.join(farm_dir, "config.json")
        self.servers_file = os.path.join(farm_dir, "servers.json")
        
        self.bot_token = ""
        self.admin_ids = []
        self.clones_data = []
        self.servers_list = []
        
        self.reload()

    def reload(self):
        """Перезагружает все конфигурационные файлы с диска"""
        self._load_bot_config()
        self._load_clones_config()
        self._load_servers_list()
        logger.info("Configs reloaded successfully.")

    def _load_servers_list(self):
        if os.path.exists(self.servers_file):
            try:
                with open(self.servers_file, "r", encoding="utf-8") as f:
                    self.servers_list = json.load(f)
                    if not isinstance(self.servers_list, list):
                        self.servers_list = [self.servers_list] if self.servers_list else []
            except Exception as e:
                logger.error(f"Failed to load servers list: {e}")
                self.servers_list = []
        else:
            logger.warning(f"Servers file not found: {self.servers_file}")
            self.servers_list = []

    def _load_bot_config(self):
        if os.path.exists(self.bot_token_file):
            try:
                with open(self.bot_token_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.bot_token = data.get("bot_token", "")
                    self.admin_ids = data.get("admin_ids", [])
            except Exception as e:
                logger.error(f"Failed to load user bot config: {e}")
        else:
            logger.warning(f"Bot config not found: {self.bot_token_file}")

    def _load_clones_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # {DEVICE_ID}.json might wrap clones in "clones" key or be a list
                    self.clones_data = data.get("clones", []) if isinstance(data, dict) else data
            except Exception as e:
                logger.error(f"Failed to parse clones JSON: {e}")
        else:
            logger.warning(f"Clones config file not found: {self.config_file}")
            self.clones_data = []

    def get_clone(self, clone_name: str) -> dict:
        """Возвращает словарь с данными клона по имени"""
        for c in self.clones_data:
            if c.get("name") == clone_name:
                return c
        return {}

    def update_clone_status(self, clone_name: str, status: str):
        """Обновляет статус клона и сохраняет в DEV_2.json (config_file)"""
        updated = False
        for c in self.clones_data:
            if c.get("name") == clone_name:
                c["status"] = status
                updated = True
                break
        
        if updated and os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                if isinstance(data, dict) and "clones" in data:
                    data["clones"] = self.clones_data
                else:
                    data = self.clones_data
                    
                with open(self.config_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
            except Exception as e:
                logger.error(f"Failed to update clone status in config: {e}")
