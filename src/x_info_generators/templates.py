import re

import jinja2
from markupsafe import Markup, escape

from .utils import lang_flag


def _get_env():
    env = jinja2.Environment(
        loader=jinja2.PackageLoader("x_info_generators", "templates"),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["score_color_class"] = score_color_class
    env.filters["linebreaks"] = linebreaks
    env.filters["format_duration"] = format_duration
    env.filters["lang_flag"] = lang_flag
    return env


def format_duration(seconds):
    """Seconds -> '1h 21min' / '45min' / '2h'. Empty string if falsy."""
    if not seconds:
        return ""
    h, m = divmod(int(seconds) // 60, 60)
    if h and m:
        return f"{h}h {m:02d}min"
    if h:
        return f"{h}h"
    return f"{m}min"


def linebreaks(value) -> Markup:
    """Turn plain text into HTML paragraphs: each run of newlines starts a new <p>.

    Text is escaped, so this is safe for untrusted content (Wikipedia summaries,
    plots). Every newline becomes a paragraph break (no <br>) since sources like
    the wikipedia library separate paragraphs with single newlines.
    """
    if not value:
        return Markup("")
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n+", text.strip())
    # Build with str(escape(...)): concatenating a Markup would re-escape the
    # literal "<p>" tags into &lt;p&gt;.
    html = "".join(f"<p>{escape(para.strip())}</p>" for para in paragraphs if para.strip())
    return Markup(html)


def score_color_class(value, scale=10):
    """Return CSS class for a score value."""
    try:
        value = float(value)
    except (ValueError, TypeError):
        return "score-unknown"

    if scale == 100:
        value = value / 10

    if value >= 9.0:
        return "score-9x"
    elif value >= 8.0:
        return "score-8x"
    elif value >= 7.0:
        return "score-7x"
    elif value >= 6.0:
        return "score-6x"
    elif value >= 5.0:
        return "score-5x"
    else:
        return "score-0x"


def render_template(name: str, **context) -> str:
    env = _get_env()
    template = env.get_template(name)
    return template.render(**context)
