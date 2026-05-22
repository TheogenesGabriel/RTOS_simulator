import threading
import re


class LeitorSerial:

    def __init__(self, porta="COM6", baud=115200, timeout=0.089):
        self.porta = porta
        self.baud = baud
        self._to = timeout
        self.conectado = False
        self.erro = ""
        self._lock = threading.Lock()

        self._ax = self._ay = self._az = 0.0
        self._gx = self._gy = self._gz = 0.0
        self._pitch = self._roll = self._yaw = 0.0
        self._estado = "IDLE"

        self._cb_estado = None  # callback(estado_str)

        threading.Thread(target=self._loop, daemon=True).start()

    # ── callback ──────────────────────────────────────────────────────────────

    def set_callback_estado(self, cb):
        self._cb_estado = cb

    # ── propriedades thread-safe ──────────────────────────────────────────────

    @property
    def ac_x(self):
        with self._lock: return self._ax

    @property
    def ac_y(self):
        with self._lock: return self._ay

    @property
    def ac_z(self):
        with self._lock: return self._az

    @property
    def gy_x(self):
        with self._lock: return self._gx

    @property
    def gy_y(self):
        with self._lock: return self._gy

    @property
    def gy_z(self):
        with self._lock: return self._gz

    @property
    def pitch(self):
        with self._lock: return self._pitch

    @property
    def roll(self):
        with self._lock: return self._roll

    @property
    def yaw(self):
        with self._lock: return self._yaw

    @property
    def estado(self):
        with self._lock: return self._estado

    # ── compat com código antigo ──────────────────────────────────────────────

    def pitch_graus(self):
        return self.pitch

    def roll_graus(self):
        return self.roll

    def gyro_dps(self):
        with self._lock: return (self._gx, self._gy, self._gz)

    # ── loop serial ───────────────────────────────────────────────────────────

    def _loop(self):
        try:
            import serial
        except ImportError:
            self.erro = "pyserial não instalado"
            return

        try:
            ser = serial.Serial(self.porta, self.baud, timeout=self._to)
            self.conectado = True
            print(f"[Serial v4] {self.porta} @ {self.baud}")
        except Exception as e:
            self.erro = str(e)
            print(f"[Serial v4] Erro: {e}")
            return

        while True:
            try:
                linha = ser.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                continue

            if not linha:
                continue

            nums_re = r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?"

            if linha.startswith("Ax:"):
                n = re.findall(nums_re, linha)
                if len(n) >= 3:
                    with self._lock:
                        self._ax, self._ay, self._az = float(n[0]), float(n[1]), float(n[2])

            elif linha.startswith("Gx:"):
                n = re.findall(nums_re, linha)
                if len(n) >= 3:
                    with self._lock:
                        self._gx, self._gy, self._gz = float(n[0]), float(n[1]), float(n[2])

            elif linha.startswith("An:"):
                n = re.findall(nums_re, linha)
                if len(n) >= 3:
                    with self._lock:
                        self._pitch, self._roll, self._yaw = float(n[0]), float(n[1]), float(n[2])

            elif linha.startswith("St:"):
                partes = linha.split(None, 1)
                if len(partes) == 2:
                    est = partes[1].strip()
                    with self._lock:
                        self._estado = est
                    print(f"[Serial v4] St: {est}")
                    if self._cb_estado:
                        try:
                            self._cb_estado(est)
                        except Exception as exc:
                            print(f"[Serial v4] callback erro: {exc}")

