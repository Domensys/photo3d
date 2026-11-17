"""3D-просмотрщик для Kivy: вращение одним пальцем, масштаб щипком/колесом."""
from __future__ import annotations
from kivy.graphics import (Fbo, Rectangle, Color, Mesh as GMesh, Translate, Rotate, Scale,
                           PushMatrix, PopMatrix, ClearBuffers, Callback, InstructionGroup)
from kivy.graphics.opengl import glEnable, glDisable, GL_DEPTH_TEST
from kivy.graphics.transformation import Matrix
from kivy.uix.widget import Widget
from kivy.vector import Vector

from meshio3d import _normal

FMT = [(b'v_pos', 3, 'float'), (b'v_normal', 3, 'float'), (b'v_color', 3, 'float')]
DEFAULT_COLOR = (.62, .75, .85)
MAX_FACES = 120000      # для плавности на телефоне показываем не больше
MAX_POINTS = 200000
CHUNK_FACES = 21845     # 3 вершины на грань, лимит Kivy: 65535 вершин на один Mesh

VS = '''
#ifdef GL_ES
precision highp float;
#endif
attribute vec3 v_pos;
attribute vec3 v_normal;
attribute vec3 v_color;
uniform mat4 modelview_mat;
uniform mat4 projection_mat;
uniform mat3 normal_mat;
varying vec3 n_vec;
varying vec3 c_vec;
void main(void) {
    gl_Position = projection_mat * modelview_mat * vec4(v_pos, 1.0);
    n_vec = normal_mat * v_normal;
    c_vec = v_color;
    gl_PointSize = 3.0;
}
'''

FS = '''
#ifdef GL_ES
precision mediump float;
#endif
varying vec3 n_vec;
varying vec3 c_vec;
void main(void) {
    float ln = length(n_vec);
    float l = 1.0;
    if (ln > 0.5) {
        l = 0.30 + 0.70 * abs(dot(n_vec / ln, normalize(vec3(0.35, 0.55, 0.75))));
    }
    gl_FragColor = vec4(c_vec * l, 1.0);
}
'''


def _shader_file():
    import os, tempfile
    path = os.path.join(tempfile.gettempdir(), 'photo3d_view.glsl')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('---VERTEX SHADER---\n' + VS + '\n---FRAGMENT SHADER---\n' + FS)
    return path


def build_chunks(mesh):
    """Готовит массивы вершин (потокобезопасно, без OpenGL). Возвращает (chunks, info)."""
    center, radius = mesh.center_radius()
    k = 1.0 / radius
    cx, cy, cz = center
    cols = mesh.colors
    vs = mesh.vertices
    chunks = []
    shown = ''
    if mesh.faces:
        faces = mesh.faces
        step = 1
        if len(faces) > MAX_FACES:
            step = -(-len(faces) // MAX_FACES)
            faces = faces[::step]
            shown = f'показано {len(faces):,} из {len(mesh.faces):,} граней'
        buf = []; count = 0
        for f in faces:
            a, b, c = vs[f[0]], vs[f[1]], vs[f[2]]
            n = _normal(a, b, c)
            for idx, p in ((f[0], a), (f[1], b), (f[2], c)):
                col = cols[idx] if cols else DEFAULT_COLOR
                buf.extend(((p[0] - cx) * k, (p[1] - cy) * k, (p[2] - cz) * k, n[0], n[1], n[2], col[0], col[1], col[2]))
            count += 3
            if count >= CHUNK_FACES * 3:
                chunks.append((buf, list(range(count)), 'triangles')); buf = []; count = 0
        if count:
            chunks.append((buf, list(range(count)), 'triangles'))
    else:
        idxs = range(len(vs))
        if len(vs) > MAX_POINTS:
            step = -(-len(vs) // MAX_POINTS)
            idxs = range(0, len(vs), step)
            shown = f'показано {len(idxs):,} из {len(vs):,} точек'
        buf = []; count = 0
        for i in idxs:
            p = vs[i]; col = cols[i] if cols else DEFAULT_COLOR
            buf.extend(((p[0] - cx) * k, (p[1] - cy) * k, (p[2] - cz) * k, 0.0, 0.0, 0.0, col[0], col[1], col[2]))
            count += 1
            if count >= 65000:
                chunks.append((buf, list(range(count)), 'points')); buf = []; count = 0
        if count:
            chunks.append((buf, list(range(count)), 'points'))
    return chunks, shown


class Viewer3D(Widget):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._touches = {}
        self.rot_x_angle = -60.0
        self.rot_y_angle = 0.0
        self.zoom = 1.0
        self.base = 2.0
        with self.canvas.before:
            Color(.055, .075, .11, 1)
            self.bg = Rectangle(size=self.size, pos=self.pos)
        with self.canvas:
            self.fbo = Fbo(size=(64, 64), with_depthbuffer=True, compute_normal_mat=True,
                           clear_color=(0, 0, 0, 0))
            Color(1, 1, 1, 1)
            self.view = Rectangle(size=self.size, pos=self.pos)
        self.fbo.shader.source = _shader_file()
        self.group = InstructionGroup()
        with self.fbo:
            ClearBuffers(clear_depth=True)
            Callback(self._gl_on)
            PushMatrix()
            Translate(0, 0, -3)
            self.rot_x = Rotate(self.rot_x_angle, 1, 0, 0)
            self.rot_y = Rotate(self.rot_y_angle, 0, 0, 1)
            self.scale_i = Scale(2.0)
            self.fbo.add(self.group)
            PopMatrix()
            Callback(self._gl_off)
        self.view.texture = self.fbo.texture
        self.bind(size=self._resize, pos=self._resize)
        self._resize()

    @staticmethod
    def _gl_on(*_):
        glEnable(GL_DEPTH_TEST)

    @staticmethod
    def _gl_off(*_):
        glDisable(GL_DEPTH_TEST)

    def _resize(self, *_):
        w = max(16, int(self.width)); h = max(16, int(self.height))
        self.fbo.size = (w, h)
        self.view.size = self.size
        self.view.pos = self.pos
        self.bg.size = self.size
        self.bg.pos = self.pos
        self.view.texture = self.fbo.texture
        asp = w / float(h)
        self.base = 2.1 * min(1.0, asp)
        self._apply_zoom(1.0)
        self.fbo['projection_mat'] = Matrix().view_clip(-asp, asp, -1, 1, 1, 100, 1)
        self.fbo.ask_update()

    def set_chunks(self, chunks):
        self.group.clear()
        for verts, idx, mode in chunks:
            self.group.add(GMesh(vertices=verts, indices=idx, fmt=FMT, mode=mode))
        self.reset_view()

    def reset_view(self):
        self.rot_x.angle = -60.0
        self.rot_y.angle = 0.0
        self.zoom = 1.0
        self._apply_zoom(1.0)

    def _apply_zoom(self, factor):
        self.zoom = max(.2, min(12.0, self.zoom * factor))
        s = self.base * self.zoom
        self.scale_i.xyz = (s, s, s)
        self.fbo.ask_update()

    # ----- управление -----
    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        if touch.is_mouse_scrolling:
            self._apply_zoom(1.15 if touch.button == 'scrolldown' else 1 / 1.15)
            return True
        touch.grab(self)
        self._touches[touch.uid] = touch
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is not self or touch.uid not in self._touches:
            return super().on_touch_move(touch)
        if len(self._touches) >= 2:
            other = next(t for u, t in self._touches.items() if u != touch.uid)
            prev = Vector(touch.ppos).distance(Vector(other.pos))
            now = Vector(touch.pos).distance(Vector(other.pos))
            if prev > 1:
                self._apply_zoom(now / prev)
        else:
            self.rot_y.angle += touch.dx * 0.5
            self.rot_x.angle -= touch.dy * 0.5
            self.fbo.ask_update()
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._touches.pop(touch.uid, None)
            return True
        return super().on_touch_up(touch)
