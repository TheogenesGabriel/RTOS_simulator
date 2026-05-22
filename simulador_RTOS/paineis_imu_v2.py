"""
paineis_imu_v2.py
=================
Substitua as classes HorizonteArtificial e GraficoIMU3D do rocket_visualizer.py
por estas versões, que reproduzem fielmente o estilo da imagem de referência:

  • Fundo do painel: #1a1f2e  |  borda: #2e3650  |  cantos: 12px
  • Título: letras espaçadas, cor #8ca0c8  |  LED verde sólido no canto
  • Horizonte: círculo grande com clip circular real (pygame.SRCALPHA + máscara)
      – Céu: azul escuro #1e4a80 → #2266aa
      – Terra: marrom escuro #4a2e08 → #2e1a04
      – Linha do horizonte: branca fina
      – Pitch lines simples, texto à direita
      – Referência de atitude: linha dourada + círculo central
      – Triângulo de roll no aro superior
  • Dados PITCH / ROLL / YAW: label espaçado (cinza) + valor colorido grande
      – PITCH: #5ecfff  |  ROLL: #3dffa0  |  YAW: #ffc940
  • IMU 3D:
      – Sem foguete → apenas eixos isométricos limpos + vetor de aceleração
      – X: #ff6b78  |  Y: #3dffa0  |  Z: #a89ec9 (lilás)
      – Vetor aceleração: ponto laranja #ffaa40
      – Grade ultra-suave (quase invisível)
      – Seção ACC com label duplo "ACC\nX" e barra estreita + quadrado indicador
      – "|A| MAGNITUDE" com valor amarelo destacado
      – PITCH / ROLL / YAW abaixo com mesmas cores do horizonte
"""

import math, time
import pygame


# ─────────────────────────────────────────────────────────────
# PALETA DE CORES
# ─────────────────────────────────────────────────────────────
_C = {
    "panel_bg":    (26,  31,  46, 240),
    "panel_bdr":   (46,  54,  80, 255),
    "title":       (140, 160, 200, 255),
    "led_green":   (34,  216, 128, 255),
    "sep":         (42,  48,  72, 255),

    "sky_top":     ( 20,  55, 100, 255),
    "sky_bot":     ( 30,  90, 160, 255),
    "gnd_top":     ( 80,  50,  12, 255),
    "gnd_bot":     ( 45,  28,   4, 255),
    "horizon_ln":  (255, 255, 255, 200),
    "pitch_ln":    (255, 255, 255, 110),
    "pitch_txt":   (220, 220, 220, 160),
    "ref_gold":    (255, 200,  50, 255),
    "aro_bdr":     ( 38,  52,  90, 255),
    "aro_bdr2":    ( 80, 130, 200, 180),

    "lbl_gray":    (100, 120, 160, 255),
    "val_pitch":   ( 94, 207, 255, 255),   # ciano
    "val_roll":    ( 61, 255, 160, 255),   # verde
    "val_yaw":     (255, 201,  64, 255),   # âmbar

    "axis_x":      (255, 107, 120, 255),   # vermelho-coral
    "axis_y":      ( 61, 255, 160, 255),   # verde
    "axis_z":      (168, 158, 201, 255),   # lilás
    "accel_vec":   (255, 170,  64, 255),   # laranja
    "grid":        ( 38,  50,  80,  80),

    "bar_bg":      ( 28,  38,  58, 255),
    "bar_x":       (255, 107, 120, 200),
    "bar_y":       ( 61, 255, 160, 200),
    "bar_z":       ( 94, 207, 255, 200),
    "acc_lbl":     ( 90, 110, 150, 255),
    "acc_val_x":   (255, 107, 120, 255),
    "acc_val_y":   ( 61, 255, 160, 255),
    "acc_val_z":   ( 94, 207, 255, 255),
    "mag_val":     (255, 201,  64, 255),
}


def _rr(surf, cor, rect, r):
    """Desenha retângulo com cantos arredondados."""
    pygame.draw.rect(surf, cor, rect, border_radius=r)


def _circulo_clip(raio):
    """Retorna uma Surface com máscara circular branca para uso com BLEND_RGBA_MULT."""
    d = raio * 2
    m = pygame.Surface((d, d), pygame.SRCALPHA)
    m.fill((0, 0, 0, 0))
    pygame.draw.circle(m, (255, 255, 255, 255), (raio, raio), raio)
    return m


# ═════════════════════════════════════════════════════════════
# HORIZONTE ARTIFICIAL  (redesign)
# ═════════════════════════════════════════════════════════════

class HorizonteArtificial:
    RAIO_BORDA = 12
    RAIO_HRZ   = 96        # raio do círculo do horizonte
    PAD        = 16
    LINHA_H    = 24

    def __init__(self, display, raio=96, margem=14, offset_topo=0):
        self.raio        = raio
        self.RAIO_HRZ    = raio
        self.margem      = margem
        self.display     = display
        self._offset_topo = offset_topo

        D   = raio * 2 + 4
        PAD = self.PAD
        LH  = self.LINHA_H
        pw  = 280
        ph  = PAD + 20 + 8 + D + 14 + LH * 3 + PAD + 4

        self._pw, self._ph = pw, ph
        self._D            = D
        W, _H              = display
        self._px           = W - pw - margem
        self._py           = offset_topo

        self._surf      = pygame.Surface((pw, ph), pygame.SRCALPHA)
        self._ahrs_surf = pygame.Surface((D, D),   pygame.SRCALPHA)
        self._mask      = _circulo_clip(raio)

        try:
            self._f_title = pygame.font.SysFont("consolas", 16)
        except Exception:
            self._f_title = pygame.font.Font(None, 14)

        try:
            self._f_label = pygame.font.SysFont("consolas", 16)
        except Exception:
            self._f_label = pygame.font.Font(None, 16)

        try:
            self._f_val = pygame.font.SysFont("consolas", 18)
        except Exception:
            self._f_val = pygame.font.Font(None, 24)

        try:
            self._f_tiny = pygame.font.SysFont("consolas", 16)
        except Exception:
            self._f_tiny = pygame.font.Font(None, 10)

        # compatibilidade com código legado
        self._font_titulo  = self._f_title
        self._font_label   = self._f_label
        self._font_valor   = self._f_val
        self._font_tiny    = self._f_tiny

        # cores públicas (compatibilidade)
        self.COR_VALOR_PITCH = _C["val_pitch"][:3]
        self.COR_VALOR_ROLL  = _C["val_roll"][:3]
        self.COR_VALOR_YAW   = _C["val_yaw"][:3]

    def bottom_y(self):
        return self._py + self._ph

    # ── horizonte circular ────────────────────────────────────
    def _desenhar_ahrs(self, pitch, roll):
        D  = self._D
        R  = self.RAIO_HRZ
        s  = self._ahrs_surf
        s.fill((0, 0, 0, 0))

        pitch_px = int(pitch * (R / 32.0))
        cx, cy   = D // 2, D // 2

        # ── camada de fundo (vai ser clipada) ─────────────────
        layer = pygame.Surface((D * 3, D * 3), pygame.SRCALPHA)

        # céu
        for row in range(D * 3 // 2 - pitch_px + 1):
            t = row / max(1, D * 3 // 2 - pitch_px)
            r = int(_C["sky_top"][0] * (1 - t) + _C["sky_bot"][0] * t)
            g = int(_C["sky_top"][1] * (1 - t) + _C["sky_bot"][1] * t)
            b = int(_C["sky_top"][2] * (1 - t) + _C["sky_bot"][2] * t)
            pygame.draw.line(layer, (r, g, b, 255), (0, row), (D * 3, row))

        # terra
        gnd_h = D * 3 - (D * 3 // 2 - pitch_px)
        for row in range(gnd_h):
            t = row / max(1, gnd_h)
            r = int(_C["gnd_top"][0] * (1 - t) + _C["gnd_bot"][0] * t)
            g = int(_C["gnd_top"][1] * (1 - t) + _C["gnd_bot"][1] * t)
            b = int(_C["gnd_top"][2] * (1 - t) + _C["gnd_bot"][2] * t)
            pygame.draw.line(layer, (r, g, b, 255),
                             (0, D * 3 // 2 - pitch_px + row),
                             (D * 3, D * 3 // 2 - pitch_px + row))

        # linha do horizonte
        pygame.draw.line(layer, _C["horizon_ln"],
                         (0,     D * 3 // 2 - pitch_px),
                         (D * 3, D * 3 // 2 - pitch_px), 1)

        # pitch lines
        for p in range(-25, 30, 5):
            if p == 0:
                continue
            y_off = D * 3 // 2 - pitch_px - int(p * (R / 32.0))
            lw    = int(R * 0.28) if p % 10 == 0 else int(R * 0.14)
            pygame.draw.line(layer, _C["pitch_ln"],
                             (D * 3 // 2 - lw, y_off),
                             (D * 3 // 2 + lw, y_off), 1)
            if p % 10 == 0:
                sign = "+" if p > 0 else ""
                lbl  = self._f_tiny.render(f"{sign}{p}", True, _C["pitch_txt"][:3])
                lbl.set_alpha(_C["pitch_txt"][3])
                layer.blit(lbl, (D * 3 // 2 + lw + 4, y_off - lbl.get_height() // 2))

        # rota + clip circular
        rotated = pygame.transform.rotate(layer, roll)
        rw, rh  = rotated.get_size()
        bg      = pygame.Surface((D, D), pygame.SRCALPHA)
        bg.blit(rotated, (D // 2 - rw // 2, D // 2 - rh // 2))
        bg.blit(self._mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        s.blit(bg, (0, 0))

        # aro externo
        pygame.draw.circle(s, _C["aro_bdr"],  (cx, cy), R, 4)
        pygame.draw.circle(s, _C["aro_bdr2"], (cx, cy), R, 1)

        # marcações do aro (ticks de roll)
        for ang in range(-60, 61, 10):
            rad   = math.radians(ang - 90)
            tick  = 10 if ang % 30 == 0 else 5
            x1    = cx + math.cos(rad) * R
            y1    = cy + math.sin(rad) * R
            x2    = cx + math.cos(rad) * (R - tick)
            y2    = cy + math.sin(rad) * (R - tick)
            pygame.draw.line(s, (190, 190, 190, 140),
                             (int(x1), int(y1)), (int(x2), int(y2)), 1)

        # triângulo de roll (aponta para onde a aeronave inclinou)
        ang_tri = math.radians(-roll - 90)
        tx = cx + math.cos(ang_tri) * (R - 13)
        ty = cy + math.sin(ang_tri) * (R - 13)
        tri = [
            (tx + math.cos(ang_tri) * 9,      ty + math.sin(ang_tri) * 9),
            (tx + math.cos(ang_tri + 2.3) * 6, ty + math.sin(ang_tri + 2.3) * 6),
            (tx + math.cos(ang_tri - 2.3) * 6, ty + math.sin(ang_tri - 2.3) * 6),
        ]
        pygame.draw.polygon(s, _C["ref_gold"], tri)

        # referência central de atitude (linha + círculo)
        hw1 = int(R * 0.42)
        hw2 = int(R * 0.14)
        pygame.draw.line(s, _C["ref_gold"], (cx - hw1, cy), (cx - hw2, cy), 3)
        pygame.draw.line(s, _C["ref_gold"], (cx + hw2, cy), (cx + hw1, cy), 3)
        pygame.draw.circle(s, (0, 0, 0, 0),    (cx, cy), 6)   # apaga centro
        pygame.draw.circle(s, _C["ref_gold"],   (cx, cy), 5, 2)

    # ── painel completo ───────────────────────────────────────
    def desenhar(self, surface, pitch, roll, yaw=0.0, usando_sensor=False):
        PAD = self.PAD
        D   = self._D
        pw  = self._pw
        ph  = self._ph
        LH  = self.LINHA_H
        px  = self._px
        py  = self._py
        s   = self._surf
        s.fill((0, 0, 0, 0))

        # fundo + borda do painel
        _rr(s, _C["panel_bg"],  (0, 0, pw, ph), self.RAIO_BORDA)
        _rr(s, _C["panel_bdr"], (0, 0, pw, ph), self.RAIO_BORDA)
        _rr(s, _C["panel_bg"],  (1, 1, pw - 2, ph - 2), self.RAIO_BORDA)

        # título
        titulo = self._f_title.render("ARTIFICIAL  HORIZON", True, _C["title"][:3])
        titulo.set_alpha(_C["title"][3])
        s.blit(titulo, (PAD, PAD + 2))

        # LED
        led_cor = (34, 216, 128) if usando_sensor else (34, 216, 128)
        lx = pw - PAD - 8
        ly = PAD + 8
        pygame.draw.circle(s, (0, 0, 0, 180), (lx, ly), 7)
        pygame.draw.circle(s, led_cor, (lx, ly), 5)

        sep_y = PAD + 22
        pygame.draw.line(s, _C["sep"], (PAD, sep_y), (pw - PAD, sep_y), 1)

        # horizonte
        self._desenhar_ahrs(pitch, roll)
        ahrs_x = (pw - D) // 2
        ahrs_y = sep_y + 8
        s.blit(self._ahrs_surf, (ahrs_x, ahrs_y))

        # separador abaixo do horizonte
        dados_y = ahrs_y + D + 12
        pygame.draw.line(s, _C["sep"], (PAD, dados_y - 4), (pw - PAD, dados_y - 4), 1)

        # PITCH / ROLL / YAW
        dados = [
            ("PITCH", f"{pitch:+.1f}°", _C["val_pitch"]),
            ("ROLL",  f"{roll:+.1f}°",  _C["val_roll"]),
            ("YAW",   f"{yaw:+.1f}°",   _C["val_yaw"]),
        ]
        for i, (lbl_txt, val_txt, cor_val) in enumerate(dados):
            y_l   = dados_y + i * LH
            lbl_s = self._f_label.render(lbl_txt, True, _C["lbl_gray"][:3])
            lbl_s.set_alpha(_C["lbl_gray"][3])
            val_s = self._f_val.render(val_txt, True, cor_val[:3])
            val_s.set_alpha(cor_val[3])
            s.blit(lbl_s, (PAD, y_l + (LH - lbl_s.get_height()) // 2))
            s.blit(val_s, (pw - PAD - val_s.get_width(),
                           y_l + (LH - val_s.get_height()) // 2))

        surface.blit(s, (px, py))


# ═════════════════════════════════════════════════════════════
# GRÁFICO IMU 3D  (redesign)
# ═════════════════════════════════════════════════════════════

class GraficoIMU3D:
    RAIO_BORDA  = 12
    PAD         = 16
    LINHA_H     = 24
    AXLEN       = 66          # comprimento dos eixos em pixels
    _COS30      = math.cos(math.radians(30))
    _SIN30      = math.sin(math.radians(30))

    def __init__(self, display, margem=14, offset_topo=0):
        self.display    = display
        self.margem     = margem
        W, _H           = display
        PAD             = self.PAD
        LH              = self.LINHA_H

        ISO_H = self.AXLEN * 2 + 30   # altura da área isométrica

        pw  = 280
        ph  = (PAD + 22 + 4          # cabeçalho
               + ISO_H               # área 3D
               + 8                   # gap
               + 18                  # título "ACCELEROMETER"
               + LH * 3              # barras ACC X/Y/Z
               + 8                   # gap
               + LH                  # magnitude
               + 4                   # sep
               + LH * 3              # pitch/roll/yaw
               + PAD)

        self._pw, self._ph = pw, ph
        self._px = W - pw - margem
        self._py = offset_topo

        # centro isométrico dentro do painel
        self._iso_y0  = PAD + 24 + 4
        self._iso_h   = ISO_H
        self._cx_iso  = pw // 2
        self._cy_iso  = self._iso_y0 + ISO_H // 2 + 4

        self._surf = pygame.Surface((pw, ph), pygame.SRCALPHA)

        # trail (legado)
        self._trail_corpo: list = []
        self._gravando_corpo    = False

        try:
            self._f_title = pygame.font.SysFont("consolas", 16)
        except Exception:
            self._f_title = pygame.font.Font(None, 14)
        try:
            self._f_label = pygame.font.SysFont("consolas", 14)
        except Exception:
            self._f_label = pygame.font.Font(None, 14)
        try:
            self._f_val = pygame.font.SysFont("consolas", 18)
        except Exception:
            self._f_val = pygame.font.Font(None, 24)
        try:
            self._f_tiny = pygame.font.SysFont("consolas", 10) ##
        except Exception:
            self._f_tiny = pygame.font.Font(None, 12)
        try:
            self._f_acc_lbl = pygame.font.SysFont("consolas", 14)#<--
        except Exception:
            self._f_acc_lbl = pygame.font.Font(None, 13)
        try:
            self._f_acc_val = pygame.font.SysFont("consolas", 14)
        except Exception:
            self._f_acc_val = pygame.font.Font(None, 16)

        # cores públicas (compatibilidade)
        self.COR_EIXO_X      = _C["axis_x"][:3]
        self.COR_EIXO_Y      = _C["axis_y"][:3]
        self.COR_EIXO_Z      = _C["axis_z"][:3]
        self.COR_VETOR_ACCEL = _C["accel_vec"][:3]
        self.COR_TRAIL_CORPO = (80, 180, 255, 200)
        self.COR_SENSOR_OK   = (0,  220,  80, 255)
        self.COR_SENSOR_AUTO = (255, 160,   0, 255)
        self.TRAIL_MAX       = 30
        self.COMPRIMENTO_EIXO = self.AXLEN

    def bottom_y(self):
        return self._py + self._ph

    def set_gravando(self, ativo: bool):
        if ativo and not self._gravando_corpo:
            self._trail_corpo.clear()
        self._gravando_corpo = ativo

    # ── projeção isométrica ───────────────────────────────────
    def _iso(self, x3, y3, z3):
        px = (x3 - y3) * self._COS30
        py = (x3 + y3) * self._SIN30 - z3
        return (self._cx_iso + px * self.AXLEN,
                self._cy_iso + py * self.AXLEN)

    # ── seta de eixo ─────────────────────────────────────────
    def _eixo(self, s, dest, cor, label):
        O   = self._iso(0, 0, 0)
        tip = self._iso(*dest)
        pygame.draw.line(s, cor, (int(O[0]), int(O[1])), (int(tip[0]), int(tip[1])), 2)
        dx   = tip[0] - O[0]
        dy   = tip[1] - O[1]
        mag  = math.sqrt(dx * dx + dy * dy) or 1
        nx, ny = dx / mag, dy / mag
        ts = 8
        pygame.draw.polygon(s, cor, [
            (int(tip[0]), int(tip[1])),
            (int(tip[0] - nx * ts - ny * 5), int(tip[1] - ny * ts + nx * 5)),
            (int(tip[0] - nx * ts + ny * 5), int(tip[1] - ny * ts - nx * 5)),
        ])
        lbl = self._f_tiny.render(label, True, cor)
        s.blit(lbl, (int(tip[0]) + 4, int(tip[1]) - lbl.get_height() // 2 - 2))

    # ── grade isométrica suave ────────────────────────────────
    def _grade(self, s):
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                p1 = self._iso(i, -1, j); p2 = self._iso(i, 1, j)
                pygame.draw.line(s, _C["grid"],
                                 (int(p1[0]), int(p1[1])), (int(p2[0]), int(p2[1])), 1)
                p3 = self._iso(-1, i, j); p4 = self._iso(1, i, j)
                pygame.draw.line(s, _C["grid"],
                                 (int(p3[0]), int(p3[1])), (int(p4[0]), int(p4[1])), 1)

    # ── barra de aceleração ───────────────────────────────────
    def _barra(self, s, y0, label_top, label_bot, valor, cor_bar, cor_val, bx, bw, bh=8):
        PAD = self.PAD

        # label duplo (ex: "ACC" em cima, "X" embaixo)
        l1 = self._f_acc_lbl.render(label_top, True, _C["acc_lbl"][:3])
        l2 = self._f_acc_lbl.render(label_bot, True, _C["acc_lbl"][:3])
        l1.set_alpha(_C["acc_lbl"][3])
        l2.set_alpha(_C["acc_lbl"][3])
        label_w = max(l1.get_width(), l2.get_width()) + 6

        # posição da barra
        bar_x = PAD + label_w + 4
        bar_w = bw - label_w - 8

        # fundo da barra
        bar_rect = pygame.Rect(bar_x, y0 + (self.LINHA_H - bh) // 2, bar_w, bh)
        pygame.draw.rect(s, _C["bar_bg"], bar_rect, border_radius=3)

        # marcação central
        cx_ = bar_x + bar_w // 2
        pygame.draw.line(s, (60, 75, 100, 160),
                         (cx_, bar_rect.top), (cx_, bar_rect.bottom), 1)

        # fill da barra (de centro para o lado)
        frac = max(-1.0, min(1.0, valor / 2.0))   # normaliza em [-1,1] com escala 2g
        if abs(frac) > 0.005:
            fill_w = int(abs(frac) * bar_w // 2)
            if frac > 0:
                fill_rect = pygame.Rect(cx_, bar_rect.top, fill_w, bh)
            else:
                fill_rect = pygame.Rect(cx_ - fill_w, bar_rect.top, fill_w, bh)
            pygame.draw.rect(s, cor_bar, fill_rect, border_radius=2)

        # quadradinho indicador
        sq = 6
        sq_x = cx_ + int(frac * bar_w // 2) - sq // 2
        sq_y = bar_rect.centery - sq // 2
        pygame.draw.rect(s, cor_bar[:3] + (230,),
                         pygame.Rect(sq_x, sq_y, sq, sq), border_radius=1)

        # valor textual
        val_txt = f"{valor:+.2f}g"
        val_s   = self._f_acc_val.render(val_txt, True, cor_val[:3])
        val_s.set_alpha(cor_val[3])
        val_x   = bx + bw - val_s.get_width()
        s.blit(val_s, (val_x, y0 + (self.LINHA_H - val_s.get_height()) // 2))

        # labels à esquerda
        s.blit(l1, (PAD, y0 + 1))
        s.blit(l2, (PAD, y0 + l1.get_height() + 1))

    # ── desenho principal ─────────────────────────────────────
    def desenhar(self, surface, ax, ay, az,
                 pitch=0.0, roll=0.0, yaw=0.0, usando_sensor=False):
        PAD = self.PAD
        pw  = self._pw
        ph  = self._ph
        LH  = self.LINHA_H
        s   = self._surf
        s.fill((0, 0, 0, 0))

        # fundo + borda
        _rr(s, _C["panel_bg"],  (0, 0, pw, ph), self.RAIO_BORDA)
        _rr(s, _C["panel_bdr"], (0, 0, pw, ph), self.RAIO_BORDA)
        _rr(s, _C["panel_bg"],  (1, 1, pw - 2, ph - 2), self.RAIO_BORDA)

        # título
        titulo = self._f_title.render("ORIENTATION  &  ACCEL", True, _C["title"][:3])
        titulo.set_alpha(_C["title"][3])
        s.blit(titulo, (PAD, PAD + 2))

        # LED
        led_cor = (34, 216, 128)
        lx = pw - PAD - 8
        ly = PAD + 8
        pygame.draw.circle(s, (0, 0, 0, 180), (lx, ly), 7)
        pygame.draw.circle(s, led_cor, (lx, ly), 5)

        if self._gravando_corpo:
            rec_s = self._f_tiny.render("● REC", True, (255, 60, 60))
            s.blit(rec_s, (pw - PAD - 52, PAD + 3))

        sep_y = PAD + 22
        pygame.draw.line(s, _C["sep"], (PAD, sep_y), (pw - PAD, sep_y), 1)

        # ── área isométrica ───────────────────────────────────
        self._grade(s)

        O = self._iso(0, 0, 0)

        # eixos
        self._eixo(s, (1.2, 0, 0), _C["axis_x"][:3], "X")
        self._eixo(s, (0, 1.2, 0), _C["axis_y"][:3], "Y")
        self._eixo(s, (0, 0, 1.2), _C["axis_z"][:3], "Z")

        # vetor de aceleração (ponto laranja + linha)
        sc = 0.9
        vx_  = max(-sc, min(sc, ax))
        vy_  = max(-sc, min(sc, ay))
        vz_  = max(-sc, min(sc, az))
        tip  = self._iso(vx_, vy_, vz_)
        pygame.draw.line(s, (*_C["accel_vec"][:3], 160),
                         (int(O[0]), int(O[1])), (int(tip[0]), int(tip[1])), 2)
        pygame.draw.circle(s, _C["accel_vec"][:3],
                           (int(tip[0]), int(tip[1])), 5)

        # ── seção ACC ─────────────────────────────────────────
        iso_bot   = self._iso_y0 + self._iso_h + 6
        acc_title = self._f_acc_lbl.render("ACCELEROMETER", True, _C["lbl_gray"][:3])
        acc_title.set_alpha(_C["lbl_gray"][3])
        s.blit(acc_title, (PAD, iso_bot + 2))

        bar_y0 = iso_bot + 18
        bar_bw = pw - PAD * 2   # largura total disponível para o bloco de barra

        acc_items = [
            ("ACC", "X", ax, _C["bar_x"], _C["acc_val_x"]),
            ("ACC", "Y", ay, _C["bar_y"], _C["acc_val_y"]),
            ("ACC", "Z", az, _C["bar_z"], _C["acc_val_z"]),
        ]
        for i, (lt, lb, val, cor_b, cor_v) in enumerate(acc_items):
            self._barra(s, bar_y0 + i * LH, lt, lb, val,
                        cor_b, cor_v, PAD, bar_bw)

        # separador + magnitude
        mag_y = bar_y0 + LH * 3 + 4
        pygame.draw.line(s, _C["sep"], (PAD, mag_y), (pw - PAD, mag_y), 1)

        mag_val = math.sqrt(ax * ax + ay * ay + az * az)
        ml = self._f_acc_lbl.render("|A|  MAGNITUDE", True, _C["lbl_gray"][:3])
        ml.set_alpha(_C["lbl_gray"][3])
        mv = self._f_val.render(f"{mag_val:.2f}g", True, _C["mag_val"][:3])
        mv.set_alpha(_C["mag_val"][3])
        s.blit(ml, (PAD, mag_y + 4))
        s.blit(mv, (pw - PAD - mv.get_width(), mag_y + 2))

        # ── PITCH / ROLL / YAW ────────────────────────────────
        ang_y = mag_y + LH + 2
        pygame.draw.line(s, _C["sep"], (PAD, ang_y - 2), (pw - PAD, ang_y - 2), 1)

        ang_items = [
            ("PITCH", f"{pitch:+.1f}°", _C["val_pitch"]),
            ("ROLL",  f"{roll:+.1f}°",  _C["val_roll"]),
            ("YAW",   f"{yaw:+.1f}°",   _C["val_yaw"]),
        ]
        for i, (lbl_txt, val_txt, cor_v) in enumerate(ang_items):
            y_l   = ang_y + i * LH
            lbl_s = self._f_label.render(lbl_txt, True, _C["lbl_gray"][:3])
            lbl_s.set_alpha(_C["lbl_gray"][3])
            val_s = self._f_val.render(val_txt, True, cor_v[:3])
            val_s.set_alpha(cor_v[3])
            s.blit(lbl_s, (PAD, y_l + (LH - lbl_s.get_height()) // 2))
            s.blit(val_s, (pw - PAD - val_s.get_width(),
                           y_l + (LH - val_s.get_height()) // 2))

        surface.blit(s, (self._px, self._py))


# ─────────────────────────────────────────────────────────────
# INSTRUÇÕES DE USO
# ─────────────────────────────────────────────────────────────
"""
No rocket_visualizer.py, substitua as classes HorizonteArtificial e GraficoIMU3D
pelo conteúdo acima. Você também pode simplesmente importar:

    from paineis_imu_v2 import HorizonteArtificial, GraficoIMU3D

e adicionar no topo do rocket_visualizer.py (após os imports existentes):

    try:
        from paineis_imu_v2 import HorizonteArtificial, GraficoIMU3D
    except ImportError:
        pass  # usa as classes originais se o arquivo não estiver presente

A assinatura pública de ambas as classes é idêntica à versão original:
  - HorizonteArtificial(display, raio, margem, offset_topo)
      .desenhar(surface, pitch, roll, yaw, usando_sensor)
      .bottom_y()
  - GraficoIMU3D(display, margem, offset_topo)
      .desenhar(surface, ax, ay, az, pitch, roll, yaw, usando_sensor)
      .bottom_y()
      .set_gravando(bool)
"""