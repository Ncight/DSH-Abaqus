# -*- coding: utf-8 -*-
"""Fingerprint of what a human approves in the preview panel - written once, imported by the panel
(preview_server.py) and by the ANSYS toolbox (tools/ans/ans.py).

    abaqus   the INP file                   sha256(file bytes)
             (unchanged; tools/abq/abq.py computes the same thing on its own)
    mapdl    the CDB and its solve segment  sha256( sha256(job.cdb) + "\\n" + sha256(job.solve.dat) )
             The two digests are lower-case hex text; a solve segment that does not exist yet stands
             as the text "none" in place of its digest.  The record also keeps both digests ("parts"),
             so that the panel can say which of the two files changed after a decision.

Standard library only.
"""
import hashlib
import os

ABAQUS = 'abaqus'
MAPDL = 'mapdl'
SOLVE_SUFFIX = '.solve.dat'
NO_SOLVE = 'none'


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def solve_path(cdb_path):
    """job.cdb -> job.solve.dat next to it"""
    return os.path.splitext(cdb_path)[0] + SOLVE_SUFFIX


def combine(cdb_sha, solve_sha):
    """the MAPDL fingerprint from the two file digests (solve_sha None = no solve segment yet)"""
    text = '%s\n%s' % (cdb_sha, solve_sha if solve_sha else NO_SOLVE)
    return hashlib.sha256(text.encode('ascii')).hexdigest()


def fingerprint(path, solver, sha=file_sha256):
    """-> {'sha256': fingerprint, 'parts': None | {'cdb': digest, 'solve': digest or None}}

    sha: the function that hashes one file (the panel passes its cached version)."""
    if solver == MAPDL:
        sp = solve_path(path)
        cdb = sha(path)
        solve = sha(sp) if os.path.isfile(sp) else None
        return {'sha256': combine(cdb, solve), 'parts': {'cdb': cdb, 'solve': solve}}
    return {'sha256': sha(path), 'parts': None}
