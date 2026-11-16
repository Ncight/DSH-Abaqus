# -*- coding: utf-8 -*-
"""Which solver a model file belongs to, told from its content and not from its suffix.

    sniff(path)    -> 'abaqus' | 'mapdl' | 'apdl' | None    what the first lines look like
    classify(path) -> 'abaqus' | 'mapdl' | None             what the panel lists the file as (None: not listed)

The panel previews two kinds of file: Abaqus input files and MAPDL CDB files written by CDWRITE.
  mapdl   a CDB: the CDWRITE header (first line "/COM,ANSYS RELEASE ...", then "*IF,_CDRDOFF" a few lines
          further down - every CDB written in step 0 of the ANSYS plan has both), or a block command
          (NBLOCK, EBLOCK, ETBLOCK, CMBLOCK) at the start of a line
  abaqus  a keyword line ("*HEADING", "*NODE", ...): one "*" followed by a word that is not one of the
          APDL commands that also start with "*" ("*SET", "*DIM", "*IF", ...)
  apdl    a MAPDL command stream ("/PREP7", "ET,", "FINISH", ...): not previewed
Only the first 200 effective lines count (blank lines and comment lines starting with "**" or "!" do not),
and at most 5000 lines or 1 MB are read.
A .inp that shows none of these - an include file with data lines only - is listed as Abaqus, as the panel
always did; a .cdb that is not a CDB is not listed.
Standard library only.
"""
import re

ABAQUS = 'abaqus'
MAPDL = 'mapdl'
APDL = 'apdl'
SUFFIXES = ('.inp', '.cdb')
EFFECTIVE_LINES = 200
MAX_LINES = 5000
MAX_BYTES = 1 << 20

# APDL commands whose name starts with "*" (MAPDL command reference); a line starting with one of them
# is not an Abaqus keyword line
APDL_STAR = set(('*ABBR *AFUN *ASK *AXPY *CFCLOS *CFCLOSE *CFOPEN *CFWRITE *COMP *CREATE *CYCLE *DEL *DIM *DMAT '
                 '*DO *DOT *DOWHILE *ECHO *EIGEN *ELSE *ELSEIF *END *ENDDO *ENDIF *EXIT *EXPORT *FFT *FREE *GET '
                 '*GO *IF *INIT *ITENGINE *LIST *LSBAC *LSDUMP *LSENGINE *LSFACTOR *LSRESTORE *MERGE *MFOURI '
                 '*MFUN *MOPER *MSG *MULT *MWRITE *NRM *PREAD *REMOVE *REPEAT *RETURN *SCAL *SET *SMAT *SORT '
                 '*SREAD *STATUS *TAXIS *TOPER *TREAD *ULIB *USE *VABS *VCOL *VCUM *VEC *VEDIT *VFACT *VFILL '
                 '*VFUN *VGET *VITRP *VLEN *VMASK *VOPER *VPLOT *VPUT *VREAD *VSCFUN *VSTAT *VWRITE *WRK').split())
CDB_BLOCK = re.compile(r'(NBLOCK|EBLOCK|ETBLOCK|CMBLOCK)\s*,')
APDL_LINE = re.compile(r'(/PREP7|/SOLU|/POST1|/POST26|/CLEAR|/BATCH|/FILNAM|/TITLE|/UNITS|FINISH\b|ET,|KEYOPT,|MP,|'
                       r'MPDATA,|SECTYPE,|SECDATA,|ANTYPE,|SOLVE\b|CDWRITE|CDREAD|NSEL,|ESEL,|CMSEL,)')
KEYWORD = re.compile(r'\*[A-Z]')


def sniff(path):
    """-> 'abaqus' | 'mapdl' | 'apdl' | None (None also when the file cannot be read)"""
    header = cdrdoff = apdl = False
    effective = 0
    read = 0
    try:
        with open(path, 'rb') as f:
            for n, raw in enumerate(f):
                read += len(raw)
                if n >= MAX_LINES or read > MAX_BYTES:
                    break
                s = raw.decode('latin-1').strip().upper()
                if not s or s.startswith('**') or s.startswith('!'):
                    continue
                effective += 1
                if effective == 1 and s.startswith('/COM,ANSYS RELEASE'):
                    header = True
                if header and '_CDRDOFF' in s:
                    cdrdoff = True
                if (header and cdrdoff) or CDB_BLOCK.match(s):
                    return MAPDL
                if KEYWORD.match(s):
                    word = ' '.join(s.split(',', 1)[0].split())
                    if word not in APDL_STAR:
                        return ABAQUS
                    apdl = True
                elif APDL_LINE.match(s):
                    apdl = True
                if effective >= EFFECTIVE_LINES:
                    break
    except OSError:
        return None
    return APDL if apdl else None


def classify(path):
    """-> the solver the panel lists the file under, or None when the panel does not list it"""
    low = path.lower()
    if low.endswith('.cdb'):
        return MAPDL if sniff(path) == MAPDL else None
    if low.endswith('.inp'):
        kind = sniff(path)
        if kind == MAPDL:           # a CDB written with the .inp suffix
            return MAPDL
        if kind == APDL:            # a MAPDL command stream: the panel does not preview those
            return None
        return ABAQUS               # Abaqus keywords, or no telling (data lines only): listed as before
    return None
