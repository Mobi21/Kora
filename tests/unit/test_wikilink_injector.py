"""Unit tests for the markdown-aware wikilink injector — Phase 8c.

Edge case tests:
- Entity in normal text -> linked
- Entity in code block -> NOT linked
- Entity in frontmatter -> NOT linked
- Entity in inline code -> NOT linked
- Entity in existing [[link]] -> NOT linked
- Entity in markdown link [text](url) -> NOT linked
- Entity in HTML comment -> NOT linked
- Longest match wins: "Sarah Connor" over "Sarah"
- First occurrence only per entity
- Case sensitivity
- Multiple entities in same paragraph
- Entity at start/end of text
- Entity name that is substring of another word (should NOT match)
"""

from __future__ import annotations

from kora_v2.agents.background.vault_organizer import (
    _tokenize_markdown,
    inject_wikilinks,
)

# ══════════════════════════════════════════════════════════════════════════
# Basic linking
# ══════════════════════════════════════════════════════════════════════════


class TestBasicLinking:
    """Entity in normal text should be linked."""

    def test_entity_in_plain_text(self) -> None:
        body = "I met Sarah at the park today."
        result = inject_wikilinks(body, ["Sarah"])
        assert result == "I met [[Sarah]] at the park today."

    def test_entity_at_start_of_text(self) -> None:
        body = "Sarah was there yesterday."
        result = inject_wikilinks(body, ["Sarah"])
        assert result == "[[Sarah]] was there yesterday."

    def test_entity_at_end_of_text(self) -> None:
        body = "I met Sarah"
        result = inject_wikilinks(body, ["Sarah"])
        assert result == "I met [[Sarah]]"

    def test_multiple_entities_in_same_paragraph(self) -> None:
        body = "I met Sarah and John at the park."
        result = inject_wikilinks(body, ["Sarah", "John"])
        assert "[[Sarah]]" in result
        assert "[[John]]" in result

    def test_empty_body(self) -> None:
        assert inject_wikilinks("", ["Sarah"]) == ""

    def test_empty_entities(self) -> None:
        body = "I met Sarah."
        assert inject_wikilinks(body, []) == body

    def test_entity_not_in_text(self) -> None:
        body = "No entities here."
        assert inject_wikilinks(body, ["Sarah"]) == body


# ══════════════════════════════════════════════════════════════════════════
# Excluded regions
# ══════════════════════════════════════════════════════════════════════════


class TestExcludedRegions:
    """Entities in excluded regions should NOT be linked."""

    def test_entity_in_fenced_code_block(self) -> None:
        body = "Before code.\n```\nSarah is in code\n```\nAfter code."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" not in result
        assert "Sarah is in code" in result

    def test_entity_in_tilde_code_block(self) -> None:
        body = "Before.\n~~~\nSarah in tilde code\n~~~\nAfter."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" not in result

    def test_entity_in_code_block_with_language(self) -> None:
        body = "Before.\n```python\nSarah = 42\n```\nAfter."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" not in result

    def test_entity_in_inline_code(self) -> None:
        body = "Use `Sarah` as a variable name."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" not in result
        assert "`Sarah`" in result

    def test_entity_in_double_backtick_inline_code(self) -> None:
        body = "Use ``Sarah`` as a variable name."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" not in result

    def test_entity_in_frontmatter(self) -> None:
        # Note: inject_wikilinks operates on body only (no frontmatter),
        # but the tokenizer should handle it if frontmatter slips through.
        body = "---\nauthor: Sarah\n---\n\nSarah is here."
        result = inject_wikilinks(body, ["Sarah"])
        # The frontmatter Sarah should NOT be linked
        # The body Sarah SHOULD be linked
        assert "author: Sarah" in result  # frontmatter preserved
        assert "[[Sarah]] is here." in result

    def test_entity_in_existing_wikilink(self) -> None:
        body = "Already linked: [[Sarah]] is here."
        result = inject_wikilinks(body, ["Sarah"])
        # Should not double-link
        assert result.count("[[Sarah]]") == 1
        assert "[[[" not in result

    def test_entity_in_embed_link(self) -> None:
        body = "See also: ![[Sarah]] for details."
        result = inject_wikilinks(body, ["Sarah"])
        assert "![[Sarah]]" in result
        assert result.count("[[Sarah]]") == 1  # just the embed

    def test_entity_in_markdown_link_text(self) -> None:
        body = "See [Sarah](https://example.com) for details."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" not in result
        assert "[Sarah](https://example.com)" in result

    def test_entity_in_markdown_link_url(self) -> None:
        body = "See [link](https://Sarah.com) for details."
        result = inject_wikilinks(body, ["Sarah"])
        # URL should not be modified
        assert "[[Sarah]]" not in result

    def test_entity_in_html_comment(self) -> None:
        body = "Before.<!-- Sarah is commented out -->After."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" not in result
        assert "<!-- Sarah is commented out -->" in result

    def test_entity_in_multiline_html_comment(self) -> None:
        body = "Before.\n<!--\nSarah is commented out\n-->\nAfter."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" not in result


# ══════════════════════════════════════════════════════════════════════════
# Matching rules
# ══════════════════════════════════════════════════════════════════════════


class TestMatchingRules:
    """Test word-boundary, longest-match, first-occurrence, and case rules."""

    def test_longest_match_wins(self) -> None:
        """'Sarah Connor' preferred over 'Sarah' when text has 'Sarah Connor'."""
        body = "I met Sarah Connor at the park."
        result = inject_wikilinks(body, ["Sarah", "Sarah Connor"])
        assert "[[Sarah Connor]]" in result
        # "Sarah" should not be independently linked since it's part of "Sarah Connor"
        assert result.count("[[") == 1

    def test_longest_match_with_standalone(self) -> None:
        """Both link when 'Sarah Connor' and standalone 'Sarah' appear."""
        body = "Sarah Connor talked to Sarah about it."
        result = inject_wikilinks(body, ["Sarah", "Sarah Connor"])
        assert "[[Sarah Connor]]" in result
        # Standalone Sarah should be linked
        assert "[[Sarah]]" in result

    def test_first_occurrence_only(self) -> None:
        """Only the first occurrence of each entity should be linked."""
        body = "Sarah went home. Sarah came back. Sarah left again."
        result = inject_wikilinks(body, ["Sarah"])
        assert result.count("[[Sarah]]") == 1
        # First occurrence should be linked
        assert result.startswith("[[Sarah]]")

    def test_case_sensitive(self) -> None:
        """Entity 'Sarah' should NOT match 'sarah'."""
        body = "I met sarah at the park."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" not in result
        assert "sarah" in result

    def test_case_sensitive_exact_match(self) -> None:
        """Entity 'Sarah' should match exact case."""
        body = "I met Sarah at the park."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" in result

    def test_word_boundary_not_substring(self) -> None:
        """Entity name that is substring of another word should NOT match."""
        body = "The Sarah-mania was incredible."
        result = inject_wikilinks(body, ["Sarah"])
        # "Sarah" appears as part of "Sarah-mania", but the hyphen acts
        # as a word boundary, so it should match "Sarah" before the hyphen
        # Actually with word-boundary matching, "Sarah" followed by "-" should match
        # because "-" is not a word character.
        assert "[[Sarah]]" in result

    def test_word_boundary_prefix_no_match(self) -> None:
        """Entity should not match when it's a prefix of a longer word."""
        body = "We discussed Sarahstyle today."
        result = inject_wikilinks(body, ["Sarah"])
        # "Sarah" is prefix of "Sarahstyle" — word boundary check fails
        assert "[[Sarah]]" not in result

    def test_word_boundary_suffix_no_match(self) -> None:
        """Entity should not match when it's a suffix of a longer word."""
        body = "I saw deSarah today."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]]" not in result


# ══════════════════════════════════════════════════════════════════════════
# Complex scenarios
# ══════════════════════════════════════════════════════════════════════════


class TestComplexScenarios:
    """Test interactions between multiple rules."""

    def test_mixed_excluded_and_linkable(self) -> None:
        """Entity in code should be skipped, entity in text should be linked."""
        body = "I talked to `Sarah` about code. Then Sarah went home."
        result = inject_wikilinks(body, ["Sarah"])
        assert "`Sarah`" in result  # inline code preserved
        assert "[[Sarah]] went home." in result

    def test_entity_after_code_block(self) -> None:
        """Entity after a code block should still be linked."""
        body = "```\ndef foo():\n    pass\n```\nSarah reviewed the code."
        result = inject_wikilinks(body, ["Sarah"])
        assert "[[Sarah]] reviewed the code." in result

    def test_multiple_code_blocks(self) -> None:
        body = "```\nSarah code\n```\nText\n```\nSarah code 2\n```\nSarah here."
        result = inject_wikilinks(body, ["Sarah"])
        # Only the last Sarah (in text) should be linked
        assert result.count("[[Sarah]]") == 1
        assert "[[Sarah]] here." in result

    def test_nested_excluded_regions(self) -> None:
        """HTML comment inside code block."""
        body = "```\n<!-- Sarah -->\n```\nSarah is here."
        result = inject_wikilinks(body, ["Sarah"])
        assert result.count("[[Sarah]]") == 1
        assert "[[Sarah]] is here." in result

    def test_entities_across_paragraphs(self) -> None:
        body = "First paragraph about Sarah.\n\nSecond paragraph about John."
        result = inject_wikilinks(body, ["Sarah", "John"])
        assert "[[Sarah]]" in result
        assert "[[John]]" in result

    def test_entity_with_special_chars(self) -> None:
        """Entity name with regex-special characters."""
        body = "I met Dr. Smith at the hospital."
        result = inject_wikilinks(body, ["Dr. Smith"])
        assert "[[Dr. Smith]]" in result

    def test_preserves_other_formatting(self) -> None:
        """Wikilink injection should preserve other markdown formatting."""
        body = "**Sarah** went to the _store_."
        result = inject_wikilinks(body, ["Sarah"])
        # Bold markers may be around the entity
        assert "[[Sarah]]" in result


# ══════════════════════════════════════════════════════════════════════════
# Tokenizer internals
# ══════════════════════════════════════════════════════════════════════════


class TestTokenizer:
    """Test the tokenizer directly for region classification."""

    def test_frontmatter_detected(self) -> None:
        text = "---\ntitle: Hello\n---\n\nBody text."
        regions = _tokenize_markdown(text)
        # First region should be frontmatter (not linkable)
        assert not regions[0].linkable
        assert "---" in regions[0].text
        # At least one region should be linkable (body)
        assert any(r.linkable for r in regions)

    def test_code_block_detected(self) -> None:
        text = "Before.\n```\ncode here\n```\nAfter."
        regions = _tokenize_markdown(text)
        code_regions = [r for r in regions if "code here" in r.text]
        assert len(code_regions) == 1
        assert not code_regions[0].linkable

    def test_inline_code_detected(self) -> None:
        text = "Use `var` here."
        regions = _tokenize_markdown(text)
        inline_regions = [r for r in regions if "`var`" in r.text]
        assert len(inline_regions) == 1
        assert not inline_regions[0].linkable

    def test_wikilink_detected(self) -> None:
        text = "See [[link]] here."
        regions = _tokenize_markdown(text)
        link_regions = [r for r in regions if "[[link]]" in r.text]
        assert len(link_regions) == 1
        assert not link_regions[0].linkable

    def test_embed_detected(self) -> None:
        text = "See ![[file]] here."
        regions = _tokenize_markdown(text)
        embed_regions = [r for r in regions if "![[file]]" in r.text]
        assert len(embed_regions) == 1
        assert not embed_regions[0].linkable

    def test_markdown_link_detected(self) -> None:
        text = "See [text](url) here."
        regions = _tokenize_markdown(text)
        link_regions = [r for r in regions if "[text](url)" in r.text]
        assert len(link_regions) == 1
        assert not link_regions[0].linkable

    def test_html_comment_detected(self) -> None:
        text = "Before <!-- comment --> After"
        regions = _tokenize_markdown(text)
        comment_regions = [r for r in regions if "comment" in r.text]
        assert len(comment_regions) == 1
        assert not comment_regions[0].linkable

    def test_all_regions_cover_full_text(self) -> None:
        """All regions should concatenate back to the original text."""
        text = "Hello `code` world [[link]] end.\n```\nblock\n```\nDone."
        regions = _tokenize_markdown(text)
        reconstructed = "".join(r.text for r in regions)
        assert reconstructed == text
