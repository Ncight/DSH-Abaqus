# -*- coding: utf-8 -*-
"""Tool change that goes with guidance v2: `abq run` reports results and the verification verdict by itself.

Before: script -> check -> datacheck -> run -> odb -> verify were six separate calls the model had to remember.
After:  script (auto-checks the INP) -> run (pre-check, gate, solve, diagnosis, result digest, verification).
The separate commands stay for when more detail is needed.   py patch_tool_v2.py   (idempotent)
"""
import ast

p = r'E:\DSH-Abaqus\tools\abq\abq.py'
t = open(p, encoding='utf-8').read()
if 'def verify_job(' in t:
    print('already applied')
    raise SystemExit

a = ("def cmd_verify(a):\n"
     "    cwd, job = resolve_job(a.job)\n"
     "    d = diag.diagnose(cwd, job)\n"
     "    R = []     # (level, text)  level: FAIL / WARN / OK / INFO\n")
assert a in t, 'verify head'
t = t.replace(a, "def verify_job(cwd, job, d=None, data=None):\n"
                 "    \"\"\"checks of a finished job -> (verdict, items, extractor data); prints the report, writes <job>.verify.json\"\"\"\n"
                 "    if d is None:\n"
                 "        d = diag.diagnose(cwd, job)\n"
                 "    R = []     # (level, text)  level: FAIL / WARN / OK / INFO\n")

a = ("    odb = os.path.join(cwd, job + '.odb')\n"
     "    data = None\n"
     "    if os.path.isfile(odb) and d['state'] == 'completed':\n"
     "        data, err = extract(odb)\n")
assert a in t, 'verify extract'
t = t.replace(a, "    odb = os.path.join(cwd, job + '.odb')\n"
                 "    if data is None and os.path.isfile(odb) and d['state'] == 'completed':\n"
                 "        data, err = extract(odb)\n")

a = ("    return 0 if verdict != 'FAIL' else 1\n"
     "\n"
     "\n"
     "# ---------------------------------------------------------------------------- sweep\n")
assert a in t, 'verify tail'
t = t.replace(a, "    return verdict, R, data\n"
                 "\n"
                 "\n"
                 "def cmd_verify(a):\n"
                 "    cwd, job = resolve_job(a.job)\n"
                 "    verdict, _, _ = verify_job(cwd, job)\n"
                 "    return 0 if verdict != 'FAIL' else 1\n"
                 "\n"
                 "\n"
                 "# ---------------------------------------------------------------------------- sweep\n")

a = ("    if d['state'] == 'completed' and not to:\n"
     "        P('下一步: abq odb %s.odb （结果摘要）→ abq verify %s （收工前核查）' % (job, job))\n"
     "        return 0\n")
assert a in t, 'run tail'
t = t.replace(a, "    if d['state'] == 'completed' and not to:\n"
                 "        if a.no_post:\n"
                 "            P('下一步: abq odb %s.odb （结果摘要）→ abq verify %s （收工前核查）' % (job, job))\n"
                 "            return 0\n"
                 "        data, err = extract(os.path.join(cwd, job + '.odb'))\n"
                 "        if data is None:\n"
                 "            P('结果库读取失败: %s' % err)\n"
                 "        else:\n"
                 "            P('—— 结果摘要（模型自身单位制下的原始值）——', *odb_digest(data))\n"
                 "        verdict, _, _ = verify_job(cwd, job, d, data)\n"
                 "        P('更多细节: abq odb %s.odb（--step 名字 --frame N 指定帧；--probe x,y,z --series S,U 取某点时程）；完整 JSON: %s' % (\n"
                 "            job, (data or {}).get('_json', '—')))\n"
                 "        return 0 if verdict != 'FAIL' else 1\n")

a = "    s.add_argument('--force', action='store_true', help='忽略静态体检的错误')\n    s.set_defaults(fn=cmd_run)\n"
assert a in t, 'run args'
t = t.replace(a, "    s.add_argument('--force', action='store_true', help='忽略静态体检的错误')\n"
                 "    s.add_argument('--no-post', action='store_true', help='求解后不自动给出结果摘要和核查')\n"
                 "    s.set_defaults(fn=cmd_run)\n")

a = "    abq run <job|file.inp>         完整求解并给出诊断\n"
assert a in t, 'doc'
t = t.replace(a, "    abq run <job|file.inp>         求解：自动体检、求解诊断、结果摘要、核查结论\n")
ast.parse(t)
open(p, 'w', encoding='utf-8').write(t)
print('abq run now reports results and the verification verdict')
