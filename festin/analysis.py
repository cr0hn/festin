import asyncio
import argparse

from urllib.parse import urlparse

import aiohttp
import aiohttp_proxy

from lxml import etree
from async_dns.core import types
from colorama import Fore, Back, Style
from async_dns.resolver import ProxyResolver
from aiohttp_proxy import ProxyConnector, ProxyType

from . import valid_domain_or_link
from .s3 import get_redirection, parse_result, S3Bucket

PS = f"{Fore.YELLOW}SKIP{Style.RESET_ALL}"
PB = f"{Fore.GREEN}BUCKET{Style.RESET_ALL}"
PBE = f"{Fore.RED}BUCKET-ERROR{Style.RESET_ALL}"
PC= f"{Fore.MAGENTA}CRAWLER-S3-LINK{Style.RESET_ALL}"
PC3= f"{Fore.MAGENTA}CRAWLER-S3{Style.RESET_ALL}"
PCE= f"{Fore.RED}CRAWLER-ERROR{Style.RESET_ALL}"
PD= f"{Fore.BLUE}DNS{Style.RESET_ALL}"
PDE= f"{Fore.RED}DNS-ERROR{Style.RESET_ALL}"

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

async def check_tor_connection(cli_args) -> bool:

    try:
        async with aiohttp.ClientSession(connector=build_tor_connector(
                cli_args),
                timeout=aiohttp.ClientTimeout(total=cli_args.http_timeout)
        ) as session:
            async with session.get("https://www.google.com") as response:
                return True
    except aiohttp_proxy.errors.SocksConnectionError as e:
        return False


class BucketRedirectException(Exception):

    def __init__(self, redirection):
        self.redirection = redirection


async def get_bucket_info(cli_args, domain, bucket_name: str):
    async with aiohttp.ClientSession(connector=build_tor_connector(
            cli_args),
            timeout=aiohttp.ClientTimeout(total=cli_args.http_timeout)
    ) as session:

        if not bucket_name.startswith("http"):
            bucket_name = f"http://{bucket_name}"

        async with session.get(bucket_name) as response:

            if str(response.status).startswith("2"):
                content = await response.text()

                if objects := parse_result(content):
                    print(f"[{PB}] Found "
                          f"'{len(objects)}' objects at "
                          f"bucket '{bucket_name}'")

                    yield S3Bucket(
                        domain=domain,
                        bucket_name=bucket_name,
                        objects=[path for path in objects]
                    )

            elif response.status == 301:
                redirection_url = get_redirection(await response.read())

                raise BucketRedirectException(redirection_url)


async def get_links(cli_args: argparse.Namespace,
                    domain: str,
                    recursion_level: int,
                    input_queue: asyncio.Queue,
                    results_queue: asyncio.Queue):
    debug = cli_args.debug
    quiet = cli_args.quiet

    # Get links
    # found_domains= {"http": set(), "https": set()}
    found_domains= {}

    found = []

    for scheme in ("http", "https"):
        try:
            async with aiohttp.ClientSession(connector=build_tor_connector(
                    cli_args),
                    timeout=aiohttp.ClientTimeout(total=cli_args.http_timeout)
            ) as session:

                async with session.get(f"{scheme}://{domain}",
                                       verify_ssl=False) as response:
                    content = await response.text()

                    header_content_type = response.headers.get("Content-Type", "")

                    if "xml" in header_content_type:
                        content_type = "xml"
                    elif "html" in header_content_type:
                        content_type = "html"
                    else:
                        continue

                    if hasattr(content, "encode"):
                        content = content.encode("UTF-8")

                    found_domains[scheme] = (
                        content,
                        content_type,
                        response.status
                    )

        except asyncio.exceptions.TimeoutError as e:
            if debug:
                print(f"[{PCE}] Error in 'get_links'. Timeout Error "
                      f"for '{scheme}://{domain}'")
        except Exception as e:
            if debug:
                print(f"[{PCE}] Error in 'get_links': {str(e)}")
            continue

    #
    # Analyze found links
    #
    already_added_domains = set()

    for scheme, (content, content_type, status_code) in found_domains.items():

        origin = f"{scheme}://{domain}"

        if content_type == "html":

            if not content:
                continue

            tree = etree.HTML(content)

            try:
                for link in list(tree.xpath(".//@href") + tree.xpath(".//@src")):
                    link_domain = urlparse(link).netloc

                    if not link_domain:
                        continue

                    if link_domain in already_added_domains:
                        continue
                    else:
                        already_added_domains.add(link_domain)

                    if message := valid_domain_or_link(link_domain):
                        print(message)
                        continue

                    if not quiet:
                        if "s3." in link:
                            print(f"[{PC}] "
                                  f"Possible s3 bucket found. "
                                  f"'{origin}' -> "
                                  f"'{link_domain}'", flush=True)
                        else:
                            print(f"[{PC3}] Adding "
                                  f"domain to proposal. "
                                  f"{origin} -> '{link_domain}'", flush=True)

                    await input_queue.put((link_domain, recursion_level - 1))
            except AttributeError as e:
                print(f"[{PCE}] Error in parsing response from '{domain}'"
                      f": {str(e)}")
                continue

        if content_type == "xml":

            try:
                if str(status_code).startswith("2"):
                    if objects := parse_result(content):
                        print(f"[{PB}] Found "
                              f"'{len(objects)}' objects at "
                              f"bucket '{origin}'")

                        await results_queue.put(S3Bucket(
                            domain=domain,
                            bucket_name=origin,
                            objects=[path for path in objects]
                        ))

                elif response.status == 301:
                    redirection_url = get_redirection(await response.read())

                    await input_queue.put(
                        (redirection_url, recursion_level - 1)
                    )
            except Exception as e:
                # Parser error
                continue

async def get_dns_info(cli_args: argparse.Namespace,
                       domain: str,
                       recursion_level: int,
                       input_queue: asyncio.Queue):

    debug = cli_args.debug

    if cli_args.dns_resolver:
        dns_servers =[(None, cli_args.dns_resolver.split(","))]
    else:
        dns_servers = None
    try:
        resolver = ProxyResolver(proxies=dns_servers)
    except Exception as e:
        pass

    for _ in range(3):
        try:
            cname_response = await resolver.query(domain, types.CNAME)
        except Exception as e:
            if debug:
                print(f"[{PDE}] Error in 'get_dns_info': : {str(e)}")
            return

        try:
            for resp in cname_response.an:
                if resp.data and resp.qtype==types.CNAME:
                    print(f"[{PD}] Found new CNAME. '{domain}' -> "
                          f"'{resp.data}'", flush=True)

                    if message := valid_domain_or_link(resp.data):
                        print(message)
                        continue

                    await input_queue.put((resp.data, recursion_level - 1))

            break

        except AttributeError:
            await asyncio.sleep(1)


async def get_s3(cli_args: argparse.Namespace,
                 domain: str,
                 recursion_level: int,
                 input_queue: asyncio.Queue,
                 results_queue: asyncio.Queue):

    debug = cli_args.debug
    quiet = cli_args.quiet

    try:

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

            try:
                async for bucket in get_bucket_info(
                        cli_args,
                        domain,
                        bucket_name
                ):
                    await results_queue.put(bucket)
            except BucketRedirectException as red:
                if quiet:
                    print(
                        f"[{PB}] Found a redirection for bucket "
                        f"'{domain}' -> {red.redirection}",
                        flush=True)

                await input_queue.put((red.redirection, recursion_level - 1))


    except asyncio.exceptions.TimeoutError as e:
        if debug:
            print(f"[{PBE}] Error in 'get_s3'. Timeout Error "
                  f"for '{bucket_name}'")
    except Exception as e:
        if debug:
            print(f"[{PBE}] Error in 'get_s3': {str(e)} ")


__all__ = ("get_s3", "get_dns_info", "get_links", "check_tor_connection")
