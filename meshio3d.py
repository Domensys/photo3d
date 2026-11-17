"""Чтение и запись 3D-сеток и облаков точек: OBJ, STL (ASCII/binary), PLY (ASCII/binary)."""
from __future__ import annotations
import math
import struct
import zipfile
from pathlib import Path

PLY_TYPES = {
    'char': 'b', 'int8': 'b', 'uchar': 'B', 'uint8': 'B',
    'short': 'h', 'int16': 'h', 'ushort': 'H', 'uint16': 'H',
    'int': 'i', 'int32': 'i', 'uint': 'I', 'uint32': 'I',
    'float': 'f', 'float32': 'f', 'double': 'd', 'float64': 'd',
}


class Mesh:
    def __init__(self, vertices=None, faces=None, colors=None):
        self.vertices = vertices if vertices is not None else []
        self.faces = faces if faces is not None else []
        self.colors = colors  # list[(r, g, b)] в диапазоне 0..1 или None

    @property
    def is_cloud(self):
        return bool(self.vertices) and not self.faces

    def bbox(self):
        vs = self.vertices
        if not vs:
            return (0, 0, 0), (0, 0, 0)
        xs = [v[0] for v in vs]; ys = [v[1] for v in vs]; zs = [v[2] for v in vs]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def size(self):
        lo, hi = self.bbox()
        return tuple(hi[i] - lo[i] for i in range(3))

    def center_radius(self):
        lo, hi = self.bbox()
        c = tuple((lo[i] + hi[i]) / 2 for i in range(3))
        r = max((sum((v[i] - c[i]) ** 2 for i in range(3)) for v in self.vertices), default=1.0) ** .5
        return c, (r or 1.0)

    def scale(self, k):
        self.vertices = [(v[0] * k, v[1] * k, v[2] * k) for v in self.vertices]

    def scale_to_longest(self, target):
        longest = max(self.size())
        if longest <= 0:
            raise ValueError('Модель пустая или вырожденная')
        k = target / longest
        self.scale(k)
        return k

    def clean(self):
        """Убирает треугольники с неверными индексами и нулевой площадью."""
        n = len(self.vertices); vs = self.vertices; good = []
        for f in self.faces:
            a, b, c = f
            if a == b or b == c or a == c or min(f) < 0 or max(f) >= n:
                continue
            p, q, r = vs[a], vs[b], vs[c]
            ux, uy, uz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
            wx, wy, wz = r[0] - p[0], r[1] - p[1], r[2] - p[2]
            cx, cy, cz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
            if cx * cx + cy * cy + cz * cz > 1e-24:
                good.append(f)
        self.faces = good

    # ---------------- чтение ----------------
    @classmethod
    def load(cls, path):
        path = Path(path)
        ext = path.suffix.lower()
        if ext not in ('.obj', '.stl', '.ply'):
            ext = cls._sniff(path)
        if ext == '.obj':
            m = cls._load_obj(path)
        elif ext == '.stl':
            m = cls._load_stl(path)
        elif ext == '.ply':
            m = cls._load_ply(path)
        else:
            raise ValueError('Поддерживаются форматы OBJ, STL, PLY')
        if not m.vertices:
            raise ValueError('В файле не найдено геометрии')
        m.clean()
        return m

    @staticmethod
    def _sniff(path):
        head = path.read_bytes()[:512]
        if head.startswith(b'ply'):
            return '.ply'
        if head.lstrip().lower().startswith(b'solid'):
            return '.stl'
        try:
            txt = head.decode('utf-8', 'ignore')
            if any(l.startswith(('v ', 'f ')) for l in txt.splitlines()):
                return '.obj'
        except Exception:
            pass
        data = path.read_bytes()
        if len(data) >= 84 and 84 + struct.unpack_from('<I', data, 80)[0] * 50 == len(data):
            return '.stl'
        return ''

    @classmethod
    def _load_obj(cls, path):
        v = []; col = []; f = []; has_col = False
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            p = line.split()
            if not p:
                continue
            try:
                if p[0] == 'v' and len(p) >= 4:
                    v.append((float(p[1]), float(p[2]), float(p[3])))
                    if len(p) >= 7:
                        col.append((float(p[4]), float(p[5]), float(p[6]))); has_col = True
                    else:
                        col.append((.62, .75, .85))
                elif p[0] == 'f' and len(p) >= 4:
                    ids = []
                    for x in p[1:]:
                        i = int(x.split('/')[0])
                        ids.append(i - 1 if i > 0 else len(v) + i)
                    for i in range(1, len(ids) - 1):
                        f.append((ids[0], ids[i], ids[i + 1]))
            except ValueError:
                continue
        return cls(v, f, col if has_col else None)

    @classmethod
    def _load_stl(cls, path):
        data = path.read_bytes()
        verts = []; faces = []; index = {}

        def add(p):
            i = index.get(p)
            if i is None:
                i = len(verts); index[p] = i; verts.append(p)
            return i

        if len(data) >= 84 and 84 + struct.unpack_from('<I', data, 80)[0] * 50 == len(data):
            count = struct.unpack_from('<I', data, 80)[0]; off = 84
            for _ in range(count):
                vals = struct.unpack_from('<12f', data, off)
                off += 50
                faces.append((add(vals[3:6]), add(vals[6:9]), add(vals[9:12])))
        else:
            tri = []
            for line in data.decode('utf-8', 'ignore').splitlines():
                p = line.split()
                if len(p) >= 4 and p[0].lower() == 'vertex':
                    tri.append(add((float(p[1]), float(p[2]), float(p[3]))))
                    if len(tri) == 3:
                        faces.append(tuple(tri)); tri = []
        return cls(verts, faces)

    @classmethod
    def _load_ply(cls, path):
        data = path.read_bytes()
        end = data.find(b'end_header')
        if end < 0:
            raise ValueError('Некорректный PLY: нет end_header')
        nl = data.find(b'\n', end)
        header = data[:end].decode('ascii', 'ignore').splitlines()
        body = data[nl + 1:]
        fmt = 'ascii'; elements = []
        for line in header:
            p = line.split()
            if not p:
                continue
            if p[0] == 'format':
                fmt = p[1]
            elif p[0] == 'element':
                elements.append([p[1], int(p[2]), []])
            elif p[0] == 'property' and elements:
                if p[1] == 'list':
                    elements[-1][2].append(('list', p[2], p[3], p[4]))
                else:
                    elements[-1][2].append(('scalar', p[1], p[1], p[2]))
        verts = []; colors = None; faces = []
        if fmt == 'ascii':
            tokens = body.decode('ascii', 'ignore').split()
            pos = 0
            for name, count, props in elements:
                rows = []
                for _ in range(count):
                    row = {}
                    for kind, t1, t2, pname in props:
                        if kind == 'scalar':
                            row[pname] = float(tokens[pos]); pos += 1
                        else:
                            n = int(tokens[pos]); pos += 1
                            row[pname] = [int(float(x)) for x in tokens[pos:pos + n]]; pos += n
                    rows.append(row)
                verts, colors, faces = cls._ply_take(name, props, rows, verts, colors, faces)
        else:
            endian = '<' if fmt == 'binary_little_endian' else '>'
            off = 0
            for name, count, props in elements:
                rows = []
                simple = all(k == 'scalar' for k, *_ in props)
                if simple:
                    code = endian + ''.join(PLY_TYPES[t1] for _, t1, _, _ in props)
                    size = struct.calcsize(code)
                    names = [pn for _, _, _, pn in props]
                    for tup in struct.iter_unpack(code, body[off:off + size * count]):
                        rows.append(dict(zip(names, tup)))
                    off += size * count
                else:
                    for _ in range(count):
                        row = {}
                        for kind, t1, t2, pname in props:
                            if kind == 'scalar':
                                c = PLY_TYPES[t1]; row[pname] = struct.unpack_from(endian + c, body, off)[0]
                                off += struct.calcsize(c)
                            else:
                                cc = PLY_TYPES[t1]; n = struct.unpack_from(endian + cc, body, off)[0]
                                off += struct.calcsize(cc)
                                ic = PLY_TYPES[t2]
                                row[pname] = list(struct.unpack_from(endian + str(n) + ic, body, off))
                                off += struct.calcsize(ic) * n
                        rows.append(row)
                verts, colors, faces = cls._ply_take(name, props, rows, verts, colors, faces)
        return cls(verts, faces, colors)

    @staticmethod
    def _ply_take(name, props, rows, verts, colors, faces):
        if name == 'vertex':
            names = {pn: (t1) for _, t1, _, pn in props}
            verts = [(r['x'], r['y'], r['z']) for r in rows]
            rk = next((k for k in ('red', 'r', 'diffuse_red') if k in names), None)
            if rk:
                gk = {'red': 'green', 'r': 'g', 'diffuse_red': 'diffuse_green'}[rk]
                bk = {'red': 'blue', 'r': 'b', 'diffuse_red': 'diffuse_blue'}[rk]
                is_byte = PLY_TYPES.get(names[rk]) in ('B', 'b', 'H', 'h', 'I', 'i')
                d = 255.0 if is_byte else 1.0
                colors = [(min(1, max(0, r[rk] / d)), min(1, max(0, r[gk] / d)), min(1, max(0, r[bk] / d))) for r in rows]
        elif name == 'face':
            key = next((pn for k, _, _, pn in props if k == 'list' and pn in ('vertex_indices', 'vertex_index')), None)
            if key is None:
                key = next((pn for k, _, _, pn in props if k == 'list'), None)
            if key:
                for r in rows:
                    ids = r[key]
                    for i in range(1, len(ids) - 1):
                        faces.append((ids[0], ids[i], ids[i + 1]))
        return verts, colors, faces

    # ---------------- запись ----------------
    def save(self, path):
        path = Path(path)
        ext = path.suffix.lower()
        if ext == '.obj':
            self._save_obj(path)
        elif ext == '.stl':
            self._save_stl(path)
        elif ext == '.ply':
            self._save_ply(path)
        elif ext == '.dxf':
            self._save_dxf(path)
        elif ext == '.3mf':
            self._save_3mf(path)
        else:
            raise ValueError('Сохранение: OBJ, STL, PLY, DXF или 3MF')
        return path

    def _save_obj(self, path):
        with path.open('w', encoding='utf-8', newline='\n') as out:
            out.write('# Photo3D\n')
            cols = self.colors
            for i, v in enumerate(self.vertices):
                if cols:
                    c = cols[i]
                    out.write('v %.9g %.9g %.9g %.4f %.4f %.4f\n' % (v[0], v[1], v[2], c[0], c[1], c[2]))
                else:
                    out.write('v %.9g %.9g %.9g\n' % v)
            for f in self.faces:
                out.write('f %d %d %d\n' % (f[0] + 1, f[1] + 1, f[2] + 1))

    def _save_stl(self, path):
        if not self.faces:
            raise ValueError('В STL нельзя сохранить облако точек: нет треугольников. Используйте PLY.')
        vs = self.vertices
        with path.open('wb') as out:
            out.write(b'Photo3D binary STL'.ljust(80, b' '))
            out.write(struct.pack('<I', len(self.faces)))
            for f in self.faces:
                a, b, c = vs[f[0]], vs[f[1]], vs[f[2]]
                n = _normal(a, b, c)
                out.write(struct.pack('<12fH', n[0], n[1], n[2], *a, *b, *c, 0))

    def _save_ply(self, path):
        cols = self.colors
        with path.open('wb') as out:
            head = ['ply', 'format binary_little_endian 1.0', 'comment Photo3D',
                    'element vertex %d' % len(self.vertices),
                    'property float x', 'property float y', 'property float z']
            if cols:
                head += ['property uchar red', 'property uchar green', 'property uchar blue']
            if self.faces:
                head += ['element face %d' % len(self.faces), 'property list uchar int vertex_indices']
            head.append('end_header')
            out.write(('\n'.join(head) + '\n').encode('ascii'))
            for i, v in enumerate(self.vertices):
                if cols:
                    c = cols[i]
                    out.write(struct.pack('<3f3B', v[0], v[1], v[2],
                                          int(c[0] * 255 + .5), int(c[1] * 255 + .5), int(c[2] * 255 + .5)))
                else:
                    out.write(struct.pack('<3f', *v))
            for f in self.faces:
                out.write(struct.pack('<B3i', 3, f[0], f[1], f[2]))


def _mesh_save_dxf(self, path):
    """ASCII DXF (R12) с 3DFACE: AutoCAD, BricsCAD, FreeCAD, LibreCAD."""
    if not self.faces:
        raise ValueError('В DXF нельзя сохранить облако точек: нет треугольников. Используйте PLY.')
    vs = self.vertices
    with path.open('w', encoding='ascii', newline='\r\n') as out:
        out.write('0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n')
        out.write('0\nSECTION\n2\nENTITIES\n')
        for f in self.faces:
            a, b, c = vs[f[0]], vs[f[1]], vs[f[2]]
            out.write('0\n3DFACE\n8\n0\n')
            for code, p in ((10, a), (11, b), (12, c), (13, c)):
                out.write('%d\n%.9g\n%d\n%.9g\n%d\n%.9g\n' % (code, p[0], code + 10, p[1], code + 20, p[2]))
        out.write('0\nENDSEC\n0\nEOF\n')


def _mesh_save_3mf(self, path):
    """3MF (единицы — миллиметры): SolidWorks, Fusion 360, 3D Builder, слайсеры."""
    if not self.faces:
        raise ValueError('В 3MF нельзя сохранить облако точек: нет треугольников. Используйте PLY.')
    vs = self.vertices
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<resources><object id="1" type="model"><mesh><vertices>']
    parts.extend('<vertex x="%.9g" y="%.9g" z="%.9g"/>' % tuple(v) for v in vs)
    parts.append('</vertices><triangles>')
    parts.extend('<triangle v1="%d" v2="%d" v3="%d"/>' % tuple(f) for f in self.faces)
    parts.append('</triangles></mesh></object></resources><build><item objectid="1"/></build></model>')
    ctypes = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ctypes)
        z.writestr('_rels/.rels', rels)
        z.writestr('3D/3dmodel.model', ''.join(parts))


Mesh._save_dxf = _mesh_save_dxf
Mesh._save_3mf = _mesh_save_3mf


def _normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    wx, wy, wz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx
    ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return nx / ln, ny / ln, nz / ln
