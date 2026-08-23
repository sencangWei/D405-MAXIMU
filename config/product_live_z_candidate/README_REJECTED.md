# Rejected diagnostic candidate

This directory is retained only to reproduce the 2026-08-23 blind A/B. It is
not a product configuration and must not replace `config/product_live_stm32/`.

The candidate used the 30-pose accelerometer matrix and `acc_n=0.1`. Its Z
improvement did not generalize across recordings and it shortened a horizontal
trajectory side by about 13.2%, so it was rejected. The only explicit entrypoint
is `./run_vins_realtime.sh product-live-z-candidate`, which prints an unsigned
diagnostic warning.
