
import os

TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT = os.getenv("TG_CHAT", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
DB_PATH = "data/deals.db"

# All four stores are scanned on every workflow run; searches rotate in four-run blocks.
STORES = {
    "Amazon.sa": "https://www.amazon.sa/s?k={query}",
    "Noon": "https://www.noon.com/saudi-en/search/?q={query}",
    "Extra": "https://www.extra.com/en-sa/search/?text={query}",
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

MIN_DROP_PERCENT = 25
MIN_HISTORY_POINTS = 3
MAX_PRODUCTS_PER_SEARCH = 20
REQUEST_TIMEOUT = 25
