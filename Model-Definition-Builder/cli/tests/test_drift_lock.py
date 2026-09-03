# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The lockfile's manifest hash must not depend on the contributor's OS: a
Windows checkout reads definition.yaml with CRLF, Linux and CI with LF, and a
raw hash re-stamped every lock file whenever the other platform regenerated."""
import hashlib
import json

from mdb.core import drift


def test_manifest_hash_is_the_same_for_crlf_and_lf_checkouts(tmp_path):
    lf = b"schema: 1\nkind: llm\nmodel_id: x\n"
    crlf = lf.replace(b"\n", b"\r\n")
    files = {"options.json": b"{}\n"}

    drift.write_lock(tmp_path, lf, files)
    from_lf = json.loads((tmp_path / drift.LOCK_FILENAME).read_text(encoding="utf-8"))
    drift.write_lock(tmp_path, crlf, files)
    from_crlf = json.loads((tmp_path / drift.LOCK_FILENAME).read_text(encoding="utf-8"))

    assert from_lf["manifest_sha256"] == from_crlf["manifest_sha256"]
    # ...and it is the hash of the canonical (LF) bytes, i.e. what CI computes.
    assert from_lf["manifest_sha256"] == hashlib.sha256(lf).hexdigest()
    assert from_lf["files"] == from_crlf["files"] == {"options.json": hashlib.sha256(b"{}\n").hexdigest()}
