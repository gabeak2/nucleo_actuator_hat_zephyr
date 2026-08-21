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
    shell_print(sh, "DAC Channel %d: Current readback = %.2f mA", ch, cur);
  } else {
    shell_print(sh, "--- Actuator Status (Current Readback) ---");
    for (int i = 0; i < 8; i++) {
      ret = get_current_ma(i, &cur);
      if (ret < 0) {
        shell_print(sh, "CH %d: Error reading", i);
      } else {
        shell_print(sh, "CH %d: %.2f mA", i, cur);
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

SHELL_STATIC_SUBCMD_SET_CREATE(
    sub_dac_test,
    SHELL_CMD_ARG(sweep, NULL,
                  "Sweep: sweep <ch> <start_ma> <stop_ma> <step_ma> <delay_ms>",
                  cmd_dac_test_sweep, 6, 0),
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
