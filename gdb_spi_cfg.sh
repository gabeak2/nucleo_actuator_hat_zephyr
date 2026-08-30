/Users/gabe/zephyr-sdk-1.0.1/gnu/arm-zephyr-eabi/bin/arm-zephyr-eabi-gdb build/zephyr/zephyr.elf \
  -ex "target remote localhost:3333" \
  -ex "print/x ((SPI_TypeDef *)0x40013000)->CFG2" \
  -ex "print/x ((SPI_TypeDef *)0x40013000)->CFG1" \
  -ex "print/x ((SPI_TypeDef *)0x40013000)->CR1" \
  -ex "print/x ((GPIO_TypeDef *)0x40020000)->MODER" \
  -ex "print/x ((GPIO_TypeDef *)0x40020000)->ODR" \
  -ex "quit"
