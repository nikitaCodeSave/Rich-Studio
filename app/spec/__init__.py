"""Bot API specification: source, change log and validation."""

from . import chats
from .convert import to_input_blocks
from .diff import diff_specs
from .entities import text_blocks
from .parse import check_spec_shape, enum_values, number_range, version_tuple
from .source import Spec
from .validator import Validator, keyboard_errors, method_errors, tg_unescape, validate_rich_message

__all__ = [
    "Spec",
    "Validator",
    "chats",
    "check_spec_shape",
    "current",
    "diff_specs",
    "enum_values",
    "keyboard_errors",
    "method_errors",
    "number_range",
    "text_blocks",
    "tg_unescape",
    "to_input_blocks",
    "validate_rich_message",
    "version_tuple",
]

# the spec the running studio uses; loaded on start, refreshed in the background
current = Spec()
