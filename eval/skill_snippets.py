# -*- coding: utf-8 -*-
"""List the code inside the skills, so that it can be checked against scripts that were actually run.

    py skill_snippets.py                 summary: fenced blocks per skill
    py skill_snippets.py --dump DIR      write every python / inp block to DIR/<skill>__<n>.<ext>
    py skill_snippets.py --calls         API names used in python blocks (method calls and attribute reads); names that no
                                         executed script uses are flagged UNVERIFIED (exit code 1)
    py skill_snippets.py --verbatim [SKILL]   statements of the python blocks that appear word for word in an executed
                                         script; with a skill name the missing ones are listed
    py skill_snippets.py --skills DIR ...     look at another copy of the skills (a candidate) instead of the live ones

"Executed scripts" are the verification scripts in eval/skill_checks/ (run by skill_checks/run_all.py), the templates
shipped with the skills, the verified case library and the reference solutions of the benchmark.  --calls is a coverage
check by name: it proves that an interface exists and is used somewhere in running code, not that every argument quoted in
the skill is right.  --verbatim is the strict form; blocks written after 2026-11-10 are kept identical to a check script.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILLS = os.path.join(ROOT, '.dsh', 'skills')
CHECKS = os.path.join(HERE, 'skill_checks')
FENCE = re.compile(r'^```(\w*)\s*$')


def blocks(path):
    out = []
    lang, buf, start = None, [], 0
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            m = FENCE.match(line.rstrip('\n'))
            if m and lang is None:
                lang, buf, start = (m.group(1) or 'text'), [], i
            elif m and lang is not None:
                out.append((lang, start, buf))
                lang = None
            elif lang is not None:
                buf.append(line.rstrip('\n'))
    return out


def names(code):
    """API names in a piece of python: .method( calls and .attribute reads (lower-camel identifiers)"""
    code = re.sub(r'#.*', '', code)
    code = re.sub(r"'[^'\n]*'|\"[^\"\n]*\"", "''", code)
    calls = set(re.findall(r'\.([A-Za-z_][A-Za-z0-9_]*)\s*\(', code))
    attrs = set(re.findall(r'\.([a-z][A-Za-z0-9_]*)\b(?!\s*\()', code))
    kw = set(re.findall(r'\b([a-z][A-Za-z0-9]*)=', code))
    return calls, attrs, kw


def code_lines(text):
    """python source -> set of statements, comments and spacing removed (so that the same statement compares equal)"""
    out = set()
    for line in text.splitlines():
        quote, cut = None, len(line)
        for i, ch in enumerate(line):                      # cut at the first # that is not inside a string
            if quote:
                if ch == quote and line[i - 1] != '\\':
                    quote = None
            elif ch in '\'"':
                quote = ch
            elif ch == '#':
                cut = i
                break
        norm = re.sub(r'\s+', '', line[:cut])
        if norm and not norm.startswith('#'):
            out.add(norm)
    return out


def verbatim(skills, only):
    """lines of the python blocks of a skill that appear word for word in a script that is run by skill_checks/run_all.py
    or shipped with a skill.  Stricter than --calls; expected to be complete only for the blocks written that way."""
    have = set()
    for base in (CHECKS, SKILLS):
        for r, _, fs in os.walk(base):
            for fn in fs:
                if fn.endswith('.py'):
                    with open(os.path.join(r, fn), encoding='utf-8', errors='replace') as f:
                        have |= code_lines(f.read())
    missing_total = 0
    for d in skills:
        if only and only not in d:
            continue
        miss = []
        n = 0
        for lang, start, buf in blocks(os.path.join(SKILLS, d, 'SKILL.md')):
            if lang not in ('python', 'py'):
                continue
            for i, line in enumerate(buf):
                for norm in code_lines(line):
                    n += 1
                    if norm not in have:
                        miss.append((start + 1 + i, line.strip()))
        print('%-26s %3d statements, %3d not found word for word in an executed script' % (d, n, len(miss)))
        if only:
            for ln, text in miss:
                print('    line %d: %s' % (ln, text[:120]))
        missing_total += len(miss)
    return missing_total


def main():
    global SKILLS
    args = sys.argv[1:]
    if '--skills' in args:                                  # e.g. a candidate: --skills eval/variants/v3/.dsh/skills
        SKILLS = os.path.abspath(args[args.index('--skills') + 1])
    skills = sorted(d for d in os.listdir(SKILLS) if os.path.isfile(os.path.join(SKILLS, d, 'SKILL.md')))
    if '--verbatim' in args:
        i = args.index('--verbatim')
        only = args[i + 1] if len(args) > i + 1 and not args[i + 1].startswith('--') else ''
        return 1 if verbatim(skills, only) and only else 0
    if '--dump' in args:
        dst = args[args.index('--dump') + 1]
        os.makedirs(dst, exist_ok=True)
    checked = {}
    # scripts that are known to run: the skill checks, the verified case library, the reference solutions of the benchmark
    for label, base, keep in (('checks', CHECKS, lambda fn: fn.endswith('.py') and fn != 'run_all.py'),
                              ('cases', os.path.join(ROOT, 'cases'), lambda fn: fn.endswith('.py')),
                              ('tasks', os.path.join(HERE, 'tasks'), lambda fn: fn == 'solve.py')):
        if not os.path.isdir(base):
            continue
        for r, _, fs in os.walk(base):
            for fn in fs:
                if keep(fn):
                    with open(os.path.join(r, fn), encoding='utf-8', errors='replace') as f:
                        checked['%s/%s' % (label, os.path.relpath(os.path.join(r, fn), base))] = names(f.read())
    for d in skills:                                        # the templates shipped with a skill are run as they are
        for fn in os.listdir(os.path.join(SKILLS, d)):
            if fn.endswith('.py'):
                with open(os.path.join(SKILLS, d, fn), encoding='utf-8', errors='replace') as f:
                    checked['(skill file) %s/%s' % (d, fn)] = names(f.read())
    total_unverified = 0
    for d in skills:
        bl = blocks(os.path.join(SKILLS, d, 'SKILL.md'))
        py = [b for b in bl if b[0] in ('python', 'py')]
        print('%-26s %2d blocks (%d python, %d lines of python)' % (d, len(bl), len(py), sum(len(b[2]) for b in py)))
        if '--dump' in args:
            for n, (lang, start, buf) in enumerate(bl, 1):
                ext = {'python': 'py', 'py': 'py'}.get(lang, 'txt')
                with open(os.path.join(dst, '%s__%02d_L%d.%s' % (d, n, start, ext)), 'w', encoding='utf-8') as f:
                    f.write('\n'.join(buf) + '\n')
        if '--calls' in args:
            for lang, start, buf in py:
                calls, attrs, _ = names('\n'.join(buf))
                for kind, group in (('call', calls), ('attr', attrs)):
                    for name in sorted(group):
                        where = [k for k, (c, a, _) in checked.items() if name in (c if kind == 'call' else (a | c))]
                        if not where:
                            total_unverified += 1
                            line = next((i for i, l in enumerate(buf) if re.search(r'\.%s\b' % re.escape(name), l)), 0)
                            print('    UNVERIFIED %s .%s   (SKILL.md line %d: %s)' % (kind, name, start + 1 + line, buf[line].strip()[:90]))
    if '--calls' in args:
        print('%d API names in skill snippets are used by no executed script' % total_unverified)
        return 1 if total_unverified else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
