"""Word extraction reaches past the document body.

python-docx walks ``document.paragraphs`` only, so headers, footers, floating
text boxes and embedded images are invisible by default — which for many real
documents is exactly where the title, captions and diagrams live.
"""

import io
import os

import pytest

docx = pytest.importorskip("docx")

from app.services.multimedia import multimedia_service as M  # noqa: E402

def _png(side: int, noise: bool) -> bytes:
    """A real PNG — python-docx validates the bytes when embedding."""
    import random
    import struct
    import zlib

    rows = b""
    rnd = random.Random(0)
    for _ in range(side):
        px = (
            bytes(rnd.randrange(256) for _ in range(side * 3))
            if noise
            else b"\x00" * (side * 3)
        )
        rows += b"\x00" + px

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 0))  # level 0: stays over the floor
        + chunk(b"IEND", b"")
    )


# Noise at this size lands well past the 8 KB "spacer, not content" floor.
_PNG = _png(64, noise=True)
_PNG_TINY = _png(1, noise=False)


@pytest.fixture
def sample(tmp_path):
    d = docx.Document()
    d.add_paragraph("Body paragraph one.")
    table = d.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Region"
    table.cell(0, 1).text = "Revenue"
    table.cell(1, 0).text = "Ghana"
    table.cell(1, 1).text = "120"
    d.sections[0].header.paragraphs[0].text = "Confidential — Acme Corp"
    d.sections[0].footer.paragraphs[0].text = "Draft, do not circulate"
    path = tmp_path / "sample.docx"
    d.save(str(path))
    return str(path)


class TestTextExtraction:
    def test_body_and_tables(self, sample):
        text = M.extract_text_from_docx(sample)
        assert "Body paragraph one." in text
        assert "--- Table 1 ---" in text
        assert "Region | Revenue" in text
        assert "Ghana | 120" in text

    def test_header_and_footer(self, sample):
        text = M.extract_text_from_docx(sample)
        assert "--- Header ---" in text
        assert "Confidential — Acme Corp" in text
        assert "--- Footer ---" in text
        assert "Draft, do not circulate" in text

    def test_repeated_running_head_is_not_duplicated(self, tmp_path):
        d = docx.Document()
        d.sections[0].header.paragraphs[0].text = "Same Head"
        d.add_paragraph("x")
        path = tmp_path / "r.docx"
        d.save(str(path))
        text = M.extract_text_from_docx(str(path))
        assert text.count("Same Head") == 1

    def test_empty_document_says_so(self, tmp_path):
        path = tmp_path / "empty.docx"
        docx.Document().save(str(path))
        assert "no extractable text" in M.extract_text_from_docx(str(path))


class TestEmbeddedImages:
    def test_images_are_written_out(self, tmp_path):
        d = docx.Document()
        d.add_paragraph("See diagram:")
        d.add_picture(io.BytesIO(_PNG))
        path = tmp_path / "img.docx"
        d.save(str(path))

        written = M.extract_docx_images(str(path))
        try:
            assert len(written) == 1
            assert os.path.getsize(written[0]) > 8192
        finally:
            for p in written:
                os.path.exists(p) and os.remove(p)

    def test_tiny_images_are_skipped_as_chrome(self, tmp_path):
        """Bullets and spacers describe as noise, so they are filtered out."""
        d = docx.Document()
        d.add_picture(io.BytesIO(_PNG_TINY))
        path = tmp_path / "tiny.docx"
        d.save(str(path))
        assert M.extract_docx_images(str(path)) == []

    def test_a_document_with_no_images_is_fine(self, sample):
        assert M.extract_docx_images(sample) == []

    def test_bad_file_does_not_raise(self, tmp_path):
        broken = tmp_path / "broken.docx"
        broken.write_bytes(b"not a docx")
        assert M.extract_docx_images(str(broken)) == []
