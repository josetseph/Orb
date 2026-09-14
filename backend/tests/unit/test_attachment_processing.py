"""Attachments already carrying an extraction block are not redone by ingestion."""

from app.workflows.agents.ingestion_agent import (
    _strip_prior_multimedia_enrichment as strip_prior,
    attachment_key,
    classify_attachment,
    extraction_srcs,
    parse_attachments,
    place_extraction,
    remove_extraction,
)

PDF = "/vault-files/kb/attachments/Agenda%20(v2).pdf"
M4A = "/vault-files/kb/attachments/Talk%20(Farmer).m4a"
NOTE = f"Day 1.\n\n[📎 Agenda (v2).pdf]({PDF})\n\nMiddle.\n\n[🎤 Talk (Farmer).m4a]({M4A})\n\nEnd."


def _pending(content: str) -> list[str]:
    """What multimodal_node would still process: linked attachments without a block."""
    items = parse_attachments(content)
    kept = strip_prior(content, keep={i["lower_url"] for i in items})
    done = extraction_srcs(kept)
    return [i["url"] for i in items if i["lower_url"] not in done]


def test_block_present_means_attachment_is_skipped():
    once = place_extraction(NOTE, M4A, "[Audio Transcript (Talk)]: words")
    assert _pending(once) == [PDF]
    # ...and the kept transcript survives the strip that ingestion applies.
    assert "words" in strip_prior(once, keep={attachment_key(M4A)})


def test_orphan_block_is_removed_and_legacy_still_stripped():
    orphan = place_extraction(NOTE, "/vault-files/kb/gone.pdf", "STALE")
    legacy = orphan + "\n\n[PDF Extraction (old.pdf)]: legacy text"
    cleaned = strip_prior(legacy, keep={attachment_key(PDF), attachment_key(M4A)})
    assert "STALE" not in cleaned and "legacy text" not in cleaned
    assert cleaned.rstrip().endswith("End.")


def test_force_reprocess_replaces_the_block_once():
    once = place_extraction(NOTE, M4A, "OLD")
    again = place_extraction(remove_extraction(once, M4A), M4A, "NEW")
    assert "OLD" not in again and again.count("<!-- orb:extract") == 1
    assert again.index(M4A) < again.index("NEW") < again.index("End.")


def test_keep_none_strips_every_block():
    out = place_extraction(place_extraction(NOTE, PDF, "A"), M4A, "B")
    assert "orb:extract" not in strip_prior(out)


def test_classify_attachment():
    kinds = {
        "a.pdf": "pdf", "a.mov": "video", "a.PNG": "image", "a.docx": "docx",
        "a.csv": "spreadsheet", "a.m4a": "audio", "a.doc": None,
    }
    for name, kind in kinds.items():
        item = {"emoji": "📎", "filename": name, "url": name, "lower_url": name.lower()}
        assert classify_attachment(item) == kind, name
    mic = {"emoji": "🎤", "filename": "rec", "url": "/vault-files/k/rec.webm", "lower_url": "/vault-files/k/rec.webm"}
    assert classify_attachment(mic) == "video"  # extension wins over the mic marker
    mic["url"] = mic["lower_url"] = "/vault-files/k/rec.bin"
    assert classify_attachment(mic) == "audio"
