// Test code just to look at LL macros
#include <stm32h7xx_ll_adc.h>
void disable_adc(void) {
  if (LL_ADC_IsEnabled(ADC3)) {
    LL_ADC_Disable(ADC3);
    while (LL_ADC_IsEnabled(ADC3));
  }
  ADC3->CFGR &= ~ADC_CFGR_DMNGT;
  LL_ADC_Enable(ADC3);
  while (!LL_ADC_IsActiveFlag_ADRDY(ADC3));
}
