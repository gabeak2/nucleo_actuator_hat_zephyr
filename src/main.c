#include <math.h>
#include <soc.h>
#include <stdlib.h>
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/dac.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/shell/shell.h>

LOG_MODULE_REGISTER(main, LOG_LEVEL_INF);

/* DAC Device Configuration */
#define DAC_NODE DT_NODELABEL(dac0)
#if !DT_NODE_EXISTS(DAC_NODE)
#error "Devicetree node dac0 does not exist"
#endif
static const struct device *dac_dev = DEVICE_DT_GET(DAC_NODE);

/* ADC Device Configuration */
#define ADC_NODE DT_NODELABEL(adc3)
#if !DT_NODE_EXISTS(ADC_NODE)
#error "Devicetree node adc3 does not exist"
#endif
static const struct device *adc_dev = DEVICE_DT_GET(ADC_NODE);

#define ADC_RESOLUTION 12
#define ADC_GAIN ADC_GAIN_1
#define ADC_REFERENCE ADC_REF_INTERNAL
#define ADC_ACQUISITION_TIME ADC_ACQ_TIME_DEFAULT

/*
 * Mapping from DAC channel to ADC3 channel as per user request:
 * DAC ch0 -> ADC3_IN5
 * DAC ch1 -> ADC3_IN4
 * DAC ch2 -> ADC3_IN2
 * DAC ch3 -> ADC3_IN3
 * DAC ch4 -> ADC3_IN7
 * DAC ch5 -> ADC3_IN6
 * DAC ch6 -> ADC3_IN0
 * DAC ch7 -> ADC3_IN1
 */
static const uint8_t dac_to_adc_map[8] = {5, 4, 2, 3, 7, 6, 0, 1};

/* Helper for ADC setup */
static int setup_adc_channel(uint8_t channel_id) {
  struct adc_channel_cfg cfg = {
      .gain = ADC_GAIN,
      .reference = ADC_REFERENCE,
      .acquisition_time = ADC_ACQUISITION_TIME,
      .channel_id = channel_id,
  };
  return adc_channel_setup(adc_dev, &cfg);
}

/* Helper to read averaged current in mA */
/* Formula: I = (V - 1.5) / 10. V is in volts.
 * For mA: I_mA = (V_mV - 1500) / 10
 */
static int get_current_ma(uint8_t dac_ch, float *current_ma) {
  int ret;
  uint8_t adc_ch = dac_to_adc_map[dac_ch];
  int64_t sum = 0;

#define SAMPLES 100
  int16_t sample_buffer[SAMPLES];

  struct adc_sequence_options options = {
      .extra_samplings = SAMPLES - 1,
      .interval_us = 0, /* Read as fast as possible */
  };

  struct adc_sequence sequence = {
      .options = &options,
      .channels = BIT(adc_ch),
      .buffer = sample_buffer,
      .buffer_size = sizeof(sample_buffer),
      .resolution = ADC_RESOLUTION,
  };

  ret = adc_read(adc_dev, &sequence);
  if (ret < 0) {
    return ret;
  }

  for (int i = 0; i < SAMPLES; i++) {
    sum += sample_buffer[i];
  }

  float avg_raw = (float)sum / (float)SAMPLES;
  /* Assume 3300mV reference for Nucleo H753ZI */
  float v_mv = (avg_raw * 3300.0f) / ((1 << ADC_RESOLUTION) - 1);
  *current_ma = (v_mv - 1500.0f) / 10.0f;

  return 0;
}

/* Shell Command: dac set <channel> <current_ma> */
static int cmd_dac_set(const struct shell *sh, size_t argc, char **argv) {
  uint32_t channel = strtoul(argv[1], NULL, 10);
  float current_ma = strtof(argv[2], NULL);
  int ret;

  if (channel > 7) {
    shell_error(sh, "Invalid channel: 0-7 allowed");
    return -EINVAL;
  }

  /* 1.5V = 0mA, 10mV/mA */
  float v_mv = 1500.0f + (current_ma * 10.0f);

  /* Clamp voltage to 0-3000mV range */
  if (v_mv < 0.0f) {
    v_mv = 0.0f;
  } else if (v_mv > 3000.0f) {
    v_mv = 3000.0f;
  }

  /* Convert to DAC counts: 3000mV = 65535 */
  uint32_t value = (uint32_t)((v_mv / 3000.0f) * 65535.0f);

  ret = dac_write_value(dac_dev, channel, value);
  if (ret < 0) {
    /* Fallback setup */
    struct dac_channel_cfg cfg = {.channel_id = channel, .resolution = 16};
    dac_channel_setup(dac_dev, &cfg);
    ret = dac_write_value(dac_dev, channel, value);
  }

  if (ret < 0) {
    shell_error(sh, "Failed to write DAC value (err %d)", ret);
    return ret;
  }

  shell_print(sh, "Channel %d set to %.2f mA (DAC count: %u, V: %.2f mV)",
              channel, (double)current_ma, value, (double)v_mv);
  return 0;
}

/* Shell Command: dac status [<channel>] */
static int cmd_dac_status(const struct shell *sh, size_t argc, char **argv) {
  float cur;
  int ret;

  if (argc > 1) {
    uint32_t ch = strtoul(argv[1], NULL, 10);
    if (ch > 7) {
      shell_error(sh, "Invalid channel: 0-7 allowed");
      return -EINVAL;
    }
    ret = get_current_ma(ch, &cur);
    if (ret < 0) {
      shell_error(sh, "ADC read failed (err %d)", ret);
      return ret;
    }
    shell_print(sh, "DAC Channel %d: Current readback = %.2f mA", ch,
                (double)cur);
  } else {
    shell_print(sh, "--- Actuator Status (Current Readback) ---");
    for (int i = 0; i < 8; i++) {
      ret = get_current_ma(i, &cur);
      if (ret < 0) {
        shell_print(sh, "CH %d: Error reading", i);
      } else {
        shell_print(sh, "CH %d: %.2f mA", i, (double)cur);
      }
    }
  }
  return 0;
}
/* Shell Command: dac test sweep <channel> <start_ma> <stop_ma> <step_ma>
 * <delay_ms> */
static int cmd_dac_test_sweep(const struct shell *sh, size_t argc,
                              char **argv) {
  uint32_t channel = strtoul(argv[1], NULL, 10);
  float start_ma = strtof(argv[2], NULL);
  float stop_ma = strtof(argv[3], NULL);
  float step_ma = strtof(argv[4], NULL);
  uint32_t delay_ms = strtoul(argv[5], NULL, 10);
  int ret;

  if (channel > 7) {
    shell_error(sh, "Invalid channel: 0-7 allowed");
    return -EINVAL;
  }

  if (step_ma <= 0.0f) {
    shell_error(sh, "Step must be positive");
    return -EINVAL;
  }

  /* Direction */
  float dir = (stop_ma >= start_ma) ? 1.0f : -1.0f;
  float current = start_ma;

  shell_print(
      sh, "Starting sweep on CH %d: %.2f to %.2f mA, step %.2f mA, delay %u ms",
      channel, (double)start_ma, (double)stop_ma, (double)step_ma, delay_ms);

  while (true) {
    /* Set DAC */
    float v_mv = 1500.0f + (current * 10.0f);
    if (v_mv < 0.0f)
      v_mv = 0.0f;
    else if (v_mv > 3000.0f)
      v_mv = 3000.0f;

    uint32_t value = (uint32_t)((v_mv / 3000.0f) * 65535.0f);

    ret = dac_write_value(dac_dev, channel, value);
    if (ret < 0) {
      struct dac_channel_cfg cfg = {.channel_id = channel, .resolution = 16};
      dac_channel_setup(dac_dev, &cfg);
      ret = dac_write_value(dac_dev, channel, value);
    }
    if (ret < 0) {
      shell_error(sh, "DAC write failed (err %d)", ret);
      return ret;
    }

    /* Wait */
    k_msleep(delay_ms);

    /* Read ADC */
    float measured_ma = 0.0f;
    ret = get_current_ma(channel, &measured_ma);
    if (ret < 0) {
      shell_error(sh, "ADC read failed at %.2f mA setpoint (err %d)",
                  (double)current, ret);
      return ret;
    }

    shell_print(sh, "Setpoint: %.2f mA -> Measured: %.2f mA", (double)current,
                (double)measured_ma);

    /* Check termination */
    if ((dir > 0.0f && current >= stop_ma) ||
        (dir < 0.0f && current <= stop_ma)) {
      break;
    }

    current += (dir * step_ma);
    /* Prevent overshooting */
    if (dir > 0.0f && current > stop_ma)
      current = stop_ma;
    if (dir < 0.0f && current < stop_ma)
      current = stop_ma;
  }

  shell_print(sh, "Sweep complete.");
  return 0;
}

#ifndef M_PI
#define M_PI 3.14159265358979323846f
#endif

/* Helper to convert mA to DAC value (clamped) */
static inline uint32_t current_ma_to_dac_value(float current_ma) {
  float v_mv = 1500.0f + (current_ma * 10.0f);
  if (v_mv < 0.0f) {
    v_mv = 0.0f;
  } else if (v_mv > 3000.0f) {
    v_mv = 3000.0f;
  }
  return (uint32_t)((v_mv / 3000.0f) * 65535.0f);
}

/* Helper for single ADC conversion (driver path, used only to force
 * calibration / clocking of ADC3 during boot sanity checks if ever needed) */
static inline int read_single_adc_raw(uint8_t adc_ch, int16_t *sample) {
  struct adc_sequence sequence = {
      .channels = BIT(adc_ch),
      .buffer = sample,
      .buffer_size = sizeof(*sample),
      .resolution = ADC_RESOLUTION,
  };
  return adc_read(adc_dev, &sequence);
}

#include <stm32h7xx_ll_adc.h>
#include <stm32h7xx_ll_gpio.h>
#include <stm32h7xx_ll_spi.h>

#include <zephyr/drivers/spi.h>

#include <stm32_ll_bus.h>
#include <stm32_ll_gpio.h>

/* ===================== SPI (DAC) fast path ===================== */

/* Prepare SPI hardware ONCE before bare-metal fast-path loop. */
static inline void dac_spi_begin(void) {
  LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_SPI1);

  if (LL_SPI_IsEnabled(SPI1)) {
    LL_SPI_SuspendMasterTransfer(SPI1);
    uint32_t to = 5000;
    while (!(READ_BIT(SPI1->SR, SPI_SR_SUSP)) && --to)
      ;
    LL_SPI_Disable(SPI1);
    to = 5000;
    while (LL_SPI_IsEnabled(SPI1) && --to)
      ;
  }

  SPI1->IFCR = 0xFFFFFFFFUL;

  /* Drain and clear any stale RX/OVR state left over from a previous
   * command before we start driving the bus again. */
  if (LL_SPI_IsActiveFlag_OVR(SPI1)) {
    (void)LL_SPI_ReceiveData8(SPI1);
    LL_SPI_ClearFlag_OVR(SPI1);
  }

  LL_SPI_SetTransferSize(SPI1, 3U);
  LL_SPI_Enable(SPI1);
}

/* Hot path: CS + transmit 3 bytes + wait EOT + drain RX + clear flags + CS.
 * SPI stays enabled during the entire sweep, zero setup/teardown overhead. */
static inline void dac_write_fast(uint8_t dac_ch, uint16_t dac_val) {
  uint8_t b0 = 0x08 + dac_ch;
  uint8_t b1 = (uint8_t)(dac_val >> 8);
  uint8_t b2 = (uint8_t)(dac_val & 0xFF);

  /* Defensive: if a prior call overran (see note below) and somehow left
   * OVR set, clear it now so this transfer isn't affected. */
  if (LL_SPI_IsActiveFlag_OVR(SPI1)) {
    (void)LL_SPI_ReceiveData8(SPI1);
    LL_SPI_ClearFlag_OVR(SPI1);
  }

  LL_GPIO_ResetOutputPin(GPIOA, LL_GPIO_PIN_4);

  LL_SPI_TransmitData8(SPI1, b0);
  LL_SPI_TransmitData8(SPI1, b1);
  LL_SPI_TransmitData8(SPI1, b2);

  LL_SPI_StartMasterTransfer(SPI1);

  uint32_t to = 20000;
  while (!LL_SPI_IsActiveFlag_EOT(SPI1) && --to) {
    /* spin */
  }

  /* CRITICAL: this bus is full-duplex (MISO is wired per the devicetree),
   * so every TX byte clocks in a corresponding RX byte whether we want it
   * or not. If we never read RXDR, the RX FIFO fills after roughly 4-5
   * calls and the peripheral raises OVR, which silently blocks all
   * subsequent transfers -- this was the root cause of "first value
   * works, then nothing toggles". Always drain RX after every transfer. */
  while (LL_SPI_IsActiveFlag_RXP(SPI1)) {
    (void)LL_SPI_ReceiveData8(SPI1);
  }

  LL_SPI_ClearFlag_EOT(SPI1);
  LL_SPI_ClearFlag_TXTF(SPI1);
  if (LL_SPI_IsActiveFlag_OVR(SPI1)) {
    LL_SPI_ClearFlag_OVR(SPI1);
  }

  LL_GPIO_SetOutputPin(GPIOA, LL_GPIO_PIN_4);
}

/* Clean up SPI hardware after bare-metal sweep completes. */
static inline void dac_spi_end(void) {
  if (LL_SPI_IsEnabled(SPI1)) {
    LL_SPI_Disable(SPI1);
    uint32_t to = 5000;
    while (LL_SPI_IsEnabled(SPI1) && --to)
      ;
  }
}

/* ===================== ADC3 (current sense) fast path ===================== */

/* Maps raw STM32 ADC input number (0-7, matching dac_to_adc_map values) to
 * the LL_ADC_CHANNEL_x macro LL expects. Extend if you ever use channels
 * beyond IN7. */
static const uint32_t ll_adc_channel_lut[8] = {
    LL_ADC_CHANNEL_0, LL_ADC_CHANNEL_1, LL_ADC_CHANNEL_2, LL_ADC_CHANNEL_3,
    LL_ADC_CHANNEL_4, LL_ADC_CHANNEL_5, LL_ADC_CHANNEL_6, LL_ADC_CHANNEL_7,
};

static void adc3_prepare_channel(uint8_t adc_ch) {
  __ASSERT_NO_MSG(adc_ch < 8);

  /* Enable internal voltage regulator if not enabled and wait for stabilization
   */
  if (!(ADC3->CR & ADC_CR_ADVREGEN)) {
    ADC3->CR |= ADC_CR_ADVREGEN;
  }
  k_busy_wait(50); /* 50 us startup delay for ADVREGEN */

  /* Pre-select only the active ADC channel pin in PCSEL register (CRITICAL on
   * STM32H7!) */
  ADC3->PCSEL = BIT(adc_ch);

  /* Make sure ADC3 is disabled before reconfiguring calibration/sequencer */
  if (LL_ADC_IsEnabled(ADC3)) {
    LL_ADC_REG_StopConversion(ADC3);
    uint32_t to = 100000;
    while (LL_ADC_REG_IsStopConversionOngoing(ADC3) && --to)
      ;
    LL_ADC_Disable(ADC3);
    to = 100000;
    while (LL_ADC_IsEnabled(ADC3) && --to)
      ;
  }

  /* Calibrate ADC3 with generous timeout (~100 ms) */
  LL_ADC_StartCalibration(ADC3, LL_ADC_CALIB_OFFSET_LINEARITY,
                          LL_ADC_SINGLE_ENDED);
  uint32_t to = 5000000;
  while (LL_ADC_IsCalibrationOnGoing(ADC3) && --to)
    ;
  if (to == 0) {
    printk("ADC ERROR: Calibration timed out!\n");
  }

  /* Regular sequencer: single conversion, rank 1 = our channel. */
  LL_ADC_REG_SetSequencerLength(ADC3, LL_ADC_REG_SEQ_SCAN_DISABLE);
  LL_ADC_REG_SetSequencerRanks(ADC3, LL_ADC_REG_RANK_1,
                               ll_adc_channel_lut[adc_ch]);
  LL_ADC_REG_SetTriggerSource(ADC3, LL_ADC_REG_TRIG_SOFTWARE);

  /* Give the channel a real sampling time -- long enough for the current
   * sense signal to settle, short enough to not eat the whole budget. */
  LL_ADC_SetChannelSamplingTime(ADC3, ll_adc_channel_lut[adc_ch],
                                LL_ADC_SAMPLINGTIME_8CYCLES_5);

  /* Force plain DR (no DMA) transfer mode directly via CFGR */
  MODIFY_REG(ADC3->CFGR, ADC_CFGR_DMNGT, 0);

  /* Force single-conversion mode explicitly -- if CONT was left set, the ADC
   * would free-run and retrigger conversions continuously on its own, racing
   * against explicit StartConversion() calls in the sample loop. */
  LL_ADC_REG_SetContinuousMode(ADC3, LL_ADC_REG_CONV_SINGLE);
  LL_ADC_REG_SetOverrun(ADC3, LL_ADC_REG_OVR_DATA_OVERWRITTEN);
  LL_ADC_SetResolution(ADC3, LL_ADC_RESOLUTION_12B);

  /* Enable and wait for ADC ready. */
  LL_ADC_ClearFlag_ADRDY(ADC3);
  LL_ADC_Enable(ADC3);
  to = 5000000;
  while (!LL_ADC_IsActiveFlag_ADRDY(ADC3) && --to)
    ;
  if (to == 0) {
    printk("ADC ERROR: ADRDY timed out!\n");
  }
  LL_ADC_ClearFlag_ADRDY(ADC3);
}

static inline void adc3_shutdown(void) {
  if (LL_ADC_IsEnabled(ADC3)) {
    LL_ADC_REG_StopConversion(ADC3);
    uint32_t to = 5000;
    while (LL_ADC_REG_IsStopConversionOngoing(ADC3) && --to)
      ;
    LL_ADC_Disable(ADC3);
  }
}

/* Direct Fast-Path ADC3 read. Assumes adc3_prepare_channel() has already
 * enabled the ADC and selected the channel. */
static inline uint16_t adc_read_fast(void) {
  ADC3->ISR = ADC_ISR_EOC | ADC_ISR_EOS | ADC_ISR_OVR | ADC_ISR_EOSMP;
  LL_ADC_REG_StartConversion(ADC3);
  uint32_t to = 20000;
  while (!LL_ADC_IsActiveFlag_EOC(ADC3) && --to)
    ;
  return (uint16_t)LL_ADC_REG_ReadConversionData12(ADC3);
}

/* Run Lock-In FRA measurement for a single frequency */
static int run_fra_single_freq(uint8_t dac_ch, uint8_t adc_ch, float dc_ma,
                               float amp_ma, float freq_hz, int n_meas,
                               int n_settle, float *gain_db, float *phase_deg) {
  /* Points per cycle: 32 gives smoother waveform fidelity/less ZOH artifact
   * at low frequencies where there is plenty of timing headroom. Above
   * ~2kHz the required sample period shrinks enough that the fixed
   * per-sample cost (SPI transfer + ADC conversion + bookkeeping, roughly
   * 3.7-3.9us measured on this hardware) no longer fits at P=32, causing
   * every sample to overrun above ~8kHz. Dropping to P=16 above 2kHz halves
   * the required sample rate and restores headroom through 10kHz. Fewer
   * points per cycle doesn't hurt the lock-in result -- the demodulation
   * only cares about correlating against the fundamental sin/cos, and 16
   * points/cycle is still comfortably above Nyquist for that. */
  int P = 32;
  if (freq_hz > 8000.0f) {
    P = 16;
  }

  if (n_meas < 1) {
    n_meas = 1;
  }
  if (n_settle < 0) {
    n_settle = 0;
  }

  /* Pre-compute 1-cycle DAC values and sin/cos reference tables */
  uint16_t dac_lut[32];
  float sin_lut[32];
  float cos_lut[32];

  for (int p = 0; p < P; p++) {
    float theta = (2.0f * (float)M_PI * (float)p) / (float)P;
    sin_lut[p] = sinf(theta);
    cos_lut[p] = cosf(theta);
    float i_val = dc_ma + amp_ma * sin_lut[p];
    dac_lut[p] = (uint16_t)current_ma_to_dac_value(i_val);
  }

  uint32_t dt_cycles =
      (uint32_t)(sys_clock_hw_cycles_per_sec() / (freq_hz * (float)P));
  if (dt_cycles == 0) {
    dt_cycles = 1;
  }

  int total_samples = (n_settle + n_meas) * P;
  int meas_start_sample = n_settle * P;
  int total_meas_samples = n_meas * P;

  double sum_i = 0.0;
  double sum_q = 0.0;
  uint32_t overrun_count = 0;
  uint16_t min_adc = 65535, max_adc = 0;

  /* Enable ARM Cortex-M7 DWT Hardware Cycle Counter (480MHz continuous
   * UP-counter) */
  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

  uint32_t start_cycle = DWT->CYCCNT;

  /* Lock the scheduler for the duration of the timing-critical sample loop.
   * Without this, Zephyr's deferred logging/shell backend thread can wake
   * up mid-measurement to drain a backlog of shell_print() output (this is
   * exactly what "N messages dropped" warnings indicate) and preempt this
   * thread for long enough to blow a sample deadline -- especially at
   * higher frequencies where dt_cycles shrinks to just a few microseconds
   * and any preemption at all causes an overrun. k_sched_lock() prevents
   * any other thread from running until we unlock, while still allowing
   * hardware interrupts (SysTick, UART RX) to fire normally. */
  k_sched_lock();

  for (int step = 0; step < total_samples; step++) {
    int p = step % P;

    /* Fast Direct DAC write over 50MHz SPI (~0.8 us) */
    dac_write_fast(dac_ch, dac_lut[p]);

    /* Short 1us settling delay for DAC & analog circuitry before sampling ADC
     */
    // uint32_t settle_offset = (dt_cycles > 1920) ? 480 : (dt_cycles / 4);
    // uint32_t sample_cycle =
    //     start_cycle + (uint32_t)(((uint64_t)step * dt_cycles) +
    //     settle_offset);
    // while ((int32_t)(DWT->CYCCNT - sample_cycle) < 0) {
    //   /* Spin-wait */
    // }

    /* Fast Direct ADC conversion (~0.3 us) */
    uint16_t raw_sample = adc_read_fast();

    if (raw_sample < min_adc)
      min_adc = raw_sample;
    if (raw_sample > max_adc)
      max_adc = raw_sample;

    bool is_meas = (step >= meas_start_sample);

    if (is_meas) {
      float v_mv = ((float)raw_sample * 3300.0f) / ((1 << ADC_RESOLUTION) - 1);
      float i_meas = (v_mv - 1500.0f) / 10.0f;
      sum_i += (double)(i_meas * sin_lut[p]);
      sum_q += (double)(i_meas * cos_lut[p]);
    }

    /* Absolute timestamp for next sample step */
    uint32_t target_cycle =
        start_cycle + (uint32_t)(((uint64_t)(step + 1) * dt_cycles));
    if (DWT->CYCCNT > target_cycle &&
        (DWT->CYCCNT - target_cycle) < 0x7FFFFFFFUL) {
      overrun_count++;
    }
    while ((DWT->CYCCNT < target_cycle) &&
           (target_cycle - DWT->CYCCNT) < 0x7FFFFFFFUL) {
      /* Spin-wait for sub-microsecond precision */
    }
  }

  k_sched_unlock();

  if (overrun_count > 0) {
    printk("FRA %.1f Hz: %u timing overruns!\n", (double)freq_hz,
           overrun_count);
  }

  /* Compute In-phase (I) and Quadrature (Q) components */
  double I = (2.0 * sum_i) / (double)total_meas_samples;
  double Q = (2.0 * sum_q) / (double)total_meas_samples;

  double measured_amp = sqrt(I * I + Q * Q);
  if (measured_amp < 1e-9) {
    measured_amp = 1e-9;
  }

  *gain_db = (float)(20.0 * log10(measured_amp / (double)amp_ma));
  *phase_deg = (float)(atan2(Q, I) * (180.0 / (double)M_PI));

  return 0;
}

/* Shell Command: dac test fra <channel> <dc_ma> <amp_ma> <f_start_hz>
 * <f_stop_hz> <ppd> [n_meas] [n_settle] */
static int cmd_dac_test_fra(const struct shell *sh, size_t argc, char **argv) {
  uint32_t channel = strtoul(argv[1], NULL, 10);
  float dc_ma = strtof(argv[2], NULL);
  float amp_ma = strtof(argv[3], NULL);
  float f_start = strtof(argv[4], NULL);
  float f_stop = strtof(argv[5], NULL);
  float ppd = strtof(argv[6], NULL);

  int n_meas = 50;   /* Default 50 measurement cycles */
  int n_settle = 10; /* Default 10 settling cycles */

  if (argc >= 8) {
    n_meas = (int)strtol(argv[7], NULL, 10);
    if (n_meas < 1) {
      n_meas = 1;
    }
  }
  if (argc >= 9) {
    n_settle = (int)strtol(argv[8], NULL, 10);
    if (n_settle < 0) {
      n_settle = 0;
    }
  }

  if (channel > 7) {
    shell_error(sh, "Invalid channel: 0-7 allowed");
    return -EINVAL;
  }

  if (amp_ma <= 0.0f) {
    shell_error(sh, "Amplitude must be > 0 mA");
    return -EINVAL;
  }

  if (f_start <= 0.0f || f_stop < f_start) {
    shell_error(sh, "Invalid frequency range");
    return -EINVAL;
  }

  if (ppd <= 0.0f) {
    shell_error(sh, "Points per decade must be > 0");
    return -EINVAL;
  }

  uint8_t adc_ch = dac_to_adc_map[channel];

  shell_print(sh,
              "Starting FRA Sweep on CH %d: DC=%.2fmA, Amp=%.2fmA, "
              "%.1fHz-%.1fHz, PPD=%.1f, Cycles=%d (settle=%d)",
              channel, (double)dc_ma, (double)amp_ma, (double)f_start,
              (double)f_stop, (double)ppd, n_meas, n_settle);
  shell_print(sh, "freq_hz,gain_db,phase_deg");

  /* Prime DAC via OS driver ONCE so GPIO alt-function/clocks are set up
   * before we take over SPI1 with the bare-metal fast path. */
  dac_write_value(dac_dev, channel, current_ma_to_dac_value(dc_ma));

  /* Prepare SPI peripheral ONCE for the entire sweep */
  dac_spi_begin();

  /* Prepare ADC3 ONCE for the entire sweep */
  adc3_prepare_channel(adc_ch);

  float log_start = log10f(f_start);
  float log_stop = log10f(f_stop);
  float log_step = 1.0f / ppd;

  for (float log_f = log_start; log_f <= log_stop + 1e-4f; log_f += log_step) {
    float f = powf(10.0f, log_f);
    if (f > f_stop) {
      f = f_stop;
    }

    float gain_db = 0.0f;
    float phase_deg = 0.0f;

    int ret = run_fra_single_freq((uint8_t)channel, adc_ch, dc_ma, amp_ma, f,
                                  n_meas, n_settle, &gain_db, &phase_deg);
    if (ret < 0) {
      shell_error(sh, "FRA measurement error at %.2f Hz (err %d)", (double)f,
                  ret);
      break;
    }

    shell_print(sh, "%.2f,%.3f,%.2f", (double)f, (double)gain_db,
                (double)phase_deg);
  }

  /* Restore DAC channel back to baseline DC value */
  dac_write_fast(channel, (uint16_t)current_ma_to_dac_value(dc_ma));
  dac_spi_end();
  adc3_shutdown();

  shell_print(sh, "Sweep Complete");
  return 0;
}

/* Shell Command: dac test fastwrite <ch> <val_low> <val_high>
 * Alternates bare-metal dac_write_fast between two raw 16-bit values every
 * second. Use this to verify the fast SPI path actually updates the DAC output.
 * Press any key to stop. */
static int cmd_dac_test_fastwrite(const struct shell *sh, size_t argc,
                                  char **argv) {
  uint8_t ch = (uint8_t)strtoul(argv[1], NULL, 10);
  uint16_t val_lo = (uint16_t)strtoul(argv[2], NULL, 10);
  uint16_t val_hi = (uint16_t)strtoul(argv[3], NULL, 10);

  /* Prime SPI via OS driver once */
  dac_write_value(dac_dev, ch, val_lo);
  dac_spi_begin();

  shell_print(sh, "Toggling CH%d: %u <-> %u (fast path). Ctrl+C to stop.", ch,
              val_lo, val_hi);

  for (int i = 0; i < 20; i++) {
    uint16_t v = (i & 1) ? val_hi : val_lo;
    dac_write_fast(ch, v);
    shell_print(sh, "  step %d: wrote %u", i, v);
    k_sleep(K_MSEC(1000));
  }

  dac_spi_end();

  shell_print(sh, "Done.");
  return 0;
}

/* Shell Command: dac test adcraw <ch>
 * Sanity check: primes ADC3 for the channel and prints a handful of raw
 * bare-metal readings. Use this to confirm adc3_prepare_channel() /
 * adc_read_fast() actually produce nonzero, sane values before trusting the
 * FRA sweep output. */
static int cmd_dac_test_adcraw(const struct shell *sh, size_t argc,
                               char **argv) {
  uint32_t ch = strtoul(argv[1], NULL, 10);
  if (ch > 7) {
    shell_error(sh, "Invalid channel: 0-7 allowed");
    return -EINVAL;
  }
  uint8_t adc_ch = dac_to_adc_map[ch];

  adc3_prepare_channel(adc_ch);

  for (int i = 0; i < 10; i++) {
    ADC3->ISR = ADC_ISR_EOC | ADC_ISR_EOS | ADC_ISR_OVR | ADC_ISR_EOSMP;
    LL_ADC_REG_StartConversion(ADC3);
    uint32_t to = 20000;
    while (!LL_ADC_IsActiveFlag_EOC(ADC3) && --to)
      ;
    uint32_t isr = ADC3->ISR;
    uint16_t raw = (uint16_t)LL_ADC_REG_ReadConversionData12(ADC3);
    float v_mv = ((float)raw * 3300.0f) / ((1 << ADC_RESOLUTION) - 1);
    shell_print(sh, "raw=%u (0x%04X)  v_mv=%.2f  to=%u  ISR=0x%08X", raw, raw,
                (double)v_mv, to, isr);
    k_sleep(K_MSEC(100));
  }

  adc3_shutdown();
  return 0;
}

/* Shell Command: dac test rawcycle <ch> [dc_ma] [amp_ma] [freq_hz]
 * Captures and prints 1 full 32-point sine wave cycle of DAC command vs ADC
 * readback at exact FRA sample timing. */
static int cmd_dac_test_rawcycle(const struct shell *sh, size_t argc,
                                 char **argv) {
  uint32_t ch = strtoul(argv[1], NULL, 10);
  float dc_ma = (argc > 2) ? strtof(argv[2], NULL) : 50.0f;
  float amp_ma = (argc > 3) ? strtof(argv[3], NULL) : 10.0f;
  float freq_hz = (argc > 4) ? strtof(argv[4], NULL) : 1000.0f;

  if (ch > 7) {
    shell_error(sh, "Invalid channel: 0-7 allowed");
    return -EINVAL;
  }
  if (freq_hz <= 0.0f || freq_hz > 50000.0f) {
    shell_error(sh, "Invalid frequency");
    return -EINVAL;
  }

  uint8_t adc_ch = dac_to_adc_map[ch];
  const int P = 32;
  uint16_t dac_lut[32];
  float target_ma[32];
  uint16_t adc_lut[32];

  for (int p = 0; p < P; p++) {
    float theta = (2.0f * (float)M_PI * (float)p) / (float)P;
    target_ma[p] = dc_ma + amp_ma * sinf(theta);
    dac_lut[p] = (uint16_t)current_ma_to_dac_value(target_ma[p]);
  }

  uint32_t dt_cycles =
      (uint32_t)(sys_clock_hw_cycles_per_sec() / (freq_hz * (float)P));
  if (dt_cycles == 0) {
    dt_cycles = 1;
  }

  /* Prime hardware */
  dac_write_value(dac_dev, ch, current_ma_to_dac_value(dc_ma));
  dac_spi_begin();
  adc3_prepare_channel(adc_ch);

  CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
  DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

  k_sched_lock();
  uint32_t start_cycle = DWT->CYCCNT;

  /* Run 5 settling cycles + 1 capture cycle */
  int total_samples = 6 * P;
  for (int step = 0; step < total_samples; step++) {
    int p = step % P;
    dac_write_fast((uint8_t)ch, dac_lut[p]);

    uint16_t raw_sample = adc_read_fast();
    if (step >= 5 * P) {
      adc_lut[p] = raw_sample;
    }

    uint32_t target_cycle =
        start_cycle + (uint32_t)(((uint64_t)(step + 1) * dt_cycles));
    while ((DWT->CYCCNT < target_cycle) &&
           (target_cycle - DWT->CYCCNT) < 0x7FFFFFFFUL) {
      /* Spin-wait */
    }
  }

  /* Restore DC setpoint */
  dac_write_fast((uint8_t)ch, (uint16_t)current_ma_to_dac_value(dc_ma));
  k_sched_unlock();

  dac_spi_end();
  adc3_shutdown();

  shell_print(
      sh,
      "--- Raw FRA Cycle Capture (CH %d, %.1f Hz, DC=%.2fmA, Amp=%.2fmA) ---",
      ch, (double)freq_hz, (double)dc_ma, (double)amp_ma);
  shell_print(sh, "pt  | DAC (mA) | DAC cnt | ADC raw | ADC (mV) | ADC (mA)");
  shell_print(sh, "----+----------+---------+---------+----------+---------");

  uint16_t min_raw = 65535, max_raw = 0;
  for (int p = 0; p < P; p++) {
    uint16_t raw = adc_lut[p];
    if (raw < min_raw)
      min_raw = raw;
    if (raw > max_raw)
      max_raw = raw;
    float v_mv = ((float)raw * 3300.0f) / ((1 << ADC_RESOLUTION) - 1);
    float i_ma = (v_mv - 1500.0f) / 10.0f;
    shell_print(sh, "%2d  |  %6.2f  |  %5u  |  %5u  | %8.2f | %7.2f", p,
                (double)target_ma[p], dac_lut[p], raw, (double)v_mv,
                (double)i_ma);
  }

  float min_v = ((float)min_raw * 3300.0f) / ((1 << ADC_RESOLUTION) - 1);
  float max_v = ((float)max_raw * 3300.0f) / ((1 << ADC_RESOLUTION) - 1);
  float min_i = (min_v - 1500.0f) / 10.0f;
  float max_i = (max_v - 1500.0f) / 10.0f;

  shell_print(sh, "--------------------------------------------------------");
  shell_print(sh, "DAC Command Span: %.2f mA to %.2f mA (Pk-Pk = %.2f mA)",
              (double)(dc_ma - amp_ma), (double)(dc_ma + amp_ma),
              (double)(2.0f * amp_ma));
  shell_print(
      sh, "ADC Readback Span: %.2f mA to %.2f mA (Pk-Pk = %.2f mA, raw=%u..%u)",
      (double)min_i, (double)max_i, (double)(max_i - min_i), min_raw, max_raw);

  return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(
    sub_dac_test,
    SHELL_CMD_ARG(sweep, NULL,
                  "Sweep: sweep <ch> <start_ma> <stop_ma> <step_ma> <delay_ms>",
                  cmd_dac_test_sweep, 6, 0),
    SHELL_CMD_ARG(fra, NULL,
                  "FRA Sweep: fra <ch> <dc_ma> <amp_ma> <f_start> <f_stop> "
                  "<ppd> [n_meas] [n_settle]",
                  cmd_dac_test_fra, 7, 2),
    SHELL_CMD_ARG(fastwrite, NULL,
                  "Fast SPI test: fastwrite <ch> <val_lo> <val_hi>",
                  cmd_dac_test_fastwrite, 4, 0),
    SHELL_CMD_ARG(adcraw, NULL, "Raw ADC sanity check: adcraw <ch>",
                  cmd_dac_test_adcraw, 2, 0),
    SHELL_CMD_ARG(rawcycle, NULL,
                  "Capture 1 full sine cycle: rawcycle <ch> [dc_ma] [amp_ma] "
                  "[freq_hz]",
                  cmd_dac_test_rawcycle, 2, 3),
    SHELL_SUBCMD_SET_END);

SHELL_STATIC_SUBCMD_SET_CREATE(
    sub_dac,
    SHELL_CMD_ARG(set, NULL, "Set value: set <ch> <val>", cmd_dac_set, 3, 0),
    SHELL_CMD_ARG(status, NULL, "Read current: status [<ch>]", cmd_dac_status,
                  1, 1),
    SHELL_CMD_ARG(test, &sub_dac_test, "Test commands", NULL, 1, 0),
    SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(dac, &sub_dac, "DAC and Actuator commands", NULL);

int main(void) {
  int ret;

  LOG_INF("Starting Actuator Controller on Nucleo H753ZI");

  if (!device_is_ready(dac_dev) || !device_is_ready(adc_dev)) {
    LOG_ERR("Hardware devices not ready");
    return 0;
  }

  /* Initialize all DAC channels */
  for (int i = 0; i < 8; i++) {
    struct dac_channel_cfg dac_cfg = {.channel_id = i, .resolution = 16};
    ret = dac_channel_setup(dac_dev, &dac_cfg);
    if (ret < 0)
      LOG_WRN("DAC CH %d init failed", i);
  }

  /* Initialize all ADC channels used for current sensing */
  for (int i = 0; i < 8; i++) {
    ret = setup_adc_channel(dac_to_adc_map[i]);
    if (ret < 0)
      LOG_WRN("ADC CH %d init failed", dac_to_adc_map[i]);
  }

  LOG_INF("Ready. Use 'dac status' to see current readbacks.");

  while (1) {
    k_sleep(K_FOREVER);
  }
  return 0;
}