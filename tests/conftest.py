"""Shared test fixtures."""

import argparse

import pytest


def make_cli_args(**overrides) -> argparse.Namespace:
    """Build a CLI namespace with sane defaults for tests."""
    defaults = {
        "domains": [],
        "version": False,
        "file_domains": None,
        "watch": False,
        "concurrency": 5,
        "no_links": False,
        "http_timeout": 5,
        "http_max_recursion": 3,
        "domain_regex": None,
        "domain_black_list": None,
        "domain_white_list": None,
        "result_file": None,
        "discovered_domains": None,
        "raw_discovered_domains": None,
        "tor": False,
        "debug": False,
        "no_print": False,
        "quiet": True,
        "index": False,
        "index_server": "redis://localhost:6379",
        "no_dnsdiscover": False,
        "dns_resolver": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def cli_args():
    return make_cli_args()


@pytest.fixture
def input_queue():
    import asyncio

    return asyncio.Queue()


@pytest.fixture
def results_queue():
    import asyncio

    return asyncio.Queue()
