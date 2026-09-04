"""Built-in domain blacklist.

``BLACK_LIST_FLD`` entries match by registrable suffix (see
``utils.valid_domain_or_link``), so bare TLD labels like ``s.w.org`` are not
valid here — a suffix check against ``"s.w.org"`` would never match a real
host. It has been removed.
"""

BLACK_LIST_DOMAINS = frozenset(
    {
        "cdnjs.cloudflare.com",
        "ajax.googleapis.com",
        "maxcdn.bootstrapcdn.com",
        "cdn.statuspage.io",
        "static.getclicky.com",
    }
)

# Registrable-domain suffixes (FLD) to skip entirely, including any subdomain.
BLACK_LIST_FLD = frozenset(
    {
        "cloudfront.net",
        "etclicky.com",
        "eurolandir.com",
        "trafficmanager.net",
        "googleapis.com",
        "gstatic.com",
        "facebook.com",
        "elb.amazonaws.com",
        "amakaiedge.net",
        "googlehosted.com",
        "edgekey.net",
        "slideshare.net",
        "linkedin.com",
        "omniture.com",
        "2o7.net",
        "twitter.com",
        "x.com",
        "google.com",
        "youtube.com",
        "googletagmanager.com",
        "wordpress.org",
        "wordpress.com",
        "ghs.googlehosted.com",
        "itunes.apple.com",
        "instagram.com",
        "spotify.com",
        "akadns.net",
        "fontawesome.com",
        "snapchat.com",
        "vimeo.com",
        "gmpg.org",
        "office365.com",
        "office.com",
        "jquery.org",
        "adobe.com",
        "adobelogin.com",
        "jqueryui.com",
        "jquerymobile.com",
        "adform.net",
        "packtpub.com",
        "mozilla.org",
        "w3c.org",
        "stackexchange.com",
        "stackoverflow.com",
        "serverfault.com",
        "superuser.com",
        "bing.com",
        "microsoft.com",
        "apple.com",
        "amazon.com",
        "amazonaws.com",
    }
)

# Hostname prefixes to skip.
BLACK_LIST_PREFIXES = frozenset(
    {
        "cdn",
    }
)

__all__ = ("BLACK_LIST_DOMAINS", "BLACK_LIST_FLD", "BLACK_LIST_PREFIXES")
