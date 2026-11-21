# The GPIF MMCM is visible after the out-of-context IP is linked.
set gpif_clock [get_clocks -of_objects [get_pins gen_clks/inst/mmcm_adv_inst/CLKOUT1]]
set codec_clocks [get_clocks -include_generated_clocks codec_data_clk_p]
set_false_path -from $gpif_clock -to $codec_clocks
set_false_path -from $codec_clocks -to $gpif_clock
