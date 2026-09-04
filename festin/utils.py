"""Domain validation helpers."""

from colorama import Fore, Style

from .black_list import (
    BLACK_LIST_DOMAINS,
    BLACK_LIST_FLD,
    BLACK_LIST_PREFIXES,
)


def _matches_fld(domain: str, fld: str) -> bool:
    """True if domain IS the FLD or a subdomain of it (label-boundary)."""
    return domain == fld or domain.endswith(f".{fld}")


def valid_domain_or_link(domain_or_link: str) -> str | None:
    """Return a skip reason if the domain is blacklisted, else None."""
    colored_prefix = f"{Fore.YELLOW}SKIP{Style.RESET_ALL}"

    for fld in BLACK_LIST_FLD:
        if _matches_fld(domain_or_link, fld):
            return f"[{colored_prefix}] domain '{domain_or_link}' is in blacklist"

    for prefix in BLACK_LIST_PREFIXES:
        if domain_or_link.startswith(f"{prefix}."):
            return f"[{colored_prefix}] domain '{domain_or_link}' has a prefix blacklisted"

    if domain_or_link in BLACK_LIST_DOMAINS:
        return f"[{colored_prefix}] domain '{domain_or_link}' is in blacklist"

    return None


__all__ = ("valid_domain_or_link",)
