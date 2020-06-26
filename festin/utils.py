from colorama import Fore, Back, Style

from .black_list import *


def valid_domain_or_link(domain_or_link: str) -> None or str:
    colored_prefix = f"{Fore.YELLOW}SKIP{Style.RESET_ALL}"
    if any(domain_or_link.endswith(d) for d in BLACK_LIST_TLD):
        return f"[{colored_prefix}] domain '{domain_or_link}' is in blacklist"

    if any(domain_or_link.startswith(d) for d in BLACK_LIST_PREFISES):
        return f"[{colored_prefix}] domain '{domain_or_link}' has a prefix " \
               f"blacklisted"

    if domain_or_link in BLACK_LIST_DOMAINS:
        return f"[{colored_prefix}] domain '{domain_or_link}' is in blacklist"

    return None


__all__ = ("valid_domain_or_link",)
