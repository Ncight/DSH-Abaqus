# -*- coding: utf-8 -*-
"""Local preview server: watches a workspace for *.inp and serves an Abaqus-style read-only viewer
with a review / approval gate.

    py preview_server.py --root E:\\DSH-Abaqus --port 8765 [--max-auto-mb 60]

Binds to 127.0.0.1 only. Standard library only.

Endpoints
    GET  /                      the viewer page
    GET  /api/list              *.inp files of the workspace, newest first
    GET  /api/model?path=       parsed model as JSON (gzip).  Files above the automatic size limit
                                answer {tooLarge: true, ...} instead; add &force=1 (page only) to parse
                                them anyway, which is still refused when memory is short
    GET  /api/highlight?path=&kind=&name=
    GET  /api/symbols?path=&step=
    GET  /api/review?path=      stored review decision + whether it still matches the file
    POST /api/review            {path, status: approved|rejected, note, comments[, dryRun]}

Review state lives in <root>/.abq/review.json and <root>/.abq/review/<inp base name>.md.

Memory: at most one parsed model is kept, and it is released before the next one is parsed.
"""
import argparse
import datetime
import gc
import hashlib
import json
import os
import sys
import threading
import time
import traceback
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import inp_model  # noqa: E402

# not scanned: tooling, benchmark scratch (.dsh-eval), fetched reference examples (refs)
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.dsh', '.agents', '.abq', 'tools', '.dsh-eval', 'refs'}
MAX_FILES = 500
MAX_POST = 256 * 1024
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml'}

ROOT = None
PORT = 8765
LIMIT_MB = None             # automatic parse limit; None = inp_model.max_auto_mb()
_cache = {}                 # path -> (key, model, gzip body)   -- never more than one entry
_lock = threading.Lock()
_sha_cache = {}
_review_lock = threading.Lock()


def limit_mb():
    return LIMIT_MB if LIMIT_MB else inp_model.max_auto_mb()


def list_inps():
    out = []
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            if f.lower().endswith('.inp'):
                p = os.path.join(d, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append({'path': os.path.relpath(p, ROOT).replace('\\', '/'), 'mtime': st.st_mtime,
                            'size': st.st_size})
                if len(out) >= MAX_FILES:
                    break
    out.sort(key=lambda x: -x['mtime'])
    return out


def resolve(rel):
    p = os.path.realpath(os.path.join(ROOT, rel))
    root = os.path.realpath(ROOT)
    if os.path.commonpath([p, root]) != root or not p.lower().endswith('.inp') or not os.path.isfile(p):
        raise ValueError('无效的文件路径: %s' % rel)
    return p


class TooLarge(Exception):
    def __init__(self, info):
        Exception.__init__(self, info.get('reason') or 'too large')
        self.info = info


def too_large_info(rel, size_bytes, refused=None):
    avail = inp_model.available_commit_gb()
    est = inp_model.estimate_memory_gb(size_bytes)
    info = {'tooLarge': True, 'path': rel, 'file': os.path.basename(rel),
            'sizeMB': round(size_bytes / 1048576.0, 1), 'limitMB': limit_mb(),
            'estimatedMemoryGB': round(est, 1), 'reserveGB': inp_model.MEMORY_RESERVE_GB,
            'availableCommitGB': None if avail is None else round(avail, 1)}
    info['canForce'] = avail is None or avail >= est + inp_model.MEMORY_RESERVE_GB
    if refused:
        info['refused'] = True
        info['reason'] = refused
    return info


def get_model(rel, force=False):
    """-> (model, gzip body of the view).  Raises TooLarge for files that must not be parsed now."""
    p = resolve(rel)
    st = os.stat(p)
    key = (st.st_mtime, st.st_size)
    with _lock:
        hit = _cache.get(p)
        if hit and hit[0] == key:
            return hit[1], hit[2]
        if st.st_size > limit_mb() * 1048576.0 and not force:
            raise TooLarge(too_large_info(rel, st.st_size))
        _cache.clear()              # release the previous model before parsing the next one
        gc.collect()
        t0 = time.time()
        try:
            mdl = inp_model.Model(p, force=force, max_auto_mb=limit_mb())
        except inp_model.TooLargeError as e:            # the files it includes push it over the limit
            raise TooLarge(too_large_info(rel, int(e.size_mb * 1048576)))
        except inp_model.MemoryRefusedError as e:
            raise TooLarge(too_large_info(rel, int(e.size_mb * 1048576), str(e)))
        except MemoryError:
            gc.collect()
            raise TooLarge(too_large_info(rel, st.st_size, '解析时内存不足, 已放弃并释放'))
        extra = {'path': rel, 'mtime': st.st_mtime, 'size': st.st_size,
                 'parseSeconds': round(time.time() - t0, 2)}
        gz = zlib.compressobj(1, zlib.DEFLATED, 31)
        parts = []
        try:
            for piece in mdl.view_json_iter(extra):
                parts.append(gz.compress(piece))
            parts.append(gz.flush())
        except MemoryError:
            del mdl, parts
            gc.collect()
            raise TooLarge(too_large_info(rel, st.st_size, '生成视图时内存不足, 已放弃并释放'))
        body = b''.join(parts)
        _cache[p] = (key, mdl, body)
        return mdl, body


# ---------------------------------------------------------------- review state
def abq_dir():
    return os.path.join(os.path.realpath(ROOT), '.abq')


def inside_abq(path):
    base = abq_dir()
    return os.path.commonpath([os.path.realpath(path), base]) == base


def file_sha256(p):
    st = os.stat(p)
    key = (p, st.st_mtime, st.st_size)
    hit = _sha_cache.get(p)
    if hit and hit[0] == key:
        return hit[1]
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    _sha_cache[p] = (key, h.hexdigest())
    return h.hexdigest()


def load_reviews():
    p = os.path.join(abq_dir(), 'review.json')
    try:
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get('files'), dict):
            return data
    except (OSError, ValueError):
        pass
    return {'version': 1, 'files': {}}


def write_atomic(path, text):
    if not inside_abq(path):
        raise ValueError('拒绝写入 .abq 目录之外: %s' % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def review_state(rel):
    p = resolve(rel)
    entry = load_reviews()['files'].get(rel)
    sha = file_sha256(p)
    current = bool(entry) and entry.get('sha256') == sha
    state = 'none' if not entry else (entry.get('status') if current else 'stale')
    return {'path': rel, 'entry': entry, 'current': current, 'sha256': sha, 'state': state}


def clean_text(v, limit):
    return str(v if v is not None else '').replace('\r\n', '\n').replace('\r', '\n').strip()[:limit]


def review_markdown(rel, entry, checks=None):
    """the 审查意见单: what the analyst decided, in a form an LLM agent can act on directly"""
    approved = entry['status'] == 'approved'
    out = ['# 审查意见单：%s' % os.path.basename(rel), '',
           '- 文件：`%s`' % rel,
           '- 结论：**%s**' % ('批准求解' if approved else '退回修改'),
           '- 时间：%s' % entry['decidedAt'],
           '- 文件指纹 (sha256)：`%s`' % entry['sha256'], '']
    comments = entry.get('comments') or []
    if comments:
        out += ['## 需要修改的项目（%d 项）' % len(comments), '']
        for i, c in enumerate(comments, 1):
            out += ['%d. 对象：%s' % (i, c.get('target') or '（未指明）'),
                    '   - 当前值：%s' % (c.get('current') or '（未填）'),
                    '   - 要求改为：%s' % (c.get('note') or '（未填）')]
        out.append('')
    elif not approved:
        out += ['## 需要修改的项目', '', '（没有逐项标注，见下面的总体意见）', '']
    out += ['## 总体意见', '', entry.get('note') or '（无）', '']
    if checks and not approved:
        bad = [c for c in checks if c['level'] in ('error', 'warn')]
        if bad:
            out += ['## 预览工具体检中的错误与警告（供参考）', '']
            for c in bad[:12]:
                out.append('- [%s] %s' % ('错误' if c['level'] == 'error' else '警告', c['msg']))
            if len(bad) > 12:
                out.append('- … 另有 %d 条' % (len(bad) - 12))
            out.append('')
    if approved:
        out += ['> 本批准只对上面指纹对应的文件内容有效；文件一旦改动，需要重新审查。', '']
    else:
        out += ['> 请按上面各项修改建模脚本，重新写出 INP 后再提交审查；未列出的内容不要改动。', '']
    return '\n'.join(out)


def save_review(body):
    rel = clean_text(body.get('path'), 1000).replace('\\', '/')
    p = resolve(rel)
    status = body.get('status')
    if status not in ('approved', 'rejected'):
        raise ValueError('status 只能是 approved 或 rejected')
    comments = []
    raw = body.get('comments') or []
    if not isinstance(raw, list):
        raise ValueError('comments 必须是数组')
    for c in raw[:200]:
        if isinstance(c, dict):
            item = {'target': clean_text(c.get('target'), 500), 'current': clean_text(c.get('current'), 2000),
                    'note': clean_text(c.get('note'), 2000)}
            if item['target'] or item['note']:
                comments.append(item)
    entry = {'sha256': file_sha256(p), 'status': status,
             'decidedAt': datetime.datetime.now().isoformat(timespec='seconds'),
             'note': clean_text(body.get('note'), 8000), 'comments': comments}
    checks = None
    with _lock:
        hit = _cache.get(p)
        if hit is not None:
            try:
                checks = hit[1].checks()
            except Exception:
                checks = None
    md = review_markdown(rel, entry, checks)
    base = os.path.splitext(os.path.basename(p))[0]
    md_path = os.path.join(abq_dir(), 'review', base + '.md')
    res = {'ok': True, 'path': rel, 'entry': entry, 'markdown': md,
           'mdPath': os.path.relpath(md_path, os.path.realpath(ROOT)).replace('\\', '/')}
    if body.get('dryRun'):
        res['dryRun'] = True
        return res
    with _review_lock:
        data = load_reviews()
        data['version'] = 1
        data['files'][rel] = entry
        write_atomic(os.path.join(abq_dir(), 'review.json'), json.dumps(data, ensure_ascii=False, indent=1) + '\n')
        write_atomic(md_path, md)
    res.update({'current': True, 'state': status})
    return res


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype, encoding=None):
        try:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if encoding:
                self.send_header('Content-Encoding', encoding)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8'),
                   'application/json; charset=utf-8')

    def _own_origins(self):
        return {'http://127.0.0.1:%d' % PORT, 'http://localhost:%d' % PORT, 'http://[::1]:%d' % PORT}

    def _host_ok(self):
        """only answer when addressed as a loopback host (a page on another site cannot rebind to us)"""
        host = (self.headers.get('Host') or '').strip().lower()
        return host in ('127.0.0.1:%d' % PORT, 'localhost:%d' % PORT, '[::1]:%d' % PORT)

    def _same_origin(self):
        origin = self.headers.get('Origin')
        return origin is None or origin.strip().lower() in self._own_origins()

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if not self._host_ok():
                return self._json({'error': 'forbidden host'}, 403)
            if u.path == '/':
                return self._file(os.path.join(HERE, 'static', 'index.html'))
            if u.path.startswith('/static/'):
                name = os.path.basename(u.path)
                return self._file(os.path.join(HERE, 'static', name))
            if u.path == '/favicon.ico':            # browsers ask for it; no icon, no error in the console
                return self._send(204, b'', 'image/x-icon')
            if u.path == '/api/list':
                return self._json({'root': ROOT, 'files': list_inps(), 'maxAutoMB': limit_mb()})
            if u.path == '/api/model':
                # parsing an oversized file is an explicit act of the page itself, never of a link
                force = q.get('force') == '1' and self.headers.get('X-Abq-Preview') == '1' and self._same_origin()
                try:
                    body = get_model(q['path'], force)[1]
                except TooLarge as e:
                    return self._json(e.info)
                if 'gzip' in (self.headers.get('Accept-Encoding') or ''):
                    return self._send(200, body, 'application/json; charset=utf-8', 'gzip')
                return self._send(200, zlib.decompress(body, 31), 'application/json; charset=utf-8')
            if u.path == '/api/highlight':
                try:
                    mdl = get_model(q['path'])[0]
                except TooLarge as e:
                    return self._json(e.info)
                with _lock:
                    body = mdl.highlight_json(q.get('kind', ''), q.get('name', ''))
                return self._send(200, body, 'application/json; charset=utf-8')
            if u.path == '/api/symbols':
                try:
                    mdl = get_model(q['path'])[0]
                except TooLarge as e:
                    return self._json(e.info)
                with _lock:
                    return self._json(mdl.symbols(int(q.get('step', '-1'))))
            if u.path == '/api/review':
                return self._json(review_state(q['path']))
            if u.path == '/api/health':
                return self._json({'ok': True, 'pid': os.getpid(), 'root': ROOT, 'maxAutoMB': limit_mb(),
                                   'cached': [os.path.basename(k) for k in _cache]})
            self._json({'error': 'not found'}, 404)
        except Exception as e:  # report to the page instead of dying
            self._json({'error': '%s: %s' % (type(e).__name__, e),
                        'trace': traceback.format_exc().splitlines()[-6:]}, 500)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if not self._host_ok():
                return self._reject(403, 'forbidden host', n)
            if not self._same_origin():
                return self._reject(403, '跨站请求被拒绝 (Origin 不是本服务)', n)
            ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if ctype != 'application/json':
                return self._reject(415, 'Content-Type 必须是 application/json', n)
            if n <= 0 or n > MAX_POST:
                return self._reject(413, '请求体为空或过大', n)
            raw = self.rfile.read(n)
            if u.path != '/api/review':
                return self._json({'error': 'not found'}, 404)
            try:
                body = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                return self._json({'error': '请求体不是合法的 JSON'}, 400)
            if not isinstance(body, dict):
                return self._json({'error': '请求体必须是 JSON 对象'}, 400)
            try:
                return self._json(save_review(body))
            except ValueError as e:
                return self._json({'error': str(e)}, 400)
        except Exception as e:
            self._json({'error': '%s: %s' % (type(e).__name__, e),
                        'trace': traceback.format_exc().splitlines()[-6:]}, 500)

    def _reject(self, code, msg, n):
        # the body is not read: close the connection so that it cannot be taken for the next request
        self.close_connection = True
        self._json({'error': msg}, code)

    def _file(self, p):
        if not os.path.isfile(p):
            return self._json({'error': 'not found'}, 404)
        with open(p, 'rb') as f:
            body = f.read()
        self._send(200, body, MIME.get(os.path.splitext(p)[1].lower(), 'application/octet-stream'))


def main():
    global ROOT, PORT, LIMIT_MB
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=os.path.abspath(os.path.join(HERE, '..', '..')))
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--max-auto-mb', type=float, default=None,
                    help='INP files above this size are only parsed on request '
                         '(default: env ABQ_PREVIEW_MAX_AUTO_MB or 60)')
    a = ap.parse_args()
    ROOT = os.path.abspath(a.root)
    PORT = a.port
    LIMIT_MB = a.max_auto_mb if a.max_auto_mb and a.max_auto_mb > 0 else None
    # one panel per port: on Windows a second server could bind the same port and the two would answer at random
    import socket
    probe = socket.socket()
    probe.settimeout(0.5)
    try:
        probe.connect(('127.0.0.1', a.port))
        print('preview: port %d is already in use (the panel is probably running already: http://127.0.0.1:%d/)'
              % (a.port, a.port), flush=True)
        return
    except OSError:
        pass
    finally:
        probe.close()
    ThreadingHTTPServer.allow_reuse_address = False
    srv = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    srv.daemon_threads = True
    print('preview: http://127.0.0.1:%d/   root=%s   auto-parse limit %.0f MB' % (a.port, ROOT, limit_mb()),
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
