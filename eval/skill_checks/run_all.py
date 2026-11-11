# -*- coding: utf-8 -*-
"""Run every verification script behind the statements quoted in the skills (one Abaqus process at a time).

    py run_all.py [--only WORD] [--keep]

Each check is a short chain of abq commands in its own folder under .dsh-eval/skill_checks/ (gate off, low priority, the
abq tool's own memory guard applies).  A check passes when every command exits with 0 and the expected text, if one is
given, appears in the output of the last command.  Run this after editing a skill, after changing the abq tool and after
an Abaqus upgrade.  `py ../skill_snippets.py --calls` lists API names quoted in a skill that no executed script uses.
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ABQ = os.path.join(ROOT, 'abq.cmd')
SK = os.path.join(ROOT, '.dsh', 'skills')
WORK = os.path.join(ROOT, '.dsh-eval', 'skill_checks')

# (title, skill, [(kind, argument), ...], text expected in the output of the last command or None)
#   kind: script = abq script (CAE), run = abq run, python = abq python (ODB), syspy = system Python
CHECKS = [
    ('recipes on a model built with CAE', 'abaqus-postprocess',
     [('script', 'fx_cae_beam.py'), ('run', 'fx_cae'), ('python', 'chk_odb_recipes.py fx_cae.odb')], 'RECIPES OK'),
    ('abq odb --set / --series and the summary.json key paths', 'abaqus-postprocess',
     [('script', 'fx_cae_beam.py'), ('run', 'fx_cae'),
      ('odb', 'fx_cae.odb --set FixEnd --set Tip --series RF,U --probe 0.025,0.05,1'), ('syspy', 'chk_summary_json.py')], 'SUMMARY OK'),
    ('recipes on a directly written input file', 'abaqus-postprocess',
     [('syspy', 'fx_flat_inp.py'), ('run', 'fx_flat'), ('python', 'chk_odb_recipes.py fx_flat.odb')], 'RECIPES OK'),
    ('templates template_solid_static.py and read_odb_example.py', 'abaqus-postprocess',
     [('script', 'template_solid_static.py'), ('run', 'beam_demo'), ('python', 'read_odb_example.py beam_demo')], 'mid_deflection'),
    ('concrete damaged plasticity and Mohr-Coulomb', 'abaqus-materials-units',
     [('script', 'chk_materials.py'), ('run', 'chk_cdp'), ('run', 'chk_mc'), ('python', 'chk_materials_read.py')], 'MATERIALS OK'),
    ('Tie with 2021 argument names, position tolerance', 'abaqus-contact',
     [('script', 'chk_tie.py'), ('run', 'chk_tie'), ('run', 'chk_tie_gap'), ('python', 'chk_tie_read.py')], 'TIE OK'),
    ('2-D contact of two deformable bodies', 'abaqus-contact', [('script', 'snip_2d_contact.py'), ('run', 'snip_contact')], None),
    ('analytical rigid indenter, reading contact pressure', 'abaqus-contact',
     [('script', 'snip_rigid_contact.py'), ('run', 'snip_rigid'), ('python', 'read_cpress.py snip_rigid.odb')], 'CPRESS OK'),
    ('rules for an axisymmetric analytical rigid surface', 'abaqus-contact', [('script', 'snip_axi_rigid.py')], None),
    ('closed profile for BaseShell', 'abaqus-cae-scripting', [('script', 'snip_axi_ball.py')], None),
    ('axisymmetric part, biased seeds', 'abaqus-cae-scripting', [('script', 'snip_axisym.py'), ('run', 'snip_axisym')], None),
    ('findAt returns None instead of raising', 'abaqus-cae-scripting', [('script', 'snip_findat.py')], None),
    ('built-in sum() is shadowed in CAE', 'abaqus-cae-scripting', [('script', 'snip_sum.py')], None),
    ('frame of beams and a shell plate', 'abaqus-beams-shells', [('script', 'snip_beam_shell.py'), ('run', 'snip_beam_shell')], None),
    ('beam section orientation', 'abaqus-beams-shells', [('script', 'snip_beam_orient.py'), ('run', 'snip_orient')], None),
    ('frequency step, explicit step with plasticity', 'abaqus-dynamics',
     [('script', 'snip_dynamics.py'), ('run', 'snip_freq'), ('run', 'snip_explicit')], None),
    ('rigid elements and contact pair keywords', 'abaqus-inp-direct', [('syspy', 'gen_rigid_contact_inp.py'), ('run', 'rigid_inp')], None),
    ('generator template gen_inp_template.py', 'abaqus-inp-direct', [('syspy', 'gen_inp_template.py'), ('run', 'block')], None),
    ('viscous base, consolidation, undrained strength, CDP tension (keyword form)', 'abaqus-geotech',
     [('syspy', 'gen_geotech_inp.py'), ('run', 'geo_wave'), ('run', 'geo_consol'), ('run', 'geo_mc'), ('run', 'geo_cdp'),
      ('python', 'chk_geotech_read.py')], 'GEOTECH OK'),
]


def run(kind, arg, cwd):
    env = dict(os.environ, ABQ_GATE='off', ABQ_LOW_PRIORITY='1', PYTHONUTF8='1')
    if kind == 'syspy':
        cmd = [sys.executable] + arg.split(' ')
    else:
        cmd = ['cmd.exe', '/d', '/c', ABQ, kind] + arg.split(' ')
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=1800)
    out = (p.stdout + p.stderr).decode('utf-8', errors='replace')
    return p.returncode, out


def main():
    args = sys.argv[1:]
    only = args[args.index('--only') + 1].lower() if '--only' in args else ''
    skills = os.path.abspath(args[args.index('--skills') + 1]) if '--skills' in args else SK    # e.g. a candidate's skills
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    rows = []
    for n, (title, skill, steps, expect) in enumerate(CHECKS, 1):
        if only and only not in title.lower() and only not in skill.lower():
            continue
        cwd = os.path.join(WORK, '%02d' % n)
        shutil.rmtree(cwd, ignore_errors=True)
        os.makedirs(cwd)
        for fn in os.listdir(HERE):                            # the scripts refer to each other's files by bare name
            if fn.endswith('.py') and fn != 'run_all.py' and not fn.startswith('cpu_bench'):
                shutil.copy2(os.path.join(HERE, fn), cwd)
        for d in os.listdir(skills):                           # the templates shipped with the skills: always run a copy,
            for fn in os.listdir(os.path.join(skills, d)):     # never inside the skills folder itself
                if fn.endswith('.py'):
                    shutil.copy2(os.path.join(skills, d, fn), cwd)
        t0 = time.time()
        ok, note, out = True, '', ''
        for kind, arg in steps:
            code, out = run(kind, arg, cwd)
            if code != 0:
                ok = False
                tail = [l.strip() for l in out.splitlines() if l.strip()][-4:]
                note = '`%s %s` exit %s: %s' % (kind, os.path.basename(arg.split(' ')[0]), code, ' | '.join(tail)[:400])
                break
        if ok and expect and expect not in out:
            ok, note = False, 'expected text %r not in the output of the last command' % expect
        rows.append({'check': title, 'skill': skill, 'ok': ok, 'seconds': round(time.time() - t0, 1), 'note': note})
        print('%s  %-28s %-52s %5.0f s  %s' % ('ok  ' if ok else 'FAIL', skill, title, time.time() - t0, note), flush=True)
        if ok and '--keep' not in args:
            shutil.rmtree(cwd, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    with open(os.path.join(WORK, 'report.json'), 'w', encoding='utf-8') as f:
        json.dump({'time': time.strftime('%Y-%m-%dT%H:%M:%S'), 'rows': rows}, f, ensure_ascii=False, indent=1)
    bad = [r for r in rows if not r['ok']]
    print('\n%d checks, %d failed' % (len(rows), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
