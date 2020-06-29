![Festin logo](https://raw.githubusercontent.com/cr0hn/festin/master/images/festin-logo-banner.png)

## `FestIN` the powered S3 bucket finder and content discover

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->


- [Why Festin](#why-festin)
- [Install](#install)
  - [Using Python](#using-python)
  - [Using Docker](#using-docker)
- [Options](#options)
- [Examples](#examples)
  - [dnsrecon + festin](#dnsrecon--festin)
- [License](#license)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## Why Festin

There's a lot of S3 tools for enumeration and discover S3 bucket. Some of them are great but anyone have a complete list of features that `Festin` has. 

There're the main features that do `Festin` great:

- Various techniques for finding buckets: crawling, dns crawling and S3 responses analysis.
- Proxy support for tunneling requests.
- AWS credentials are not needed.
- Works with any S3 compatible provider, not only with AWS.
- Allows to configure custom DNS servers.
- Integrated high performance HTTP crawler.
- Recursively search and feedback from the 3 engines: a domain found by dns crawler is send to S3 and Http Crawlers analyzer and the same for the S3 and Crawler.
- Works as 'watching' mode, listening for new domains in real time.
- Save all of the domains discovered in a separate file for further analysis.
- Allow to download bucket objects and put then in a FullText Search Engine (Redis Search) automatically, indexing the objects content allowing powerful search further.
- Limit the search for specific domain/s.

## Install

### Using Python

    Python 3.8 of above needed!

```bash
$ pip install festin
$ festin -h
```

### Using Docker

```bash
$ docker run --rm -it cr0hn/fesin -h
```

## Options

```text
usage: __main__.py [-h] [--version] [-f FILE_DOMAINS] [-w] [-c CONCURRENCY] [--no-links] [-T HTTP_TIMEOUT] [-M HTTP_MAX_RECURSION] [-dr DOMAIN_REGEX] [-rr RESULT_FILE] [-rd DISCOVERED_DOMAINS] [-ra RAW_DISCOVERED_DOMAINS]
                   [--tor] [--debug] [--no-print] [-q] [--index] [--index-server INDEX_SERVER] [-dn] [-ds DNS_RESOLVER]
                   [domains [domains ...]]

Festin - the powered S3 bucket finder and content discover

positional arguments:
  domains

optional arguments:
  -h, --help            show this help message and exit
  --version             show version
  -f FILE_DOMAINS, --file-domains FILE_DOMAINS
                        file with domains
  -w, --watch           watch for new domains in file domains '-f' option
  -c CONCURRENCY, --concurrency CONCURRENCY
                        max concurrency

HTTP Probes:
  --no-links            extract web site links
  -T HTTP_TIMEOUT, --http-timeout HTTP_TIMEOUT
                        set timeout for http connections
  -M HTTP_MAX_RECURSION, --http-max-recursion HTTP_MAX_RECURSION
                        maximum recursison when follow links
  -dr DOMAIN_REGEX, --domain-regex DOMAIN_REGEX
                        only follow domains that matches this regex

Results:
  -rr RESULT_FILE, --result-file RESULT_FILE
                        results file
  -rd DISCOVERED_DOMAINS, --discovered-domains DISCOVERED_DOMAINS
                        file name for storing new discovered after apply filters
  -ra RAW_DISCOVERED_DOMAINS, --raw-discovered-domains RAW_DISCOVERED_DOMAINS
                        file name for storing any domain without filters

Connectivity:
  --tor                 Use Tor as proxy

Display options:
  --debug               enable debug mode
  --no-print            doesn't print results in screen
  -q, --quiet           Use quiet mode

Redis Search:
  --index               Download and index documents into Redis
  --index-server INDEX_SERVER
                        Redis Search ServerDefault: redis://localhost:6379

DNS options:
  -dn, --no-dnsdiscover
                        not follow dns cnames
  -ds DNS_RESOLVER, --dns-resolver DNS_RESOLVER
                        comma separated custom domain name servers
```

## Examples

- Run against *target.com* with default options and leaving result to target.com.result file
  ```festin target.com -rr target.com.result```

- Run against *target.com* using tor proxy, with concurrency of 5, using DNS 212.166.64.1 for resolving CNAMEs and leaving result to target.com.result file
  ```festin target.com -c 5 -rr target.com.result --tor -ds 212.166.64.1```

### dnsrecon + festin

The domain chosen for this example is *target.com*.

- **Step 1**: Run dnsrecon with desired options against target domain and save the output
  ```dnsrecon -d target.com -t crt -c target.com.csv```
  With this command we are going to find out other domains realted to target.com. This will help to maximize our chances of success.

- **Step 2**: Prepare the previous generated file to feed festin
  ```tail -n +2 target.com.csv | sort -u | cut -d "," -f 2 >> target.com.domains```
  With this command we generate a file with one domain per line. This is the input that festin needs.

- **Step 3**: Run festin with desired options and save output
  ```festin -f target.com.domains -c 5 -rr target.com.result --tor -ds 212.166.64.1 >target.com.stdout 2>target.com.stderr```

In this example the resulting files are:

- target.com.result: Main result file with one line per bucket found. Each line is a JSON object.
- target.com.stdout: The standard output of festin command execution
- target.com.stderr: The standard error of festin command execution

## License

This project is distributed under [BSD license](https://github.com/cr0hn/festin/blob/master/LICENSE>)
