"""Unit tests for domain blacklist validation."""

import pytest

from festin.utils import valid_domain_or_link


class TestValidDomainOrLink:
    @pytest.mark.parametrize(
        "domain",
        [
            "cdn.jsdelivr.net",  # prefix blacklist
            "cdnjs.cloudflare.com",  # exact domain blacklist
            "www.google.com",  # FLD blacklist
            "sub.google.com",  # FLD blacklist matches subdomains
            "adobestorage.s3.amazonaws.com",  # FLD blacklist
        ],
    )
    def test_blacklisted(self, domain):
        message = valid_domain_or_link(domain)
        assert message is not None
        assert "SKIP" in message

    @pytest.mark.parametrize(
        "domain",
        [
            "example.com",
            "storage.example-bucket.com",
            "sub.example.com",
        ],
    )
    def test_allowed(self, domain):
        assert valid_domain_or_link(domain) is None

    def test_fld_blacklist_does_not_overmatch(self):
        """A domain merely containing a blacklisted label must pass."""
        assert valid_domain_or_link("google.example.com") is None

    def test_message_is_str(self):
        result = valid_domain_or_link("www.facebook.com")
        assert isinstance(result, str)
