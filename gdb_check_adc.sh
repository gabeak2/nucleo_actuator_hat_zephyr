/Users/gabe/zephyr-sdk-1.0.1/gnu/arm-zephyr-eabi/bin/arm-zephyr-eabi-gdb build/zephyr/zephyr.elf -ex "target remote localhost:3333" -ex "print/x ((ADC_TypeDef *)0x58026000)->CR" -ex "quit"
