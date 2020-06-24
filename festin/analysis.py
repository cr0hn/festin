import asyncio
import argparse
from collections import Callable
from typing import List

from urllib.parse import urlparse

import aiohttp

from lxml import etree
from async_dns.core import types
from async_dns.resolver import ProxyResolver
from aiohttp_proxy import ProxyConnector, ProxyType

from .s3 import get_redirection, parse_result, S3Bucket

def build_tor_connector(cli_args: argparse.Namespace) \
        -> ProxyConnector or None:

    if cli_args.tor:
        return ProxyConnector(
            proxy_type=ProxyType.SOCKS5,
            host='127.0.0.1',
            port=9050,
            verify_ssl=False
        )
    else:
        return None


async def get_links(cli_args: argparse.Namespace,
                    domain: str,
                    input_queue: asyncio.Queue):
    quiet = cli_args.quiet

    # Get links
    found_domains = set()

    for scheme in ("http", "https"):
        try:
            async with aiohttp.ClientSession(connector=build_tor_connector(
                    cli_args)
            ) as session:

                async with session.get(f"{scheme}://{domain}",
                                       verify_ssl=False) as response:
                    content = await response.text()

                    if "html" not in response.headers.get("Content-Type", ""):
                        continue

                    if hasattr(content, "encode"):
                        content = content.encode("UTF-8")

                    tree = etree.HTML(content)

                    for res in list(tree.xpath(".//@src") + tree.xpath(".//@src")):
                        if loc := urlparse(res).netloc:
                            found_domains.add(loc)

        except Exception as e:
            print(e)
            continue

    if not quiet and found_domains:
        print(f"    > Found '{len(found_domains)}' new "
              f"domains from website links ", flush=True)

    for d in found_domains:
        if not quiet:
            print(f"      -> Adding domain from link '{d}'", flush=True)

        await input_queue.put(d)


async def get_dns_info(cli_args: argparse.Namespace,
                       domain: str,
                       input_queue: asyncio.Queue):

    if cli_args.dns_resolver:
        dns_servers =("*", cli_args.dns_resolver.split(","))
    else:
        dns_servers = None

    resolver = ProxyResolver(proxies=dns_servers)

    try:
        cname_response = await resolver.query(domain, types.CNAME)
    except Exception as e:
        print(e)
        return

    for resp in cname_response.an:
        if resp.data:
            print(f"        +> Found new CNAME '{resp.data}'", flush=True)
            await input_queue.put(resp.data)


async def get_s3(cli_args: argparse.Namespace,
                 domain: str,
                 input_queue: asyncio.Queue,
                 results_queue: asyncio.Queue):

    quiet = cli_args.quiet

    try:
        async with aiohttp.ClientSession(connector=build_tor_connector(
                cli_args)) as session:

            if domain.endswith("s3.amazonaws.com"):
                bucket_name = domain
            elif "s3" in domain:
                _s = domain.find("s3")  # Another S3 provider
                provider = domain[_s:]
                domain = domain[:_s - 1]
                bucket_name = f"http://{provider}/{domain}"
            else:
                bucket_name = "https://s3.amazonaws.com/{domain}".format(
                    domain=domain
                )

            async with session.get(bucket_name) as response:

                if str(response.status).startswith("2"):
                    content = await response.text()

                    if objects := parse_result(content):
                        await results_queue.put(S3Bucket(
                            domain=domain,
                            bucket_name=bucket_name,
                            objects=[path for path in objects]
                        ))

                elif response.status == 301:
                    redirection_url = get_redirection(await response.read())

                    if quiet:
                        print(
                            f"  >> Found a redirection for bucket '{domain}' "
                            f"-> {redirection_url}",
                            flush=True)

                    await input_queue.put(redirection_url)

    except Exception as e:
        print(e)


__all__ = ("get_s3", "get_dns_info", "get_links")
