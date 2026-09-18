from .youtube import search as youtube_search
from .zingmp3 import search as zingmp3_search
from .nhaccuatui import search as nhaccuatui_search

PROVIDERS = {"youtube": youtube_search, "zingmp3": zingmp3_search, "nhaccuatui": nhaccuatui_search}
