"""
rocket_visualizer.py  —  v11.1
Adicionado ESTADO_INTRO: ao entrar no modo Missão, um painel de boas-vindas
apresenta as 3 fases da missão ao usuário antes de iniciar.
"""

import os, sys, math, time, ctypes, random, threading
import pygame
from pygame.locals import *
from OpenGL.GL import *
from OpenGL.GLU import *
import numpy as np
import paineis_imu_v2
from paineis_imu_v2 import HorizonteArtificial, GraficoIMU3D
from leitor_serial_v3 import LeitorSerial


# ============================================================
# CONFIG (Coloque a porta Serial que estiver conectado no mpu)
# ============================================================
SERIAL_PORT    = "COM6"
SERIAL_BAUD    = 115200
SERIAL_TIMEOUT = 0.07
FPS_ALVO       = 60
FUNDO_AMP_X    = 0.018
FUNDO_AMP_Y    = 0.010

MODOS_SIM = ["Guiagem", "Missão", "Calibração"]
SIM_DESC  = {
    "Guiagem":    "Trajetória guiada",
    "Missão":     "Sequência de missão",
    "Calibração": "Calibração do sensor",
}

FADE_DURACAO = 0.35


# ============================================================
# GERENCIADOR DE FONTES
# ============================================================

class GerenciadorFontes:
    _ARQUIVOS = {
        "orbitron":      ["Orbitron-VariableFont_wght.ttf", "Orbitron[wght].ttf", "Orbitron-Regular.ttf", "orbitron.ttf"],
        "sharetechmono": ["ShareTechMono-Regular.ttf", "ShareTechMono.ttf"],
        "rajdhani":      ["Rajdhani-Regular.ttf", "Rajdhani.ttf"],
        "rajdhani_bold": ["Rajdhani-Bold.ttf", "Rajdhani-SemiBold.ttf"],
    }
    _FALLBACK_DISPLAY = ["impact", "arial", "monospace"]
    _FALLBACK_DADOS   = ["consolas", "couriernew", "courier", "monospace"]
    _FALLBACK_LABEL   = ["calibri", "segoeui", "arial", "sans"]
    _FALLBACK_MONO    = ["consolas", "couriernew", "monospace"]

    def __init__(self, base_dir):
        self._base  = base_dir
        self._cache = {}
        self._ttf   = {}
        for familia, candidatos in self._ARQUIVOS.items():
            self._ttf[familia] = self._achar(candidatos)
        for familia, caminho in self._ttf.items():
            status = f"✓  {os.path.basename(caminho)}" if caminho else "–  fallback sistema"
            print(f"[Fontes] {familia}: {status}")

    def _achar(self, candidatos):
        pastas = [self._base]
        try:
            for entry in os.scandir(self._base):
                if entry.is_dir():
                    pastas.append(entry.path)
        except Exception:
            pass
        for pasta in pastas:
            for nome in candidatos:
                caminho = os.path.join(pasta, nome)
                if os.path.isfile(caminho):
                    return caminho
        return None

    def _sysfont(self, nomes, tamanho, bold):
        for nome in nomes:
            try:   return pygame.font.SysFont(nome, tamanho, bold=bold)
            except: continue
        return pygame.font.Font(None, tamanho)

    def _get(self, familia, fallbacks, tamanho, bold=False):
        chave = (familia, tamanho, bold)
        if chave in self._cache:
            return self._cache[chave]
        caminho = self._ttf.get(familia)
        if caminho:
            try:
                f = pygame.font.Font(caminho, tamanho)
                self._cache[chave] = f
                return f
            except: pass
        f = self._sysfont(fallbacks, tamanho, bold)
        self._cache[chave] = f
        return f

    def display(self, tamanho, bold=False):
        return self._get("orbitron", self._FALLBACK_DISPLAY, tamanho, bold)

    def dados(self, tamanho):
        return self._get("sharetechmono", self._FALLBACK_DADOS, tamanho, False)

    def label(self, tamanho, bold=False):
        fam = "rajdhani_bold" if bold else "rajdhani"
        return self._get(fam, self._FALLBACK_LABEL, tamanho, bold)

    def mono(self, tamanho, bold=False):
        return self._get("sharetechmono", self._FALLBACK_MONO, tamanho, bold)


_fontes: GerenciadorFontes = None  # type: ignore

def fontes():
    return _fontes


# ============================================================
# FILTRO COMPLEMENTAR
# ============================================================

class FiltroAngulo:
    def __init__(self):
        self.pitch = self.roll = self.yaw = 0.0

    def atualizar(self, leitor, dt):
        if not leitor.conectado:
            return False
        self.pitch = leitor.pitch
        self.roll  = leitor.roll
        self.yaw   = leitor.yaw
        return True


# ============================================================
# MTL LOADER — lê map_Kd para localizar textura difusa
# ============================================================

def localizar_textura_mtl(caminho_obj, base_dir):
    mtl_nome   = None
    map_kd_rel = None

    try:
        with open(caminho_obj, 'r') as f:
            for linha in f:
                linha = linha.strip()
                if linha.lower().startswith("mtllib"):
                    partes = linha.split(None, 1)
                    if len(partes) > 1:
                        mtl_nome = partes[1].strip()
                    break
    except Exception:
        pass

    if mtl_nome is None:
        return None

    candidatos_pasta = [base_dir]
    try:
        for entry in os.scandir(base_dir):
            if entry.is_dir():
                candidatos_pasta.append(entry.path)
    except Exception:
        pass

    mtl_path = None
    for pasta in candidatos_pasta:
        p = os.path.join(pasta, mtl_nome)
        if os.path.isfile(p):
            mtl_path = p
            break

    if mtl_path is None:
        return None

    try:
        with open(mtl_path, 'r') as f:
            for linha in f:
                linha = linha.strip()
                if linha.lower().startswith("map_kd"):
                    partes = linha.split(None, 1)
                    if len(partes) > 1:
                        map_kd_rel = partes[1].strip()
                    break
    except Exception:
        pass

    if map_kd_rel is None:
        return None

    for pasta in [os.path.dirname(mtl_path), base_dir] + candidatos_pasta:
        p = os.path.join(pasta, map_kd_rel)
        if os.path.isfile(p):
            return p
        p = os.path.join(pasta, os.path.basename(map_kd_rel))
        if os.path.isfile(p):
            return p

    return None


# ============================================================
# OBJ LOADER — com suporte a UV (vt)
# ============================================================

def carregar_obj(caminho):
    pos_raw   = []
    uv_raw    = []
    norm_raw  = []
    faces_raw = []

    with open(caminho, 'r') as f:
        for linha in f:
            linha = linha.strip()
            if linha.startswith('v '):
                pos_raw.append(list(map(float, linha.split()[1:4])))
            elif linha.startswith('vt '):
                uv_raw.append(list(map(float, linha.split()[1:3])))
            elif linha.startswith('vn '):
                norm_raw.append(list(map(float, linha.split()[1:4])))
            elif linha.startswith('f '):
                face = []
                for p in linha.split()[1:]:
                    comp = p.split('/')
                    iv  = int(comp[0]) - 1
                    ivt = int(comp[1]) - 1 if len(comp) > 1 and comp[1] != '' else -1
                    ivn = int(comp[2]) - 1 if len(comp) > 2 and comp[2] != '' else -1
                    face.append((iv, ivt, ivn))
                faces_raw.append(face)

    tem_uv   = len(uv_raw)  > 0
    tem_norm = len(norm_raw) > 0

    pos_arr  = np.array(pos_raw,  dtype=np.float32) if pos_raw  else np.zeros((1, 3), np.float32)
    pos_arr -= pos_arr.mean(axis=0)
    max_n = np.max(np.linalg.norm(pos_arr, axis=1))
    if max_n > 0:
        pos_arr /= max_n

    uv_arr   = np.array(uv_raw,   dtype=np.float32) if tem_uv   else np.zeros((1, 2), np.float32)
    norm_arr = np.array(norm_raw,  dtype=np.float32) if tem_norm else np.zeros((1, 3), np.float32)

    vert_map  = {}
    verts_out = []
    uvs_out   = []
    norms_out = []
    tris_out  = []

    def get_idx(iv, ivt, ivn):
        key = (iv, ivt, ivn)
        if key in vert_map:
            return vert_map[key]
        idx = len(verts_out)
        vert_map[key] = idx
        verts_out.append(pos_arr[iv])
        uvs_out.append(uv_arr[ivt]    if (tem_uv   and ivt >= 0) else np.array([0.0, 0.0]))
        norms_out.append(norm_arr[ivn] if (tem_norm and ivn >= 0) else np.array([0.0, 0.0, 1.0]))
        return idx

    for face in faces_raw:
        idxs = [get_idx(iv, ivt, ivn) for iv, ivt, ivn in face]
        for i in range(1, len(idxs) - 1):
            tris_out.append([idxs[0], idxs[i], idxs[i + 1]])

    verts_final   = np.array(verts_out,  dtype=np.float32)
    uvs_final     = np.array(uvs_out,    dtype=np.float32)
    normais_final = np.array(norms_out,  dtype=np.float32)
    faces_final   = np.array(tris_out,   dtype=np.int32)

    if not tem_norm:
        normais_final = np.zeros_like(verts_final)
        fn = np.cross(
            verts_final[faces_final[:, 1]] - verts_final[faces_final[:, 0]],
            verts_final[faces_final[:, 2]] - verts_final[faces_final[:, 0]]
        )
        for i, (a, b, c) in enumerate(faces_final):
            normais_final[a] += fn[i]
            normais_final[b] += fn[i]
            normais_final[c] += fn[i]
        mag = np.linalg.norm(normais_final, axis=1, keepdims=True)
        normais_final /= np.where(mag == 0, 1, mag)

    print(f"[OBJ] {len(verts_final)} vértices únicos, {len(faces_final)} triângulos, UV={'sim' if tem_uv else 'não'}")
    return verts_final, normais_final, uvs_final, faces_final, tem_uv


# ============================================================
# VBO — posição(3) + normal(3) + uv(2) = 8 floats por vértice
# ============================================================

def criar_vbo(verts, normais, uvs, faces):
    idx   = faces.flatten()
    n     = len(idx)
    dados = np.empty((n, 8), dtype=np.float32)
    dados[:, 0:3] = verts[idx]
    dados[:, 3:6] = normais[idx]
    dados[:, 6:8] = uvs[idx]

    vbo = glGenBuffers(1)
    glBindBuffer(GL_ARRAY_BUFFER, vbo)
    flat = dados.flatten()
    glBufferData(GL_ARRAY_BUFFER, flat.nbytes, flat, GL_STATIC_DRAW)
    glBindBuffer(GL_ARRAY_BUFFER, 0)
    return vbo, n


# ============================================================
# TEXTURA
# ============================================================

def carregar_textura(caminho):
    surf  = pygame.transform.flip(pygame.image.load(caminho), False, True)
    dados = pygame.image.tostring(surf, "RGB")
    w, h  = surf.get_width(), surf.get_height()
    tex_id = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, tex_id)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, dados)
    glGenerateMipmap(GL_TEXTURE_2D)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
    return tex_id


# ============================================================
# FUNDO
# ============================================================

def desenhar_fundo(tex_id, ox, oy):
    glDisable(GL_LIGHTING); glDisable(GL_DEPTH_TEST); glDisable(GL_CULL_FACE)
    glMatrixMode(GL_PROJECTION); glPushMatrix(); glLoadIdentity(); glOrtho(-1, 1, -1, 1, -1, 1)
    glMatrixMode(GL_MODELVIEW);  glPushMatrix(); glLoadIdentity()
    glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, tex_id); glColor3f(1, 1, 1)
    glBegin(GL_QUADS)
    glTexCoord2f(0 + ox, 0 + oy); glVertex2f(-1, -1)
    glTexCoord2f(1 + ox, 0 + oy); glVertex2f( 1, -1)
    glTexCoord2f(1 + ox, 1 + oy); glVertex2f( 1,  1)
    glTexCoord2f(0 + ox, 1 + oy); glVertex2f(-1,  1)
    glEnd()
    glDisable(GL_TEXTURE_2D)
    glPopMatrix(); glMatrixMode(GL_PROJECTION); glPopMatrix(); glMatrixMode(GL_MODELVIEW)
    glEnable(GL_DEPTH_TEST); glEnable(GL_LIGHTING)


# ============================================================
# DESENHAR MODELO COM TEXTURA
# ============================================================

def desenhar_modelo_texturizado(vbo, n_verts, tex_modelo, tem_uv):
    STRIDE = 8 * 4  # 8 floats × 4 bytes

    glBindBuffer(GL_ARRAY_BUFFER, vbo)
    glEnableClientState(GL_VERTEX_ARRAY)
    glEnableClientState(GL_NORMAL_ARRAY)
    glVertexPointer(3, GL_FLOAT, STRIDE, ctypes.c_void_p(0))
    glNormalPointer(GL_FLOAT,   STRIDE, ctypes.c_void_p(12))

    if tem_uv and tex_modelo is not None:
        glEnableClientState(GL_TEXTURE_COORD_ARRAY)
        glTexCoordPointer(2, GL_FLOAT, STRIDE, ctypes.c_void_p(24))
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, tex_modelo)
        glTexEnvf(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        glColor4f(1.0, 1.0, 1.0, 1.0)
    else:
        glColor3f(0.75, 0.80, 0.95)

    glEnable(GL_LIGHTING)
    glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
    glDrawArrays(GL_TRIANGLES, 0, n_verts)

    if tem_uv and tex_modelo is not None:
        glDisable(GL_TEXTURE_2D)
        glDisableClientState(GL_TEXTURE_COORD_ARRAY)

    glDisableClientState(GL_VERTEX_ARRAY)
    glDisableClientState(GL_NORMAL_ARRAY)
    glBindBuffer(GL_ARRAY_BUFFER, 0)


# ============================================================
# DETRITOS
# ============================================================

class Detritos:
    def __init__(self, n=50):
        np.random.seed(42); self.n = n
        self.pos     = np.random.uniform(-1, 1, (n, 2)).astype(np.float32)
        self.prof    = np.random.uniform(0, 1, n).astype(np.float32)
        self.tom     = np.random.uniform(0.25, 0.68, n).astype(np.float32)
        self.ferroso = np.random.uniform(0.0, 0.35, n).astype(np.float32)
        self.escala  = np.random.uniform(0.003 * 1.75, 0.014 * 1.75, n).astype(np.float32)
        self.oval    = (np.random.rand(n) > 0.55).astype(bool)
        self.angulo  = np.random.uniform(0, math.pi * 2, n).astype(np.float32)
        self._ratio  = np.random.uniform(1.6, 2.8, n).astype(np.float32)
        self.nverts  = np.random.randint(5, 8, n)
        self.raios   = [(1.0 + np.random.uniform(-0.4, 0.4, self.nverts[i])).astype(np.float32) for i in range(n)]

    def atualizar(self, dt, vel_x, vel_y):
        for i in range(self.n):
            fator = (1.0 - self.prof[i]) * 0.55 + 0.06
            self.pos[i, 0] = (self.pos[i, 0] + vel_x * fator * dt + 1.0) % 2.0 - 1.0
            self.pos[i, 1] = (self.pos[i, 1] + vel_y * fator * dt + 1.0) % 2.0 - 1.0

    def desenhar(self):
        glDisable(GL_LIGHTING); glDisable(GL_DEPTH_TEST)
        glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glMatrixMode(GL_PROJECTION); glPushMatrix(); glLoadIdentity(); glOrtho(-1, 1, -1, 1, -1, 1)
        glMatrixMode(GL_MODELVIEW);  glPushMatrix(); glLoadIdentity()
        LX, LY = 0.6, 0.8; lm = math.sqrt(LX * LX + LY * LY); LX /= lm; LY /= lm
        for i in range(self.n):
            t_ = self.tom[i]; p = self.prof[i]; fe = self.ferroso[i]
            s  = self.escala[i] * (0.4 + (1.0 - p) * 0.6)
            cx, cy = self.pos[i, 0], self.pos[i, 1]
            ang = self.angulo[i]; alpha = 0.82 + (1.0 - p) * 0.16
            rb = min(t_ + fe * 0.18 * (1 - p), 1.0)
            gb = max(t_ - fe * 0.05 * (1 - p), 0.0)
            bb = max(t_ - fe * 0.10 * (1 - p), 0.0)
            rs, gs, bs = rb * 0.55, gb * 0.55, bb * 0.55
            rl = min(rb * 1.35 + 0.12, 1.0)
            gl_ = min(gb * 1.35 + 0.10, 1.0)
            bl  = min(bb * 1.35 + 0.10, 1.0)

            def _cor(vx, vy):
                diff = (vx * LX + vy * LY + 1.0) * 0.5
                if diff < 0.5:
                    f = diff * 2; return rs + (rb - rs) * f, gs + (gb - gs) * f, bs + (bb - bs) * f
                else:
                    f = (diff - 0.5) * 2; return rb + (rl - rb) * f, gb + (gl_ - gb) * f, bb + (bl - bb) * f

            if self.oval[i]:
                rx = s; ry = s * self._ratio[i]; segs = 14
                glBegin(GL_TRIANGLE_FAN)
                glColor4f((rb + rs) * 0.5, (gb + gs) * 0.5, (bb + bs) * 0.5, alpha)
                glVertex2f(cx, cy)
                for k in range(segs + 1):
                    theta = ang + 2 * math.pi * k / segs
                    vx, vy = math.cos(theta), math.sin(theta)
                    r2, g2, b2 = _cor(vx, vy)
                    glColor4f(min(r2, 1), min(g2, 1), min(b2, 1), alpha * 0.85)
                    glVertex2f(cx + rx * vx, cy + ry * vy)
                glEnd()
            else:
                nv = self.nverts[i]; raios = self.raios[i]
                glBegin(GL_TRIANGLE_FAN)
                glColor4f((rb + rs) * 0.5, (gb + gs) * 0.5, (bb + bs) * 0.5, alpha)
                glVertex2f(cx, cy)
                for k in range(nv + 1):
                    theta = ang + 2 * math.pi * k / nv
                    vx, vy = math.cos(theta), math.sin(theta)
                    rv = s * raios[k % nv]
                    r2, g2, b2 = _cor(vx, vy)
                    glColor4f(min(r2, 1), min(g2, 1), min(b2, 1), alpha * 0.85)
                    glVertex2f(cx + rv * vx, cy + rv * vy)
                glEnd()
        glPopMatrix(); glMatrixMode(GL_PROJECTION); glPopMatrix(); glMatrixMode(GL_MODELVIEW)
        glDisable(GL_BLEND); glEnable(GL_DEPTH_TEST); glEnable(GL_LIGHTING)


# ============================================================
# METEOROS
# ============================================================

class Meteoros:
    MAX = 18

    def __init__(self):
        self.px     = np.zeros(self.MAX, np.float32)
        self.py     = np.zeros(self.MAX, np.float32)
        self.dx     = np.zeros(self.MAX, np.float32)
        self.dy     = np.zeros(self.MAX, np.float32)
        self.vel    = np.zeros(self.MAX, np.float32)
        self.vida   = np.zeros(self.MAX, np.float32)
        self.brilho = np.zeros(self.MAX, np.float32)
        self.cauda  = np.zeros(self.MAX, np.float32)
        self.ativo  = np.zeros(self.MAX, bool)
        self.timer  = 0.0
        self.intensidade = 1.0

    def _spawn(self, i):
        coords = [(-1.0, np.random.uniform(-1, 1)), (1.0, np.random.uniform(-1, 1)),
                  (np.random.uniform(-1, 1), 1.0),  (np.random.uniform(-1, 1), -1.0)]
        self.px[i], self.py[i] = coords[np.random.randint(4)]
        ang = np.random.uniform(math.pi * 1.1, math.pi * 1.6)
        self.dx[i] = math.cos(ang); self.dy[i] = math.sin(ang)
        self.vel[i]    = np.random.uniform(0.35, 0.80) * self.intensidade
        self.vida[i]   = 1.0
        self.brilho[i] = np.random.uniform(0.7, 1.0)
        self.cauda[i]  = np.random.uniform(0.04, 0.12)
        self.ativo[i]  = True

    def atualizar(self, dt):
        intervalo = max(0.15, 1.2 / self.intensidade)
        self.timer += dt
        if self.timer > intervalo:
            self.timer = 0.0
            spawned = 0
            for i in range(self.MAX):
                if not self.ativo[i] and spawned < max(1, int(self.intensidade)):
                    self._spawn(i); spawned += 1
        for i in range(self.MAX):
            if self.ativo[i]:
                self.px[i] += self.dx[i] * self.vel[i] * dt
                self.py[i] += self.dy[i] * self.vel[i] * dt
                self.vida[i] -= dt * 0.55
                if self.vida[i] <= 0 or abs(self.px[i]) > 1.3 or abs(self.py[i]) > 1.3:
                    self.ativo[i] = False

    def limpar(self):
        self.ativo[:] = False
        self.timer = 0.0

    def desenhar(self):
        glDisable(GL_LIGHTING); glDisable(GL_DEPTH_TEST)
        glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE)
        glMatrixMode(GL_PROJECTION); glPushMatrix(); glLoadIdentity(); glOrtho(-1, 1, -1, 1, -1, 1)
        glMatrixMode(GL_MODELVIEW);  glPushMatrix(); glLoadIdentity()
        glLineWidth(1.5)
        for i in range(self.MAX):
            if not self.ativo[i]: continue
            v = max(0.0, self.vida[i]); b = self.brilho[i]; c = self.cauda[i]
            hx, hy = self.px[i], self.py[i]
            r_c = min(1.0, 0.95 + (self.intensidade - 1.0) * 0.05)
            g_c = max(0.5, 0.97 - (self.intensidade - 1.0) * 0.1)
            glBegin(GL_LINES)
            glColor4f(r_c, g_c, 1.0, b * v); glVertex2f(hx, hy)
            glColor4f(0.7, 0.5, 0.9, 0.1);   glVertex2f(hx - self.dx[i] * c, hy - self.dy[i] * c)
            glEnd()
            glPointSize(2.5); glColor4f(1, 1, 1, b * v)
            glBegin(GL_POINTS); glVertex2f(hx, hy); glEnd()
        glLineWidth(1.0)
        glPopMatrix(); glMatrixMode(GL_PROJECTION); glPopMatrix(); glMatrixMode(GL_MODELVIEW)
        glDisable(GL_BLEND); glPointSize(1.2); glEnable(GL_DEPTH_TEST); glEnable(GL_LIGHTING)


# ============================================================
# CÂMERA
# ============================================================

class Camera:
    def __init__(self):
        self.azimute = 0.0; self.elevacao = 15.0; self.distancia = 5.0
        self.drag = False; self.last_mouse = (0, 0)

    def evento(self, event):
        if event.type == MOUSEBUTTONDOWN and event.button == 1:
            self.drag = True; self.last_mouse = event.pos
        elif event.type == MOUSEBUTTONUP and event.button == 1:
            self.drag = False
        elif event.type == MOUSEMOTION and self.drag:
            dx = event.pos[0] - self.last_mouse[0]
            dy = event.pos[1] - self.last_mouse[1]
            self.azimute  += dx * 0.4
            self.elevacao -= dy * 0.4
            self.elevacao  = max(-89, min(89, self.elevacao))
            self.last_mouse = event.pos
        elif event.type == MOUSEWHEEL:
            self.distancia = max(2.0, min(20.0, self.distancia - event.y * 0.3))

    def aplicar(self):
        glTranslatef(0, 0, -self.distancia)
        glRotatef(-self.elevacao, 1, 0, 0)
        glRotatef(-self.azimute,  0, 1, 0)

    def get_posicao(self):
        az = math.radians(self.azimute); el = math.radians(self.elevacao)
        return (self.distancia * math.cos(el) * math.sin(az),
                self.distancia * math.sin(el),
                self.distancia * math.cos(el) * math.cos(az))


# ============================================================
# ILUMINAÇÃO
# ============================================================

def configurar_iluminacao():
    glEnable(GL_LIGHTING); glEnable(GL_NORMALIZE)
    glEnable(GL_LIGHT0)
    glLightfv(GL_LIGHT0, GL_POSITION, (5, 8, 5, 1))
    glLightfv(GL_LIGHT0, GL_DIFFUSE,  (1.0, 0.95, 0.85, 1))
    glLightfv(GL_LIGHT0, GL_SPECULAR, (1.0, 1.0,  1.0,  1))
    glLightfv(GL_LIGHT0, GL_AMBIENT,  (0.05, 0.05, 0.08, 1))
    glEnable(GL_LIGHT1)
    glLightfv(GL_LIGHT1, GL_POSITION, (-4, -2, -3, 1))
    glLightfv(GL_LIGHT1, GL_DIFFUSE,  (0.15, 0.2, 0.35, 1))
    glLightfv(GL_LIGHT1, GL_SPECULAR, (0, 0, 0, 1))
    glLightfv(GL_LIGHT1, GL_AMBIENT,  (0, 0, 0, 1))
    glEnable(GL_COLOR_MATERIAL)
    glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
    glMaterialfv(GL_FRONT_AND_BACK, GL_SPECULAR, (0.8, 0.85, 0.9, 1))
    glMaterialf(GL_FRONT_AND_BACK,  GL_SHININESS, 64.0)


# ============================================================
# PAINEL SUPERIOR DE MODOS
# ============================================================

class PainelModos:
    ALTURA_BARRA = 90
    PAD_TOPO     = 16
    BTN_H        = 50
    BTN_PAD_X    = 20
    BTN_GAP      = 6
    CLIP_OFF     = 8

    COR_BG             = (4,   8,  20, 250)
    COR_BORDA_BAIXO    = (30,  80, 160,  90)
    COR_LABEL          = (80, 140, 210, 180)
    COR_BTN_NORMAL     = (8,  16,  36, 200)
    COR_BTN_HOVER      = (20,  50, 120, 220)
    COR_BTN_ATIVO      = (10,  28,  70, 235)
    COR_BTN_ATIVO_CAL  = (8,  30,  32, 235)
    COR_BORDA_ATIVO    = (60, 140, 255, 210)
    COR_BORDA_CAL_AT   = (30, 200, 200, 200)
    COR_TEXTO_INATIVO  = (80, 140, 210, 190)
    COR_TEXTO_ATIVO    = (180, 220, 255, 255)
    COR_TEXTO_CAL      = (120, 240, 230, 255)
    COR_DESC_TAG       = (50,  90, 160, 160)
    COR_DESC_VAL       = (90, 160, 240, 220)
    COR_LED_OK         = (40, 200, 120, 230)

    def __init__(self, display):
        self.W, self.H = display
        self._surface  = pygame.Surface((self.W, self.ALTURA_BARRA), pygame.SRCALPHA)
        self.f_label   = fontes().label(25, bold=True)
        self.f_btn     = fontes().display(20)
        self.f_desc    = fontes().label(14)
        self.sim_idx   = 0
        self._btn_rects_sim = []
        self._hover_sim     = -1
        self._calcular_layout()

    def _calcular_layout(self):
        btn_y   = self.PAD_TOPO + (self.ALTURA_BARRA - self.PAD_TOPO * 2 - self.BTN_H) // 2
        lbl_w   = self.f_label.size("SIMULAÇÃO")[0]
        lbl_gap = 16
        btn_ws  = [self.f_btn.size(n)[0] + self.BTN_PAD_X * 2 + 24 for n in MODOS_SIM]
        desc_gap = 24
        desc_w  = max(self.f_desc.size(d)[0] for d in SIM_DESC.values())
        total_w = lbl_w + lbl_gap + sum(btn_ws) + self.BTN_GAP * (len(MODOS_SIM) - 1) + desc_gap + desc_w
        x0 = (self.W - total_w) // 2
        self._label_pos = (x0, btn_y + (self.BTN_H - self.f_label.get_height()) // 2)
        x = x0 + lbl_w + lbl_gap
        self._btn_rects_sim = []
        for w in btn_ws:
            self._btn_rects_sim.append(pygame.Rect(x, btn_y, w, self.BTN_H))
            x += w + self.BTN_GAP

    def evento(self, event):
        if event.type == MOUSEMOTION:
            mx, my = event.pos
            if my < self.ALTURA_BARRA:
                self._hover_sim = next(
                    (i for i, r in enumerate(self._btn_rects_sim) if r.collidepoint(mx, my)), -1)
            else:
                self._hover_sim = -1
            return my < self.ALTURA_BARRA
        if event.type == MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if my < self.ALTURA_BARRA:
                for i, r in enumerate(self._btn_rects_sim):
                    if r.collidepoint(mx, my):
                        self.sim_idx = i
                return True
        return False

    def desenhar(self, surface, pausado):
        s = self._surface
        s.fill((0, 0, 0, 0))
        t = time.time()

        pygame.draw.rect(s, self.COR_BG, (0, 0, self.W, self.ALTURA_BARRA))

        CAN, ESP, COR_CAN = 10, 1, (40, 100, 200, 130)
        for x, y, dx, dy in [(4, 4, 1, 1), (self.W - 4, 4, -1, 1),
                              (4, self.ALTURA_BARRA - 4, 1, -1), (self.W - 4, self.ALTURA_BARRA - 4, -1, -1)]:
            pygame.draw.line(s, COR_CAN, (x, y), (x + dx * CAN, y), ESP)
            pygame.draw.line(s, COR_CAN, (x, y), (x, y + dy * CAN), ESP)

        scan_x   = int(((t * 0.18) % 1.2 - 0.1) * self.W)
        scan_surf = pygame.Surface((120, self.ALTURA_BARRA), pygame.SRCALPHA)
        for i in range(60):
            a = int(8 * math.sin(math.pi * i / 60))
            pygame.draw.line(scan_surf, (60, 140, 255, a), (i, 0), (i, self.ALTURA_BARRA))
            pygame.draw.line(scan_surf, (60, 140, 255, a), (119 - i, 0), (119 - i, self.ALTURA_BARRA))
        s.blit(scan_surf, (scan_x - 60, 0))

        lbl = self.f_label.render("SIMULAÇÃO", True, self.COR_LABEL[:3])
        lbl.set_alpha(self.COR_LABEL[3])
        s.blit(lbl, (self._label_pos[0], self.ALTURA_BARRA // 2 - lbl.get_height() // 2))

        div_x = self._label_pos[0] + lbl.get_width() + 18
        for dy in range(14, self.ALTURA_BARRA - 14):
            a = int(120 * math.sin(math.pi * (dy - 14) / (self.ALTURA_BARRA - 28)))
            pygame.draw.line(s, (40, 100, 200, a), (div_x, dy), (div_x, dy))

        for i, (nome, rect) in enumerate(zip(MODOS_SIM, self._btn_rects_sim)):
            ativo  = self.sim_idx == i
            hover  = self._hover_sim == i
            eh_cal = (nome == "Calibração")

            if ativo:
                cor_bg    = self.COR_BTN_ATIVO_CAL if eh_cal else self.COR_BTN_ATIVO
                cor_borda = self.COR_BORDA_CAL_AT  if eh_cal else self.COR_BORDA_ATIVO
                cor_txt   = self.COR_TEXTO_CAL      if eh_cal else self.COR_TEXTO_ATIVO
            elif hover:
                cor_bg    = self.COR_BTN_HOVER
                cor_borda = (50, 110, 200, 160)
                cor_txt   = self.COR_TEXTO_ATIVO
            else:
                cor_bg    = self.COR_BTN_NORMAL
                cor_borda = (30, 70, 150, 100)
                cor_txt   = self.COR_TEXTO_INATIVO

            rx, ry, rw, rh = rect
            pts = [(rx + self.CLIP_OFF, ry), (rx + rw, ry),
                   (rx + rw - self.CLIP_OFF, ry + rh), (rx, ry + rh)]
            pygame.draw.polygon(s, cor_bg,    pts)
            pygame.draw.polygon(s, cor_borda, pts, 1)

            dot_x, dot_y = rx + 12, ry + rh // 2
            pygame.draw.circle(s, cor_borda[:3] + (220,), (dot_x, dot_y), 3)
            if ativo:
                pygame.draw.circle(s, (255, 255, 255, 80), (dot_x - 1, dot_y - 1), 1)
                glow_a   = int(180 + 70 * math.sin(t * 3.0))
                glow_cor = (self.COR_BORDA_CAL_AT if eh_cal else self.COR_BORDA_ATIVO)[:3] + (glow_a,)
                pygame.draw.line(s, glow_cor,
                                 (rx + self.CLIP_OFF + 4, ry + rh - 1),
                                 (rx + rw - self.CLIP_OFF - 4, ry + rh - 1), 1)

            txt = self.f_btn.render(nome, True, cor_txt[:3])
            txt.set_alpha(cor_txt[3])
            s.blit(txt, txt.get_rect(center=rect.center))

        desc_x = self._btn_rects_sim[-1].right + 28
        tag_s  = fontes().label(9).render("MODO ATIVO", True, self.COR_DESC_TAG[:3])
        tag_s.set_alpha(self.COR_DESC_TAG[3])
        s.blit(tag_s, (desc_x, self.ALTURA_BARRA // 2 - 14))
        val_s = fontes().label(18).render(SIM_DESC[MODOS_SIM[self.sim_idx]], True, self.COR_DESC_VAL[:3])
        val_s.set_alpha(self.COR_DESC_VAL[3])
        s.blit(val_s, (desc_x, self.ALTURA_BARRA // 2 + 1))

        led_x = self.W - 120
        led_a = int(180 + 70 * math.sin(t * 2.2))
        pygame.draw.circle(s, self.COR_LED_OK[:3] + (led_a,), (led_x, self.ALTURA_BARRA // 2), 4)
        pygame.draw.circle(s, (255, 255, 255, 60), (led_x - 1, self.ALTURA_BARRA // 2 - 1), 1)
        st_s = fontes().label(9).render("SISTEMA NOMINAL", True, self.COR_DESC_VAL[:3])
        st_s.set_alpha(160)
        s.blit(st_s, (led_x + 10, self.ALTURA_BARRA // 2 - st_s.get_height() // 2))

        pygame.draw.line(s, self.COR_BORDA_BAIXO[:3] + (90,),
                         (0, self.ALTURA_BARRA - 1), (self.W, self.ALTURA_BARRA - 1), 1)
        glow_w = self.W // 3
        for gx in range(glow_w):
            a = int(200 * math.sin(math.pi * gx / glow_w))
            pygame.draw.line(s, (60, 140, 255, a),
                             (self.W // 2 - glow_w // 2 + gx, self.ALTURA_BARRA - 1),
                             (self.W // 2 - glow_w // 2 + gx, self.ALTURA_BARRA - 1))

        if pausado:
            pau = self.f_btn.render("⏸  PAUSADO", True, (255, 200, 40))
            s.blit(pau, (led_x - 80, self.ALTURA_BARRA // 2 - pau.get_height() // 2 - 14))

        surface.blit(s, (0, 0))

    @property
    def modo_sim(self): return MODOS_SIM[self.sim_idx]


# ============================================================
# FADE OVERLAY — utilitário OpenGL
# ============================================================

def desenhar_fade_opengl(alpha_0_1, display):
    a = max(0.0, min(1.0, alpha_0_1))
    glDisable(GL_LIGHTING); glDisable(GL_DEPTH_TEST); glDisable(GL_CULL_FACE)
    glMatrixMode(GL_PROJECTION); glPushMatrix(); glLoadIdentity(); glOrtho(-1, 1, -1, 1, -1, 1)
    glMatrixMode(GL_MODELVIEW);  glPushMatrix(); glLoadIdentity()
    glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glColor4f(0.0, 0.0, 0.0, a)
    glBegin(GL_QUADS)
    glVertex2f(-1, -1); glVertex2f(1, -1)
    glVertex2f( 1,  1); glVertex2f(-1,  1)
    glEnd()
    glDisable(GL_BLEND)
    glPopMatrix(); glMatrixMode(GL_PROJECTION); glPopMatrix(); glMatrixMode(GL_MODELVIEW)
    glEnable(GL_DEPTH_TEST); glEnable(GL_LIGHTING)


# ============================================================
# MÁQUINA DE ESTADOS — MODO MISSÃO
# ============================================================

class MaquinaMissao:
    DURACAO_AVISO    = 5.0
    DURACAO_CHUVA    = 5.0
    DURACAO_CORRECAO = 10.0

    ESTADO_IDLE     = "IDLE"
    ESTADO_INTRO    = "INTRO"      # ← novo: painel de boas-vindas
    ESTADO_AVISO    = "AVISO"
    ESTADO_CHUVA    = "CHUVA"
    ESTADO_CORRECAO = "CORRECAO"
    ESTADO_PERGUNTA = "PERGUNTA"

    def __init__(self):
        self._estado   = self.ESTADO_IDLE
        self._t_estado = 0.0
        self._iniciada = False

    def reiniciar(self):
        self._estado   = self.ESTADO_IDLE
        self._t_estado = 0.0
        self._iniciada = False

    def forcar_estado(self, estado: str):
        mapa = {
            "IDLE":     self.ESTADO_IDLE,
            "CHUVA":    self.ESTADO_CHUVA,
            "CORRECAO": self.ESTADO_CORRECAO,
            "PERGUNTA": self.ESTADO_PERGUNTA,
        }
        novo = mapa.get(estado)
        if novo and novo != self._estado:
            self._estado   = novo
            self._t_estado = 0.0
            self._iniciada = (novo != self.ESTADO_IDLE)

    def iniciar(self):

        if self._estado == self.ESTADO_IDLE and not self._iniciada:
            self._estado   = self.ESTADO_INTRO
            self._t_estado = 0.0
            self._iniciada = True

    def responder_intro(self, sim: bool):
        """Chamado quando o usuário pressiona S ou N no painel de introdução."""
        if self._estado != self.ESTADO_INTRO:
            return
        if sim:
            self._estado   = self.ESTADO_AVISO
            self._t_estado = 0.0
        else:
            self._estado   = self.ESTADO_IDLE
            self._t_estado = 0.0
            self._iniciada = False

    def responder_pergunta(self, sim: bool):
        if self._estado != self.ESTADO_PERGUNTA:
            return
        if sim:
            self._estado   = self.ESTADO_AVISO
            self._t_estado = 0.0
        else:
            self._estado   = self.ESTADO_IDLE
            self._t_estado = 0.0
            self._iniciada = False

    def atualizar(self, dt):
        self._t_estado += dt


        if self._estado == self.ESTADO_INTRO:
            return self._estado

        if self._estado == self.ESTADO_AVISO and self._t_estado >= self.DURACAO_AVISO:
            self._estado   = self.ESTADO_CHUVA
            self._t_estado = 0.0
        elif self._estado == self.ESTADO_CHUVA and self._t_estado >= self.DURACAO_CHUVA:
            self._estado   = self.ESTADO_CORRECAO
            self._t_estado = 0.0
        elif self._estado == self.ESTADO_CORRECAO and self._t_estado >= self.DURACAO_CORRECAO:
            self._estado   = self.ESTADO_PERGUNTA
            self._t_estado = 0.0
        return self._estado

    @property
    def estado(self): return self._estado

    @property
    def t_estado(self): return self._t_estado

    def tempo_restante_aviso(self):
        return max(0.0, self.DURACAO_AVISO - self._t_estado)

    def progresso_correcao(self):
        return min(1.0, self._t_estado / self.DURACAO_CORRECAO)


# ============================================================
# OVERLAY DE INTRODUÇÃO À MISSÃO  (novo)
# ============================================================

def desenhar_overlay_intro(surface, maquina: MaquinaMissao, display):
    W, H = display
    t    = maquina.t_estado

    # fundo escurecido
    escuro = pygame.Surface((W, H), pygame.SRCALPHA)
    escuro.fill((0, 0, 0, 150))
    surface.blit(escuro, (0, 0))

    pw, ph = 800, 510
    px, py = (W - pw) // 2, (H - ph) // 2

    painel  = pygame.Surface((pw, ph), pygame.SRCALPHA)
    pulso_a = int(120 + 90 * math.sin(t * 2.0))

    # fundo do painel
    pygame.draw.rect(painel, (6, 12, 28, 248),        (0, 0, pw, ph), border_radius=16)
    pygame.draw.rect(painel, (50, 130, 220, pulso_a), (0, 0, pw, ph), 2, border_radius=16)
    # linha interna suave
    pygame.draw.rect(painel, (20, 60, 120, 60),       (3, 3, pw - 6, ph - 6), 1, border_radius=14)

    PAD = 30

    f_badge  = fontes().label(15, bold=True)
    f_titulo = fontes().display(28, bold=True)
    f_sub    = fontes().label(18)
    f_fase_t = fontes().label(17, bold=True)
    f_fase_d = fontes().label(15)
    f_btn    = fontes().display(22, bold=True)
    f_hint   = fontes().display(14)

    # ── Badge ──────────────────────────────────────────────────
    badge_txt = f_badge.render("  MODO MISSÃO  ", True, (120, 200, 255))
    badge_bg  = pygame.Surface((badge_txt.get_width() + 16, badge_txt.get_height() + 10), pygame.SRCALPHA)
    pygame.draw.rect(badge_bg, (18, 48, 100, 210), badge_bg.get_rect(), border_radius=7)
    pygame.draw.rect(badge_bg, (50, 120, 220, 140), badge_bg.get_rect(), 1, border_radius=7)
    painel.blit(badge_bg,  (PAD, PAD))
    painel.blit(badge_txt, (PAD + 8, PAD + 5))

    y = PAD + badge_bg.get_height() + 16

    # ── Título ─────────────────────────────────────────────────
    brilho     = 0.88 + 0.12 * math.sin(t * 2.5)
    cor_titulo = (int(205 * brilho), int(230 * brilho), int(255 * brilho))
    titulo_s   = f_titulo.render("Bem-vindo ao Modo Missão", True, cor_titulo)
    painel.blit(titulo_s, (PAD, y))
    y += titulo_s.get_height() + 8

    sub_s = f_sub.render(
        "Simulação de operação orbital em tempo real com o sensor MPU6050.", True, (120, 165, 215))
    painel.blit(sub_s, (PAD, y))
    y += sub_s.get_height() + 16

    pygame.draw.line(painel, (35, 85, 165, 110), (PAD, y), (pw - PAD, y), 1)
    y += 16

    # ── Fases ──────────────────────────────────────────────────
    FASES = [
        (
            (255, 210, 50),
            (55, 32, 8, 210),
            (80, 50, 10, 170),
            "Fase 1 — Aviso de chuva de meteoros",
            "Um alerta com contagem regressiva indica que uma tempestade se aproxima. Prepare-se!",
        ),
        (
            (255, 80, 60),
            (60, 10, 10, 210),
            (80, 15, 15, 170),
            "Fase 2 — Tempestade de meteoros",
            "O satélite entra em turbulência severa. Pitch, roll e yaw oscilam de forma caótica.",
        ),
        (
            (55, 220, 130),
            (8, 48, 22, 210),
            (12, 65, 30, 170),
            "Fase 3 — Correção de órbita",
            "Os propulsores ativam correção automática. Acompanhe a barra até voltar ao nominal.",
        ),
    ]

    fase_h = 66
    for cor, cor_bg, cor_borda_bg, nome, desc in FASES:
        largura_fase = pw - PAD * 2
        fase_bg = pygame.Surface((largura_fase, fase_h), pygame.SRCALPHA)
        pygame.draw.rect(fase_bg, cor_bg,      (0, 0, largura_fase, fase_h), border_radius=10)
        pygame.draw.rect(fase_bg, cor_borda_bg, (0, 0, largura_fase, fase_h), 1, border_radius=10)

        # marcador lateral colorido
        pygame.draw.rect(fase_bg, (*cor, 220), (0, 10, 4, fase_h - 20), border_radius=2)
        # círculo indicador
        pygame.draw.circle(fase_bg, (*cor, 200), (22, fase_h // 2), 8)
        pygame.draw.circle(fase_bg, (255, 255, 255, 60), (20, fase_h // 2 - 2), 3)

        nome_s = f_fase_t.render(nome, True, cor)
        fase_bg.blit(nome_s, (40, 10))
        desc_s = f_fase_d.render(desc, True, (170, 200, 230))
        fase_bg.blit(desc_s, (40, 10 + nome_s.get_height() + 5))

        painel.blit(fase_bg, (PAD, y))
        y += fase_h + 8

    y += 4
    pygame.draw.line(painel, (35, 85, 165, 110), (PAD, y), (pw - PAD, y), 1)
    y += 12

    # ── Dica sensor ────────────────────────────────────────────
    dica_s = f_fase_d.render(
        "  Com o MPU6050 conectado, a atitude real do hardware é usada em todas as fases.",
        True, (95, 155, 210))
    dica_s.set_alpha(195)
    painel.blit(dica_s, (PAD, y))
    y += dica_s.get_height() + 20

    # ── Botões ─────────────────────────────────────────────────
    btn_w, btn_h = 175, 54
    espaco       = 28
    total_btn    = btn_w * 2 + espaco
    bx_s         = (pw - total_btn) // 2
    bx_n         = bx_s + btn_w + espaco

    piscar    = math.sin(t * 4.0) > 0
    cor_s_bg  = (28, 155, 65, 230) if piscar else (18, 110, 48, 195)
    cor_s_brd = (70, 235, 130, 190)
    pygame.draw.rect(painel, cor_s_bg,  (bx_s, y, btn_w, btn_h), border_radius=10)
    pygame.draw.rect(painel, cor_s_brd, (bx_s, y, btn_w, btn_h), 2, border_radius=10)
    txt_s = f_btn.render("S — Iniciar", True, (230, 255, 235))
    painel.blit(txt_s, txt_s.get_rect(center=(bx_s + btn_w // 2, y + btn_h // 2)))

    pygame.draw.rect(painel, (55, 18, 18, 200),  (bx_n, y, btn_w, btn_h), border_radius=10)
    pygame.draw.rect(painel, (185, 60, 60, 170), (bx_n, y, btn_w, btn_h), 2, border_radius=10)
    txt_n = f_btn.render("N — Cancelar", True, (255, 205, 205))
    painel.blit(txt_n, txt_n.get_rect(center=(bx_n + btn_w // 2, y + btn_h // 2)))

    y += btn_h + 12

    hint_s = f_hint.render("Pressione  S  para iniciar  ou  N  para cancelar", True, (100, 148, 205))
    hint_s.set_alpha(int(160 + 65 * math.sin(t * 2.0)))
    painel.blit(hint_s, hint_s.get_rect(center=(pw // 2, y + hint_s.get_height() // 2)))

    surface.blit(painel, (px, py))


# ============================================================
# OVERLAY DE MISSÃO
# ============================================================

def desenhar_overlay_missao(surface, maquina: MaquinaMissao, display):
    W, H   = display
    estado = maquina.estado

    # ── Introdução (novo estado) ────────────────────────────────
    if estado == MaquinaMissao.ESTADO_INTRO:
        desenhar_overlay_intro(surface, maquina, display)
        return

    if estado == MaquinaMissao.ESTADO_AVISO:
        t_rest = maquina.tempo_restante_aviso()
        pw, ph = 700, 160
        px, py = (W - pw) // 2, (H - ph) // 2 - 40
        painel  = pygame.Surface((pw, ph), pygame.SRCALPHA)
        pulso_a = int(160 + 80 * math.sin(maquina.t_estado * 6.0))
        pygame.draw.rect(painel, (20, 5, 5, 200),       (0, 0, pw, ph), border_radius=14)
        pygame.draw.rect(painel, (220, 40, 40, pulso_a), (0, 0, pw, ph), 3, border_radius=14)
        f_aviso = fontes().display(22, bold=True)
        f_count = fontes().display(52, bold=True)
        txt1    = f_aviso.render("CHUVA DE METEOROS SE APROXIMANDO", True, (255, 200, 40))
        txt1.set_alpha(230)
        painel.blit(txt1, txt1.get_rect(center=(pw // 2, 40)))
        segundos  = int(math.ceil(t_rest))
        cor_count = (255, 80, 60) if segundos <= 2 else (255, 200, 40)
        txt2 = f_count.render(f"{segundos}s", True, cor_count)
        painel.blit(txt2, txt2.get_rect(center=(pw // 2, 110)))
        surface.blit(painel, (px, py))

    elif estado == MaquinaMissao.ESTADO_CHUVA:
        f_status = fontes().display(20, bold=True)
        t_rest   = max(0.0, MaquinaMissao.DURACAO_CHUVA - maquina.t_estado)
        pulso    = int(180 + 75 * math.sin(maquina.t_estado * 8.0))
        txt = f_status.render(
            f"TEMPESTADE DE METEOROS  —  {t_rest:.1f}s  —  MPU6050 ATIVO", True, (255, 80, 60))
        txt.set_alpha(pulso)
        bg = pygame.Surface((txt.get_width() + 28, txt.get_height() + 12), pygame.SRCALPHA)
        pygame.draw.rect(bg, (30, 5, 5, 180), bg.get_rect(), border_radius=8)
        cx, cy = (W - bg.get_width()) // 2, 120
        surface.blit(bg,  (cx, cy))
        surface.blit(txt, (cx + 14, cy + 6))

    elif estado == MaquinaMissao.ESTADO_CORRECAO:
        f_status = fontes().display(16, bold=True)
        prog     = maquina.progresso_correcao()
        t_rest   = max(0.0, MaquinaMissao.DURACAO_CORRECAO - maquina.t_estado)
        txt = f_status.render(f"  CORREÇÃO DE ÓRBITA  —  {t_rest:.0f}s", True, (80, 220, 140))
        bg  = pygame.Surface((txt.get_width() + 28, txt.get_height() + 12), pygame.SRCALPHA)
        pygame.draw.rect(bg, (5, 25, 15, 180), bg.get_rect(), border_radius=8)
        cx, cy = (W - bg.get_width()) // 2, 120
        surface.blit(bg,  (cx, cy))
        surface.blit(txt, (cx + 14, cy + 6))
        bw, bh = 400, 10
        bx, by = (W - bw) // 2, cy + txt.get_height() + 18
        bg2 = pygame.Surface((bw, bh), pygame.SRCALPHA)
        pygame.draw.rect(bg2, (20,  60,  40, 160), (0, 0, bw, bh),              border_radius=5)
        pygame.draw.rect(bg2, (60, 220, 120, 200), (0, 0, int(bw * prog), bh),  border_radius=5)
        surface.blit(bg2, (bx, by))

    elif estado == MaquinaMissao.ESTADO_PERGUNTA:
        desenhar_overlay_pergunta(surface, maquina, display)


def desenhar_overlay_pergunta(surface, maquina: MaquinaMissao, display):
    W, H = display
    t    = maquina.t_estado
    escuro = pygame.Surface((W, H), pygame.SRCALPHA)
    escuro.fill((0, 0, 0, 120))
    surface.blit(escuro, (0, 0))
    pw, ph = 680, 260
    px, py = (W - pw) // 2, (H - ph) // 2 - 30
    painel  = pygame.Surface((pw, ph), pygame.SRCALPHA)
    pulso_a = int(140 + 100 * math.sin(t * 2.5))
    pygame.draw.rect(painel, (8,  18, 35, 230),      (0, 0, pw, ph), border_radius=16)
    pygame.draw.rect(painel, (40, 220, 120, pulso_a), (0, 0, pw, ph), 3, border_radius=16)
    pygame.draw.rect(painel, (20, 120,  70, 60),      (0, 0, pw, ph), 1, border_radius=16)
    f_titulo = fontes().display(24, bold=True)
    f_sub    = fontes().label(20, bold=True)
    f_hint   = fontes().display(15, bold=True)
    f_tecla  = fontes().display(28, bold=True)
    brilho     = 0.88 + 0.12 * math.sin(t * 3.0)
    cor_titulo = (int(60 * brilho), int(230 * brilho), int(120 * brilho))
    txt_titulo = f_titulo.render("MISSÃO CONCLUÍDA", True, cor_titulo)
    painel.blit(txt_titulo, txt_titulo.get_rect(center=(pw // 2, 48)))
    pygame.draw.line(painel, (40, 120, 80, 120), (40, 78), (pw - 40, 78), 1)
    txt_sub = f_sub.render("Deseja executar a missão novamente?", True, (200, 225, 255))
    painel.blit(txt_sub, txt_sub.get_rect(center=(pw // 2, 115)))
    btn_gap = 40; btn_w = 120; btn_h = 56
    bx_s = (pw - btn_w * 2 - btn_gap) // 2
    bx_n = bx_s + btn_w + btn_gap
    by_b = 158
    piscar_s = math.sin(t * 4.0) > 0
    cor_s_bg = (30, 180, 80, 220) if piscar_s else (20, 130, 60, 190)
    pygame.draw.rect(painel, cor_s_bg,         (bx_s, by_b, btn_w, btn_h), border_radius=10)
    pygame.draw.rect(painel, (60, 230, 120, 180), (bx_s, by_b, btn_w, btn_h), 2, border_radius=10)
    txt_s = f_tecla.render("S", True, (255, 255, 255))
    painel.blit(txt_s, txt_s.get_rect(center=(bx_s + btn_w // 2, by_b + btn_h // 2 - 6)))
    painel.blit(fontes().label(13).render("Sim", True, (180, 255, 200)),
                fontes().label(13).render("Sim", True, (180, 255, 200)).get_rect(
                    center=(bx_s + btn_w // 2, by_b + btn_h - 10)))
    pygame.draw.rect(painel, (60, 20,  20, 190),  (bx_n, by_b, btn_w, btn_h), border_radius=10)
    pygame.draw.rect(painel, (180, 60, 60, 160),  (bx_n, by_b, btn_w, btn_h), 2, border_radius=10)
    txt_n = f_tecla.render("N", True, (255, 255, 255))
    painel.blit(txt_n, txt_n.get_rect(center=(bx_n + btn_w // 2, by_b + btn_h // 2 - 6)))
    painel.blit(fontes().label(13).render("Não", True, (255, 180, 180)),
                fontes().label(13).render("Não", True, (255, 180, 180)).get_rect(
                    center=(bx_n + btn_w // 2, by_b + btn_h - 10)))
    txt_hint = f_hint.render("Pressione  S  ou  N", True, (120, 160, 200))
    txt_hint.set_alpha(int(180 + 60 * math.sin(t * 2.0)))
    painel.blit(txt_hint, txt_hint.get_rect(center=(pw // 2, 232)))
    surface.blit(painel, (px, py))


# ============================================================
# OVERLAY DE CALIBRAÇÃO
# ============================================================

def desenhar_overlay_calibracao(surface, leitor, filtro, pitch, roll, yaw, display):
    W, H = display
    t    = time.time()
    pw, ph = 520, 420
    px, py = (W - pw) // 2, (H - ph) // 2
    painel  = pygame.Surface((pw, ph), pygame.SRCALPHA)
    pulso_a = int(130 + 80 * math.sin(t * 2.2))
    pygame.draw.rect(painel, (8, 14, 28, 235),        (0, 0, pw, ph), border_radius=14)
    pygame.draw.rect(painel, (80, 160, 220, pulso_a),  (0, 0, pw, ph), 3, border_radius=14)
    PAD = 18
    f_titulo = fontes().display(18, bold=True)
    f_label  = fontes().label(15, bold=True)
    f_valor  = fontes().dados(14)
    f_hint   = fontes().label(14)
    COR_BASE      = (80, 160, 220)
    COR_CLARA     = (140, 195, 235)
    COR_BRILHANTE = (200, 230, 255)
    COR_BARRA     = (80, 160, 220)
    COR_BARRA_BG  = (18, 35, 55, 180)
    t_titulo = f_titulo.render("CALIBRAÇÃO DO SENSOR", True, COR_BRILHANTE)
    painel.blit(t_titulo, t_titulo.get_rect(center=(pw // 2, PAD + t_titulo.get_height() // 2)))
    sep_y = PAD + t_titulo.get_height() + 8
    pygame.draw.line(painel, (*COR_BASE, 100), (PAD, sep_y), (pw - PAD, sep_y), 1)
    conectado  = leitor.conectado
    led_cor    = (80, 220, 160) if conectado else (80, 130, 180)
    status_txt = "MPU6050 CONECTADO" if conectado else "MPU6050 DESCONECTADO"
    pygame.draw.circle(painel, led_cor, (PAD + 8, sep_y + 22), 7)
    pygame.draw.circle(painel, (255, 255, 255, 80), (PAD + 6, sep_y + 20), 3)
    painel.blit(f_label.render(status_txt, True, COR_BRILHANTE), (PAD + 22, sep_y + 14))
    y0 = sep_y + 50
    painel.blit(f_label.render("ACELERÔMETRO  (raw)", True, COR_CLARA), (PAD, y0))
    y0 += f_label.get_height() + 4
    bw = pw - PAD * 2 - 60
    for label, val, escala in [("Ax", leitor.ac_x, 16384.0),
                                ("Ay", leitor.ac_y, 16384.0),
                                ("Az", leitor.ac_z, 16384.0)]:
        painel.blit(f_label.render(f"{label}", True, COR_CLARA), (PAD, y0))
        val_s = f_valor.render(f"{val:+8.0f}", True, COR_BRILHANTE)
        painel.blit(val_s, (PAD + 36, y0))
        bx = PAD + 36 + val_s.get_width() + 8; by_ = y0 + 3; bh2 = 12
        bw2 = bw - (bx - PAD)
        pygame.draw.rect(painel, COR_BARRA_BG, (bx, by_, bw2, bh2), border_radius=4)
        bar_w = int(bw2 * max(0.0, min(1.0, (val + escala) / (2 * escala))))
        if bar_w > 0:
            pygame.draw.rect(painel, (*COR_BARRA, 200), (bx, by_, bar_w, bh2), border_radius=4)
        pygame.draw.line(painel, (255, 255, 255, 60), (bx + bw2 // 2, by_), (bx + bw2 // 2, by_ + bh2), 1)
        y0 += 22
    y0 += 8
    painel.blit(f_label.render("GIROSCÓPIO  (°/s)", True, COR_CLARA), (PAD, y0))
    y0 += f_label.get_height() + 4
    gx, gy_, gz = leitor.gyro_dps() if conectado else (0.0, 0.0, 0.0)
    escala_gy   = 131.0 * 250
    for label, val in [("Gx", gx), ("Gy", gy_), ("Gz", gz)]:
        painel.blit(f_label.render(f"{label}", True, COR_CLARA), (PAD, y0))
        val_s = f_valor.render(f"{val:+8.1f}°/s", True, COR_BRILHANTE)
        painel.blit(val_s, (PAD + 36, y0))
        bx = PAD + 36 + val_s.get_width() + 8; by_ = y0 + 3; bh2 = 12
        bw2 = bw - (bx - PAD)
        pygame.draw.rect(painel, COR_BARRA_BG, (bx, by_, bw2, bh2), border_radius=4)
        bar_w = int(bw2 * max(0.0, min(1.0, (val + escala_gy) / (2 * escala_gy))))
        if bar_w > 0:
            pygame.draw.rect(painel, (*COR_BARRA, 200), (bx, by_, bar_w, bh2), border_radius=4)
        pygame.draw.line(painel, (255, 255, 255, 60), (bx + bw2 // 2, by_), (bx + bw2 // 2, by_ + bh2), 1)
        y0 += 22
    y0 += 10
    pygame.draw.line(painel, (*COR_BASE, 70), (PAD, y0), (pw - PAD, y0), 1)
    y0 += 8
    painel.blit(f_label.render("ÂNGULOS FILTRADOS", True, COR_CLARA), (PAD, y0))
    y0 += f_label.get_height() + 4
    col_w = (pw - PAD * 2) // 3
    for i, (lbl, val) in enumerate([("PITCH", pitch), ("ROLL", roll), ("YAW", yaw)]):
        xc  = PAD + i * col_w + col_w // 2
        l_s = f_label.render(lbl, True, COR_CLARA)
        v_s = f_valor.render(f"{val:+.1f}°", True, COR_BRILHANTE)
        painel.blit(l_s, l_s.get_rect(center=(xc, y0 + l_s.get_height() // 2)))
        painel.blit(v_s, v_s.get_rect(center=(xc, y0 + l_s.get_height() + 4 + v_s.get_height() // 2)))
    hint_txt = f_hint.render("Posicione o sensor plano e pressione C para zerar", True, COR_CLARA)
    hint_txt.set_alpha(int(160 + 60 * math.sin(t * 1.8)))
    painel.blit(hint_txt, hint_txt.get_rect(center=(pw // 2, ph - 20)))
    surface.blit(painel, (px, py))


# ============================================================
# POSICIONAMENTO DOS PAINÉIS LATERAIS
# ============================================================

def calcular_offset_paineis(display, barra_h, horizonte_ph, imu_ph, fps_h,
                             gap=10, gap_hz_imu=22):
    _, H      = display
    area_topo = barra_h + gap
    bloco_h   = horizonte_ph + gap_hz_imu + imu_ph + gap + fps_h
    off_topo  = area_topo + max(0, (H - area_topo - bloco_h) // 2)
    return off_topo, off_topo + horizonte_ph + gap_hz_imu, off_topo + horizonte_ph + gap_hz_imu + imu_ph + gap


# ============================================================
# FPS OVERLAY
# ============================================================

def desenhar_fps_overlay(surface, fps, px_direita, py_abaixo, margem):
    f_fps = fontes().display(17)
    COR_FPS = (255, 200, 40, 230)
    txt = f_fps.render(f"{fps:.0f} FPS", True, COR_FPS[:3])
    txt.set_alpha(COR_FPS[3])
    pad = 6
    bg  = pygame.Surface((txt.get_width() + pad * 2, txt.get_height() + pad * 2), pygame.SRCALPHA)
    pygame.draw.rect(bg, (8, 12, 22, 180), bg.get_rect(), border_radius=6)
    surface.blit(bg,  (px_direita - bg.get_width(),         py_abaixo))
    surface.blit(txt, (px_direita - txt.get_width() - pad,  py_abaixo + pad))

FPS_H = 40


# ============================================================
# SETA DE DIREÇÃO
# ============================================================

def desenhar_seta_direcao(t_global):
    glDisable(GL_LIGHTING); glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA); glDisable(GL_CULL_FACE)
    pulso  = 0.75 + 0.25 * math.sin(t_global * 4.0)
    escala = 0.92 + 0.08 * math.sin(t_global * 4.0)
    R, G, B    = 1.4, 0.85, 0.1
    BICO_Z     = 0.64; HASTE = 0.38; CABECA = 0.13; RAIO_CAB = 0.055
    tip_z  = BICO_Z - HASTE * escala
    base_z = tip_z  + CABECA * escala
    segs   = 16
    glBegin(GL_TRIANGLE_FAN)
    glColor4f(R, G, B, 0.85 * pulso); glVertex3f(0, 0, tip_z)
    for k in range(segs + 1):
        ang = 2 * math.pi * k / segs
        glColor4f(R * 0.7, G * 0.7, 0.05, 0.7 * pulso)
        glVertex3f(RAIO_CAB * escala * math.cos(ang), RAIO_CAB * escala * math.sin(ang), base_z)
    glEnd()
    glBlendFunc(GL_SRC_ALPHA, GL_ONE)
    glPointSize(10.0); glColor4f(1, 0.95, 0.4, 0.35 * pulso)
    glBegin(GL_POINTS); glVertex3f(0, 0, tip_z); glEnd()
    glPointSize(4.5);  glColor4f(1, 1, 0.8, 0.9 * pulso)
    glBegin(GL_POINTS); glVertex3f(0, 0, tip_z); glEnd()
    glDisable(GL_BLEND); glPointSize(1.0); glEnable(GL_CULL_FACE); glEnable(GL_LIGHTING)


# ============================================================
# SPLASH
# ============================================================

def desenhar_splash(surface, t, display):
    W, H = display
    surface.fill((4, 6, 14))
    for gx in range(0, W, 40):
        for gy in range(0, H, 40):
            b = int(18 + 12 * math.sin((gx + gy) * 0.05 + t * 0.8))
            pygame.draw.circle(surface, (b, b, b + 12), (gx, gy), 1)
    scan_y = int((H * 0.5) + (H * 0.25) * math.sin(t * 1.2))
    for dy in range(-50, 51):
        a = max(0, 1.0 - abs(dy) / 50.0)
        pygame.draw.line(surface, (0, int(45 * a), int(28 * a)), (0, scan_y + dy), (W, scan_y + dy))
    for frac, esp, op in [(0.30, 1, 55), (0.70, 1, 55), (0.28, 2, 110), (0.72, 2, 110)]:
        y_l = int(H * frac)
        pygame.draw.line(surface, (int(op * 0.18), int(op * 0.55), op), (W // 8, y_l), (7 * W // 8, y_l), esp)
    ft_titulo = fontes().display(96, bold=True)
    titulo    = "Rtos Simulator"
    for raio, alpha in [(10, 25), (6, 50), (3, 90)]:
        gs = ft_titulo.render(titulo, True, (0, 100, 200))
        gs.set_alpha(alpha)
        for ox, oy in [(-raio, 0), (raio, 0), (0, -raio), (0, raio),
                       (-raio, -raio), (raio, raio), (-raio, raio), (raio, -raio)]:
            surface.blit(gs, gs.get_rect(center=(W // 2 + ox, H // 2 - 70 + oy)))
    p  = 0.90 + 0.10 * math.sin(t * 2.5)
    ts = ft_titulo.render(titulo, True, (int(230 * p), int(240 * p), int(255 * p)))
    tr = ts.get_rect(center=(W // 2, H // 2 - 70))
    surface.blit(ts, tr)
    linha_cor = (int(255 * p), int(200 * p), int(30 * p))
    pygame.draw.line(surface, linha_cor, (tr.left, tr.bottom + 10), (tr.right, tr.bottom + 10), 3)
    pygame.draw.line(surface, (255, 220, 80, 80), (tr.left + 20, tr.bottom + 14), (tr.right - 20, tr.bottom + 14), 1)
    sub = fontes().label(28, bold=True).render("Real-Time Operations Simulator in Space", True, (140, 180, 240))
    surface.blit(sub, sub.get_rect(center=(W // 2, H // 2 + 8)))
    ft_hint  = fontes().display(18, bold=True)
    piscar   = math.sin(t * 3.5) > 0.0
    hint_cor = (255, 215, 40) if piscar else (80, 100, 50)
    hint     = ft_hint.render("—  PRESSIONE  ENTER  PARA  INICIAR  —", True, hint_cor)
    if piscar:
        hg = ft_hint.render("—  PRESSIONE  ENTER  PARA  INICIAR  —", True, (255, 240, 120))
        hg.set_alpha(60)
        surface.blit(hg, hg.get_rect(center=(W // 2 + 1, H // 2 + 78)))
    surface.blit(hint, hint.get_rect(center=(W // 2, H // 2 + 77)))
    ver = fontes().mono(15).render(
        "v1.0  |  SOs de Alta Criticidade |  © 2026", True, (55, 85, 130))
    surface.blit(ver, ver.get_rect(center=(W // 2, H - 32)))
    m, tc, cc, e = 24, 32, (255, 200, 40), 2
    for cx, cy, dx, dy in [(m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)]:
        pygame.draw.line(surface, cc, (cx, cy), (cx + dx * tc, cy), e)
        pygame.draw.line(surface, cc, (cx, cy), (cx, cy + dy * tc), e)


def renderizar_splash_como_textura(surface, display):
    dados  = pygame.image.tostring(surface, "RGB", True)
    w, h   = display
    tex_id = glGenTextures(1)
    glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, tex_id)
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, w, h, 0, GL_RGB, GL_UNSIGNED_BYTE, dados)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glDisable(GL_DEPTH_TEST); glDisable(GL_LIGHTING)
    glMatrixMode(GL_PROJECTION); glPushMatrix(); glLoadIdentity(); glOrtho(-1, 1, -1, 1, -1, 1)
    glMatrixMode(GL_MODELVIEW);  glPushMatrix(); glLoadIdentity()
    glBegin(GL_QUADS)
    glTexCoord2f(0, 0); glVertex2f(-1, -1)
    glTexCoord2f(1, 0); glVertex2f( 1, -1)
    glTexCoord2f(1, 1); glVertex2f( 1,  1)
    glTexCoord2f(0, 1); glVertex2f(-1,  1)
    glEnd()
    glDeleteTextures([tex_id])
    glPopMatrix(); glMatrixMode(GL_PROJECTION); glPopMatrix(); glMatrixMode(GL_MODELVIEW)
    glEnable(GL_DEPTH_TEST); glEnable(GL_LIGHTING)


# ============================================================
# FADE SPLASH → PROGRAMA
# ============================================================

def executar_fade_entrada(display, splash_surface, clock, duracao=0.9):
    t_inicio = time.time()
    while True:
        dt          = clock.tick(FPS_ALVO) / 1000.0
        t_decorrido = time.time() - t_inicio
        progresso   = min(1.0, t_decorrido / duracao)
        alpha       = progresso * progresso * (3 - 2 * progresso)
        for event in pygame.event.get():
            if event.type == QUIT or (event.type == KEYDOWN and event.key == K_ESCAPE):
                pygame.quit(); sys.exit()
        glClear(GL_COLOR_BUFFER_BIT)
        renderizar_splash_como_textura(splash_surface, display)
        desenhar_fade_opengl(alpha, display)
        pygame.display.flip()
        if progresso >= 1.0:
            break


# ============================================================
# LÓGICA DE ATITUDE
# ============================================================

def calcular_atitude_guiagem(t, roll_p, pitch_p, yaw_p, dt):
    alfa = 1.0 - math.exp(-dt * 3.0)
    return (roll_p  + (math.sin(t * 0.15) * 10           - roll_p)  * alfa,
            pitch_p + (-5.0 + math.sin(t * 0.10) * 5     - pitch_p) * alfa,
            yaw_p   + (math.cos(t * 0.08) * 15           - yaw_p)   * alfa)


def calcular_atitude_sim(t, modo_sim, roll_p, pitch_p, yaw_p, dt,
                         maquina, leitor, filtro):
    if modo_sim in ("Guiagem", "Calibração"):
        if filtro.atualizar(leitor, dt):
            return filtro.pitch, filtro.roll, filtro.yaw, True
        if modo_sim == "Guiagem":
            r, p, y = calcular_atitude_guiagem(t, roll_p, pitch_p, yaw_p, dt)
        else:
            alfa = 1.0 - math.exp(-dt * 2.0)
            r = roll_p  + (0.0 - roll_p)  * alfa
            p = pitch_p + (0.0 - pitch_p) * alfa
            y = yaw_p   + (0.0 - yaw_p)   * alfa
        return r, p, y, False

    # ── MODO MISSÃO ──────────────────────────────────────────
    if leitor.conectado and filtro.atualizar(leitor, dt):
        return filtro.pitch, filtro.roll, filtro.yaw, True

    estado = maquina.estado

    # Satélite parado em IDLE, INTRO, AVISO e PERGUNTA
    if estado in (MaquinaMissao.ESTADO_IDLE,
                  MaquinaMissao.ESTADO_INTRO,
                  MaquinaMissao.ESTADO_AVISO,
                  MaquinaMissao.ESTADO_PERGUNTA):
        alfa = 1.0 - math.exp(-dt * 2.0)
        return (roll_p  + (0.0 - roll_p)  * alfa,
                pitch_p + (0.0 - pitch_p) * alfa,
                yaw_p   + (0.0 - yaw_p)   * alfa, False)

    if estado == MaquinaMissao.ESTADO_CHUVA:
        amp = 25.0
        r = max(-55.0, min(55.0, roll_p  + math.sin(t * 3.7 + 0.5) * amp * dt * 8))
        p = max(-55.0, min(55.0, pitch_p + math.cos(t * 2.9 + 1.2) * amp * dt * 8))
        y = yaw_p + math.sin(t * 1.8 + 2.1) * amp * dt * 4
        return r, p, y, False

    if estado == MaquinaMissao.ESTADO_CORRECAO:
        alfa = 1.0 - math.exp(-dt * 1.2)
        r = roll_p  + (0.0 - roll_p)  * alfa; r = 0.0 if abs(r) < 0.1 else r
        p = pitch_p + (0.0 - pitch_p) * alfa; p = 0.0 if abs(p) < 0.1 else p
        y = yaw_p   + (0.0 - yaw_p)   * alfa; y = 0.0 if abs(y) < 0.1 else y
        return r, p, y, False

    r, p, y = calcular_atitude_guiagem(t, roll_p, pitch_p, yaw_p, dt)
    return r, p, y, False


# ============================================================
# MAIN
# ============================================================

def main():
    global _fontes
    pygame.init()
    display = (1560, 960)
    pygame.display.set_mode(display, DOUBLEBUF | OPENGL)
    pygame.display.set_caption("Rtos Simulator v11.1")
    glClearColor(0, 0, 0, 1)

    BASE    = os.path.dirname(os.path.abspath(__file__))
    _fontes = GerenciadorFontes(BASE)

    leitor  = LeitorSerial(porta=SERIAL_PORT, baud=SERIAL_BAUD)
    filtro  = FiltroAngulo()
    maquina = MaquinaMissao()
    _MAPA_FASE = {
        "METEOR_SHOWER": "CHUVA",
        "AWAIT_BUTTON":  "CORRECAO",
        "ORBITAL_FIX":   "CORRECAO",
        "SUCCESS":       "PERGUNTA",
        "IDLE":          "IDLE",
    }

    def _on_estado_serial(estado_str: str):
        python_fase = _MAPA_FASE.get(estado_str)
        if python_fase:
            maquina.forcar_estado(python_fase)

    leitor.set_callback_estado(_on_estado_serial)

    # ── Splash ──────────────────────────────────────────────
    splash_surface = pygame.Surface(display)
    t_start = time.time(); ck = pygame.time.Clock(); splash_ativo = True
    while splash_ativo:
        ck.tick(FPS_ALVO); t_sp = time.time() - t_start
        for event in pygame.event.get():
            if event.type == QUIT or (event.type == KEYDOWN and event.key == K_ESCAPE):
                pygame.quit(); sys.exit()
            elif event.type == KEYDOWN and event.key == K_RETURN:
                splash_ativo = False
        desenhar_splash(splash_surface, t_sp, display)
        glClear(GL_COLOR_BUFFER_BIT)
        renderizar_splash_como_textura(splash_surface, display)
        pygame.display.flip()

    executar_fade_entrada(display, splash_surface.copy(), pygame.time.Clock(), duracao=0.9)

    # ── OpenGL 3D ────────────────────────────────────────────
    glMatrixMode(GL_PROJECTION); glLoadIdentity()
    gluPerspective(45, display[0] / display[1], 0.1, 200.0)
    glMatrixMode(GL_MODELVIEW)
    glEnable(GL_DEPTH_TEST); glDepthFunc(GL_LEQUAL)
    glEnable(GL_CULL_FACE); glCullFace(GL_BACK); glShadeModel(GL_SMOOTH)

    caminho_obj = os.path.join(BASE, "meu_objeto3.obj")
    caminho_png = os.path.join(BASE, "fundo.png")

    print("Carregando modelo...")
    try:
        verts, normais, uvs, faces, tem_uv = carregar_obj(caminho_obj)
    except FileNotFoundError:
        print(f"[ERRO] Modelo não encontrado: {caminho_obj}"); pygame.quit(); sys.exit(1)

    vbo, n_verts = criar_vbo(verts, normais, uvs, faces)
    print(f"  {len(verts)} vértices, {len(faces)} triângulos")

    # ── Textura do modelo ────────────────────────────────────
    tex_modelo = None
    if tem_uv:
        caminho_tex_modelo = localizar_textura_mtl(caminho_obj, BASE)
        if caminho_tex_modelo is None:
            for nome in ["v1_difuse.jpg", "10477_Satellite_v1_Diffuse.jpg",
                         "satellite_diffuse.jpg", "diffuse.jpg", "texture.jpg", "texture.png"]:
                p = os.path.join(BASE, nome)
                if os.path.isfile(p):
                    caminho_tex_modelo = p; break
                try:
                    for entry in os.scandir(BASE):
                        if entry.is_dir():
                            p2 = os.path.join(entry.path, nome)
                            if os.path.isfile(p2):
                                caminho_tex_modelo = p2; break
                except Exception:
                    pass
                if caminho_tex_modelo:
                    break
        if caminho_tex_modelo:
            try:
                tex_modelo = carregar_textura(caminho_tex_modelo)
                print(f"[Textura modelo] Carregada: {os.path.basename(caminho_tex_modelo)}")
            except Exception as e:
                print(f"[Textura modelo] Falha: {e}")
        else:
            print("[Textura modelo] Não encontrada — usando cor sólida.")
    else:
        print("[Textura modelo] OBJ sem UV — usando cor sólida.")

    print("Carregando textura de fundo...")
    try:
        tex_fundo = carregar_textura(caminho_png)
    except FileNotFoundError:
        print(f"[ERRO] Textura não encontrada: {caminho_png}"); pygame.quit(); sys.exit(1)

    configurar_iluminacao()

    camera   = Camera()
    meteoros = Meteoros()
    detritos = Detritos(n=50)

    BARRA_H = 110
    painel  = PainelModos(display)

    _hz_tmp  = paineis_imu_v2.HorizonteArtificial(display=display, raio=90, margem=14, offset_topo=0)
    _imu_tmp = paineis_imu_v2.GraficoIMU3D(display=display, margem=14, offset_topo=0)

    off_hz, off_imu, off_fps = calcular_offset_paineis(
        display, BARRA_H, _hz_tmp._ph, _imu_tmp._ph, FPS_H, gap=10, gap_hz_imu=22)

    horizonte   = paineis_imu_v2.HorizonteArtificial(display=display, raio=90, margem=14, offset_topo=off_hz)
    grafico_imu = paineis_imu_v2.GraficoIMU3D(display=display, margem=14, offset_topo=off_imu)
    margem_dir  = 14
    px_fps      = display[0] - margem_dir

    t0 = time.time(); pausado = False; t_pausado = 0.0; t_pausa = 0.0
    t  = 0.0; fundo_ox = fundo_oy = 0.0
    roll = pitch = yaw = 0.0
    usando_sensor_efetivo = False
    mostrar_ponteiro = False
    clock = pygame.time.Clock()

    _ultimo_modo_sim = painel.modo_sim
    fade_modo_ativo  = False
    fade_modo_alpha  = 0.0
    fade_modo_fase   = "out"
    fade_modo_timer  = 0.0
    fade_entrada_alpha = 1.0
    FADE_ENTRADA_DUR   = 0.55
    fade_entrada_timer = 0.0

    while True:
        dt = clock.tick(FPS_ALVO) / 1000.0; fps = clock.get_fps()

        if fade_entrada_alpha > 0.0:
            fade_entrada_timer += dt
            p_e = min(1.0, fade_entrada_timer / FADE_ENTRADA_DUR)
            fade_entrada_alpha = 1.0 - p_e * p_e * (3 - 2 * p_e)

        for event in pygame.event.get():
            if event.type == QUIT or (event.type == KEYDOWN and event.key == K_ESCAPE):
                pygame.quit(); sys.exit()

            # ── Resposta ao painel de introdução da missão ────────
            if (not fade_modo_ativo and painel.modo_sim == "Missão"
                    and maquina.estado == MaquinaMissao.ESTADO_INTRO
                    and event.type == KEYDOWN):
                if event.key == K_s:
                    maquina.responder_intro(True)
                elif event.key == K_n:
                    maquina.responder_intro(False)
                    roll = pitch = yaw = 0.0

            # ── Resposta ao painel de conclusão da missão ─────────
            if (not fade_modo_ativo and painel.modo_sim == "Missão"
                    and maquina.estado == MaquinaMissao.ESTADO_PERGUNTA
                    and event.type == KEYDOWN):
                if event.key == K_s:
                    maquina.responder_pergunta(True);  roll = pitch = yaw = 0.0
                elif event.key == K_n:
                    maquina.responder_pergunta(False); roll = pitch = yaw = 0.0

            consumido = painel.evento(event) if not fade_modo_ativo else False
            if not consumido:
                if event.type == KEYDOWN:
                    if event.key == K_SPACE:
                        if not pausado: t_pausa = time.time()
                        else:           t_pausado += time.time() - t_pausa
                        pausado = not pausado
                    elif event.key == K_n and maquina.estado not in (
                            MaquinaMissao.ESTADO_PERGUNTA, MaquinaMissao.ESTADO_INTRO):
                        if not fade_modo_ativo:
                            painel.sim_idx = (painel.sim_idx + 1) % len(MODOS_SIM)
                    elif event.key == K_r:
                        camera.azimute = 0; camera.elevacao = 15; camera.distancia = 5
                    elif event.key == K_p:
                        mostrar_ponteiro = not mostrar_ponteiro
                    elif event.key == K_c and painel.modo_sim == "Calibração":
                        filtro.pitch = filtro.roll = filtro.yaw = 0.0
                        roll = pitch = yaw = 0.0
                camera.evento(event)

        if painel.modo_sim != _ultimo_modo_sim and not fade_modo_ativo:
            fade_modo_ativo = True; fade_modo_fase = "out"
            fade_modo_timer = fade_modo_alpha = 0.0

        if fade_modo_ativo:
            fade_modo_timer += dt
            meio = FADE_DURACAO / 2.0
            if fade_modo_fase == "out":
                p_f = min(1.0, fade_modo_timer / meio)
                fade_modo_alpha = p_f * p_f * (3 - 2 * p_f)
                if fade_modo_timer >= meio:
                    _ultimo_modo_sim = painel.modo_sim
                    maquina.reiniciar(); meteoros.limpar()
                    roll = pitch = yaw = 0.0
                    fade_modo_fase = "in"; fade_modo_timer = 0.0
            else:
                p_f = min(1.0, fade_modo_timer / meio)
                fade_modo_alpha = 1.0 - p_f * p_f * (3 - 2 * p_f)
                if fade_modo_timer >= meio:
                    fade_modo_ativo = False; fade_modo_alpha = 0.0

        if not pausado and not (fade_modo_ativo and fade_modo_fase == "out"):
            t = time.time() - t0 - t_pausado

            if painel.modo_sim == "Missão":
                maquina.iniciar()
                # Avança a máquina apenas fora do estado PERGUNTA e INTRO
                if maquina.estado not in (MaquinaMissao.ESTADO_PERGUNTA,
                                          MaquinaMissao.ESTADO_INTRO):
                    maquina.atualizar(dt)

            roll, pitch, yaw, usando_sensor_efetivo = calcular_atitude_sim(
                t, painel.modo_sim, roll, pitch, yaw, dt, maquina, leitor, filtro)

            em_chuva = (painel.modo_sim == "Missão" and maquina.estado == MaquinaMissao.ESTADO_CHUVA)
            if em_chuva:
                meteoros.intensidade = 4.0
                meteoros.atualizar(dt)
                detritos.atualizar(dt, vel_x=-0.15, vel_y=0.05)
            else:
                if meteoros.intensidade != 1.0:
                    meteoros.limpar()
                meteoros.intensidade = 1.0
                if painel.modo_sim == "Guiagem":
                    meteoros.atualizar(dt)
                    detritos.atualizar(dt, vel_x=-0.15, vel_y=0.05)

            fundo_ox = math.sin(t * 0.18) * FUNDO_AMP_X
            fundo_oy = math.cos(t * 0.11) * FUNDO_AMP_Y

        # ── Renderização ──────────────────────────────────────
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT); glLoadIdentity()

        desenhar_fundo(tex_fundo, fundo_ox, fundo_oy)

        em_chuva_render = (painel.modo_sim == "Missão" and maquina.estado == MaquinaMissao.ESTADO_CHUVA)
        if em_chuva_render or painel.modo_sim == "Guiagem":
            meteoros.desenhar()
            detritos.desenhar()

        camera.aplicar()
        glPushMatrix()
        glRotatef(170, 0, 1, 0)
        glRotatef(pitch, 1, 0, 0); glRotatef(roll, 0, 1, 0); glRotatef(yaw, 0, 0, 1)
        glScalef(2.5, 2.5, 2.5)
        if mostrar_ponteiro:
            desenhar_seta_direcao(t_global=t)
        desenhar_modelo_texturizado(vbo, n_verts, tex_modelo, tem_uv)
        glPopMatrix()

        # ── Overlay 2D ────────────────────────────────────────
        overlay_surf = pygame.Surface(display, pygame.SRCALPHA)

        horizonte.desenhar(overlay_surf, pitch=pitch, roll=roll, yaw=yaw,
                           usando_sensor=usando_sensor_efetivo)

        if leitor.conectado and usando_sensor_efetivo:
            ax_g, ay_g, az_g = leitor.ac_x, leitor.ac_y, leitor.ac_z
        else:
            ax_g = math.sin(math.radians(pitch)) * 0.5
            ay_g = math.sin(math.radians(roll))  * 0.5
            az_g = math.cos(math.radians(pitch)) * math.cos(math.radians(roll))

        grafico_imu.desenhar(overlay_surf, ax_g, ay_g, az_g,
                             pitch=pitch, roll=roll, yaw=yaw,
                             usando_sensor=usando_sensor_efetivo)

        desenhar_fps_overlay(overlay_surf, fps, px_fps, off_fps, margem_dir)
        painel.desenhar(overlay_surf, pausado)

        if painel.modo_sim == "Missão":
            desenhar_overlay_missao(overlay_surf, maquina, display)
        if painel.modo_sim == "Calibração":
            desenhar_overlay_calibracao(overlay_surf, leitor, filtro, pitch, roll, yaw, display)
        if mostrar_ponteiro:
            grafico_imu.set_gravando(mostrar_ponteiro)

        glDisable(GL_LIGHTING); glDisable(GL_DEPTH_TEST)
        glMatrixMode(GL_PROJECTION); glPushMatrix(); glLoadIdentity()
        glOrtho(0, display[0], display[1], 0, -1, 1)
        glMatrixMode(GL_MODELVIEW); glPushMatrix(); glLoadIdentity()
        glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glWindowPos2i(0, 0)
        glDrawPixels(display[0], display[1], GL_RGBA, GL_UNSIGNED_BYTE,
                     pygame.image.tostring(overlay_surf, "RGBA", True))
        glDisable(GL_BLEND)
        glPopMatrix(); glMatrixMode(GL_PROJECTION); glPopMatrix(); glMatrixMode(GL_MODELVIEW)
        glEnable(GL_DEPTH_TEST); glEnable(GL_LIGHTING)

        if fade_modo_ativo and fade_modo_alpha > 0.001:
            desenhar_fade_opengl(fade_modo_alpha, display)
        if fade_entrada_alpha > 0.001:
            desenhar_fade_opengl(fade_entrada_alpha, display)

        pygame.display.flip()

if __name__ == "__main__":
    main()
