// Mouse and touch control of the preview camera: free rotation (a virtual trackball), pan and zoom.
//
// It replaces three.js OrbitControls, which keeps camera.up fixed: there a horizontal drag turns the model about the
// vertical axis only, and a vertical drag stops at the poles, so the model could not be turned about the other two
// axes (user, 2026-11-15).  Here every drag turns the model about the screen axes and camera.up turns with it:
//
//   left drag                     rotate freely; the model follows the cursor.  A drag that starts near the edge of the
//                                 view (outside rollZone of the inscribed circle) turns it about the line of sight,
//                                 as in Abaqus/CAE and ANSYS Mechanical
//   right drag, or left drag with Ctrl / Shift / Meta      pan
//   middle drag, wheel            zoom towards the target (wheel up: closer)
//   touch                         one finger rotates, two fingers pan and pinch
//
// What index.html uses: new ViewControls(camera, canvas), .target, .rotateSpeed, the 'change' event, update(),
// dispose().  The rotation step is the one of three.js TrackballControls (axis = drag direction x eye vector).
import { EventDispatcher, Quaternion, Vector2, Vector3 } from 'three';

const _change = { type: 'change' };
const _q = new Quaternion();
const _eye = new Vector3(), _dir = new Vector3(), _up = new Vector3(), _side = new Vector3(), _move = new Vector3(),
  _axis = new Vector3(), _v = new Vector3();

export class ViewControls extends EventDispatcher {
  constructor(object, domElement) {
    super();
    this.object = object;
    this.domElement = domElement;
    this.target = new Vector3();
    this.enabled = true;
    this.rotateSpeed = 1.0;      // radians for a drag across half the shorter side of the view
    this.zoomSpeed = 1.0;
    this.panSpeed = 1.0;
    this.rollZone = 0.9;         // drags that start outside this part of the inscribed circle roll
    this.minDistance = 0;
    this.maxDistance = Infinity;
    this.mode = null;            // 'rotate' | 'roll' | 'pan' | 'zoom' | 'touch' while a pointer is down
    this._pointers = new Map();  // pointerId -> Vector2 (client coordinates)
    this._last = new Vector2();
    this._touch = null;          // two fingers: { mid: Vector2, dist }
    this._onDown = this._onDown.bind(this);
    this._onMove = this._onMove.bind(this);
    this._onUp = this._onUp.bind(this);
    this._onWheel = this._onWheel.bind(this);
    this._onContext = e => e.preventDefault();
    domElement.style.touchAction = 'none';
    domElement.addEventListener('pointerdown', this._onDown);
    domElement.addEventListener('pointermove', this._onMove);
    domElement.addEventListener('pointerup', this._onUp);
    domElement.addEventListener('pointercancel', this._onUp);
    domElement.addEventListener('wheel', this._onWheel, { passive: false });
    domElement.addEventListener('contextmenu', this._onContext);
  }

  dispose() {
    const d = this.domElement;
    d.removeEventListener('pointerdown', this._onDown);
    d.removeEventListener('pointermove', this._onMove);
    d.removeEventListener('pointerup', this._onUp);
    d.removeEventListener('pointercancel', this._onUp);
    d.removeEventListener('wheel', this._onWheel);
    d.removeEventListener('contextmenu', this._onContext);
    this._pointers.clear();
    this.mode = null;
  }

  update() {
    this.object.lookAt(this.target);
    this.dispatchEvent(_change);
    return true;
  }

  // ------------------------------------------------------------------ the three motions (also used by the tests)
  // dx, dy: cursor movement in pixels (y down, as clientY)
  rotate(dx, dy) {
    const r = this._radius();
    const mx = dx / r, my = -dy / r;
    const angle = Math.hypot(mx, my) * this.rotateSpeed;
    if (!angle) return;
    _eye.copy(this.object.position).sub(this.target);
    _dir.copy(_eye).normalize();
    // screen axes from the current up vector (made orthogonal to the line of sight first)
    _up.copy(this.object.up).addScaledVector(_dir, -this.object.up.dot(_dir));
    if (_up.lengthSq() < 1e-20) _up.set(0, 0, 1).addScaledVector(_dir, -_dir.z);
    _up.normalize();
    _side.crossVectors(_up, _dir).normalize();
    _move.copy(_up).multiplyScalar(my).addScaledVector(_side, mx);
    _axis.crossVectors(_move, _eye).normalize();
    _q.setFromAxisAngle(_axis, angle);
    _eye.applyQuaternion(_q);
    this.object.up.copy(_up).applyQuaternion(_q);
    this.object.position.copy(this.target).add(_eye);
    this.update();
  }

  // a0, a1: the cursor's angle about the centre of the view before and after (radians, counter-clockwise on screen)
  roll(a0, a1) {
    let d = a1 - a0;
    if (d > Math.PI) d -= 2 * Math.PI;
    if (d < -Math.PI) d += 2 * Math.PI;
    if (!d) return;
    _dir.copy(this.object.position).sub(this.target).normalize();
    // the model turns with the cursor: the camera turns the other way about the line of sight
    _q.setFromAxisAngle(_dir, -d);
    this.object.up.applyQuaternion(_q);
    this.update();
  }

  pan(dx, dy) {
    const h = this._rect().height || 1;
    const dist = _eye.copy(this.object.position).sub(this.target).length();
    const perPx = this.object.isPerspectiveCamera
      ? 2 * dist * Math.tan((this.object.fov * Math.PI / 180) / 2) / h
      : (this.object.top - this.object.bottom) / this.object.zoom / h;
    _side.set(1, 0, 0).applyQuaternion(this.object.quaternion);
    _up.set(0, 1, 0).applyQuaternion(this.object.quaternion);
    _v.copy(_side).multiplyScalar(-dx * perPx * this.panSpeed).addScaledVector(_up, dy * perPx * this.panSpeed);
    this.object.position.add(_v);
    this.target.add(_v);
    this.update();
  }

  // factor < 1: closer
  dolly(factor) {
    _eye.copy(this.object.position).sub(this.target);
    const d = Math.min(this.maxDistance, Math.max(this.minDistance, _eye.length() * factor));
    if (!(d > 0)) return;
    _eye.setLength(d);
    this.object.position.copy(this.target).add(_eye);
    this.update();
  }

  // ------------------------------------------------------------------ events
  _rect() { return this.domElement.getBoundingClientRect(); }
  _radius() { const r = this._rect(); return Math.max(1, Math.min(r.width || 1, r.height || 1) / 2); }
  _angleAt(x, y) {
    const r = this._rect();
    return Math.atan2(-(y - r.top - r.height / 2), x - r.left - r.width / 2);
  }
  _inRollZone(x, y) {
    const r = this._rect();
    return Math.hypot(x - r.left - r.width / 2, y - r.top - r.height / 2) > this.rollZone * this._radius();
  }
  _touchState() {
    const p = [...this._pointers.values()];
    return { mid: new Vector2((p[0].x + p[1].x) / 2, (p[0].y + p[1].y) / 2), dist: Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y) };
  }

  _onDown(e) {
    if (!this.enabled) return;
    if (this.domElement.setPointerCapture) {
      try { this.domElement.setPointerCapture(e.pointerId); } catch (err) { /* pointer already gone */ }
    }
    this._pointers.set(e.pointerId, new Vector2(e.clientX, e.clientY));
    this._last.set(e.clientX, e.clientY);
    if (e.pointerType === 'touch') {
      if (this._pointers.size === 2) { this.mode = 'touch'; this._touch = this._touchState(); }
      else if (this._pointers.size === 1) this.mode = 'rotate';
      return;
    }
    if (e.button === 2 || (e.button === 0 && (e.ctrlKey || e.shiftKey || e.metaKey))) this.mode = 'pan';
    else if (e.button === 1) this.mode = 'zoom';
    else if (e.button === 0) this.mode = this._inRollZone(e.clientX, e.clientY) ? 'roll' : 'rotate';
  }

  _onMove(e) {
    if (!this.enabled || !this._pointers.has(e.pointerId) || !this.mode) return;
    this._pointers.get(e.pointerId).set(e.clientX, e.clientY);
    if (this.mode === 'touch') {
      if (this._pointers.size < 2) return;
      const t = this._touchState();
      this.pan(t.mid.x - this._touch.mid.x, t.mid.y - this._touch.mid.y);
      if (t.dist > 0 && this._touch.dist > 0) this.dolly(this._touch.dist / t.dist);
      this._touch = t;
      return;
    }
    const dx = e.clientX - this._last.x, dy = e.clientY - this._last.y;
    if (this.mode === 'rotate') this.rotate(dx, dy);
    else if (this.mode === 'roll') this.roll(this._angleAt(this._last.x, this._last.y), this._angleAt(e.clientX, e.clientY));
    else if (this.mode === 'pan') this.pan(dx, dy);
    else if (this.mode === 'zoom') this.dolly(Math.pow(0.95, -this.zoomSpeed * dy * 0.2));
    this._last.set(e.clientX, e.clientY);
  }

  _onUp(e) {
    this._pointers.delete(e.pointerId);
    if (this.domElement.releasePointerCapture) {
      try { this.domElement.releasePointerCapture(e.pointerId); } catch (err) { /* not captured */ }
    }
    if (this.mode === 'touch' && this._pointers.size === 1) {
      const p = [...this._pointers.values()][0];
      this.mode = 'rotate';
      this._last.copy(p);
    } else if (!this._pointers.size) this.mode = null;
  }

  _onWheel(e) {
    if (!this.enabled) return;
    e.preventDefault();
    let dy = e.deltaY;
    if (e.deltaMode === 1) dy *= 16;          // lines
    else if (e.deltaMode === 2) dy *= 100;    // pages
    if (!dy) return;
    const f = Math.pow(0.95, this.zoomSpeed * Math.abs(dy) * 0.01);
    this.dolly(dy < 0 ? f : 1 / f);
  }
}
