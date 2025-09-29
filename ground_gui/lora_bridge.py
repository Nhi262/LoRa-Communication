from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
import threading
import json
import os

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2 import id_token
from google.auth.transport import requests as grequests
from pathlib import Path 
from typing import Optional
from typing import Optional 


class LoraBridge(QObject):
    # ---------- Signals ----------
    positionUpdated      = pyqtSignal(float, float, float)
    positionUpdatedLocal = pyqtSignal(float, float, float)
    positionUpdatedGPS   = pyqtSignal(float, float, float)
    batteryUpdated       = pyqtSignal(float, float)
    speedUpdated         = pyqtSignal(float)
    linkUpdated          = pyqtSignal(bool)
    modePushed           = pyqtSignal(bool, str, str)

    # Auth/UI
    authChanged   = pyqtSignal(bool, str)   # (ok, role)
    googleAuthErr = pyqtSignal(str)

    # ---------- Config qua ENV ----------
    ALLOWED_HD = {d.strip().lower() for d in os.getenv("ALLOWED_HD", "eiu.edu.vn").split(",") if d.strip()}
    ADMIN_EMAILS    = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
    OPERATOR_EMAILS = {e.strip().lower() for e in os.getenv("OPERATOR_EMAILS", "").split(",") if e.strip()}

    _default_role = "viewer"

    # ---------- Bridge plumbing ----------
    def __init__(self):
            super().__init__()
            self.controller = None
            self._rx_thread = None

            self._authed = False
            self._role   = "viewer"
            self._google_email = None

            self._frontend_dir = Path(os.getenv("FRONTEND_DIR", Path.cwd()))
            # --- roles.json ---
            self._roles_path: Optional[Path] = self._find_roles_file()
            self._roles: Dict[str, Any] = {
                "allowed_domains": ["eiu.edu.vn"],   # fallback
                "admin": [],
                "operator": [],
                "domain_defaults": {},
                "default_role": "viewer",
            }
            if self._roles_path:
                self._load_roles(self._roles_path)
                print(f"[Auth] roles loaded from {self._roles_path}")
            else:
                print("[Auth] roles.json not found, using defaults.")


    def set_controller(self, controller):
        self.controller = controller
        if hasattr(self.controller, "set_gui_bridge"):
            self.controller.set_gui_bridge(self)
    @pyqtSlot(str)
    def set_frontend_dir(self, path: str):
        p = Path(path).expanduser().resolve()
        if p.exists():
            self._frontend_dir = p
            print(f"[Bridge] frontend_dir = {self._frontend_dir}")
            # thử nạp lại roles nếu có
            rp = self._find_roles_file()
            if rp:
                self._load_roles(rp)
                self._roles_path = rp
                print(f"[Auth] roles reloaded from {rp}")

    # ---------- Telemetry passthrough ----------
    @pyqtSlot(float, float, float)
    def update_position(self, x, y, z):
        self.positionUpdated.emit(x, y, z)
        self.positionUpdatedLocal.emit(x, y, z)

    @pyqtSlot(float, float, float)
    def update_local_position(self, x, y, z):
        self.positionUpdatedLocal.emit(x, y, z)
        self.positionUpdated.emit(x, y, z)

    @pyqtSlot(float, float, float)
    def update_global_position(self, lat, lon, alt):
        self.positionUpdatedGPS.emit(lat, lon, alt)

    # ---------- Link control ----------
    @pyqtSlot()
    def startConnection(self):
        if not self.controller:
            print("⚠️ No controller attached.")
            return
        print("🟢 GUI yêu cầu START kết nối LoRa")
        self.controller.start()
        if hasattr(self.controller, "set_gui_bridge"):
            self.controller.set_gui_bridge(self)
        if not self._rx_thread or not self._rx_thread.is_alive():
            self._rx_thread = threading.Thread(
                target=self.controller.read_position_from_drone, daemon=True
            )
            self._rx_thread.start()

    @pyqtSlot()
    def stopConnection(self):
        if self.controller:
            print("🔴 GUI yêu cầu STOP kết nối LoRa")
            self.controller.stop()
        else:
            print("⚠️ No controller attached.")

    @pyqtSlot()
    def landConnect(self):
        if self.controller:
            print("Đã gửi yêu cầu LAND đến Lora")
            self.controller.land_req()
        else:
            print("⚠️ No controller attached.")

    @pyqtSlot()
    def offBoardConnect(self):
        if self.controller:
            print("Đã gửi yêu cầu OFFBOARD đến Lora")
            self.controller.offboard_req()
        else:
            print("⚠️ No controller attached.")

    @pyqtSlot(list)
    def receivedTargetWaypoint(self, waypoints):
        if not self.controller:
            print("⚠️ No controller attached.")
            return
        print(f"✅ Nhận {len(waypoints)} waypoint từ JS:")
        for i, wp in enumerate(waypoints, 1):
            print(f"  {i}: {wp}")
        self.controller.update_waypoints(waypoints)
        self.controller.send_waypoints_to_drone()

    # ---------- Other passthrough ----------
    @pyqtSlot(float, float)
    def update_battery(self, percent, voltage):
        self.batteryUpdated.emit(percent, voltage)

    @pyqtSlot(float)
    def update_speed(self, spd):
        self.speedUpdated.emit(spd)

    @pyqtSlot(bool)
    def update_link(self, ok: bool):
        self.linkUpdated.emit(bool(ok))

    @pyqtSlot(bool, str, str)
    def mode_push(self, ok: bool, mode: str, msg: str):
        self.modePushed.emit(bool(ok), str(mode), str(msg))

    modePush = mode_push  # alias giữ nguyên

    # ---------- Role helpers ----------
    @pyqtSlot(str)
    def set_frontend_dir(self, path: str):
        p = Path(path).expanduser().resolve()
        if p.exists():
            self._frontend_dir = p
            print(f"[Bridge] frontend_dir = {self._frontend_dir}")
            # thử nạp lại roles nếu có
            rp = self._find_roles_file()
            if rp:
                self._load_roles(rp)
                self._roles_path = rp
                print(f"[Auth] roles reloaded from {rp}")

    def _find_roles_file(self) -> Optional[Path]:
        envp = os.getenv("ROLES_FILE")
        if envp:
            q = Path(envp).expanduser().resolve()
            if q.exists(): return q
        for q in [
            self._frontend_dir / "roles.json",
            self._frontend_dir / "secrets" / "roles.json",
            Path.cwd() / "roles.json",
            Path(__file__).parent / "roles.json",
        ]:
            if q.exists(): return q
        return None

    def _load_roles(self, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # normalize
        def _lower_list(k):
            return [s.strip().lower() for s in raw.get(k, []) if isinstance(s, str) and s.strip()]

        self._roles["allowed_domains"] = _lower_list("allowed_domains")
        self._roles["admin"]    = set(_lower_list("admin"))
        self._roles["operator"] = set(_lower_list("operator"))
        self._roles["domain_defaults"] = {
            (k or "").lower(): (v or "").lower()
            for k, v in raw.get("domain_defaults", {}).items()
            if isinstance(k, str) and isinstance(v, str)
        }
        self._roles["default_role"] = (raw.get("default_role") or "viewer").lower()

    def _email_domain(self, email: str) -> str:
        return (email.split("@", 1)[1] if "@" in email else "").lower()

    def _allowed_domains(self) -> set[str]:
        ads = set(self._roles.get("allowed_domains") or [])
        # nếu không cấu hình, coi như allow-all
        return ads

    # ---------------- Role decision ----------------
    def decide_role(self, email: str) -> str:
        e = (email or "").lower()
        dom = self._email_domain(e)

        # 1) admin luôn ưu tiên
        if e in self._roles["admin"]:
            return "admin"

        # 2) email operator cụ thể
        if e in self._roles["operator"]:
            return "operator"

        # 3) mặc định theo domain (nếu có)
        by_dom = self._roles["domain_defaults"].get(dom)
        if by_dom in {"admin", "operator", "viewer"}:
            return by_dom  # ví dụ gmail.com -> operator

        # 4) mặc định chung
        return self._roles.get("default_role", "viewer")

    # ---------------- Google OAuth ----------------
    def _emit_auth_failed(self, msg: str):
        print(f"[Auth] fail: {msg}")
        self.googleAuthErr.emit(msg)
        self.authChanged.emit(False, "")

    def _find_credentials_file(self) -> Optional[Path]:
        # (giống bản bạn đang dùng)
        envp = os.getenv("GOOGLE_CLIENT_SECRETS_FILE")
        if envp:
            p = Path(envp).expanduser().resolve()
            if p.exists(): return p
        for p in [
            self._frontend_dir / "credentials.json",
            self._frontend_dir / "secrets" / "credentials.json",
            Path.cwd() / "credentials.json",
            Path(__file__).parent / "credentials.json",
        ]:
            if p.exists(): return p
        return None

    def _google_login_flow(self):
        try:
            scopes = ["openid", "https://www.googleapis.com/auth/userinfo.profile",
                      "https://www.googleapis.com/auth/userinfo.email"]

            secrets_path = self._find_credentials_file()
            if not secrets_path:
                return self._emit_auth_failed("Không tìm thấy credentials.json.")

            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes=scopes)
            creds = flow.run_local_server(port=0, open_browser=True, prompt="consent")

            # đọc client_id (hỗ trợ cả installed/web)
            with open(secrets_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            cfg = raw.get("installed") or raw.get("web") or {}
            client_id = cfg.get("client_id")
            if not client_id:
                return self._emit_auth_failed("credentials.json thiếu client_id.")

            idinfo = id_token.verify_oauth2_token(creds.id_token, grequests.Request(), client_id)
            email = (idinfo.get("email") or "").lower()
            if not email or not idinfo.get("email_verified", False):
                return self._emit_auth_failed("Email chưa được Google xác minh.")

            # Gate theo allowed_domains (dựa vào suffix email, không phụ thuộc 'hd')
            allowed = self._allowed_domains()
            if allowed:
                dom = self._email_domain(email)
                if dom not in allowed:
                    return self._emit_auth_failed("Tài khoản không thuộc domain được phép.")

            role = self.decide_role(email)
            self._google_email = email
            self._authed = True
            self._role = role
            print(f"[Auth] {email} -> {role}")
            self.authChanged.emit(True, role)

        except Exception as e:
            self._emit_auth_failed(f"OAuth error: {e}")

    @pyqtSlot()
    def google_login(self):
        threading.Thread(target=self._google_login_flow, daemon=True).start()