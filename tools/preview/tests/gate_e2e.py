# -*- coding: utf-8 -*-
"""End-to-end test of the review contract: the real preview server writes the decision, the real abq tool reads it."""
import json
import os
import subprocess
import sys
import urllib.request

BASE = 'http://127.0.0.1:8765'
REL = 'examples/demo_beam/demo_beam.inp'
CWD = r'E:\DSH-Abaqus\examples\demo_beam'
ABQ = r'E:\DSH-Abaqus\abq.cmd'


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def post(body):
    req = urllib.request.Request(BASE + '/api/review', data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def abq(*args):
    env = dict(os.environ)
    env.pop('ABQ_GATE', None)
    p = subprocess.run(['cmd.exe', '/d', '/c', ABQ] + list(args), cwd=CWD, capture_output=True, env=env)
    return p.returncode, p.stdout.decode('utf-8', 'replace')


def show(title, code, text, n=9):
    print('--- %s (exit %s)' % (title, code))
    for line in text.splitlines()[:n]:
        print('   ' + line)


print('files listed:', [f['path'] for f in get('/api/list')['files']][:6])
r = get('/api/review?path=' + REL)
print('initial state:', r.get('state'), 'current:', r.get('current'))

code, text = abq('run', 'demo_beam')
show('abq run before any review', code, text, 4)

r = post({'path': REL, 'status': 'rejected', 'note': '载荷请再核对',
          'comments': [{'target': '载荷 *DSLOAD PadTop', 'current': 'P, 500', 'note': '压强应为 300 kPa'}]})
print('POST rejected ->', {k: r.get(k) for k in ('ok', 'state', 'error')})
code, text = abq('run', 'demo_beam')
show('abq run after rejection', code, text, 16)

r = post({'path': REL, 'status': 'approved', 'note': '', 'comments': []})
print('POST approved ->', {k: r.get(k) for k in ('ok', 'state', 'error')})
code, text = abq('run', 'demo_beam')
show('abq run after approval', code, text, 4)

for f in ('review.json', os.path.join('review', 'demo_beam.md')):
    p = os.path.join(r'E:\DSH-Abaqus\.abq', f)
    print('exists', p, os.path.isfile(p))
# bad requests must be refused
for name, hdr in (('wrong content type', {'Content-Type': 'text/plain'}),
                  ('foreign origin', {'Content-Type': 'application/json', 'Origin': 'http://evil.example'})):
    req = urllib.request.Request(BASE + '/api/review', data=json.dumps({'path': REL, 'status': 'approved'}).encode(), headers=hdr)
    try:
        urllib.request.urlopen(req, timeout=10)
        print(name, '-> ACCEPTED (bad)')
    except urllib.error.HTTPError as e:
        print(name, '-> refused with HTTP', e.code)
