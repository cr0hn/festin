"""Festin - a powered S3 bucket finder and content discover."""

from .black_list import BLACK_LIST_DOMAINS, BLACK_LIST_FLD, BLACK_LIST_PREFIXES
from .logo import LOGO
from .s3 import get_redirection, parse_result
from .utils import valid_domain_or_link

__version__ = "0.1.0"

__all__ = (
    "LOGO",
    "valid_domain_or_link",
    "get_redirection",
    "parse_result",
    "BLACK_LIST_DOMAINS",
    "BLACK_LIST_FLD",
    "BLACK_LIST_PREFIXES",
    "__version__",
)
