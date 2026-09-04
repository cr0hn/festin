"""Bucket name permutation engine.

Turns a domain (e.g. ``example.com``) into a deterministic set of candidate
S3 bucket names by combining the domain parts with common environment
suffixes, and merges in operator-supplied wordlists capped to a sane size.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import aiofiles

ENV_SUFFIXES = (
    "",
    "-prod",
    "-production",
    "-staging",
    "-dev",
    "-test",
    "-qa",
    "-backup",
    "-bak",
    "-old",
    "-new",
    "-archive",
    "-tmp",
    "-public",
    "-private",
    "-data",
    "-logs",
    "-assets",
    "-static",
    "-media",
    "-uploads",
)

MAX_CANDIDATES = 5000
MAX_WORDLIST = 10000

#: Longest-first so the most specific suffix wins when stripping.
_SUFFIXES_BY_LEN = tuple(sorted((s for s in ENV_SUFFIXES if s), key=len, reverse=True))

__all__ = (
    "ENV_SUFFIXES",
    "MAX_CANDIDATES",
    "MAX_WORDLIST",
    "generate_candidates",
    "load_wordlist",
    "merge_wordlists",
)


def _clean_domain(domain: str) -> str:
    """Normalize a domain: strip whitespace, lowercase, drop empty labels."""
    labels = [part for part in domain.strip().lower().split(".") if part]
    return ".".join(labels)


def _normalize_suffixes(custom_suffixes: Iterable[str]) -> tuple[str, ...]:
    """Normalize custom suffixes to ``-word`` form, deduped, order preserved."""
    normalized: list[str] = []
    for suffix in custom_suffixes:
        text = suffix.strip().lower()
        if not text:
            continue
        if not text.startswith("-"):
            text = f"-{text}"
        if text != "-":
            normalized.append(text)
    return tuple(dict.fromkeys(normalized))


def _push_base(bases: list[str], name: str) -> None:
    if name and name not in bases:
        bases.append(name)


def _bases_for(domain: str) -> list[str]:
    """Ordered, deduplicated base names derived from a clean domain."""
    labels = domain.split(".")
    bases: list[str] = []
    _push_base(bases, domain.replace(".", "-"))
    _push_base(bases, domain.replace(".", "_"))
    if len(labels) > 1:
        core = "-".join(labels[:-1])
        _push_base(bases, core)
        _push_base(bases, core.replace("-", "_"))
    for label in labels:
        _push_base(bases, label)
    return bases


def _suffix_variants(base: str, suffix: str) -> list[str]:
    """Postfix and prefix variants of one base with one ``-word`` suffix."""
    word = suffix[1:]
    return [f"{base}{suffix}", f"{word}-{base}"]


def _cap(candidates: set[str], cap: int) -> set[str]:
    """Deterministically truncate to ``cap`` entries."""
    if cap >= len(candidates):
        return candidates
    return set(sorted(candidates)[:cap])


def generate_candidates(
    domain: str,
    custom_suffixes: Iterable[str] = (),
    max_candidates: int = MAX_CANDIDATES,
) -> set[str]:
    """Produce candidate bucket names for ``domain``.

    Combines the hyphenated/underscored full domain, the TLD-less core and
    every individual label with each environment suffix (bare, postfix and
    prefix forms), plus dotted-postfix variants of the full domain.
    """
    cleaned = _clean_domain(domain)
    if not cleaned:
        return set()

    suffixes = ENV_SUFFIXES + _normalize_suffixes(custom_suffixes)
    candidates: set[str] = set()
    for base in _bases_for(cleaned):
        candidates.add(base)
        for suffix in suffixes:
            candidates.update(_suffix_variants(base, suffix))
    for suffix in suffixes:
        candidates.add(f"{cleaned}{suffix}")
    return _cap(candidates, max_candidates)


async def load_wordlist(path: Path) -> list[str]:
    """Load a wordlist file: one word per line, no blanks/comments/dupes."""
    words: list[str] = []
    seen: set[str] = set()
    async with aiofiles.open(path, encoding="utf-8") as handle:
        async for raw in handle:
            word = raw.strip()
            if not word or word.startswith("#") or word in seen:
                continue
            seen.add(word)
            words.append(word)
            if len(words) >= MAX_WORDLIST:
                break
    return words


def _dotted_domains(candidates: set[str]) -> set[str]:
    """Extract bare dotted domains from candidates, env suffixes stripped."""
    domains: set[str] = set()
    for candidate in candidates:
        if "." not in candidate:
            continue
        for suffix in _SUFFIXES_BY_LEN:
            if candidate.endswith(suffix):
                domains.add(candidate[: -len(suffix)])
                break
        else:
            domains.add(candidate)
    return domains


def merge_wordlists(
    candidates: set[str],
    words: Iterable[str],
    max_candidates: int = MAX_CANDIDATES,
) -> list[str]:
    """Merge domain-derived candidates with wordlist-derived names.

    Word-derived forms: the word itself, ``word<env-suffix>`` for every
    environment suffix, and ``word.<dotted-domain>`` for each domain seen in
    the candidate set. Result is deduplicated, sorted and capped.
    """
    merged: set[str] = set(candidates)
    domains = _dotted_domains(candidates)
    for raw in words:
        word = raw.strip()
        if not word or word.startswith("#"):
            continue
        merged.add(word)
        for suffix in _SUFFIXES_BY_LEN:
            merged.add(f"{word}{suffix}")
        for domain in domains:
            merged.add(f"{word}.{domain}")
    return sorted(merged)[:max_candidates]
