"""config.py — 全局配置单例，避免各模块重复 load_project_config"""
from edit_utils import load_engine_config, load_project_config

_cfg = load_engine_config()
_project = load_project_config(_cfg)

def get_engine_config():
    return _cfg

def get_project_config():
    return _project
