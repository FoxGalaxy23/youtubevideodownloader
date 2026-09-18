import os
import sys
import shutil
import subprocess

import yt_dlp
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QComboBox,
    QCheckBox,
    QTextEdit,
    QProgressBar,
    QFileDialog,
    QMessageBox,
)


def escape_drawtext(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\u2019")
    text = text.replace("%", "\\%")
    text = text.replace("[", "\\[").replace("]", "\\]")
    text = text.replace(",", "\\,")
    return text


FONT_FILE_OVERRIDE = None


def find_font_file():
    if FONT_FILE_OVERRIDE and os.path.exists(FONT_FILE_OVERRIDE):
        return FONT_FILE_OVERRIDE

    if sys.platform.startswith("win"):
        candidates = [
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\calibri.ttf",
            r"C:\Windows\Fonts\tahoma.ttf",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]

    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def escape_font_path(path: str) -> str:
    path = path.replace("\\", "/")
    path = path.replace(":", "\\:")
    return path


class DownloadWorker(QThread):
    """Фоновый поток: вся работа с yt-dlp/ffmpeg идёт здесь,
    чтобы интерфейс не зависал во время загрузки."""

    log_message = pyqtSignal(str)
    progress_changed = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, url, mode, quality, watermark, output_dir):
        super().__init__()
        self.url = url
        self.mode = mode              # 'video' или 'mp3'
        self.quality = quality        # 'best' или 'compressed'
        self.watermark = watermark    # bool
        self.output_dir = output_dir

    def _progress_hook(self, d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                percent = int(downloaded * 100 / total)
                self.progress_changed.emit(percent)
        elif d.get("status") == "finished":
            self.progress_changed.emit(100)
            self.log_message.emit("Загрузка потока завершена, идёт обработка (слияние/конвертация)...")

    def _build_ydl_opts(self):
        outtmpl = os.path.join(self.output_dir, "%(title)s.%(ext)s")
        opts = {
            "outtmpl": outtmpl,
            "progress_hooks": [self._progress_hook],
            "restrictfilenames": False,
            "noprogress": True,
        }

        if self.mode == "mp3":
            opts["format"] = "bestaudio/best"
            quality_bitrate = "320" if self.quality == "best" else "128"
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": quality_bitrate,
                }
            ]
        else:  # video
            if self.quality == "best":
                opts["format"] = "bestvideo+bestaudio/best"
            else:
                opts["format"] = (
                    "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
                )
            opts["merge_output_format"] = "mp4"

        return opts

    def _apply_watermark(self, video_path, title, author, url, duration=None):

        if shutil.which("ffmpeg") is None:
            self.log_message.emit(
                "ffmpeg не найден в PATH — водяной знак не может быть наложен."
            )
            return video_path

        font_path = find_font_file()
        if font_path:
            fontfile_part = f":fontfile='{escape_font_path(font_path)}'"
        else:
            fontfile_part = ""
            self.log_message.emit(
                "Не найден файл шрифта на диске — ffmpeg попробует использовать "
                "системный шрифт по умолчанию (может не сработать на некоторых "
                "сборках ffmpeg для Windows). При проблемах пропишите путь к "
                ".ttf-файлу в FONT_FILE_OVERRIDE в начале скрипта."
            )

        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_watermarked{ext}"

        lines = [
            f"{title}",
            f"Автор: {author}",
        ]

        fontsize_expr = "max(16\\,h*0.04)"

        draw_filters = []
        for i, line in enumerate(lines):
            safe_line = escape_drawtext(line)
            y_expr = f"h*0.02+{i}*(h*0.055)"
            draw_filters.append(
                "drawtext="
                f"text='{safe_line}'"
                f"{fontfile_part}:"
                "x=w*0.02:"
                f"y={y_expr}:"
                f"fontsize={fontsize_expr}:"
                "fontcolor=white:"
                "box=1:boxcolor=black@0.5:boxborderw=6"
            )
        filter_str = ",".join(draw_filters)

        cmd = [
            "ffmpeg",
            "-y",
            "-nostdin",          # никогда не жди ввод с клавиатуры
            "-hide_banner",
            "-i", video_path,
            "-vf", filter_str,
            "-codec:a", "copy",
            "-progress", "pipe:1",   # прогресс бар
            "-nostats",
            "-loglevel", "error",
            output_path,
        ]

        self.log_message.emit("Накладываю водяной знак с помощью ffmpeg (идёт перекодирование, это может занять время)...")
        self.progress_changed.emit(0)

        popen_kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
        )
        if sys.platform.startswith("win"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        process = subprocess.Popen(cmd, **popen_kwargs)

        for line in process.stdout:
            line = line.strip()
            if line.startswith("out_time_ms=") and duration:
                try:
                    out_time_ms = int(line.split("=", 1)[1])
                    percent = int(out_time_ms / 1_000_000 / duration * 100)
                    self.progress_changed.emit(max(0, min(99, percent)))
                except (ValueError, ZeroDivisionError):
                    pass

        stderr_output = process.stderr.read()
        process.wait()

        if process.returncode != 0:
            error_text = (stderr_output or "нет данных")[-800:]
            self.log_message.emit(
                "Не удалось наложить водяной знак. Оставляю файл без него.\n"
                f"Ошибка ffmpeg: {error_text}"
            )
            return video_path

        self.progress_changed.emit(100)
        self.log_message.emit(f"Водяной знак наложен: {os.path.basename(output_path)}")
        return output_path

    def run(self):
        try:
            ydl_opts = self._build_ydl_opts()

            self.log_message.emit("Получаю информацию о видео...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
                raw_filename = ydl.prepare_filename(info)

            title = info.get("title", "video")
            author = info.get("uploader") or info.get("channel") or "неизвестен"
            webpage_url = info.get("webpage_url", self.url)

            base, _ = os.path.splitext(raw_filename)
            if self.mode == "mp3":
                final_path = base + ".mp3"
            else:
                final_path = base + ".mp4"

            if not os.path.exists(final_path):
                final_path = raw_filename

            if self.mode == "video" and self.watermark:
                duration = info.get("duration")
                final_path = self._apply_watermark(final_path, title, author, webpage_url, duration)

            self.finished_signal.emit(
                True, f"Готово! Файл сохранён:\n{final_path}"
            )

        except Exception as e:
            self.finished_signal.emit(False, f"Произошла ошибка: {e}")


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YouTube Downloader")
        self.setMinimumWidth(520)
        self.output_dir = os.getcwd()
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Ссылка на YouTube видео:"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://www.youtube.com/watch?v=...")
        layout.addWidget(self.url_input)

        mode_layout = QHBoxLayout()
        self.radio_video = QRadioButton("Видео")
        self.radio_mp3 = QRadioButton("MP3 (только аудио)")
        self.radio_video.setChecked(True)
        self.mode_group = QButtonGroup()
        self.mode_group.addButton(self.radio_video)
        self.mode_group.addButton(self.radio_mp3)
        mode_layout.addWidget(self.radio_video)
        mode_layout.addWidget(self.radio_mp3)
        layout.addLayout(mode_layout)

        quality_layout = QHBoxLayout()
        quality_layout.addWidget(QLabel("Качество:"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("Наилучшее качество", "best")
        self.quality_combo.addItem("Сжатое (быстрее, меньше размер)", "compressed")
        quality_layout.addWidget(self.quality_combo)
        layout.addLayout(quality_layout)

        self.watermark_checkbox = QCheckBox(
            "Добавить водяной знак (название, автор, ссылка на оригинал)"
        )
        layout.addWidget(self.watermark_checkbox)

        self.radio_mp3.toggled.connect(self._on_mode_changed)

        dir_layout = QHBoxLayout()
        self.dir_label = QLabel(self.output_dir)
        self.dir_label.setStyleSheet("color: gray;")
        dir_button = QPushButton("Выбрать папку...")
        dir_button.clicked.connect(self._choose_directory)
        dir_layout.addWidget(self.dir_label, stretch=1)
        dir_layout.addWidget(dir_button)
        layout.addLayout(dir_layout)

        self.download_button = QPushButton("Скачать")
        self.download_button.clicked.connect(self._start_download)
        layout.addWidget(self.download_button)

        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFixedHeight(160)
        layout.addWidget(self.log_output)

        self.setLayout(layout)

    def _on_mode_changed(self, checked_mp3):
        self.watermark_checkbox.setEnabled(not checked_mp3)
        if checked_mp3:
            self.watermark_checkbox.setChecked(False)

    def _choose_directory(self):
        chosen = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения", self.output_dir)
        if chosen:
            self.output_dir = chosen
            self.dir_label.setText(chosen)

    def _log(self, text):
        self.log_output.append(text)

    def _start_download(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Ошибка", "Введите ссылку на видео.")
            return

        mode = "mp3" if self.radio_mp3.isChecked() else "video"
        quality = self.quality_combo.currentData()
        watermark = self.watermark_checkbox.isChecked()

        self.download_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self._log(f"Начинаю: {url}")

        self.worker = DownloadWorker(url, mode, quality, watermark, self.output_dir)
        self.worker.log_message.connect(self._log)
        self.worker.progress_changed.connect(self.progress_bar.setValue)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.start()

    def _on_finished(self, success, message):
        self._log(message)
        self.download_button.setEnabled(True)
        if success:
            QMessageBox.information(self, "Готово", message)
        else:
            QMessageBox.critical(self, "Ошибка", message)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
