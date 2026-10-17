"""DocumentBegin carries the header's maintenance version (wire version 3).

libviprs/acadsharp-rs-tests#61: wire 2's DocumentBegin had a view count and the
drawing version, and a reserved word where the schema-1 `document` record has
the header's maintenance version. Every engine disagreed about what the number
was, so the field was excluded from the parity comparison for want of a way to
check it, and the wire wrote nothing.

pytest here has no .NET (ADR 0001), so what this reads is what the fixture
generator recorded: for each dumped fixture, the DocumentBegin the composed
stream carried. The recording is a run of the shim, and
``test_shim_digest.py`` is what refuses one that is stale.
"""

import re

import pytest
from g13_support import manifest

MANIFEST = manifest()

# The AC1032 save the parity corpus calls acadsharp-ac1032 (the same bytes, by
# sha256), whose header holds a maintenance version of 228. The reference
# oracle reads the same number out of the same header.
REAL = "real_AC1032.dwg"
REAL_DRAWING_VERSION = 1032
REAL_MAINTENANCE_VERSION = 228


def recorded(fixture):
    return MANIFEST["fixtures"][fixture]


class TestTheManifestRecordsWhatDocumentBeginCarried:
    def test_every_dumped_fixture_has_the_two_fields(self):
        missing = [
            name
            for name, entry in MANIFEST["fixtures"].items()
            if "document_maintenance_version" not in entry or "document_drawing_version" not in entry
        ]
        assert not missing, (
            f"{missing} were recorded without what DocumentBegin carried, so nothing "
            "here can say whether the maintenance version crossed"
        )

    def test_a_real_save_carries_its_headers_maintenance_version(self):
        entry = recorded(REAL)
        assert entry["document_drawing_version"] == REAL_DRAWING_VERSION
        assert entry["document_maintenance_version"] == REAL_MAINTENANCE_VERSION, (
            f"{REAL} recorded a maintenance version of {entry['document_maintenance_version']}"
            f", and its header holds {REAL_MAINTENANCE_VERSION}. Zero is what wire version 2 "
            "wrote, because the word was reserved"
        )

    def test_it_is_not_the_drawing_version_in_another_place(self):
        entry = recorded(REAL)
        assert entry["document_maintenance_version"] != entry["document_drawing_version"]


class TestTheWireDocumentSaysSo:
    @pytest.fixture(scope="class")
    def wire(self):
        import os

        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "docs", "WIRE.md")) as f:
            return f.read()

    def test_document_begin_names_the_field(self, wire):
        m = re.search(r"\*\*1 `DocumentBegin`\*\*:(.*?)\n\n", wire, re.S)
        assert m, "WIRE.md no longer has a paragraph for record 1"
        body = m.group(1)
        assert "maintenance_version" in body, (
            "record 1's layout does not name maintenance_version, so a consumer reading "
            "the document would still treat the word as reserved"
        )
        assert "24 bytes" in " ".join(body.split()), "the field takes the word that was reserved"
