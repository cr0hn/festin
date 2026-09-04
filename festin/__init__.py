"""Festin - a powered S3 bucket finder and content discover."""

from .black_list import BLACK_LIST_DOMAINS, BLACK_LIST_FLD, BLACK_LIST_PREFIXES
from .logo import LOGO
from .s3 import S3Bucket, download_s3_objects, get_redirection, parse_result
from .utils import valid_domain_or_link

__version__ = "0.1.0"

__all__ = (
    "LOGO",
    "S3Bucket",
    "valid_domain_or_link",
    "download_s3_objects",
    "get_redirection",
    "parse_result",
    "BLACK_LIST_DOMAINS",
    "BLACK_LIST_FLD",
    "BLACK_LIST_PREFIXES",
    "__version__",
)
