"""Response parsers for Ikariam game HTML and AJAX JSON."""

from .html_parser import GamePageParser
from .json_parser import AjaxResponseParser

__all__ = ["GamePageParser", "AjaxResponseParser"]
