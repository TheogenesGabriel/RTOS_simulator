/**
 * mpu6050_rtos.c  —  v1.0
 *
 * PROTOCOLO SERIAL (compatível com rocket_visualizer.py v11+):
 *
 *   Ax: <ax> <ay> <az>        acelerômetro em g         (todo ciclo)
 *   Gx: <gx> <gy> <gz>        giroscópio em °/s         (todo ciclo)
 *   An: <pitch> <roll> <yaw>  ângulos filtrados          (todo ciclo)
 *   St: <FASE>                mudança de estado          (ao mudar)
 *
 * FASES enviadas via St::
 *   St: IDLE
 *   St: CALIBRATING
 *   St: METEOR_SHOWER
 *   St: AWAIT_BUTTON
 *   St: ORBITAL_FIX
 *   St: SUCCESS
 *
 * FLUXO DA MISSÃO:
 *   [botão 1] → METEOR_SHOWER  (5 s de perturbação crescente)
 *             → AWAIT_BUTTON   (congela atitude, aguarda operador)
 *   [botão 2] → ORBITAL_FIX   (corrige suavemente para zero)
 *             → SUCCESS        (horizonte centrado)
 *   [botão 3] → reinicia
 */

#include <stdio.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/i2c.h"
#include "hardware/gpio.h"

#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h" // Nâo utiliza semaforo, mas caso queira utilizar o sensor de temperatura do mpu para alguma finalidade é possível implementar a lógica de semaforo para evitar a concorrência

/*
Exemplo do uso de semaforo: 

TaskMPU  ---- usa I2C ----\
                           > MPU6050
TaskTemp ---- usa I2C ----/

Dessa maneira:

Task A quer usar I2C
↓
pega mutex
↓
usa I2C
↓
libera mutex

*/

// ============================================================
// HARDWARE
// ============================================================

#define I2C_PORT              i2c0
#define I2C_SDA               0
#define I2C_SCL               1
#define MPU_ADDR              0x68
#define BOTAO_PIN             5
#define TASK_PERIOD_MS        20          // 50 Hz

// ============================================================
// MPU6050 — registradores
// ============================================================

#define ACCEL_SCALE           16384.0f    // ±2 g
#define GYRO_SCALE            131.0f      // ±250 °/s

#define REG_PWR_MGMT          0x6B
#define REG_ACCEL_CFG         0x1C
#define REG_GYRO_CFG          0x1B
#define REG_DLPF_CFG          0x1A
#define REG_ACCEL_OUT         0x3B
#define REG_TEMP_OUT          0x41
#define REG_GYRO_OUT          0x43

// ============================================================
// FILTRO COMPLEMENTAR
// ============================================================

#define ALPHA                 0.96f
#define DT                    (TASK_PERIOD_MS / 1000.0f)

// ============================================================
// LIMIAR DE ESTABILIDADE (correção concluída quando abaixo)
// ============================================================

#define STABLE_PITCH_MAX      2.0f
#define STABLE_ROLL_MAX       2.0f
#define STABLE_YAW_MAX        2.0f

// ============================================================
// MISSÃO — parâmetros
// ============================================================

#define DURACAO_METEOR_MS     5000        // 5 s de chuva
#define PASSO_CORRECAO        0.25f       // °/ciclo — correção suave
#define DEBOUNCE_MS           60          // debounce do botão

// ============================================================
// ESTADOS
// ============================================================

typedef enum {
    ESTADO_IDLE            = 0,
    ESTADO_CHUVA_METEOROS,
    ESTADO_AGUARDANDO_BOTAO,
    ESTADO_CORRECAO_ORBITAL,
    ESTADO_SUCESSO
} FaseMissao;

// ============================================================
// CALIBRAÇÃO (offsets em raw ADC)
// ============================================================

static int16_t off_ax = 0, off_ay = 0, off_az = 0;
static int16_t off_gx = 0, off_gy = 0, off_gz = 0;

// ============================================================
// SERIAL — protocolo v3
// ============================================================

static inline void serial_accel(float ax, float ay, float az) {
    printf("Ax: %.4f %.4f %.4f\n", ax, ay, az);
}

static inline void serial_gyro(float gx, float gy, float gz) {
    printf("Gx: %.4f %.4f %.4f\n", gx, gy, gz);
}

static inline void serial_angulos(float pitch, float roll, float yaw) {
    printf("An: %.4f %.4f %.4f\n", pitch, roll, yaw);
}

static void serial_estado(FaseMissao fase) {
    const char *tag;

    switch (fase) {
        case ESTADO_CHUVA_METEOROS:   tag = "METEOR_SHOWER"; break;
        case ESTADO_AGUARDANDO_BOTAO: tag = "AWAIT_BUTTON";  break;
        case ESTADO_CORRECAO_ORBITAL: tag = "ORBITAL_FIX";   break;
        case ESTADO_SUCESSO:          tag = "SUCCESS";        break;
        default:                      tag = "IDLE";           break;
    }

    printf("St: %s\n", tag);
}

// ============================================================
// MPU6050 — inicialização
// ============================================================

static void mpu6050_init(void) {
    uint8_t buf[2];

    // Reset completo
    buf[0] = REG_PWR_MGMT; buf[1] = 0x80;
    i2c_write_blocking(I2C_PORT, MPU_ADDR, buf, 2, false);
    sleep_ms(100);

    // Clock: PLL giroscópio eixo X
    buf[0] = REG_PWR_MGMT; buf[1] = 0x01;
    i2c_write_blocking(I2C_PORT, MPU_ADDR, buf, 2, false);
    sleep_ms(10);

    // DLPF: ~21 Hz de corte
    buf[0] = REG_DLPF_CFG; buf[1] = 0x04;
    i2c_write_blocking(I2C_PORT, MPU_ADDR, buf, 2, false);

    // Acelerômetro: ±2 g
    buf[0] = REG_ACCEL_CFG; buf[1] = 0x00;
    i2c_write_blocking(I2C_PORT, MPU_ADDR, buf, 2, false);

    // Giroscópio: ±250 °/s
    buf[0] = REG_GYRO_CFG; buf[1] = 0x00;
    i2c_write_blocking(I2C_PORT, MPU_ADDR, buf, 2, false);
}

// ============================================================
// MPU6050 — leitura bruta
// ============================================================

static void mpu6050_read_raw(int16_t accel[3],
                              int16_t gyro[3],
                              int16_t *temp) {
    uint8_t buf[6];
    uint8_t reg;

    reg = REG_ACCEL_OUT;
    i2c_write_blocking(I2C_PORT, MPU_ADDR, &reg, 1, true);
    i2c_read_blocking (I2C_PORT, MPU_ADDR, buf,  6, false);
    for (int i = 0; i < 3; i++)
        accel[i] = (int16_t)(buf[i*2] << 8 | buf[i*2+1]);

    reg = REG_GYRO_OUT;
    i2c_write_blocking(I2C_PORT, MPU_ADDR, &reg, 1, true);
    i2c_read_blocking (I2C_PORT, MPU_ADDR, buf,  6, false);
    for (int i = 0; i < 3; i++)
        gyro[i] = (int16_t)(buf[i*2] << 8 | buf[i*2+1]);

    reg = REG_TEMP_OUT;
    i2c_write_blocking(I2C_PORT, MPU_ADDR, &reg, 1, true);
    i2c_read_blocking (I2C_PORT, MPU_ADDR, buf,  2, false);
    *temp = (int16_t)(buf[0] << 8 | buf[1]);
}

// ============================================================
// MPU6050 — calibração (sensor plano e parado)
// ============================================================

static void mpu6050_calibrar(void) {
    const int N = 200;
    int32_t sax=0, say=0, saz=0;
    int32_t sgx=0, sgy=0, sgz=0;
    int16_t a[3], g[3], t;

    printf("St: CALIBRATING\n");

    for (int i = 0; i < N; i++) {
        mpu6050_read_raw(a, g, &t);
        sax += a[0]; say += a[1]; saz += a[2];
        sgx += g[0]; sgy += g[1]; sgz += g[2];
        sleep_ms(5);
    }

    off_ax = (int16_t)(sax / N);
    off_ay = (int16_t)(say / N);
    off_az = (int16_t)(saz / N) - (int16_t)ACCEL_SCALE;

    off_gx = (int16_t)(sgx / N);
    off_gy = (int16_t)(sgy / N);
    off_gz = (int16_t)(sgz / N);

    printf("St: IDLE\n");
}

// ============================================================
// BOTÃO — inicialização
// ============================================================

static void botao_init(void) {
    gpio_init(BOTAO_PIN);
    gpio_set_dir(BOTAO_PIN, GPIO_IN);
    gpio_pull_up(BOTAO_PIN);
}

// ============================================================
// UTIL — normaliza yaw para [-180, +180]
// ============================================================

static inline void normalizar_yaw(float *yaw) {
    while (*yaw >  180.0f) *yaw -= 360.0f;
    while (*yaw < -180.0f) *yaw += 360.0f;
}

// ============================================================
// UTIL — move valor em direção a zero por um passo
//        retorna true quando o valor está dentro do limiar
// ============================================================

static bool aproximar_zero(float *valor,
                            float  passo,
                            float  limiar) {
    if (fabsf(*valor) <= limiar) {
        *valor = 0.0f;
        return true;
    }

    if (*valor > 0.0f)
        *valor -= passo;
    else
        *valor += passo;

    return false;
}

// ============================================================
// TASK PRINCIPAL
// ============================================================

void vTaskMPU(void *pvParameters) {

    // Aguarda USB CDC estabilizar antes de qualquer print
    vTaskDelay(pdMS_TO_TICKS(2000));

    // Calibra com sensor plano e parado
    mpu6050_calibrar();

    // ── estado inicial ────────────────────────────────────────
    FaseMissao fase     = ESTADO_IDLE;
    FaseMissao fase_ant = ESTADO_IDLE;
    serial_estado(fase);

    // ── variáveis de sensor ───────────────────────────────────
    int16_t a[3], g[3];
    int16_t temp_raw;

    float ax_g   = 0.0f, ay_g   = 0.0f, az_g   = 0.0f;
    float gx_dps = 0.0f, gy_dps = 0.0f, gz_dps = 0.0f;

    // ── filtro complementar ───────────────────────────────────
    float pitch_f = 0.0f;
    float roll_f  = 0.0f;
    float yaw_f   = 0.0f;

    // ── botão ─────────────────────────────────────────────────
    bool     botao_ant = false;
    bool     clique    = false;
    uint32_t t_botao   = 0;

    // ── timer da chuva ────────────────────────────────────────
    uint32_t t_inicio_chuva = 0;

    // ── loop periódico 50 Hz ──────────────────────────────────
    TickType_t xLastWake = xTaskGetTickCount();

    while (1) {

        // ── 1. Leitura do MPU6050 ────────────────────────────
        mpu6050_read_raw(a, g, &temp_raw);

        ax_g   = (a[0] - off_ax) / ACCEL_SCALE;
        ay_g   = (a[1] - off_ay) / ACCEL_SCALE;
        az_g   = (a[2] - off_az) / ACCEL_SCALE;

        gx_dps = (g[0] - off_gx) / GYRO_SCALE;
        gy_dps = (g[1] - off_gy) / GYRO_SCALE;
        gz_dps = (g[2] - off_gz) / GYRO_SCALE;

        // ── 2. Ângulos do acelerômetro (referência estática) ─
        float pitch_acc = atan2f(-ax_g,
                                  sqrtf(ay_g*ay_g + az_g*az_g))
                          * (180.0f / (float)M_PI);

        float roll_acc  = atan2f(ay_g, az_g)
                          * (180.0f / (float)M_PI);

        // ── 3. Debounce do botão ─────────────────────────────
        bool botao_pressionado = (gpio_get(BOTAO_PIN) == 0);
        clique = false;

        uint32_t agora_ms = xTaskGetTickCount() * portTICK_PERIOD_MS;

        if (botao_pressionado && !botao_ant)
            t_botao = agora_ms;

        if (!botao_pressionado && botao_ant)
            if ((agora_ms - t_botao) >= DEBOUNCE_MS)
                clique = true;

        botao_ant = botao_pressionado;

        // ── 4. Máquina de estados ────────────────────────────
        switch (fase) {

        // ────────────────────────────────────────────────────
        // IDLE — aguarda primeiro clique para iniciar missão
        // ────────────────────────────────────────────────────
        case ESTADO_IDLE:

            // filtro roda normalmente para manter leitura real
            pitch_f = ALPHA * (pitch_f + gx_dps * DT)
                    + (1.0f - ALPHA) * pitch_acc;
            roll_f  = ALPHA * (roll_f  + gy_dps * DT)
                    + (1.0f - ALPHA) * roll_acc;
            yaw_f  += gz_dps * DT;
            normalizar_yaw(&yaw_f);

            if (clique) {
                fase           = ESTADO_CHUVA_METEOROS;
                t_inicio_chuva = agora_ms;
            }

            break;

        // ────────────────────────────────────────────────────
        // CHUVA DE METEOROS — filtro + perturbação crescente
        // A perturbação é injetada nos ângulos filtrados para
        // que o horizonte no Python fique visivelmente bagunçado
        // independente de o sensor se mover fisicamente.
        // ────────────────────────────────────────────────────
        case ESTADO_CHUVA_METEOROS: {

            // filtro complementar roda normalmente
            pitch_f = ALPHA * (pitch_f + gx_dps * DT)
                    + (1.0f - ALPHA) * pitch_acc;
            roll_f  = ALPHA * (roll_f  + gy_dps * DT)
                    + (1.0f - ALPHA) * roll_acc;
            yaw_f  += gz_dps * DT;
            normalizar_yaw(&yaw_f);

            // perturbação simulada com amplitude crescente
            float elapsed = (float)(agora_ms - t_inicio_chuva)
                            / 1000.0f;

            // amplitude cresce de 0 até 28° nos primeiros 7 s
            float amp = fminf(elapsed * 4.0f, 28.0f);

            pitch_f += sinf(elapsed * 3.7f + 0.5f) * amp * DT;
            roll_f  += cosf(elapsed * 2.9f + 1.2f) * amp * DT;
            yaw_f   += sinf(elapsed * 1.8f + 2.1f) * amp * 0.5f * DT;

            // clamp — evita valores absurdos
            if (pitch_f >  55.0f) pitch_f =  55.0f;
            if (pitch_f < -55.0f) pitch_f = -55.0f;
            if (roll_f  >  55.0f) roll_f  =  55.0f;
            if (roll_f  < -55.0f) roll_f  = -55.0f;
            normalizar_yaw(&yaw_f);

            // transição após DURACAO_METEOR_MS
            if ((agora_ms - t_inicio_chuva) >= DURACAO_METEOR_MS)
                fase = ESTADO_AGUARDANDO_BOTAO;

            break;
        }

        // ────────────────────────────────────────────────────
        // AGUARDANDO BOTÃO — atitude congelada
        // O filtro NÃO roda: evita que deriva do giroscópio
        // mude os ângulos enquanto o operador decide agir.
        // ────────────────────────────────────────────────────
        case ESTADO_AGUARDANDO_BOTAO:

            if (clique)
                fase = ESTADO_CORRECAO_ORBITAL;

            break;

        // ────────────────────────────────────────────────────
        // CORREÇÃO ORBITAL — aproxima pitch/roll/yaw de zero
        // O filtro NÃO roda aqui: apenas aproximar_zero() age.
        // Isso garante que o horizonte volte ao centro mesmo
        // que o sensor físico esteja inclinado.
        // ────────────────────────────────────────────────────
        case ESTADO_CORRECAO_ORBITAL: {

            bool ok_p = aproximar_zero(&pitch_f,
                                        PASSO_CORRECAO,
                                        STABLE_PITCH_MAX);
            bool ok_r = aproximar_zero(&roll_f,
                                        PASSO_CORRECAO,
                                        STABLE_ROLL_MAX);
            bool ok_y = aproximar_zero(&yaw_f,
                                        PASSO_CORRECAO,
                                        STABLE_YAW_MAX);

            if (ok_p && ok_r && ok_y) {
                // zera por completo — horizonte perfeitamente centrado
                pitch_f = 0.0f;
                roll_f  = 0.0f;
                yaw_f   = 0.0f;
                fase    = ESTADO_SUCESSO;
            }

            break;
        }

        // ────────────────────────────────────────────────────
        // SUCESSO — missão concluída, aguarda reinício
        // ────────────────────────────────────────────────────
        case ESTADO_SUCESSO:

            if (clique) {
                pitch_f        = 0.0f;
                roll_f         = 0.0f;
                yaw_f          = 0.0f;
                fase           = ESTADO_CHUVA_METEOROS;
                t_inicio_chuva = agora_ms;
            }

            break;

        default:
            break;
        }

        // ── 5. Envia mudança de estado ───────────────────────
        if (fase != fase_ant) {
            serial_estado(fase);
            fase_ant = fase;
        }

        // ── 6. Telemetria a cada ciclo (50 Hz) ───────────────
        serial_accel(ax_g, ay_g, az_g);
        serial_gyro(gx_dps, gy_dps, gz_dps);
        serial_angulos(pitch_f, roll_f, yaw_f);

        // ── 7. Aguarda próximo período ────────────────────────
        vTaskDelayUntil(&xLastWake, pdMS_TO_TICKS(TASK_PERIOD_MS));
    }
}

// ============================================================
// MAIN
// ============================================================

int main(void) {

    stdio_init_all();
    sleep_ms(2000);

    // I2C a 400 kHz
    i2c_init(I2C_PORT, 400 * 1000);
    gpio_set_function(I2C_SDA, GPIO_FUNC_I2C);
    gpio_set_function(I2C_SCL, GPIO_FUNC_I2C);
    gpio_pull_up(I2C_SDA);
    gpio_pull_up(I2C_SCL);

    botao_init();
    mpu6050_init();

    // Cria a tarefa principal "TaskMPU".
    xTaskCreate(
        vTaskMPU,
        "TaskMPU",
        configMINIMAL_STACK_SIZE * 6,
        NULL,
        tskIDLE_PRIORITY + 1,
        NULL
    );

    // inicia o escalonador do sistema RTOS
    vTaskStartScheduler();    
    //Entrega o controle ao FreeRTOS, o kernel vai decidir a próxima tarefa a ser executada


    // não entra aqui, quem controla a CPU é scheduler.
    while (1) tight_loop_contents();

    return 0;
}
