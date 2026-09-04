"""Domain analysis coroutines: S3 discovery, link crawling and DNS CNAMEs."""

import argparse
import asyncio
import re
from urllib.parse import urlparse

import httpx
from dns import asyncresolver, rdatatype
from lxml import etree

from . import valid_domain_or_link
from .s3 import S3Bucket, get_redirection, parse_result

PS = "SKIP"
PB = "BUCKET"
PBE = "BUCKET-ERROR"
PC = "CRAWLER-S3-LINK"
PC3 = "CRAWLER-S3"
PCE = "CRAWLER-ERROR"
PD = "DNS"
PDE = "DNS-ERROR"

TOR_SOCKS5_URL = "socks5://127.0.0.1:9050"


def build_http_client(cli_args: argparse.Namespace) -> httpx.AsyncClient:
    """Build a per-request httpx client honoring the Tor option.

    TLS verification is only disabled for Tor (where the SOCKS exit node
    would otherwise break most certificates); normal crawling keeps
    verification on to prevent MITM on followed links.
    """
    return httpx.AsyncClient(
        proxy=TOR_SOCKS5_URL if cli_args.tor else None,
        verify=False if cli_args.tor else True,
        timeout=cli_args.http_timeout,
        follow_redirects=False,
    )


async def check_tor_connection(cli_args) -> bool:
    """True if a request through the Tor SOCKS5 proxy succeeds."""
    try:
        async with build_http_client(cli_args) as client:
            await client.get("https://www.google.com")
            return True
    except (httpx.HTTPError, OSError):
        # Transport-level failures mean the proxy is unusable; anything
        # else (bugs) should surface, not be swallowed.
        return False


class BucketRedirectException(Exception):
    def __init__(self, redirection: str):
        self.redirection = redirection
        super().__init__(redirection)


async def get_bucket_info(cli_args, domain: str, bucket_name: str):
    """Probe a bucket URL; yield S3Bucket results as async generator."""
    async with build_http_client(cli_args) as client:
        if not bucket_name.startswith("http"):
            bucket_name = f"http://{bucket_name}"

        response = await client.get(bucket_name)

        if str(response.status_code).startswith("2"):
            content = response.text

            if objects := parse_result(content):
                print(f"[{PB}] Found '{len(objects)}' objects at bucket '{bucket_name}'")

                yield S3Bucket(
                    domain=domain,
                    bucket_name=bucket_name,
                    objects=[path for path in objects],
                )

        elif response.status_code == 301:
            redirection_url = get_redirection(response.content)
            raise BucketRedirectException(redirection_url)


async def get_links(
    cli_args: argparse.Namespace,
    domain: str,
    recursion_level: int,
    input_queue: asyncio.Queue,
    results_queue: asyncio.Queue,
):
    """Fetch http/https for a domain, then extract links or S3 listings."""
    found_domains = await _fetch_domain_content(cli_args, domain)

    for scheme, (content, content_type, status_code) in found_domains.items():
        origin = f"{scheme}://{domain}"
        if content_type == "html":
            await _enqueue_html_links(
                cli_args,
                domain,
                origin,
                content,
                recursion_level,
                input_queue,
            )
        elif content_type == "xml":
            await _process_xml_response(
                domain,
                origin,
                content,
                status_code,
                recursion_level,
                input_queue,
                results_queue,
            )


async def _fetch_domain_content(
    cli_args: argparse.Namespace,
    domain: str,
) -> dict[str, tuple[str | bytes, str, int]]:
    """Probe http://domain and https://domain; keep usable responses."""
    debug = cli_args.debug
    found_domains: dict[str, tuple[str | bytes, str, int]] = {}

    for scheme in ("http", "https"):
        try:
            async with build_http_client(cli_args) as client:
                response = await client.get(f"{scheme}://{domain}")

                header_content_type = response.headers.get("Content-Type", "")

                if "xml" in header_content_type:
                    content_type = "xml"
                elif "html" in header_content_type:
                    content_type = "html"
                else:
                    continue

                if content_type == "xml":
                    # Keep raw bytes for the S3 XML parser
                    content: str | bytes = response.content
                else:
                    content = response.text

                found_domains[scheme] = (
                    content,
                    content_type,
                    response.status_code,
                )

        except TimeoutError:
            if debug:
                print(f"[{PCE}] Error in 'get_links'. Timeout Error for '{scheme}://{domain}'")
        except Exception as e:
            if debug:
                print(f"[{PCE}] Error in 'get_links': {e}")

    return found_domains


async def _enqueue_html_links(
    cli_args: argparse.Namespace,
    domain: str,
    origin: str,
    content: str | bytes,
    recursion_level: int,
    input_queue: asyncio.Queue,
) -> None:
    """Extract external domains from an HTML page and enqueue them."""
    quiet = cli_args.quiet
    if not content:
        return

    tree = etree.HTML(content)
    if tree is None:
        return

    try:
        links = tree.xpath(".//@href") + tree.xpath(".//@src")
    except AttributeError as e:
        print(f"[{PCE}] Error in parsing response from '{domain}': {e}")
        return

    already_added_domains = set()
    for link in links:
        link_domain = urlparse(link).netloc

        if not link_domain or link_domain in already_added_domains:
            continue
        already_added_domains.add(link_domain)
        if message := valid_domain_or_link(link_domain):
            print(message)
            continue

        if not quiet:
            if "s3." in link:
                print(
                    f"[{PC}] Possible s3 bucket found. '{origin}' -> '{link_domain}'",
                    flush=True,
                )
            else:
                print(
                    f"[{PC3}] Adding domain to proposal. {origin} -> '{link_domain}'",
                    flush=True,
                )

        await input_queue.put((link_domain, recursion_level - 1))


async def _process_xml_response(
    domain: str,
    origin: str,
    content: str | bytes,
    status_code: int,
    recursion_level: int,
    input_queue: asyncio.Queue,
    results_queue: asyncio.Queue,
) -> None:
    """Handle S3-style XML listings and redirections."""
    try:
        if str(status_code).startswith("2"):
            if objects := parse_result(content):
                print(f"[{PB}] Found '{len(objects)}' objects at bucket '{origin}'")

                await results_queue.put(
                    S3Bucket(
                        domain=domain,
                        bucket_name=origin,
                        objects=[path for path in objects],
                    )
                )

        elif status_code == 301:
            redirection_url = get_redirection(content)

            await input_queue.put((redirection_url, recursion_level - 1))
    except Exception:
        # Parser error
        pass


async def get_dns_info(
    cli_args: argparse.Namespace,
    domain: str,
    recursion_level: int,
    input_queue: asyncio.Queue,
):
    debug = cli_args.debug

    if cli_args.dns_resolver:
        resolver = asyncresolver.Resolver(configure=False)
        resolver.nameservers = cli_args.dns_resolver
    else:
        resolver = asyncresolver.Resolver(configure=True)
    resolver.lifetime = cli_args.http_timeout

    try:
        cname_response = await resolver.resolve(domain, "CNAME")
    except Exception as e:
        if debug:
            print(f"[{PDE}] Error in 'get_dns_info': {e}")
        return

    for resp in cname_response.rrset:
        if resp.rdtype == rdatatype.CNAME:
            cname = str(resp.target).rstrip(".")
            print(f"[{PD}] Found new CNAME. '{domain}' -> '{cname}'", flush=True)

            if message := valid_domain_or_link(cname):
                print(message)
                continue

            await input_queue.put((cname, recursion_level - 1))


def _bucket_probe_url(domain: str) -> str | None:
    """Map a domain to the S3 URL to probe, or None if not probeable.

    - ``<bucket>.s3.amazonaws.com`` and ``<bucket>.s3.<region>.amazonaws.com``
      -> virtual-hosted URL of the bucket itself.
    - anything else -> path-style guess ``https://s3.amazonaws.com/<domain>/``.
    """
    if domain.endswith("s3.amazonaws.com") or re.search(
        r"\.s3\.[a-z0-9-]+\.amazonaws\.com$",
        domain,
    ):
        return f"https://{domain}/"

    if "s3" in domain:
        # Another S3 provider: virtual-hosted style on the same host
        return f"https://{domain}/"

    return f"https://s3.amazonaws.com/{domain}/"


async def get_s3(
    cli_args: argparse.Namespace,
    domain: str,
    recursion_level: int,
    input_queue: asyncio.Queue,
    results_queue: asyncio.Queue,
):
    """Probe a domain for a public S3 bucket and enqueue findings."""
    debug = cli_args.debug
    quiet = cli_args.quiet

    try:
        bucket_name = _bucket_probe_url(domain)

        async for bucket in get_bucket_info(cli_args, domain, bucket_name):
            await results_queue.put(bucket)
    except BucketRedirectException as red:
        if not quiet:
            print(
                f"[{PB}] Found a redirection for bucket '{domain}' -> {red.redirection}",
                flush=True,
            )

        await input_queue.put((red.redirection, recursion_level - 1))
    except TimeoutError:
        if debug:
            print(f"[{PBE}] Timeout probing S3 for '{domain}'")
    except Exception as e:
        if debug:
            print(f"[{PBE}] Error in 'get_s3' for '{domain}': {e}")


__all__ = ("get_s3", "get_dns_info", "get_links", "check_tor_connection")
