Com base nos arquivos que você compartilhou (o código C para o MPU6050 com FreeRTOS e o simulador Python com OpenGL), vou criar um README completo e bem estruturado para o projeto.

---

# 🚀 RTOS Simulator - Simulador de Atitude de Satélite com MPU6050 e FreeRTOS

![Python](https://img.shields.io/badge/Python-80.1%25-blue)
![C](https://img.shields.io/badge/C-19.1%25-green)
![FreeRTOS](https://img.shields.io/badge/FreeRTOS-Embedded-red)
![OpenGL](https://img.shields.io/badge/OpenGL-3D-orange)

## 📋 Visão Geral

O **RTOS Simulator** é um projeto de visualização 3D em tempo real que simula a atitude de um satélite, utilizando dados reais do sensor **MPU6050** (acelerômetro + giroscópio) ou um comportamento simulado. O sistema possui três modos operacionais:

- 🎮 **Guiagem** — Trajetória guiada com movimentos suaves e partículas.
- 🛰️ **Missão** — Sequência narrativa de 3 fases (Aviso → Chuva de Meteoros → Correção de Órbita → Conclusão).
- 🔧 **Calibração** — Monitoramento detalhado dos dados brutos do sensor e ângulos filtrados.

> ⚡ Para o modo Missão, os ângulos reais do MPU6050 (conectado à placa Raspberry Pi Pico W rodando FreeRTOS) são utilizados dinamicamente.

---

## 🧠 Arquitetura do Projeto

```
RTOS_simulator/
├── simulador_RTOS/
│   ├── RTOS_simulator_v1.py          # Simulador principal (Python/OpenGL)
│   ├── paineis_imu_v2.py             # Painéis 2D (horizonte artificial, gráfico IMU)
│   ├── leitor_serial_v3.py           # Comunicação serial com o MPU6050
│   └── meu_objeto3.obj / .mtl        # Modelo 3D do satélite
│
├── MPU6050_RTOS_BitDogLab/
│   ├── RTOS_leitor_mpu.c             # Firmware FreeRTOS para o sensor MPU6050
│   ├── FreeRTOSConfig.h              # Configuração do FreeRTOS
│   └── CMakeLists.txt
│
└── README.md
```

---

## 🖥️ Simulador Python (Interface 3D)

### Funcionalidades

- **Visualização 3D** do satélite com textura (arquivo `.obj`).
- **Câmera interativa**:
  - Arrastar com botão esquerdo para rotacionar.
  - Roda do mouse para zoom.
- **Painel superior** com seleção dos modos (Guiagem/Missão/Calibração).
- **Horizonte Artificial** e **Gráfico IMU 3D** mostrando atitude em tempo real.
- **Sistema de partículas**:
  - Meteoros (com intensidade variável na tempestade).
  - Detritos orbitais.
- **Transições com fade** entre modos.
- **Tela de splash** animada na inicialização.

### Modo Missão (detalhado)

| Fase | Duração | Comportamento |
|------|---------|----------------|
| **INTRO** | Indefinida (espera usuário) | Painel explicativo com as 3 fases. Pressione `S` para iniciar. |
| **AVISO** | 5 segundos | Satélite parado. Contagem regressiva até a chuva de meteoros. |
| **CHUVA** | 5 segundos | Ângulos oscilam caoticamente (simulados ou reais). Meteoros intensos. |
| **CORREÇÃO** | 10 segundos | Ângulos são suavemente levados a zero. Barra de progresso. |
| **PERGUNTA** | Indefinida | Pergunta se deseja repetir a missão (`S`/`N`). |

> Se o MPU6050 estiver conectado, os ângulos reais substituem a simulação durante toda a missão.

### Controles do Simulador

| Tecla | Ação |
|-------|------|
| `ENTER` | Iniciar (na tela de splash) |
| `ESPAÇO` | Pausar/retomar simulação |
| `N` | Próximo modo (Guiagem → Missão → Calibração) |
| `S` | Responder "Sim" nos diálogos da missão |
| `N` | Responder "Não" nos diálogos da missão |
| `R` | Resetar câmera |
| `P` | Mostrar/esconder ponteiro de direção 3D |
| `C` | Zerar ângulos (modo Calibração) |
| `ESC` | Sair |

---

## 🔌 Firmware Embarcado (Raspberry Pi Pico W + FreeRTOS)

O arquivo `RTOS_leitor_mpu.c` implementa um firmware para a **Raspberry Pi Pico W** que:

1. Inicializa o barramento **I2C** para comunicar com o MPU6050.
2. Realiza o **reset** do sensor.
3. Cria uma **tarefa FreeRTOS** (`vTaskMPU`) que:
   - Aguarda 3 segundos (para estabilização da USB).
   - A cada **500 ms**, lê os dados brutos do acelerômetro, giroscópio e temperatura.
   - Calcula **pitch**, **roll** e **temperatura**.
   - Envia os dados pela **USB serial** no formato esperado pelo `leitor_serial_v3.py`.

### Configurações I2C

| Parâmetro | Valor |
|-----------|-------|
| Pino SDA | GPIO 0 |
| Pino SCL | GPIO 1 |
| Frequência | 400 kHz |
| Endereço MPU6050 | 0x68 |

### Exemplo de saída serial (esperada pelo Python)

```
Acel  -> X:  0.012 g  Y: -0.034 g  Z:  0.998 g
Gyro  -> X:    124     Y:    -56     Z:     23
Pitch:  2.34      Roll: -1.23
Temp : 24.56 C
```

---

## 🧰 Dependências e Instalação

### Simulador Python

```bash
pip install pygame PyOpenGL numpy pyserial
```

### Firmware (para compilar no Pico W)

- SDK do Raspberry Pi Pico
- FreeRTOS Kernel para Pico (ex: [pico-freertos](https://github.com/FreeRTOS/FreeRTOS-Kernel))
- CMake ≥ 3.13

```bash
cd MPU6050_RTOS_BitDogLab/
mkdir build && cd build
cmake ..
make
```

O arquivo `.uf2` gerado deve ser copiado para o Pico W.

---

## ▶️ Como Executar

1. **Conecte o MPU6050** ao Raspberry Pi Pico W conforme os pinos acima.
2. **Carregue o firmware** no Pico W.
3. **Conecte o Pico W ao computador** via USB (a porta serial será, por exemplo, `COM6` no Windows ou `/dev/ttyACM0` no Linux).
4. **Execute o simulador**:
   ```bash
   python RTOS_simulator_v1.py
   ```
5. Na tela de splash, pressione `ENTER`.
6. Selecione o modo desejado e interaja.

> ⚠️ Verifique no código `RTOS_simulator_v1.py` a variável `SERIAL_PORT` (padrão `COM6`) e ajuste para sua porta.

---

## 🧪 Testes e Validação

| Componente | Teste | Status |
|------------|-------|--------|
| Leitor serial | Conectividade com Pico W | ✅ OK |
| Filtro complementar | Estabilidade dos ângulos | ✅ OK |
| Modo Missão | Sequência correta das fases | ✅ OK |
| Modelo 3D | Carregamento .obj + textura | ✅ OK |
| Partículas | Meteoros e detritos | ✅ OK |
| Transições fade | Suave entre modos | ✅ OK |

---

## 📁 Estrutura de Arquivos Esperada

```
RTOS_simulator/
│
├── RTOS_simulator_v1.py
├── paineis_imu_v2.py
├── leitor_serial_v3.py
├── meu_objeto3.obj
├── meu_objeto3.mtl
├── v1_difuse.jpg               (ou qualquer textura .jpg/.png)
├── fundo.png                   (textura do fundo estrelado)
└── fontes/                     (opcional: Orbitron.ttf, Rajdhani.ttf, etc.)
```

> Se as fontes não forem encontradas, o sistema usa fallbacks internos do sistema.

---

## 🤝 Contribuição

Contribuições são bem-vindas! Sugestões:

- Adicionar log de dados do IMU para CSV.
- Implementar modo de replay da missão.
- Suporte a outros sensores (BNO055, etc.).
- Melhorar o modelo 3D com animações de painel solar.

---

## 📜 Licença

Este projeto está sob a licença **MIT**. Você é livre para usar, modificar e distribuir, desde que mantenha os créditos originais.

---

## 👨‍🚀 Autor

**Theógenes Gabriel**  
GitHub: [@TheogenesGabriel](https://github.com/TheogenesGabriel)

Projeto desenvolvido para disciplina de **Sistemas Operacionais de Tempo Real** com foco em aplicações aeroespaciais.

---

## 🌟 Agradecimentos

- FreeRTOS.org pela documentação e kernel.
- Pygame/OpenGL community.
- InvenSense pelo MPU6050.

---

**Bom voo orbital! 🛰️**
