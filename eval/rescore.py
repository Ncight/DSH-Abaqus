# -*- coding: utf-8 -*-
"""Re-run the checkers on the finished runs of one or more eval tags (after a checker was fixed or tightened).

    py rescore.py hard_base hard_live [--tasks x06_friction_slip,x07_plastic_collapse_beam]

Only the verdict fields of out/record.json are rewritten (pass, score, failed, checkError); what the agent did is untouched.
The previous verdict is kept in out/check_prev.json, and every change of verdict is printed.
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_eval  # noqa: E402


def main():
    args = sys.argv[1:]
    only = None
    if '--tasks' in args:
        i = args.index('--tasks')
        only = set(x.strip() for x in args[i + 1].split(',') if x.strip())
        del args[i:i + 2]
    if not args:
        raise SystemExit(__doc__)
    tasks = dict((t['id'], t) for t in run_eval.load_tasks(None, 'all'))
    changed = 0
    total = 0
    for tag in args:
        base = os.path.join(run_eval.RUNS, tag)
        for d in sorted(os.listdir(base)):
            out = os.path.join(base, d, 'out')
            rp = os.path.join(out, 'record.json')
            if not os.path.isfile(rp):
                continue
            with open(rp, encoding='utf-8') as f:
                rec = json.load(f)
            task = tasks.get(rec['task'])
            if task is None or (only and rec['task'] not in only):
                continue
            total += 1
            cp = os.path.join(out, 'check.json')
            if os.path.isfile(cp):
                shutil.copy2(cp, os.path.join(out, 'check_prev.json'))
            chk = run_eval.run_checker(task, os.path.join(base, d, 'work'), out)
            new_pass = bool(chk.get('pass'))
            new_failed = [c['name'] for c in chk.get('checks', []) if not c.get('pass')]
            if new_pass != bool(rec.get('pass')):
                changed += 1
                print('VERDICT CHANGED  %s/%s: %s -> %s  (failed checks now: %s)' % (
                    tag, d, 'PASS' if rec.get('pass') else 'FAIL', 'PASS' if new_pass else 'FAIL', ', '.join(new_failed) or '-'))
            elif sorted(new_failed) != sorted(rec.get('failed') or []):
                print('same verdict, other failed checks  %s/%s: %s -> %s' % (tag, d, rec.get('failed'), new_failed))
            rec.update({'pass': new_pass, 'score': chk.get('score', 0), 'checkError': chk.get('error'), 'failed': new_failed})
            with open(rp, 'w', encoding='utf-8') as f:
                json.dump(rec, f, ensure_ascii=False, indent=1)
    print('%d runs rescored, %d verdicts changed. Rebuild the tables with: py report.py %s' % (total, changed, ' '.join(args)))


if __name__ == '__main__':
    main()
