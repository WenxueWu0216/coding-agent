
import json
from pathlib import Path

CONFIG = Path(__file__).with_name("config.json")

def get_timeout():
    data = json.loads(CONFIG.read_text())
    return data.get("timeout", 5)
