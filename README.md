# Zephyr DAC80508 Project for Nucleo H753ZI / H743ZI

This project demonstrates how to interface a TI DAC80508 (8-channel, 16-bit DAC) with a Nucleo-H753ZI or Nucleo-H743ZI board using Zephyr RTOS.

## Hardware Connections

### DAC / SPI Connection (SPI1)
| Signal | Nucleo Pin (Arduino) | DAC80508 Pin |
|--------|----------------------|--------------|
| SCK    | PA5 (D13)            | SCLK         |
| MISO   | PA6 (D12)            | SDO          |
| MOSI   | PB5 (D11)            | DIN          |
| CS     | PD10 (D2)            | SYNC         |

### Actuator Current Readback (ADC3)
| DAC Channel | ADC3 Input | Physical Pin |
| :--- | :--- | :--- |
| **CH0** | IN5 | PF3 |
| **CH1** | IN4 | PF5 |
| **CH2** | IN2 | PF9 |
| **CH3** | IN3 | PF7 |
| **CH4** | IN7 | PF8 |
| **CH5** | IN6 | PF10|
| **CH6** | IN0 | PC2 |
| **CH7** | IN1 | PC3 |

**Note:** This project is configured for an **external 3V reference**. It uses a **1/2 divider** for the reference (`DACX0508_REF_EXTERNAL_1_2`) and a **gain of 2** for the output channels, resulting in a full **0–3V** scale.

## UART Serial Console

This project configures a Zephyr shell over the ST-Link virtual COM port natively provided by the Nucleo board's programming USB.

Simply connect the board using the ST-Link USB port, open your serial terminal (e.g., PuTTY or Tera Term), and connect to the STMicroelectronics STLink Virtual COM Port at **115200 baud**.

### Available Commands

- `dac set <ch> <val>`: Set DAC value (0-65535).
- `dac status [<ch>]`: Read actuator current (mA).

## Building and Flashing

```bash
# Build for Nucleo H753ZI
west build -b nucleo_h753zi
# Or build for Nucleo H743ZI
# west build -b nucleo_h743zi

west flash
```
