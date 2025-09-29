import serial
import json
import time
import threading
import math

def _is_num(x):
    try:
        return isinstance(x, (int, float)) and math.isfinite(float(x))
    except Exception:
        return False

def _clean_json_str(s: str) -> str:
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start:end+1]
    return ""
def _as_true(v):
    if isinstance(v, bool): return v
    if isinstance(v, (int, float)): return int(v) == 1
    if isinstance(v, str): return v.strip().lower() in ("1", "true", "t", "yes", "y")
    return False

class GroundController:
    def __init__(self, port='/dev/lora_ground', baudrate=9600, gui_bridge=None):
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.waypoints = []
        self.received_thread = None
        self.received = False
        self.gui_bridge = gui_bridge

       
        self._last_hb = 0.0            
        self._link_ok = False
        self._hb_timeout = 30.0           
        self._hb_thread = None           # watchdog thread
        self._last_ack_mode = None
        self._last_ack_at = 0.0 

    # ------------- Serial -------------
    def connect(self):
        if self.ser is None or not self.ser.is_open:
            try:
                self.ser = serial.Serial(self.port, self.baudrate, timeout=0.2)
                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()
                time.sleep(0.2)
                print(f"✅ Đã kết nối LoRa tại {self.port} @ {self.baudrate}")
            except Exception as e:
                print(f"❌ Không thể kết nối: {e}")
                self.ser = None

    def start(self):
        self.connect()
        if self.ser and self.ser.is_open:
            try:
                print("[INFO] Gửi lệnh ON tới LoRa")
                self.ser.write(b'ON\n')
                self.ser.flush()
            except Exception as e:
                print(f"❌[ERROR] Lỗi gửi lệnh ON: {e}")
        else:
            print("[ERROR] Serial không mở.")

    def stop(self):
       
        self.received = False
        self._last_hb = 0.0
        if self._link_ok:
            self._link_ok = False
            self._emit_link(False)

        if self.ser and self.ser.is_open:
            try:
                print("[INFO] Gửi lệnh OFF tới LoRa")
                self.ser.write(b'OFF\n')
                self.ser.flush()
            except Exception as e:
                print(f"⚠️ Không gửi được OFF: {e}")
            try:
                self.ser.close()
                print("[INFO] Đã đóng serial")
            except Exception as e:
                print(f"⚠️ Lỗi khi đóng serial: {e}")
        else:
            print("[INFO] Serial đã đóng hoặc chưa mở.")

    def set_gui_bridge(self, bridge):
        self.gui_bridge = bridge

    # ------------- Link helper -------------
    def _emit_link(self, ok: bool):
        if self.gui_bridge and hasattr(self.gui_bridge, "update_link"):
            try:
                self.gui_bridge.update_link(bool(ok))
            except Exception as e:
                print(f"⚠️ GUI bridge error (update_link): {e}")

    def _hb_watch(self, timeout=30.0, interval=30, grace=2):
        missed = 0
        while self.received:
            last = max(self._last_hb, getattr(self, "_last_seen", 0.0))
            dt = time.monotonic() - last
            if dt > timeout:
                missed += 1
                if self._link_ok and missed >= grace:
                    self._link_ok = False
                    self._emit_link(False)
            else:
                missed = 0
                if not self._link_ok and last > 0:
                    self._link_ok = True
                    self._emit_link(True)
            time.sleep(interval)

    # ------------- RX loop -------------
    def read_position_from_drone(self):
        if not self.ser or not self.ser.is_open:
            print("⚠️ Chưa kết nối serial.")
            return

        self.received = True

        if not self._hb_thread or not self._hb_thread.is_alive():
            self._hb_thread = threading.Thread(
                target=self._hb_watch,
                kwargs=dict(timeout=self._hb_timeout, interval=0.5, grace=2),
                daemon=True
            )
            self._hb_thread.start()
        def _read_loop():
            print("📡 Bắt đầu nhận vị trí từ drone...")
            buffer = ""
            while self.received:
                try:
                    chunk = self.ser.read(256)
                    if not chunk:
                        continue

                    # Ghép buffer
                    buffer += chunk.decode('utf-8', errors='replace')

                    # CHUẨN HOÁ line ending: CRLF/CR -> LF
                    if "\r" in buffer:
                        buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")

                    # TÁCH DÒNG AN TOÀN
                    while True:
                        nl = buffer.find("\n")
                        if nl == -1:
                            break
                        line = buffer[:nl]
                        buffer = buffer[nl+1:]

                        line = line.strip()
                        if not line:
                            continue

                        clean_line = _clean_json_str(line)
                        if not clean_line:
                            print(f"⚠️ Bỏ qua gói không hợp lệ: {line}")
                            continue

                        print(f"[RAW] {clean_line}")
                        try:
                            data = json.loads(clean_line)
                        except json.JSONDecodeError:
                            print(f"⚠️ Không decode được JSON: {clean_line}")
                            continue

                        now = time.monotonic()
                        self._last_seen = now

                        if data.get("event") == "mode_push":
                            ok   = bool(data.get("status", False))
                            mode = str(data.get("mode", "")).upper()
                            msg  = str(data.get("msg", ""))
                            self._last_ack_mode = mode
                            self._last_ack_at   = now
                            if self.gui_bridge:
                                try:
                                    if hasattr(self.gui_bridge, "mode_push"):
                                        self.gui_bridge.mode_push(ok, mode, msg)
                                    elif hasattr(self.gui_bridge, "modePush"):
                                        self.gui_bridge.modePush(ok, mode, msg)
                                except Exception as e:
                                    print(f"GUI bridge error (mode_push): {e}")

                        if _as_true(data.get("hb", 0)):
                            self._last_hb = now
                            if not self._link_ok:
                                self._link_ok = True
                                self._emit_link(True)

                        if all(k in data for k in ("x","y","z")) and _is_num(data["x"]) and _is_num(data["y"]) and _is_num(data["z"]):
                            x, y, z = float(data["x"]), float(data["y"]), float(data["z"])
                            print(f"📥 Local position: x={x}, y={y}, z={z}")
                            if self.gui_bridge and hasattr(self.gui_bridge, "update_position"):
                                try: self.gui_bridge.update_position(x, y, z)
                                except Exception as e: print(f"⚠️ GUI bridge error (pos): {e}")

                        if all(k in data for k in ("lat","lon","alt")) and _is_num(data["lat"]) and _is_num(data["lon"]) and _is_num(data["alt"]):
                            lat, lon, alt = float(data["lat"]), float(data["lon"]), float(data["alt"])
                            print(f"📥 Global position: lat={lat}, lon={lon}, alt={alt}")
                            if self.gui_bridge and hasattr(self.gui_bridge, "update_global_position"):
                                try: self.gui_bridge.update_global_position(lat, lon, alt)
                                except Exception as e: print(f"⚠️ GUI bridge error (gps): {e}")
                        # ---- Battery ----
                        try:
                            percent = None; voltage = None
                            if "battery" in data and isinstance(data["battery"], dict):
                                b = data["battery"]
                                if "percent" in b and _is_num(b["percent"]):
                                    pv = float(b["percent"])
                                    percent = pv * 100.0 if pv <= 1.0 else pv
                                if "voltage" in b and _is_num(b["voltage"]):
                                    voltage = float(b["voltage"])
                            if percent is None and "percent" in data and _is_num(data["percent"]):
                                pv = float(data["percent"])
                                percent = pv * 100.0 if pv <= 1.0 else pv
                            if percent is None and "battery" in data and _is_num(data["battery"]):
                                pv = float(data["battery"])
                                percent = pv * 100.0 if pv <= 1.0 else pv
                            if voltage is None and "voltage" in data and _is_num(data["voltage"]):
                                voltage = float(data["voltage"])
                            if voltage is None and "volt" in data and _is_num(data["volt"]):
                                voltage = float(data["volt"])

                            if self.gui_bridge and (percent is not None or voltage is not None) and hasattr(self.gui_bridge, "update_battery"):
                                try:
                                    p = float(percent) if percent is not None else -1.0
                                    v = float(voltage) if voltage is not None else float("nan")
                                    self.gui_bridge.update_battery(p, v)
                                except Exception as e:
                                    print(f"⚠️ GUI bridge error (battery): {e}")
                        except Exception as e:
                            print(f"⚠️ Battery parse error: {e}")

                        # ---- Speed ----
                        try:
                            spd = None
                            if "speed" in data and _is_num(data["speed"]):
                                spd = float(data["speed"])
                            elif "vel" in data and _is_num(data["vel"]):
                                spd = float(data["vel"])
                            if spd is not None and self.gui_bridge and hasattr(self.gui_bridge, "update_speed"):
                                self.gui_bridge.update_speed(spd)
                        except Exception as e:
                            print(f"⚠️ GUI bridge error (speed): {e}")

                except Exception as e:
                    print(f"❌ Lỗi đọc serial: {e}")
                    time.sleep(0.5)

        self.received_thread = threading.Thread(target=_read_loop, daemon=True)
        self.received_thread.start()

    # ---------- Waypoints & Commands giữ nguyên ----------
    # def update_waypoints(self, new_waypoints):
    #     self.waypoints = []
    #     for i, wp in enumerate(new_waypoints):
    #         try:
    #             parsed = {
    #                 "x": float(wp.get("x")),
    #                 "y": float(wp.get("y")),
    #                 "z": float(wp.get("z", 3.5))
    #             }
    #             self.waypoints.append(parsed)
    #             print(f"   -> WP{i+1}: x={parsed['x']:.3f}, y={parsed['y']:.3f}, z={parsed['z']:.3f}")
    #         except Exception as e:
    #             print(f"⚠️ Lỗi xử lý waypoint {i+1}: {e}")
    #     print(f"✅ Cập nhật {len(self.waypoints)} waypoint.")

    def update_waypoints(self, new_waypoints):
        self.waypoints = []
        for i, wp in enumerate(new_waypoints):
            try:
                if all(k in wp for k in ("lat", "lon")):
                    parsed = {
                        "lat": float(wp.get("lat")),
                        "lon": float(wp.get("lon")),
                        "alt": float(wp.get("alt", 0.0)),
                    }
                    self.waypoints.append(parsed)
                    print(f"   -> WP{i+1}: lat={parsed['lat']:.6f}, lon={parsed['lon']:.6f}, alt={parsed['alt']:.2f}")
                else:
                    raise ValueError("Thiếu lat/lon trong waypoint.")
            except Exception as e:
                print(f"⚠️ Lỗi xử lý waypoint {i+1}: {e}")
        print(f"✅ Cập nhật {len(self.waypoints)} waypoint (GPS).")

    def remove_waypoint_by_index(self, index: int):
        if not self.waypoints: return print("⚠️ Danh sách waypoint rỗng.")
        if index < 1 or index > len(self.waypoints): return print(f"❌ Không có waypoint với index = {index}")
        del self.waypoints[index - 1]; print("✅ Đã xoá.")

    # def send_waypoints_to_drone(self):
    #     if not self.ser or not self.ser.is_open: return print("⚠️ Chưa kết nối serial.")
    #     if not self.waypoints: return print("⚠️ Không có waypoint để gửi.")
    #     try:
    #         payload = json.dumps({"waypoints": self.waypoints})
    #         self.ser.write((payload + "\n").encode('utf-8')); self.ser.flush()
    #         print(f"📤 Đã gửi {len(self.waypoints)} waypoint tới drone")
    #     except Exception as e:
    #         print(f"❌ Lỗi gửi waypoint: {e}")
    def send_waypoints_to_drone(self):
        if not self.ser or not self.ser.is_open:
            return print("⚠️ Chưa kết nối serial.")
        if not self.waypoints:
            return print("⚠️ Không có waypoint để gửi.")
        try:
            payload = json.dumps({
                "coord": "gps",
                "waypoints": self.waypoints   # [{lat,lon,alt}, ...]
            })
            self.ser.write((payload + "\n").encode('utf-8'))
            self.ser.flush()
            print(f"📤 Đã gửi {len(self.waypoints)} waypoint (GPS) tới drone")
        except Exception as e:
            print(f"❌ Lỗi gửi waypoint: {e}")

    def offboard_req(self):
        if self.ser and self.ser.is_open:
            self._send_with_retry(b'{"cmd":"offboard"}\n', "OFFBOARD", tries=0, interval=30)
        else:
            print("⚠️ Serial chưa mở.")

    def land_req(self):
        if self.ser and self.ser.is_open:
            self._send_with_retry(b'{"cmd":"land"}\n', "LAND", tries=0, interval=30)
        else:
            print("⚠️ Serial chưa mở.")

    def _send_with_retry(self, payload_bytes: bytes, expect_mode: str,
                     tries: int = 2, interval: float = 1.0):
        def worker():
            for i in range(tries + 1):
                if not self.ser or not self.ser.is_open:
                    break
                try:
                    self.ser.write(payload_bytes)
                    self.ser.flush()
                    print(f"[INFO] Sent {expect_mode} (attempt {i+1}/{tries+1})")
                except Exception as e:
                    print(f"❌ Send error: {e}")
                    time.sleep(interval)
                    continue
                t0 = time.monotonic()
                while time.monotonic() - t0 < interval:
                    if self._last_ack_mode == expect_mode and self._last_ack_at >= t0:
                        return  # đã có ACK cho lần gửi này
                    time.sleep(0.05)

            # hết tries mà vẫn chưa có phản hồi -> báo FAIL để UI nhả nút
            if self.gui_bridge and hasattr(self.gui_bridge, "mode_push"):
                try:
                    self.gui_bridge.mode_push(False, expect_mode, "No ACK (timeout)")
                except Exception as e:
                    print(f"⚠️ bridge.mode_push error: {e}")

        threading.Thread(target=worker, daemon=True).start()


def main():
    controller = GroundController(port='/dev/ttyUSB1', baudrate=9600)
    controller.start()
    controller.read_position_from_drone()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("⛔ Dừng bằng Ctrl+C")
        controller.stop()

if __name__ == '__main__':
    main()
