
import os

TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT = os.getenv("TG_CHAT", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
DB_PATH = os.getenv("DB_PATH", "data/deals.db")

# Active stores only. Historical records for retired stores remain in SQLite.
STORES = {
    "Amazon.sa": "https://www.amazon.sa/s?k={query}",
    "Jarir": "https://www.jarir.com/sa-en/catalogsearch/result/?q={query}",
}

# Main categories (about 70% of search choices).
CORE_QUERIES = [
    "gaming mouse", "mechanical keyboard", "gaming headset", "microphone",
    "audio mixer", "gaming monitor 2K 240Hz", "4K television",
    "graphics card", "computer accessories", "SSD NVMe", "RAM DDR5",
    "USB C 100W GaN charger", "UK plug charger", "power bank",
    "iPhone accessories", "iPad accessories", "car accessories",
    "dash cam", "car phone holder", "robot vacuum", "air fryer",
    "coffee machine", "men hoodie", "men sneakers", "men baseball cap",
    "men jacket", "men perfume", "fresh citrus men's fragrance",
    "men grooming trimmer",
]

# Discovery pool (about 30% of search choices).
DISCOVERY_QUERIES = [
    "travel gadgets", "home organization gadgets", "smart home devices",
    "fitness accessories", "desk accessories", "kitchen gadgets",
    "DIY tools", "outdoor accessories", "lighting gadgets",
    "wallet", "backpack", "sports shoes", "wireless charging stand",
]

# Terms matching known preferences. This is a ranking signal, not a hard filter.
PREFERRED_TERMS = [
    "ugreen", "baseus", "gan", "100w", "usb-c", "usb c", "anc",
    "noise cancelling", "2k", "240hz", "oled", "rtx", "ddr5",
    "fresh", "citrus", "aquatic", "clean", "hoodie", "sneaker",
]

# Deprioritize these scent profiles in perfume-related results.
LOW_PRIORITY_TERMS = ["spicy", "oud", "heavy tobacco", "cinnamon", "oriental spicy"]

MIN_DROP_PERCENT = float(os.getenv("MIN_DROP_PERCENT", "25"))
MIN_HISTORY_POINTS = int(os.getenv("MIN_HISTORY_POINTS", "7"))
MIN_HISTORY_DAYS = int(os.getenv("MIN_HISTORY_DAYS", "7"))
# Per-store sequential limits keep discovery broad without flooding public sources.
QUERIES_PER_STORE = max(1, min(8, int(os.getenv("QUERIES_PER_STORE", "4"))))
MAX_PRODUCTS_PER_SEARCH = max(1, min(100, int(os.getenv("MAX_PRODUCTS_PER_SEARCH", "40"))))
MAX_PRODUCTS_PER_STORE = max(1, min(400, int(os.getenv("MAX_PRODUCTS_PER_STORE", "160"))))
MAX_SEARCH_PAGES = max(1, min(5, int(os.getenv("MAX_SEARCH_PAGES", "3"))))
TRACKED_PRODUCTS_PER_STORE = max(0, min(30, int(os.getenv("TRACKED_PRODUCTS_PER_STORE", "12"))))
REQUEST_GAP_SECONDS = max(0.0, min(5.0, float(os.getenv("REQUEST_GAP_SECONDS", "0.75"))))
REQUEST_TIMEOUT = 25
