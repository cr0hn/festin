"""Tests for the bucket name permutation engine."""

from pathlib import Path

import pytest

from festin.permutations import (
    ENV_SUFFIXES,
    MAX_CANDIDATES,
    MAX_WORDLIST,
    generate_candidates,
    load_wordlist,
    merge_wordlists,
)


class TestGenerateCandidates:
    def test_canonical_members_present(self):
        candidates = generate_candidates("example.com")

        assert "example-com" in candidates
        assert "example" in candidates
        assert "example-com-prod" in candidates
        assert "prod-example-com" in candidates
        assert "example_com" in candidates

    def test_subdomain_labels_expand(self):
        candidates = generate_candidates("s3.east.example.com")

        assert "s3-east-example-com" in candidates
        assert "east" in candidates
        assert "s3-dev" in candidates
        assert "dev-s3" in candidates

    def test_dot_postfix_variant(self):
        candidates = generate_candidates("example.com")
        assert "example.com-prod" in candidates

    def test_custom_suffixes_normalized_and_applied(self):
        candidates = generate_candidates("example.com", custom_suffixes=["corp", "-HR"])

        assert "example-corp" in candidates
        assert "corp-example" in candidates
        assert "example-hr" in candidates

    def test_custom_suffixes_do_not_add_duplicates(self):
        plain = generate_candidates("example.com")
        with_default = generate_candidates("example.com", custom_suffixes=["-prod"])
        assert plain == with_default

    def test_cap_enforced_deterministic(self):
        small = len(ENV_SUFFIXES)  # forces truncation well below full size
        capped = generate_candidates("example.com", max_candidates=small)
        uncapped = generate_candidates("example.com")

        assert len(capped) == small
        assert capped == set(sorted(uncapped)[:small])

    def test_cap_at_module_default(self):
        assert len(generate_candidates("example.com")) <= MAX_CANDIDATES

    def test_empty_and_blank_domains(self):
        assert generate_candidates("") == set()
        assert generate_candidates("   ") == set()
        assert generate_candidates("..") == set()

    def test_whitespace_and_case_normalized(self):
        assert generate_candidates(" EXAMPLE.Com ") == generate_candidates("example.com")

    def test_deterministic_across_calls(self):
        assert generate_candidates("example.com") == generate_candidates("example.com")

    def test_single_label_domain(self):
        candidates = generate_candidates("intranet")

        assert "intranet" in candidates
        assert "intranet-staging" in candidates
        assert "staging-intranet" in candidates
        assert "intranet_com" not in candidates


class TestLoadWordlist:
    async def test_strips_comments_blanks_and_dupes(self, tmp_path: Path):
        path = tmp_path / "words.txt"
        path.write_text(
            "# comment line\n\nalpha\n  beta  \nalpha\n# another\n\ngamma\n",
            encoding="utf-8",
        )

        words = await load_wordlist(path)

        assert words == ["alpha", "beta", "gamma"]

    async def test_caps_at_max_wordlist(self, tmp_path: Path):
        path = tmp_path / "big.txt"
        path.write_text("\n".join(f"word{i}" for i in range(MAX_WORDLIST + 50)), encoding="utf-8")

        words = await load_wordlist(path)

        assert len(words) == MAX_WORDLIST
        assert words[0] == "word0"
        assert words[-1] == f"word{MAX_WORDLIST - 1}"

    async def test_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.txt"
        path.write_text("", encoding="utf-8")

        assert await load_wordlist(path) == []


class TestMergeWordlists:
    def test_merges_words_with_suffixes(self):
        merged = merge_wordlists({"example-com"}, ["backups"])

        assert "backups" in merged
        assert "backups-prod" in merged
        assert "backups-logs" in merged
        assert "example-com" in merged

    def test_word_dot_domain_forms(self):
        merged = merge_wordlists({"example.com-prod"}, ["logs"])

        assert "logs.example.com" in merged
        # suffix is stripped from the dotted domain before joining
        assert "logs.example.com-prod" not in merged

    def test_dedup_and_deterministic_order(self):
        merged = merge_wordlists({"example"}, ["example", "example"])
        assert merged == sorted(set(merged))
        assert merged.count("example") == 1

    def test_cap_enforced(self):
        words = [f"w{i}" for i in range(50)]
        merged = merge_wordlists(set(), words, max_candidates=5)

        assert len(merged) == 5
        assert merged == sorted(merged)

    def test_default_cap_respected(self):
        words = [f"w{i}" for i in range(MAX_CANDIDATES + 100)]
        assert len(merge_wordlists(set(), words)) <= MAX_CANDIDATES

    def test_empty_inputs(self):
        assert merge_wordlists(set(), []) == []
        assert merge_wordlists({"example"}, []) == ["example"]

    def test_blank_and_comment_words_ignored(self):
        merged = merge_wordlists(set(), ["", "  ", "# header", "real"])

        assert "real" in merged
        assert all(word == "real" or word.startswith("real-") for word in merged)


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("example.com", "example-com"),
        ("EXAMPLE.com", "example-com"),
        ("a.b.c.example.co.uk", "a-b-c-example-co-uk"),
    ],
)
def test_domain_normalization(domain: str, expected: str):
    assert expected in generate_candidates(domain)
