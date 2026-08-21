# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The .eml extractor.

Email is the format where the metadata is usually why someone is searching -
who wrote it, to whom, when - so the headers have to survive into something
retrievable rather than being dropped in favour of the body.
"""
from rag_core.extractors import ExtractorRegistry
from rag_core.extractors.builtin import BUILTINS, EmailExtractor

PLAIN = b"""From: alice@example.com
To: bob@example.com
Subject: Travel policy update
Date: Tue, 29 Jul 2026 09:15:00 +0000

Economy class is now standard for flights under eight hours.
Book at least two weeks ahead where possible.
"""

MULTIPART = b"""From: alice@example.com
To: bob@example.com
Subject: Quarterly numbers
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="XX"

--XX
Content-Type: text/plain; charset="utf-8"

The plain text version of the numbers.
--XX
Content-Type: text/html; charset="utf-8"

<html><body><p>The HTML version</p></body></html>
--XX--
"""

HTML_ONLY = b"""From: alice@example.com
Subject: HTML only
MIME-Version: 1.0
Content-Type: text/html; charset="utf-8"

<html><body><h1>Heading</h1><p>Body text here.</p></body></html>
"""


def _extract(raw, name="mail.eml"):
    return EmailExtractor().extract(raw, "/mail/" + name)


def test_headers_become_a_retrievable_heading():
    elements = _extract(PLAIN)
    heading = elements[0]
    assert heading["type"] == "heading"
    for expected in ("From: alice@example.com", "To: bob@example.com",
                     "Subject: Travel policy update", "29 Jul 2026"):
        assert expected in heading["text"]


def test_the_body_carries_the_subject_as_its_heading_path():
    """So a body chunk retrieved on its own still says which mail it came
    from - the chunker builds heading_path from this."""
    body = _extract(PLAIN)[1]
    assert body["type"] == "text"
    assert body["heading_path"] == "Travel policy update"
    assert "Economy class is now standard" in body["text"]


def test_multipart_prefers_the_plain_text_part():
    body = _extract(MULTIPART)[1]
    assert "plain text version" in body["text"]
    assert "HTML version" not in body["text"]


def test_an_html_only_mail_is_stripped_to_text():
    elements = _extract(HTML_ONLY)
    body = elements[-1]["text"]
    assert "Body text here" in body
    assert "<p>" not in body and "<html>" not in body


def test_a_mail_without_a_subject_still_extracts():
    raw = b"From: a@b.c\n\nJust a body, no subject line.\n"
    elements = _extract(raw)
    assert elements[-1]["heading_path"] == "(no subject)"
    assert "Just a body" in elements[-1]["text"]


def test_attachments_do_not_become_document_text():
    raw = (b'From: a@b.c\nSubject: With attachment\nMIME-Version: 1.0\n'
           b'Content-Type: multipart/mixed; boundary="YY"\n\n'
           b'--YY\nContent-Type: text/plain\n\nThe real body.\n'
           b'--YY\nContent-Type: text/plain; name="notes.txt"\n'
           b'Content-Disposition: attachment; filename="notes.txt"\n\n'
           b'ATTACHMENT CONTENT\n--YY--\n')
    text = " ".join(e["text"] for e in _extract(raw))
    assert "The real body" in text
    assert "ATTACHMENT CONTENT" not in text


def test_an_empty_mail_yields_nothing_rather_than_a_blank_chunk():
    assert _extract(b"\n\n") == []


def test_the_registry_routes_eml_and_msg():
    registry = ExtractorRegistry()
    assert registry.chain_for("/mail/note.eml")[0] == "email"
    # .msg is a compound OLE file, not RFC 822 - markitdown owns it. It only
    # appears in the chain where markitdown is installed, so assert the
    # ROUTING rather than availability.
    assert ".msg" in dict((e.name, e.formats) for e in BUILTINS)["markitdown"]


def test_email_needs_no_extra_packages():
    """It runs on a stock compute python; the office formats do not."""
    assert EmailExtractor().requires == []
