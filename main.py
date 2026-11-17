from __future__ import annotations
import json, shutil, subprocess, threading
from datetime import datetime
from pathlib import Path
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.popup import Popup
from kivy.uix.modalview import ModalView
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from meshio3d import Mesh
from viewer3d import Viewer3D, build_chunks

from kivy.utils import platform
ANDROID = platform == 'android'
EXTS = {'.jpg', '.jpeg', '.png'}
KV = r'''
<Body@Label>:
    size_hint_y: None
    text_size: self.width, None
    height: self.texture_size[1] + dp(6)
    halign: 'left'
    valign: 'top'
    font_size: '14sp'
    color: (.72, .78, .86, 1)
<Head@Label>:
    size_hint_y: None
    text_size: self.width, None
    height: dp(28)
    halign: 'left'
    valign: 'middle'
    font_size: '12sp'
    bold: True
    color: (.58, .66, .75, 1)
<Btn@Button>:
    size_hint_y: None
    height: dp(46)
    font_size: '15sp'
    text_size: self.width - dp(16), None
    halign: 'center'
    valign: 'middle'
    background_normal: ''
    background_color: (.16, .45, .42, 1)
Root:
    orientation: 'vertical'
    padding: [dp(14), dp(40), dp(14), dp(12)]
    spacing: dp(10)
    canvas.before:
        Color:
            rgba: (.055, .075, .11, 1)
        Rectangle:
            pos: self.pos
            size: self.size
    Label:
        text: 'PHOTO3D'
        bold: True
        size_hint_y: None
        height: dp(34)
        text_size: self.width, None
        halign: 'left'
        valign: 'middle'
        font_size: '24sp'
        color: (.93, .96, 1, 1)
    Label:
        text: 'LOCAL PHOTOGRAMMETRY'
        size_hint_y: None
        height: dp(18)
        text_size: self.width, None
        halign: 'left'
        valign: 'middle'
        font_size: '11sp'
        color: (.36, .82, .72, 1)
    TextInput:
        id: project_name
        text: 'Новый скан'
        hint_text: 'Имя проекта'
        multiline: False
        size_hint_y: None
        height: dp(48)
        font_size: '16sp'
        padding: [dp(10), dp(13), dp(10), dp(10)]
    Btn:
        text: 'Создать проект'
        on_release: app.create_project(project_name.text)
    Btn:
        text: '+ Добавить фото'
        on_release: app.pick_images()
    Btn:
        text: 'Открыть 3D-модель'
        on_release: app.open_model()
    Btn:
        text: 'Реконструкция (на ПК)'
        on_release: app.reconstruct()
    Label:
        text: app.status
        size_hint_y: None
        text_size: self.width - dp(16), None
        height: max(dp(48), self.texture_size[1] + dp(16))
        halign: 'left'
        valign: 'middle'
        font_size: '15sp'
        color: (.36, .82, .72, 1)
        canvas.before:
            Color:
                rgba: (.09, .12, .17, 1)
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [dp(8)]
    ScrollView:
        do_scroll_x: False
        bar_width: dp(3)
        BoxLayout:
            orientation: 'vertical'
            size_hint_y: None
            height: self.minimum_height
            padding: [0, dp(4), 0, dp(4)]
            spacing: dp(4)
            Head:
                text: 'ПРОЕКТ'
            Body:
                text: app.project_info
                color: (.91, .94, .98, 1)
            Head:
                text: 'СЪЁМКА'
            Body:
                text: 'Объект: снимайте кругами с перекрытием.\n\nПомещение: двигайтесь плавно, сохраняя общие детали соседних кадров.\n\nИзбегайте бликов, смаза и однотонных поверхностей.'
            Head:
                text: 'РЕКОНСТРУКЦИЯ'
            Body:
                text: 'Windows: COLMAP выполняет SfM и MVS локально.\n\nAndroid: импорт и подготовка набора снимков; тяжелая реконструкция выполняется на ПК.'
    Label:
        text: app.footer
        size_hint_y: None
        height: dp(20)
        text_size: self.width, None
        halign: 'left'
        valign: 'middle'
        font_size: '11sp'
        color: (.45, .54, .64, 1)
'''
class Root(BoxLayout):
    pass
class Photo3DApp(App):
    project_info = StringProperty('Проект не создан')
    status = StringProperty('Создайте проект и добавьте фотографии')
    footer = StringProperty('Локально · фотографии остаются на устройстве')
    def build(self):
        self.title = 'Photo3D'
        Window.softinput_mode = 'below_target'
        self.project = None
        self.images = []
        self.request_android_permissions()
        if ANDROID:
            Clock.schedule_once(self.ask_all_files_access, 1.5)
        return Builder.load_string(KV)
    def request_android_permissions(self):
        if not ANDROID:
            return
        try:
            from android.permissions import request_permissions
            request_permissions(['android.permission.READ_MEDIA_IMAGES',
                                 'android.permission.READ_EXTERNAL_STORAGE',
                                 'android.permission.WRITE_EXTERNAL_STORAGE'])
        except Exception:
            pass
    def has_all_files_access(self):
        if not ANDROID:
            return True
        try:
            from jnius import autoclass
            if autoclass('android.os.Build$VERSION').SDK_INT >= 30:
                return bool(autoclass('android.os.Environment').isExternalStorageManager())
            return True
        except Exception:
            return False

    def ask_all_files_access(self, *_):
        """Открывает системный экран «Доступ ко всем файлам» (Android 11+)."""
        if not ANDROID or self.has_all_files_access():
            return
        try:
            from jnius import autoclass
            Intent = autoclass('android.content.Intent')
            Settings = autoclass('android.provider.Settings')
            Uri = autoclass('android.net.Uri')
            act = autoclass('org.kivy.android.PythonActivity').mActivity
            intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
            intent.setData(Uri.parse('package:' + act.getPackageName()))
            act.startActivity(intent)
            self.status = 'Разрешите «Доступ ко всем файлам», чтобы папка projects была видна в телефоне'
        except Exception as exc:
            self.status = f'Не удалось открыть настройки доступа: {exc}'

    def projects_dir(self):
        """Android: /storage/emulated/0/projects (видна в «Файлах» и с ПК). Иначе папка приложения."""
        if ANDROID:
            try:
                from jnius import autoclass
                if self.has_all_files_access():
                    root = Path(autoclass('android.os.Environment').getExternalStorageDirectory().getAbsolutePath())
                    d = root / 'projects'
                    d.mkdir(parents=True, exist_ok=True)
                    return d
                ctx = autoclass('org.kivy.android.PythonActivity').mActivity
                ext = ctx.getExternalFilesDir(None)
                if ext is not None:
                    return Path(ext.getAbsolutePath()) / 'projects'
            except Exception:
                pass
        return Path(self.user_data_dir) / 'projects'
    def create_project(self, name):
        safe = ''.join(c for c in name.strip() if c.isalnum() or c in ' -_()').strip()
        if not safe:
            self.status = 'Введите имя проекта'; self.notify('Нет имени', 'Введите имя проекта (буквы или цифры).'); return
        root = self.projects_dir() / safe
        (root / 'images').mkdir(parents=True, exist_ok=True)
        (root / 'export').mkdir(parents=True, exist_ok=True)
        self.project = root
        self.images = list((root / 'images').glob('*'))
        self.save_manifest(); self.update_info()
        self.status = f'Проект «{safe}» создан. Теперь нажмите «+ Добавить фото»'
        self.notify('Проект создан', f'Проект «{safe}» создан.\nПапка: {root}')
    def notify(self, title, text):
        lab = Label(text=text, text_size=(dp(260), None), halign='center', valign='middle')
        pop = Popup(title=title, content=lab, size_hint=(.85, None), height=dp(220))
        pop.open(); Clock.schedule_once(lambda dt: pop.dismiss(), 2.5)
    # ---------------- 3D-просмотр ----------------
    def models_dir(self):
        d = self.projects_dir() / 'models'
        d.mkdir(parents=True, exist_ok=True)
        return d

    def open_model(self):
        if ANDROID:
            try:
                from androidstorage4kivy import Chooser
                self._chooser = Chooser(self._on_android_chosen)
                self._chooser.choose_content('*/*')
                return
            except Exception as exc:
                self.status = f'Системный выбор файла недоступен: {exc}'
        start = str(self.models_dir()) if ANDROID else str(Path.home())
        flt = ['*.obj', '*.stl', '*.ply', '*.OBJ', '*.STL', '*.PLY']
        chooser = FileChooserListView(path=start, filters=flt)
        body = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(8))
        body.add_widget(chooser)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        popup = Popup(title='Выбрать модель (OBJ, STL, PLY)', content=body, size_hint=(.94, .9))
        cancel, ok = Button(text='Отмена'), Button(text='Открыть')
        cancel.bind(on_release=popup.dismiss)
        ok.bind(on_release=lambda *_: self._open_selected(chooser.selection, popup))
        row.add_widget(cancel); row.add_widget(ok); body.add_widget(row); popup.open()

    def _open_selected(self, selection, popup):
        if not selection:
            self.status = 'Выберите файл модели'; return
        popup.dismiss()
        self.load_model(selection[0])

    def _on_android_chosen(self, uri_list):
        if not uri_list:
            return
        def work():
            try:
                from androidstorage4kivy import SharedStorage
                path = SharedStorage().copy_from_shared(uri_list[0])
                Clock.schedule_once(lambda dt: self.load_model(path))
            except Exception as exc:
                Clock.schedule_once(lambda dt: self._set_status(f'Не удалось прочитать файл: {exc}'))
        threading.Thread(target=work, daemon=True).start()

    def _set_status(self, text):
        self.status = text

    def load_model(self, path):
        self.status = f'Загрузка модели {Path(path).name}…'
        def work():
            try:
                mesh = Mesh.load(path)
                chunks, shown = build_chunks(mesh)
                Clock.schedule_once(lambda dt: self.show_viewer(Path(path).name, mesh, chunks, shown))
            except Exception as exc:
                Clock.schedule_once(lambda dt: self._set_status(f'Ошибка модели: {exc}'))
        threading.Thread(target=work, daemon=True).start()

    def _model_info(self, mesh, shown, scaled=False):
        sx, sy, sz = mesh.size()
        unit = 'мм' if scaled else 'ед.'
        kind = f'{len(mesh.vertices):,} точек (облако)' if mesh.is_cloud else f'{len(mesh.vertices):,} вершин, {len(mesh.faces):,} граней'
        text = f'{kind}\nРазмер: {sx:.2f} × {sy:.2f} × {sz:.2f} {unit}'
        if shown:
            text += f'\n({shown})'
        return text

    def show_viewer(self, name, mesh, chunks, shown):
        self.mesh = mesh
        self.mesh_name = Path(name).stem
        self.mesh_scaled = False
        view = ModalView(size_hint=(1, 1), auto_dismiss=False, background_color=(.055, .075, .11, 1))
        box = BoxLayout(orientation='vertical', padding=[dp(10), dp(36), dp(10), dp(10)], spacing=dp(6))
        info = Label(text=self._model_info(mesh, shown), size_hint_y=None, height=dp(62), font_size='13sp',
                     halign='left', valign='middle', color=(.72, .78, .86, 1))
        info.bind(size=lambda w, v: setattr(w, 'text_size', (w.width, None)))
        viewer = Viewer3D()
        viewer.set_chunks(chunks)
        hint = Label(text='Палец: вращать · Щипок: масштаб', size_hint_y=None, height=dp(20),
                     font_size='11sp', color=(.45, .54, .64, 1))
        size_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        size_in = TextInput(hint_text='Длиннейшая сторона, мм', multiline=False, input_filter='float', font_size='14sp',
                            padding=[dp(8), dp(12), dp(8), dp(8)])
        apply_btn = Button(text='Задать размер', size_hint_x=.45, background_normal='', background_color=(.16, .45, .42, 1))
        size_row.add_widget(size_in); size_row.add_widget(apply_btn)
        save_row = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(90), spacing=dp(2))
        for group in (('STL', '3MF', 'OBJ'), ('DXF', 'PLY')):
            line = BoxLayout(spacing=dp(6))
            for ext in group:
                b = Button(text=ext, font_size='14sp', background_normal='', background_color=(.16, .45, .42, 1))
                b.bind(on_release=lambda w, e=ext.lower(): self.save_model(e))
                line.add_widget(b)
            save_row.add_widget(line)
        bottom = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        reset = Button(text='Сбросить вид')
        close = Button(text='Закрыть')
        reset.bind(on_release=lambda *_: viewer.reset_view())
        close.bind(on_release=lambda *_: view.dismiss())
        bottom.add_widget(reset); bottom.add_widget(close)

        def apply_size(*_):
            try:
                value = float(size_in.text.replace(',', '.'))
                if value <= 0:
                    raise ValueError('Размер должен быть больше нуля')
                mesh.scale_to_longest(value)
                self.mesh_scaled = True
                chunks2, shown2 = build_chunks(mesh)
                viewer.set_chunks(chunks2)
                info.text = self._model_info(mesh, shown2, scaled=True)
            except Exception as exc:
                info.text = f'Масштаб не применён: {exc}'
        apply_btn.bind(on_release=apply_size)
        self._viewer_info = info
        for w in (info, viewer, hint, size_row, save_row, bottom):
            box.add_widget(w)
        view.add_widget(box)
        view.open()
        self.status = f'Открыта модель: {name}'

    def save_model(self, ext):
        mesh = getattr(self, 'mesh', None)
        if mesh is None:
            return
        out = self.projects_dir() / 'exports'
        out.mkdir(parents=True, exist_ok=True)
        target = out / f'{self.mesh_name}.{ext}'
        info = self._viewer_info
        info.text = f'Сохранение {target.name}…'
        def work():
            try:
                mesh.save(target)
                msg = f'Сохранено: {target}'
                if ANDROID:
                    try:
                        from androidstorage4kivy import SharedStorage
                        from jnius import autoclass
                        env = autoclass('android.os.Environment')
                        SharedStorage().copy_to_shared(str(target), collection=env.DIRECTORY_DOWNLOADS)
                        msg = f'Сохранено в «Загрузки» ({target.name}) и в {target}'
                    except Exception:
                        msg += '\n(в «Загрузки» скопировать не удалось)'
            except Exception as exc:
                msg = f'Не сохранено: {exc}'
            Clock.schedule_once(lambda dt: self._after_save(msg))
        threading.Thread(target=work, daemon=True).start()

    def _after_save(self, msg):
        self._viewer_info.text = msg
        self.status = msg
    def pick_images(self):
        if not self.project:
            self.create_project('Новый скан')
        if ANDROID:
            try:
                from androidstorage4kivy import Chooser
                self._img_chooser = Chooser(self._on_android_images)
                try:
                    self._img_chooser.choose_content('image/*', multiple=True)
                except TypeError:
                    self._img_chooser.choose_content('image/*')
                return
            except Exception as exc:
                self.status = f'Галерея недоступна ({exc}), открываю обычный выбор'
        self._pick_images_manual()

    def _on_android_images(self, uri_list):
        if not uri_list:
            return
        total = len(uri_list)
        def work():
            done = 0
            try:
                from androidstorage4kivy import SharedStorage
                storage = SharedStorage()
                for uri in uri_list:
                    try:
                        tmp = Path(storage.copy_from_shared(uri))
                        if tmp.suffix.lower() not in EXTS:
                            tmp = tmp.rename(tmp.with_suffix('.jpg'))
                        self._store_image(tmp, move=True)
                        done += 1
                    except Exception:
                        pass
                    if done % 5 == 0:
                        Clock.schedule_once(lambda dt, d=done: self._set_status(f'Импорт фото: {d} из {total}…'))
            finally:
                Clock.schedule_once(lambda dt: self._images_done(done, total))
        self.status = f'Импорт фото: 0 из {total}…'
        threading.Thread(target=work, daemon=True).start()

    def _images_done(self, done, total):
        self.save_manifest(); self.update_info()
        self.status = f'Добавлено фото: {done} из {total}'

    def _store_image(self, src, move=False):
        dest = self.project / 'images' / src.name
        if dest.exists():
            dest = dest.with_name(f'{src.stem}_{datetime.now():%H%M%S%f}{src.suffix.lower()}')
        (shutil.move if move else shutil.copy2)(str(src), str(dest))
        self.images.append(dest)

    def _pick_images_manual(self):
        start = '/storage/emulated/0/DCIM/Camera' if ANDROID else str(Path.home())
        if not Path(start).exists():
            start = str(Path.home())
        chooser = FileChooserListView(path=start, filters=['*.jpg', '*.jpeg', '*.png', '*.JPG', '*.JPEG', '*.PNG'], multiselect=True)
        body = BoxLayout(orientation='vertical', spacing=dp(8), padding=dp(8))
        body.add_widget(chooser)
        counter = Label(text='Выбрано: 0', size_hint_y=None, height=dp(22), font_size='13sp')
        body.add_widget(counter)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        popup = Popup(title='Выбрать фото', content=body, size_hint=(.94, .9))
        cancel, every, add = Button(text='Отмена'), Button(text='Выбрать все'), Button(text='Добавить')
        cancel.bind(on_release=popup.dismiss)
        def select_all(*_):
            chooser.selection = [f for f in chooser.files if Path(f).is_file() and Path(f).suffix.lower() in EXTS]
        every.bind(on_release=select_all)
        chooser.bind(selection=lambda w, v: setattr(counter, 'text', f'Выбрано: {len(v)}'))
        add.bind(on_release=lambda *_: self.import_files(chooser.selection, popup))
        for b in (cancel, every, add):
            row.add_widget(b)
        body.add_widget(row); popup.open()

    def import_files(self, files, popup):
        count = 0
        for item in files:
            src = Path(item)
            if not src.is_file() or src.suffix.lower() not in EXTS:
                continue
            self._store_image(src); count += 1
        self.save_manifest(); self.update_info(); self.status = f'Добавлено фото: {count}'; popup.dismiss()
    def save_manifest(self):
        if not self.project: return
        data = {'name': self.project.name, 'updated': datetime.now().isoformat(timespec='seconds'), 'images':[p.name for p in self.images], 'units':'unknown', 'scale':1.0}
        (self.project/'project.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    def update_info(self):
        self.project_info = f'{self.project.name}\n\nКадров: {len(self.images)}\n\n{self.project}'
    def reconstruct(self):
        if ANDROID:
            self.status = 'На телефоне модель не строится. Перенесите папку проекта на ПК с COLMAP.'; return
        if not self.project or not self.images:
            self.status = 'Создайте проект и добавьте фото'; return
        colmap = shutil.which('colmap') or shutil.which('colmap.exe')
        if not colmap:
            self.status = 'COLMAP не найден. Добавьте colmap.exe в PATH'; return
        self.status = 'COLMAP выполняет реконструкцию…'
        self.footer = 'Обработка может занять длительное время'
        threading.Thread(target=self._run, args=(colmap,), daemon=True).start()
    def _run(self, colmap):
        cmd = [colmap, 'automatic_reconstructor', '--image_path', str(self.project/'images'), '--workspace_path', str(self.project/'reconstruction'), '--quality', 'MEDIUM', '--data_type', 'INDIVIDUAL']
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if p.returncode == 0:
                result = self._find_result(colmap)
                if result:
                    msg = f'Реконструкция завершена: {result.name}'
                    Clock.schedule_once(lambda dt: self._done(msg))
                    Clock.schedule_once(lambda dt: self.load_model(str(result)), .5)
                    return
                msg = 'COLMAP завершён, но результат не найден (нужна CUDA для плотной модели)'
            else:
                log = self.project/'colmap_error.log'; log.write_text(p.stdout+'\n'+p.stderr, encoding='utf-8', errors='replace'); msg = f'COLMAP завершился с ошибкой · {log.name}'
        except Exception as exc:
            msg = f'Ошибка запуска COLMAP: {exc}'
        Clock.schedule_once(lambda dt: self._done(msg))
    def _find_result(self, colmap):
        """Лучший результат COLMAP: меш Пуассона > плотное облако > разреженное облако (PLY)."""
        ws = self.project / 'reconstruction'
        for rel in ('dense/0/meshed-poisson.ply', 'dense/0/meshed-delaunay.ply', 'dense/0/fused.ply'):
            f = ws / rel
            if f.is_file():
                return f
        sparse = ws / 'sparse' / '0'
        if sparse.is_dir():
            out = ws / 'sparse_points.ply'
            r = subprocess.run([colmap, 'model_converter', '--input_path', str(sparse),
                                '--output_path', str(out), '--output_type', 'PLY'],
                               capture_output=True, text=True, check=False)
            if r.returncode == 0 and out.is_file():
                return out
        return None

    def _done(self, msg):
        self.status = msg; self.footer = 'Результаты сохранены в проекте'
if __name__ == '__main__':
    Photo3DApp().run()

