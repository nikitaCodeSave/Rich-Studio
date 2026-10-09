"""Bot API specification: source, change log and validation."""

from .diff import diff_specs
from .parse import check_spec_shape, enum_values, version_tuple
from .source import Spec
from .validator import Validator, validate_rich_message

__all__ = [
    "Spec",
    "Validator",
    "check_spec_shape",
    "current",
    "diff_specs",
    "enum_values",
    "validate_rich_message",
    "version_tuple",
]

# the spec the running studio uses; loaded on start, refreshed in the background
current = Spec()
