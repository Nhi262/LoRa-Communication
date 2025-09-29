import sys
import os
import subprocess
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QHBoxLayout
from PyQt6 import uic
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QUrl, QTimer, Qt, QEvent


from lora_bridge import LoraBridge
from control import GroundController


os.environ["QTWEBENGINE_DICTIONARIES_PATH"] = "/dev/null"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi("ui/main.ui", self)
        self.setWindowTitle("Ground Control Station") 
        self.setContentsMargins(0, 0, 0, 0)
        if cw := self.centralWidget():
            cw.setContentsMargins(0, 0, 0, 0)
            if cw.layout():
                cw.layout().setContentsMargins(0, 0, 0, 0)
                cw.layout().setSpacing(0)
        # 1. Khởi động HTTP server
        self.http_process = subprocess.Popen(
            ["python3", "-m", "http.server", "8000"],
            cwd=os.path.abspath("index"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print("🚀 Đã khởi động HTTP server tại http://localhost:8000")

        # 2. Tạo Bridge giữa Python ↔ JavaScript (chỉ khởi tạo 1 lần!)
        self.bridge = LoraBridge()
        frontend_dir = os.path.abspath("index")
        self.bridge.set_frontend_dir(frontend_dir)
        print("FRONTEND_DIR =", frontend_dir)
        # 3. Tạo browser và channel
        self.browser = QWebEngineView(self)
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)  
        self.browser.page().setWebChannel(self.channel)

        # 4. Load map.html sau 300ms
        QTimer.singleShot(300, lambda: self.browser.load(QUrl("http://localhost:8000/map.html")))

        # 5. Gắn browser thay thế widget placeholder
        placeholder = self.findChild(QWidget, "load_map_widget")
        if placeholder:
            parent = placeholder.parent()
            if parent and parent.layout():
                layout = parent.layout()
                layout.replaceWidget(placeholder, self.browser)
                placeholder.deleteLater()

        # 6. Set layout ratio nếu là HBox
        main_layout = self.centralWidget().layout()
        if isinstance(main_layout, QHBoxLayout):
            main_layout.setStretch(0, 0)
            main_layout.setStretch(1, 10)

        # 7. Tạo GroundController, gắn vào bridge
        self.controller = GroundController(port='/dev/lora_ground', baudrate=9600, gui_bridge=self.bridge)
        self.bridge.set_controller(self.controller)

        # 8. Kết nối LoRa (không start ngay, chờ JS trigger)
        self.controller.connect()

    def closeEvent(self, event):
        if hasattr(self, 'http_process'):
            print("🛑 Đang tắt HTTP server...")
            self.http_process.terminate()
            try:
                self.http_process.wait(timeout=2)
            except Exception:
                self.http_process.kill()
        event.accept()

# ✅ Đây là phần chạy chính
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        print("⛔ Dừng chương trình thủ công.")