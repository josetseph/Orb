"""Attachment links whose filenames contain parentheses must still be found.

A markdown URL may contain balanced parentheses, so "Report (2026).pdf" is a
legal link target. The old pattern stopped at the first ")", which silently
dropped the attachment: the PDF was never read during ingestion and never
rendered in the note.
"""

import pytest

from app.workflows.agents.ingestion_agent import (
    ATTACHMENT_LINK_RE as ATTACH_RE,
    IMAGE_LINK_RE as IMAGE_RE,
)


def _link(name: str, encoded: str) -> str:
    return f"[📎 {name}](/vault-files/kb1/attachments/{encoded})"


class TestParenthesisedFilenames:
    def test_the_reported_note_yields_both_pdfs(self):
        """Two attachments; only the bracket-free one used to survive."""
        note = "\n".join(
            [
                "On day 1, we had our introduction and first Problem Situation",
                _link(
                    "Intro & Overview - Masters in Intelligent Computing Systems "
                    "(Aug 2026 seminar).pdf",
                    "Intro%20%26%20Overview%20-%20Masters%20in%20Intelligent%20"
                    "Computing%20Systems%20(Aug%202026%20seminar)-b99420ce.pdf",
                ),
                _link("PBL Intro.pdf", "PBL%20Intro-1234.pdf"),
            ]
        )
        found = ATTACH_RE.findall(note)
        assert len(found) == 2
        for _emoji, _name, url in found:
            assert url.endswith(".pdf"), f"URL truncated: {url}"

    @pytest.mark.parametrize(
        "encoded",
        [
            "Report%20(2026).pdf",
            "Notes%20(draft)%20(v2).pdf",  # two separate groups
            "Plain.pdf",
            "Spaces are unencoded.pdf",  # legacy links kept working
        ],
    )
    def test_url_survives_intact(self, encoded):
        m = ATTACH_RE.search(_link("f", encoded))
        assert m is not None, f"no match for {encoded}"
        assert m.group(3).endswith(encoded)

    def test_images_too(self):
        md = "![diagram](/vault-files/kb1/attachments/Fig%20(1).png)"
        m = IMAGE_RE.search(md)
        assert m is not None and m.group(2).endswith("Fig%20(1).png")

    def test_link_still_ends_at_its_own_paren(self):
        """The URL must not swallow trailing prose."""
        md = _link("f", "a.pdf") + " and then some text (an aside) after."
        m = ATTACH_RE.search(md)
        assert m.group(3) == "/vault-files/kb1/attachments/a.pdf"

    def test_unbalanced_paren_does_not_run_away(self):
        md = _link("f", "a.pdf") + "\nnext line ( unclosed"
        m = ATTACH_RE.search(md)
        assert "\n" not in m.group(3)
