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
        self._temp = 0.0          # °C — atualizado a ~1 Hz pela vTaskTemp
        self._estado = "IDLE"

        self._cb_estado = None    # callback(estado_str)

        threading.Thread(target=self._loop, daemon=True).start()

    # ── callbacks ─────────────────────────────────────────────────────────────

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
    def temperatura(self):
        """Temperatura interna do MPU6050 em °C (atualizada a ~1 Hz)."""
        with self._lock: return self._temp

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
            print(f"[Serial v5] {self.porta} @ {self.baud}")
        except Exception as e:
            self.erro = str(e)
            print(f"[Serial v5] Erro: {e}")
            return

        nums_re = r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?"

        while True:
            try:
                linha = ser.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                continue

            if not linha:
                continue

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

            elif linha.startswith("Tp:"):
                # Enviado a ~1 Hz pela vTaskTemp — único valor float
                n = re.findall(nums_re, linha)
                if len(n) >= 1:
                    with self._lock:
                        self._temp = float(n[0])
                    print(f"[Serial v5] Tp: {self._temp:.2f} °C")

            elif linha.startswith("St:"):
                partes = linha.split(None, 1)
                if len(partes) == 2:
                    est = partes[1].strip()
                    with self._lock:
                        self._estado = est
                    print(f"[Serial v5] St: {est}")
                    if self._cb_estado:
                        try:
                            self._cb_estado(est)
                        except Exception as exc:
                            print(f"[Serial v5] callback erro: {exc}")