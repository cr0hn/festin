![Festin logo](https://raw.githubusercontent.com/cr0hn/festin/master/images/festin-logo-banner.png)

## `FestIN` the powered S3 bucket finder and content discover

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->


- [Why Festin](#why-festin)
- [Install](#install)
  - [Using Python](#using-python)
  - [Using Docker](#using-docker)
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

## License

This project is distributed under [BSD license](https://github.com/cr0hn/festin/blob/master/LICENSE>)


