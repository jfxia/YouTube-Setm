# main_app.py

import os
import sys
import subprocess
import json
import re
import sqlite3
from datetime import datetime

import requests
import yt_dlp
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QSettings
from PyQt5.QtGui import QFont, QIcon, QPixmap, QImage
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QLineEdit, QPushButton, QProgressBar, QFileDialog,
                             QMessageBox, QGroupBox, QTextEdit, QComboBox, QFrame,
                             QTabWidget, QScrollArea)

# --- Helper Functions ---

def get_video_duration(video_path):
    """Gets video duration using ffprobe."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", video_path]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, encoding='utf-8')
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error getting video duration: {e}")
        return 0

def get_video_bitrate(video_path):
    """Gets video bitrate using ffprobe, with a fallback to format bitrate."""
    cmd_stream = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=bit_rate", "-of", "default=noprint_wrappers=1:nokey=1", video_path
    ]
    try:
        result = subprocess.run(cmd_stream, capture_output=True, text=True, check=True, encoding='utf-8')
        bitrate = result.stdout.strip()
        if bitrate and bitrate.isdigit() and int(bitrate) > 0:
            return bitrate
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    cmd_format = [
        "ffprobe", "-v", "error", "-show_entries", "format=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path
    ]
    try:
        result = subprocess.run(cmd_format, capture_output=True, text=True, check=True, encoding='utf-8')
        bitrate = result.stdout.strip()
        if bitrate and bitrate.isdigit() and int(bitrate) > 0:
            return bitrate
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Could not determine bitrate: {e}")
    
    return None

def translate_text_deepseek(text, api_key):
    """Translates text to Chinese using DeepSeek API."""
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    data = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "You are a translation assistant. Translate the given English or Japanese text into simplified Chinese. Provide only the direct translation without any explanations."},
            {"role": "user", "content": text}
        ],
        "temperature": 0.2
    }
    response = requests.post(url, headers=headers, json=data, timeout=60)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()

def translate_srt_file(input_srt, output_srt, api_key, log_signal):
    """Reads an SRT file, translates its content, and writes to a new file."""
    with open(input_srt, "r", encoding="utf-8") as f:
        lines = f.readlines()

    translated_lines = []
    text_buffer = []
    
    for i, line in enumerate(lines):
        if re.match(r'^\d+\s*$', line):
            translated_lines.append(line)
        elif '-->' in line:
            translated_lines.append(line)
        elif not line.strip():
            if text_buffer:
                original_text = ' '.join(text_buffer).strip()
                log_signal.emit(f"[INFO] Translating: {original_text}")
                try:
                    translated_text = translate_text_deepseek(original_text, api_key)
                    translated_lines.append(translated_text + '\n')
                except Exception as e:
                    log_signal.emit(f"[ERROR] Translation failed for '{original_text}': {e}")
                    translated_lines.extend([l + '\n' for l in text_buffer])
                text_buffer = []
            translated_lines.append('\n')
        else:
            text_buffer.append(line.strip())

    if text_buffer:
        original_text = ' '.join(text_buffer).strip()
        log_signal.emit(f"[INFO] Translating: {original_text}")
        try:
            translated_text = translate_text_deepseek(original_text, api_key)
            translated_lines.append(translated_text + '\n')
        except Exception as e:
            log_signal.emit(f"[ERROR] Translation failed for '{original_text}': {e}")
            translated_lines.extend([l + '\n' for l in text_buffer])

    with open(output_srt, "w", encoding="utf-8") as f:
        f.writelines(translated_lines)

def clean_youtube_url(url):
    """Cleans a YouTube URL to its most basic form."""
    patterns = [
        r'(https?://(?:www\.|m\.|music\.)?youtube\.com/watch\?v=[a-zA-Z0-9_-]+)',
        r'(https?://youtu\.be/[a-zA-Z0-9_-]+)'
    ]
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            return match.group(1)
    return url

def open_containing_folder(path):
    """Opens the folder containing the specified file."""
    if not os.path.exists(path):
        # If the path points to a file that doesn't exist, use its directory
        folder = os.path.dirname(path)
    elif os.path.isdir(path):
        folder = path
    else:
        folder = os.path.dirname(path)

    if not os.path.isdir(folder):
        print(f"Error: Folder does not exist at {folder}")
        return

    if sys.platform == 'win32':
        os.startfile(folder)
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', folder])
    else:
        subprocess.Popen(['xdg-open', folder])


# --- Database Manager ---
class DatabaseManager:
    """Manages the SQLite database for processing history."""
    def __init__(self, db_path="processing_history.db"):
        self.db_path = db_path
        self.init_database()

    def init_database(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL,
                        url TEXT NOT NULL,
                        process_type TEXT,
                        quality TEXT,
                        final_path TEXT,
                        process_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        status TEXT
                    )
                ''')
                conn.commit()
        except Exception as e:
            print(f"Database initialization error: {e}")

    def save_record(self, title, url, process_type, quality, final_path, status):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO history (title, url, process_type, quality, final_path, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (title, url, process_type, quality, final_path, status))
                conn.commit()
        except Exception as e:
            print(f"Error saving record: {e}")

    def get_history(self, limit=50):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id, title, url, process_type, quality, final_path, process_date, status FROM history ORDER BY process_date DESC LIMIT ?', (limit,))
                return cursor.fetchall()
        except Exception as e:
            print(f"Error getting history: {e}")
            return []
    
    def clear_history(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM history')
                conn.commit()
                return True
        except Exception as e:
            print(f"Error clearing history: {e}")
            return False

# --- Processing Thread ---
class ProcessingThread(QThread):
    """A single thread to handle all backend processing steps."""
    stage_changed = pyqtSignal(str)
    progress_update = pyqtSignal(int, str)
    log_message = pyqtSignal(str)
    finished = pyqtSignal(bool, str, str) # success, message, final_path
    video_info_retrieved = pyqtSignal(dict)

    def __init__(self, options):
        super().__init__()
        self.options = options
        self._is_cancelled = False
        self.current_process = None

    def run(self):
        try:
            self.stage_changed.emit("Getting video information...")
            self.log_message.emit(f"[INFO] Getting info for URL: {self.options['url']}")
            
            ydl_info_opts = {'quiet': True, 'no_warnings': True}
            with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
                info_dict = ydl.extract_info(self.options['url'], download=False)

            self.video_info = {
                'title': info_dict.get('title', 'Unknown Title'),
                'uploader': info_dict.get('uploader', 'Unknown Uploader'),
                'thumbnail': info_dict.get('thumbnail', ''),
                'duration': info_dict.get('duration', 0)
            }
            self.video_info_retrieved.emit(self.video_info)
            self.options['title'] = self.video_info['title']

            if self._is_cancelled: return
            
            if self.options['type'] == 'Audio':
                self._download_audio()
            else:
                self._process_video()

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.log_message.emit(f"[ERROR] A critical error occurred: {e}")
            self.finished.emit(False, str(e), "")

    def _download_audio(self):
        self.stage_changed.emit("Step 1/1: Downloading Audio (MP3)")
        
        output_tmpl = os.path.join(self.options['output_dir'], '%(title)s.%(ext)s')
        final_path = os.path.join(self.options['output_dir'], f"{self.options['title']}.mp3")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_tmpl,
            'progress_hooks': [self.progress_hook],
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192',}],
            'noplaylist': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([self.options['url']])
        
        if not self._is_cancelled:
            self.finished.emit(True, "Audio download completed successfully!", final_path)

    def _process_video(self):
        base_name = re.sub(r'[\\/*?:"<>|]', "", self.options['title'])
        downloaded_video_path = os.path.join(self.options['output_dir'], f"{base_name}.mp4")
        srt_path = os.path.join(self.options['output_dir'], f"{base_name}.srt")
        translated_srt_path = os.path.join(self.options['output_dir'], f"{base_name}_zh.srt")
        final_video_path = os.path.join(self.options['output_dir'], f"{base_name}_translated.mp4")

        self._download_video(downloaded_video_path)
        if self._is_cancelled: return

        self._extract_subtitles(downloaded_video_path, srt_path)
        if self._is_cancelled: return

        self._translate_subtitles(srt_path, translated_srt_path)
        if self._is_cancelled: return

        self._synthesize_video(downloaded_video_path, translated_srt_path, final_video_path)
        if self._is_cancelled: return

        ''' #delete intermediate files
        try:
            os.remove(downloaded_video_path)
            os.remove(srt_path)
            os.remove(translated_srt_path)
            self.log_message.emit("[INFO] Cleaned up intermediate files.")
        except OSError as e:
            self.log_message.emit(f"[WARN] Could not remove temporary files: {e}")
        '''

        self.finished.emit(True, "Video processing completed successfully!", final_video_path)

    def _run_subprocess(self, cmd):
        self.log_message.emit(f"[CMD] {' '.join(cmd)}")
        self.current_process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
            universal_newlines=True, encoding='utf-8', errors='ignore'
        )
        for line in self.current_process.stdout:
            if self._is_cancelled:
                self.current_process.terminate()
                self.log_message.emit("[INFO] Process terminated by user.")
                return
            self.log_message.emit(line.strip())
        
        self.current_process.wait()
        if self.current_process.returncode != 0 and not self._is_cancelled:
            raise RuntimeError(f"A subprocess failed with exit code {self.current_process.returncode}.")

    def _download_video(self, output_path):
        self.stage_changed.emit("Step 1/4: Downloading Video")
        quality_map = {"Best": "bv*+ba/b", "1080p": "bv[height<=1080]+ba/b[height<=1080]", "720p": "bv[height<=720]+ba/b[height<=720]","480p": "bv[height<=480]+ba/b[height<=480]"}
        format_selector = quality_map.get(self.options['quality'], 'bv*+ba/b')
        ydl_opts = {'format': format_selector, 'outtmpl': output_path, 'progress_hooks': [self.progress_hook], 'merge_output_format': 'mp4', 'noplaylist': True,}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([self.options['url']])

    def _extract_subtitles(self, video_path, srt_path):
        self.stage_changed.emit("Step 2/4: Extracting Subtitles (Whisper)")
        self.progress_update.emit(0, "Initializing Whisper... This may take a while.")
        cmd = ["whisper", video_path, "--model", self.options['model'], "--language", self.options['language'], "--output_format", "srt", "--output_dir", self.options['output_dir']]
        self._run_subprocess(cmd)

    def _translate_subtitles(self, srt_path, translated_srt_path):
        self.stage_changed.emit("Step 3/4: Translating Subtitles (DeepSeek API)")
        self.progress_update.emit(0, "Sending text to translation API...")
        if not self.options.get('api_key'):
            raise ValueError("DeepSeek API Key is missing. Please set it in the Settings tab.")
        translate_srt_file(srt_path, translated_srt_path, self.options['api_key'], self.log_message)
        self.log_message.emit("[INFO] Subtitles translated successfully.")

    def _synthesize_video(self, video_path, srt_path, output_path):
        self.stage_changed.emit("Step 4/4: Encoding Final Video (FFmpeg)")
        absolute_srt_path = os.path.abspath(srt_path).replace('\\', '/')
        if sys.platform == "win32":
            absolute_srt_path = absolute_srt_path.replace(':', '\\:', 1)
        filter_string = f"subtitles='{absolute_srt_path}'"
        cmd = ["ffmpeg", "-i", video_path, "-vf", filter_string]
        bitrate = get_video_bitrate(video_path)
        if bitrate:
            self.log_message.emit(f"[INFO] Detected original bitrate: {bitrate} bps. Using it for encoding.")
            cmd.extend(["-c:v", "libx264", "-preset", "medium", "-b:v", bitrate])
        else:
            self.log_message.emit("[WARN] Could not detect bitrate. Using CRF=23 for encoding.")
            cmd.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "23"])
        cmd.extend(["-c:a", "copy", "-y", output_path])
        self.log_message.emit(f"[CMD] {' '.join(cmd)}")
        total_duration = get_video_duration(video_path)
        self.current_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, encoding='utf-8', errors='ignore')
        for line in self.current_process.stdout:
            if self._is_cancelled:
                self.current_process.terminate()
                self.log_message.emit("[INFO] FFmpeg process terminated by user.")
                return
            self.log_message.emit(line.strip())
            if "time=" in line:
                match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", line)
                if match and total_duration > 0:
                    h, m, s, ms = map(int, match.groups())
                    elapsed_seconds = h * 3600 + m * 60 + s + ms / 100
                    progress = int((elapsed_seconds / total_duration) * 100)
                    self.progress_update.emit(progress, f"{progress}% encoded")
        self.current_process.wait()
        if self.current_process.returncode != 0 and not self._is_cancelled:
            raise RuntimeError("FFmpeg failed to synthesize the video.")

    def progress_hook(self, d):
        if self._is_cancelled:
            raise yt_dlp.utils.DownloadCancelled
        
        ansi_escape_pattern = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        
        if d['status'] == 'downloading':
            percent_str_raw = d.get('_percent_str', '0.0%')
            speed_str_raw = d.get('_speed_str', 'N/A')
            percent_str_clean = ansi_escape_pattern.sub('', percent_str_raw).replace('%', '').strip()
            speed_str_clean = ansi_escape_pattern.sub('', speed_str_raw).strip()
            try:
                percent = float(percent_str_clean)
                self.progress_update.emit(int(percent), speed_str_clean)
            except ValueError as e:
                self.log_message(f"[WARN] Could not parse progress update: {e}")
        elif d['status'] == 'finished':
            self.progress_update.emit(100, "Finalizing...")

    def cancel(self):
        self._is_cancelled = True
        self.log_message.emit("[ACTION] Cancellation requested by user...")
        if self.current_process:
            self.current_process.terminate()


# --- Main Application Window ---
class VideoProcessorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("YouTube Unified Video Processor")
        self.setGeometry(100, 100, 1000, 800)
        
        # Setup icon
        try:
            self.setWindowIcon(QIcon('icons/app_icon.png'))
        except Exception as e:
            print(f"Could not load icon: {e}")

        self.settings = QSettings("MyCompany", "VideoProcessorApp")
        self.db_manager = DatabaseManager()
        self.processing_thread = None
        self.current_options = {}

        self.init_ui()
        self.load_settings()
        self.load_history()

    def init_ui(self):
        self.setStyleSheet("""
            QTabBar::tab {
                background-color: #3B4252;
                border: 1px solid #4C566A;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 10px 20px;
                font-weight: bold;
                min-width: 150px; 
            }
            QMainWindow, QWidget {
                background-color: #2E3440;
                color: #D8DEE9;
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                font-size: 11pt;
            }
            QGroupBox {
                background-color: #3B4252;
                border: 1px solid #4C566A;
                border-radius: 8px;
                margin-top: 1em;
                padding: 15px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 10px;
                left: 10px;
                color: #ECEFF4;
            }
            QLabel { background-color: transparent; }
            QLineEdit, QComboBox {
                background-color: #434C5E;
                border: 1px solid #4C566A;
                border-radius: 5px;
                padding: 8px;
                min-height: 28px;
            }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #88C0D0; }
            QPushButton {
                background-color: #5E81AC;
                color: #ECEFF4;
                border: none;
                border-radius: 5px;
                padding: 10px 20px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #81A1C1; }
            QPushButton:pressed { background-color: #4C566A; }
            QPushButton:disabled { background-color: #3B4252; color: #6c7583; }
            #cancelButton { background-color: #BF616A; }
            #cancelButton:hover { background-color: #D08770; }
            QProgressBar {
                border: 1px solid #4C566A;
                border-radius: 5px;
                text-align: center;
                color: #ECEFF4;
                background-color: #3B4252;
                font-weight: bold;
            }
            QProgressBar::chunk { background-color: #A3BE8C; border-radius: 4px; }
            QTextEdit {
                background-color: #272B35;
                border: 1px solid #4C566A;
                border-radius: 5px;
                font-family: 'Consolas', 'Courier New', monospace;
            }
            QTabWidget::pane { border: none; }
            QTabBar::tab:selected {
                background-color: #434C5E;
                color: #FFFF33;
                border-bottom: 1px solid #434C5E;
            }
            QTabBar::tab:hover { background-color: #4C566A; }
            #historyItem { border: 1px solid #4C566A; border-radius: 5px; margin: 4px; padding: 8px; background-color: #434C5E;}
            #historyTitle { font-weight: bold; font-size: 12pt; }
            #historyUrl { color: #88C0D0; }
            #historyDetails { color: #B0B8C4; font-size: 9pt; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # --- Main Processing Tab ---
        main_tab = QWidget()
        main_tab_layout = QVBoxLayout(main_tab)
        
        url_group = QGroupBox("1. Input YouTube URL")
        url_layout = QHBoxLayout(url_group)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Enter YouTube video or playlist link...")
        self.paste_btn = QPushButton("Paste")
        self.paste_btn.clicked.connect(lambda: self.url_input.setText(QApplication.clipboard().text()))
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(self.paste_btn)
        main_tab_layout.addWidget(url_group)

        options_group = QGroupBox("2. Select Processing Options")
        options_layout = QGridLayout(options_group)
        options_layout.addWidget(QLabel("Process Type:"), 0, 0)
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Video (Download, Translate, Synthesize)", "Audio Only (Download as MP3)"])
        self.type_combo.currentIndexChanged.connect(self.toggle_video_options)
        options_layout.addWidget(self.type_combo, 0, 1)
        self.quality_label = QLabel("Video Quality:")
        options_layout.addWidget(self.quality_label, 1, 0)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["Best", "1080p", "720p", "480p"])
        options_layout.addWidget(self.quality_combo, 1, 1)
        self.lang_label = QLabel("Original Language:")
        options_layout.addWidget(self.lang_label, 2, 0)
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["en", "ja"])
        options_layout.addWidget(self.lang_combo, 2, 1)
        self.model_label = QLabel("Whisper Model:")
        options_layout.addWidget(self.model_label, 3, 0)
        self.model_combo = QComboBox()
        self.model_combo.addItems(["tiny", "small", "medium"])
        self.model_combo.setCurrentText("small")
        options_layout.addWidget(self.model_combo, 3, 1)
        main_tab_layout.addWidget(options_group)

        output_group = QGroupBox("3. Set Output Directory")
        output_layout = QHBoxLayout(output_group)
        self.path_display = QLineEdit(os.path.expanduser("~"))
        self.path_display.setReadOnly(True)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.select_directory)
        output_layout.addWidget(self.path_display)
        output_layout.addWidget(browse_btn)
        main_tab_layout.addWidget(output_group)

        action_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start Processing")
        self.start_btn.clicked.connect(self.start_processing)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("cancelButton")
        self.cancel_btn.clicked.connect(self.cancel_processing)
        self.cancel_btn.setEnabled(False)
        action_layout.addStretch()
        action_layout.addWidget(self.start_btn)
        action_layout.addWidget(self.cancel_btn)
        main_tab_layout.addLayout(action_layout)
        
        # --- Progress & Log Tab ---
        progress_tab = QWidget()
        progress_layout = QVBoxLayout(progress_tab)
        progress_group = QGroupBox("Live Progress")
        progress_group_layout = QVBoxLayout(progress_group)
        self.stage_label = QLabel("Status: Ready")
        font = self.stage_label.font(); font.setPointSize(14); font.setBold(True)
        self.stage_label.setFont(font)
        self.stage_label.setAlignment(Qt.AlignCenter)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_status_label = QLabel("")
        self.progress_status_label.setAlignment(Qt.AlignCenter)
        progress_group_layout.addWidget(self.stage_label)
        progress_group_layout.addWidget(self.progress_bar)
        progress_group_layout.addWidget(self.progress_status_label)
        progress_layout.addWidget(progress_group)
        log_group = QGroupBox("Detailed Log")
        log_layout = QVBoxLayout(log_group)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        log_layout.addWidget(self.log_box)
        progress_layout.addWidget(log_group)

        # --- History Tab ---
        history_tab = QWidget()
        history_tab_layout = QVBoxLayout(history_tab)
        
        history_controls = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.load_history)
        clear_btn = QPushButton("Clear All History")
        clear_btn.setObjectName("cancelButton")
        clear_btn.clicked.connect(self.clear_history)
        history_controls.addWidget(refresh_btn)
        history_controls.addStretch()
        history_controls.addWidget(clear_btn)
        history_tab_layout.addLayout(history_controls)
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")
        
        self.history_widget = QWidget()
        self.history_layout = QVBoxLayout(self.history_widget)
        self.history_layout.setAlignment(Qt.AlignTop)
        
        scroll_area.setWidget(self.history_widget)
        history_tab_layout.addWidget(scroll_area)
        
        # --- Settings Tab ---
        settings_tab = QWidget()
        settings_layout = QVBoxLayout(settings_tab)
        api_group = QGroupBox("API Keys")
        api_layout = QGridLayout(api_group)
        api_layout.addWidget(QLabel("DeepSeek API Key:"), 0, 0)
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        api_layout.addWidget(self.api_key_input, 0, 1)
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self.save_settings)
        api_layout.addWidget(save_btn, 1, 1, Qt.AlignRight)
        settings_layout.addWidget(api_group)
        settings_layout.addStretch()

        self.tabs.addTab(main_tab, "Process")
        self.tabs.addTab(progress_tab, "Progress & Log")
        self.tabs.addTab(history_tab, "History")
        self.tabs.addTab(settings_tab, "Settings")

    def load_history(self):
        # Clear current items
        for i in reversed(range(self.history_layout.count())): 
            self.history_layout.itemAt(i).widget().setParent(None)
            
        history_data = self.db_manager.get_history()
        
        if not history_data:
            empty_label = QLabel("No history yet.")
            empty_label.setAlignment(Qt.AlignCenter)
            self.history_layout.addWidget(empty_label)
            return
            
        for record in history_data:
            item_widget = self.create_history_item_widget(record)
            self.history_layout.addWidget(item_widget)
    
    def create_history_item_widget(self, record):
        # record: id, title, url, process_type, quality, final_path, process_date, status
        item_frame = QFrame()
        item_frame.setObjectName("historyItem")
        layout = QVBoxLayout(item_frame)

        title = QLabel(record[1])
        title.setObjectName("historyTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        url = QLabel(f"<a href='{record[2]}' style='color: #88C0D0;'>{record[2]}</a>")
        url.setOpenExternalLinks(True)
        url.setWordWrap(True)
        layout.addWidget(url)
        
        details_text = f"Type: {record[3]} | Quality: {record[4]} | Status: {record[7]} | Date: {record[6]}"
        details = QLabel(details_text)
        details.setObjectName("historyDetails")
        layout.addWidget(details)

        path_layout = QHBoxLayout()
        path_label = QLineEdit(record[5])
        path_label.setReadOnly(True)
        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.clicked.connect(lambda: open_containing_folder(record[5]))
        path_layout.addWidget(path_label)
        path_layout.addWidget(open_folder_btn)
        layout.addLayout(path_layout)
        
        return item_frame

    def clear_history(self):
        reply = QMessageBox.question(self, "Confirm Clear", 
                                     "Are you sure you want to delete all history records? This cannot be undone.",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            if self.db_manager.clear_history():
                self.load_history()
                QMessageBox.information(self, "Success", "History has been cleared.")
            else:
                QMessageBox.critical(self, "Error", "Failed to clear history.")

    def toggle_video_options(self, index):
        is_video = (index == 0)
        for widget in [self.quality_label, self.quality_combo, self.lang_label, self.lang_combo, self.model_label, self.model_combo]:
            widget.setVisible(is_video)

    def select_directory(self):
        path = QFileDialog.getExistingDirectory(self, "Select Save Directory", self.path_display.text())
        if path: self.path_display.setText(path)

    def load_settings(self):
        self.api_key_input.setText(self.settings.value("deepseek_api_key", ""))
        self.path_display.setText(self.settings.value("last_output_dir", os.path.expanduser("~")))

    def save_settings(self):
        self.settings.setValue("deepseek_api_key", self.api_key_input.text())
        self.settings.setValue("last_output_dir", self.path_display.text())
        QMessageBox.information(self, "Success", "Settings saved successfully.")

    def log_message(self, message):
        self.log_box.append(message)

    def start_processing(self):
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Input Error", "Please provide a valid YouTube URL.")
            return

        self.current_options = {
            'url': clean_youtube_url(url),
            'output_dir': self.path_display.text(),
            'type': 'Video' if self.type_combo.currentIndex() == 0 else 'Audio',
            'quality': self.quality_combo.currentText() if self.type_combo.currentIndex() == 0 else 'N/A',
            'language': self.lang_combo.currentText(),
            'model': self.model_combo.currentText(),
            'api_key': self.api_key_input.text()
        }

        if self.current_options['type'] == 'Video' and not self.current_options['api_key']:
            QMessageBox.warning(self, "API Key Error", "DeepSeek API Key is required for video processing. Please set it in the Settings tab.")
            return

        self.tabs.setCurrentIndex(1) # Switch to progress tab
        self.log_box.clear()
        self.progress_bar.setValue(0)
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.log_message("[INFO] Starting new process...")
        
        self.processing_thread = ProcessingThread(self.current_options)
        self.processing_thread.stage_changed.connect(self.stage_label.setText)
        self.processing_thread.progress_update.connect(lambda p, s: [self.progress_bar.setValue(p), self.progress_status_label.setText(s)])
        self.processing_thread.log_message.connect(self.log_message)
        self.processing_thread.finished.connect(self.process_finished)
        self.processing_thread.start()

    def cancel_processing(self):
        if self.processing_thread and self.processing_thread.isRunning():
            self.processing_thread.cancel()
            self.stage_label.setText("Cancelling...")
            self.cancel_btn.setEnabled(False)

    def process_finished(self, success, message, final_path):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        
        status = "Completed" if success else "Failed"
        
        # Save record to database
        self.db_manager.save_record(
            title=self.current_options.get('title', 'Unknown Title'),
            url=self.current_options.get('url'),
            process_type=self.current_options.get('type'),
            quality=self.current_options.get('quality'),
            final_path=final_path,
            status=status
        )
        self.load_history() # Refresh history tab

        if success:
            self.stage_label.setText("Finished!")
            self.progress_bar.setValue(100)
            self.log_message(f"[SUCCESS] {message}")
            QMessageBox.information(self, "Success", f"Process completed!\nOutput file: {final_path}")
        else:
            self.stage_label.setText("Error!")
            self.log_message(f"[ERROR] {message}")
            QMessageBox.critical(self, "Error", f"An error occurred during processing:\n{message}")

        self.processing_thread = None

    def closeEvent(self, event):
        #self.save_settings()
        if self.processing_thread and self.processing_thread.isRunning():
            self.cancel_processing()
            self.processing_thread.wait()
        event.accept()

if __name__ == "__main__":
    try:
        import whisper
    except ImportError as e:
        QMessageBox.critical(None, "Dependency Error", "The 'openai-whisper' library is missing.\nPlease install it via: pip install git+https://github.com/openai/whisper.git")
        sys.exit(1)

    app = QApplication(sys.argv)
    window = VideoProcessorApp()
    window.show()
    sys.exit(app.exec_())