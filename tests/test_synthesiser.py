"""Tests for synthesiser JSON validation and PDF merge."""
import pytest
from tg_compiler.synthesiser import _validate_intel, _prepend_pdf


# ---------------------------------------------------------------------------
# _validate_intel
# ---------------------------------------------------------------------------

def _good_intel() -> dict:
    return {
        "situation_summary": "Tensions remain elevated across the region.",
        "key_themes": [{"theme": "Escalation", "detail": "Fighting has intensified."}],
        "signals_and_warnings": [{"signal": "Airspace closure", "assessment": "Watch for further closures."}],
        "named_actors": [{"actor": "PM Netanyahu", "role": "Israeli PM", "activity": "Ordered strikes."}],
    }


def test_validate_intel_valid_passes():
    assert _validate_intel(_good_intel()) is None


def test_validate_intel_missing_key_fails():
    data = _good_intel()
    del data["situation_summary"]
    assert _validate_intel(data) is not None


def test_validate_intel_empty_situation_summary_fails():
    data = _good_intel()
    data["situation_summary"] = "   "
    assert _validate_intel(data) is not None


@pytest.mark.parametrize("key", ["key_themes", "signals_and_warnings", "named_actors"])
def test_validate_intel_empty_list_fails(key):
    data = _good_intel()
    data[key] = []
    assert _validate_intel(data) is not None


def test_validate_intel_key_themes_missing_subkey():
    data = _good_intel()
    data["key_themes"] = [{"theme": "X"}]  # missing 'detail'
    assert _validate_intel(data) is not None


def test_validate_intel_signals_missing_subkey():
    data = _good_intel()
    data["signals_and_warnings"] = [{"signal": "X"}]  # missing 'assessment'
    assert _validate_intel(data) is not None


def test_validate_intel_named_actors_missing_subkey():
    data = _good_intel()
    data["named_actors"] = [{"actor": "X", "role": "Y"}]  # missing 'activity'
    assert _validate_intel(data) is not None


# ---------------------------------------------------------------------------
# _prepend_pdf
# ---------------------------------------------------------------------------

def test_prepend_pdf_merges_pages(tmp_path):
    from pypdf import PdfWriter, PdfReader

    def make_pdf(path, n_pages=1):
        writer = PdfWriter()
        for _ in range(n_pages):
            writer.add_blank_page(width=612, height=792)
        with open(path, "wb") as f:
            writer.write(f)

    front = tmp_path / "front.pdf"
    briefing = tmp_path / "briefing.pdf"
    make_pdf(front, n_pages=1)
    make_pdf(briefing, n_pages=2)

    _prepend_pdf(front, briefing)

    result = PdfReader(str(briefing))
    assert len(result.pages) == 3  # 1 front + 2 briefing


def test_prepend_pdf_no_tmp_file_left(tmp_path):
    from pypdf import PdfWriter

    def make_pdf(path):
        w = PdfWriter()
        w.add_blank_page(width=612, height=792)
        with open(path, "wb") as f:
            w.write(f)

    front = tmp_path / "front.pdf"
    briefing = tmp_path / "briefing.pdf"
    make_pdf(front)
    make_pdf(briefing)

    _prepend_pdf(front, briefing)

    tmp_files = list(tmp_path.glob("*.tmp.pdf"))
    assert tmp_files == [], f"Unexpected temp files: {tmp_files}"
