import sys


class DisplayMode:
    """Centralized emoji and color management. Call setup() once at startup."""

    # Emojis
    ROCKET = "🚀"
    CLEAN = "🧹"
    PROCESS = "✨"
    SKIP = "⏩"
    SUCCESS_HTML = "📄"
    SUCCESS_DATA = "✔️"
    DOWNLOAD = "🖼️"
    ERROR = "❌"
    WARNING = "⚠️"
    INFO = "ℹ️"
    QUERY = "📡"
    SHRUG = "🤷"
    PARTY = "🎉"
    SUBDIR = "📁"
    STATS = "📊"
    CLOCK = "⏱️"
    FFMPEG = "🎬"
    YOUTUBE = "📺"
    TRAILER = "🎞️"
    PERSON = "👤"

    # Terminal colors
    C_YELLOW = "\033[93m"
    C_RED = "\033[91m"
    C_RESET = "\033[0m"

    _EMOJI_ATTRS = [
        "ROCKET", "CLEAN", "PROCESS", "SKIP", "SUCCESS_HTML", "SUCCESS_DATA",
        "DOWNLOAD", "ERROR", "WARNING", "INFO", "QUERY", "SHRUG", "PARTY",
        "SUBDIR", "STATS", "CLOCK", "FFMPEG", "YOUTUBE", "TRAILER", "PERSON",
    ]

    @classmethod
    def setup(cls, no_color: bool):
        if no_color:
            for attr in cls._EMOJI_ATTRS:
                setattr(cls, attr, "")
        if no_color or not sys.stdout.isatty():
            cls.C_YELLOW = ""
            cls.C_RED = ""
            cls.C_RESET = ""
