"""Extraction blocks are delimited, sit under their attachment, and re-ingest cleanly."""

import pytest

from app.workflows.agents.ingestion_agent import (
    EXTRACT_BLOCK_RE,
    _strip_prior_multimedia_enrichment as strip_prior,
    place_extraction,
    wrap_legacy_enrichment_blocks as wrap_legacy,
)

PDF = "/vault-files/kb/attachments/Agenda%20(v2).pdf"
M4A = "/vault-files/kb/attachments/Talk%20(Farmer).m4a"

NOTE = (
    "Day 1 notes.\n\n"
    f"[📎 Agenda (v2).pdf]({PDF})\n\n"
    "Some thoughts in the middle.\n\n"
    f"[🎤 Talk (Farmer).m4a]({M4A})\n\n"
    "Closing thoughts I wrote myself."
)


class TestPlacement:
    def test_block_lands_under_its_own_attachment(self):
        out = place_extraction(NOTE, PDF, "[PDF Extraction (Agenda)]: page one")
        after_pdf = out.split(PDF, 1)[1].lstrip(")").lstrip()
        assert after_pdf.startswith("<!-- orb:extract")
        # ...and before the next attachment, not at the end of the note.
        assert out.index("PDF Extraction") < out.index(M4A)

    def test_trailing_user_text_is_untouched(self):
        out = place_extraction(NOTE, PDF, "x")
        assert out.rstrip().endswith("Closing thoughts I wrote myself.")

    def test_two_attachments_each_get_their_own(self):
        out = place_extraction(NOTE, PDF, "PDF TEXT")
        out = place_extraction(out, M4A, "TRANSCRIPT")
        assert out.index("PDF TEXT") < out.index(M4A) < out.index("TRANSCRIPT")

    def test_unknown_url_falls_back_to_appending(self):
        out = place_extraction(NOTE, "/vault-files/kb/nope.pdf", "ORPHAN")
        assert out.rstrip().endswith("<!-- /orb:extract -->")
        assert "ORPHAN" in out

    def test_empty_section_changes_nothing(self):
        assert place_extraction(NOTE, PDF, "   \n ") == NOTE

    def test_block_is_delimited_and_names_its_source(self):
        out = place_extraction(NOTE, PDF, "body")
        block = EXTRACT_BLOCK_RE.search(out).group(0)
        assert f'src="{PDF}"' in block and block.endswith("<!-- /orb:extract -->")


class TestReIngestIsIdempotent:
    def test_strip_then_place_reproduces_the_same_note(self):
        once = place_extraction(NOTE, PDF, "v1")
        twice = place_extraction(strip_prior(once), PDF, "v1")
        assert twice == once, "re-ingest must not duplicate or drift"

    def test_re_extraction_replaces_rather_than_appends(self):
        once = place_extraction(NOTE, PDF, "OLD TEXT")
        again = place_extraction(strip_prior(once), PDF, "NEW TEXT")
        assert "OLD TEXT" not in again
        assert again.count("<!-- orb:extract") == 1

    def test_user_text_below_a_block_survives(self):
        """The old rule truncated from the first block to the end of the note."""
        enriched = place_extraction(NOTE, PDF, "extracted")
        assert "Closing thoughts I wrote myself." in strip_prior(enriched)
        assert "Some thoughts in the middle." in strip_prior(enriched)

    def test_all_blocks_are_removed_not_just_the_first(self):
        out = place_extraction(NOTE, PDF, "A")
        out = place_extraction(out, M4A, "B")
        cleaned = strip_prior(out)
        assert "orb:extract" not in cleaned and "A" not in cleaned.split("Day 1")[1][:5]
        assert cleaned.count("[📎") + cleaned.count("[🎤") == 2, "links must survive"


class TestLegacyNotes:
    """Undelimited blocks get markers first; the strip only knows delimited ones."""

    def test_pre_marker_blocks_are_wrapped_then_stripped(self):
        legacy = NOTE + "\n\n[PDF Extraction (Agenda (v2).pdf)]: old style text"
        wrapped = wrap_legacy(legacy)
        block = EXTRACT_BLOCK_RE.search(wrapped).group(0)
        assert 'src=""' in block and "old style text" in block
        assert "PDF Extraction" not in strip_prior(wrapped)
        assert "Closing thoughts I wrote myself." in strip_prior(wrapped)

    def test_wrapping_twice_is_a_no_op(self):
        legacy = NOTE + "\n\n[Audio Transcript (x.m4a)]: old\n\n[Unsupported (old.doc)]: skip"
        once = wrap_legacy(legacy)
        assert once.count("<!-- orb:extract") == 2
        assert wrap_legacy(once) == once

    def test_hand_written_image_marker_is_not_our_text_to_remove(self):
        note = "A paragraph.\n\n[Image: sketch]\n\nMore of my own words."
        assert strip_prior(note) == note

    def test_mixed_legacy_and_new_are_both_cleared(self):
        mixed = place_extraction(NOTE, PDF, "new") + "\n\n[Audio Transcript (x.m4a)]: old"
        wrapped = wrap_legacy(mixed)
        assert wrapped.count("<!-- orb:extract") == 2
        cleaned = strip_prior(wrapped)
        assert "orb:extract" not in cleaned and "Audio Transcript" not in cleaned
        assert "Closing thoughts I wrote myself." in cleaned

    def test_a_note_with_nothing_to_strip_is_unchanged(self):
        assert strip_prior(NOTE) == NOTE.rstrip()

    @pytest.mark.parametrize("empty", ["", None])
    def test_empty_input(self, empty):
        assert strip_prior(empty) == ""
