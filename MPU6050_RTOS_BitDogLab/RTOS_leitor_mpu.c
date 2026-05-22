#include <stdio.h>
#include <math.h>
#include "pico/stdlib.h"
#include "pico/binary_info.h"
#include "hardware/i2c.h"
#include "FreeRTOS.h"
#include "task.h"

#define I2C_PORT i2c0
#define I2C_SDA  0
#define I2C_SCL  1

static int addr = 0x68;

static void mpu6050_reset() {
    uint8_t buf[] = {0x6B, 0x80};
    i2c_write_blocking(I2C_PORT, addr, buf, 2, false);
    sleep_ms(100);
    buf[1] = 0x00;
    i2c_write_blocking(I2C_PORT, addr, buf, 2, false);
    sleep_ms(10);
}

static void mpu6050_read_raw(int16_t accel[3], int16_t gyro[3], int16_t *temp) {
    uint8_t buffer[6];

    uint8_t val = 0x3B;
    i2c_write_blocking(I2C_PORT, addr, &val, 1, true);
    i2c_read_blocking(I2C_PORT, addr, buffer, 6, false);
    for (int i = 0; i < 3; i++)
        accel[i] = (buffer[i * 2] << 8 | buffer[(i * 2) + 1]);

    val = 0x43;
    i2c_write_blocking(I2C_PORT, addr, &val, 1, true);
    i2c_read_blocking(I2C_PORT, addr, buffer, 6, false);
    for (int i = 0; i < 3; i++)
        gyro[i] = (buffer[i * 2] << 8 | buffer[(i * 2) + 1]);

    val = 0x41;
    i2c_write_blocking(I2C_PORT, addr, &val, 1, true);
    i2c_read_blocking(I2C_PORT, addr, buffer, 2, false);
    *temp = buffer[0] << 8 | buffer[1];
}

void vTaskMPU(void *pvParameters) {
    vTaskDelay(pdMS_TO_TICKS(3000)); // aguarda USB serial estabilizar

    int16_t accel[3], gyro[3], temp;

    while (true) {
        mpu6050_read_raw(accel, gyro, &temp);

        float ax = accel[0] / 16384.0f;
        float ay = accel[1] / 16384.0f;
        float az = accel[2] / 16384.0f;

        float roll   = atan2(ay, az)                   * 180.0f / M_PI;
        float pitch  = atan2(-ax, sqrt(ay*ay + az*az)) * 180.0f / M_PI;
        float temp_c = (temp / 340.0f) + 18.0f;

        printf("Acel  -> X: %6.3f g  Y: %6.3f g  Z: %6.3f g\n", ax, ay, az);
        printf("Gyro  -> X: %6d     Y: %6d     Z: %6d\n", gyro[0], gyro[1], gyro[2]);
        printf("Pitch: %6.2f      Roll: %6.2f\n", pitch, roll);
        printf("Temp : %.2f C\n\n", temp_c);

        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

int main() {
    stdio_init_all();

    i2c_init(I2C_PORT, 400 * 1000);
    gpio_set_function(I2C_SDA, GPIO_FUNC_I2C);
    gpio_set_function(I2C_SCL, GPIO_FUNC_I2C);
    gpio_pull_up(I2C_SDA);
    gpio_pull_up(I2C_SCL);
    bi_decl(bi_2pins_with_func(I2C_SDA, I2C_SCL, GPIO_FUNC_I2C));

    mpu6050_reset();

    xTaskCreate(vTaskMPU, "Task MPU6050", configMINIMAL_STACK_SIZE + 256, NULL, 1, NULL);

    vTaskStartScheduler();
    panic_unsupported();

    return 0;
}