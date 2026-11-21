# Analyze a failed Vivado route checkpoint without modifying the source or checkpoint.
if {$argc != 2} {
    puts stderr "Usage: diagnose-firmware-route.tcl <failed-route.dcp> <output-directory>"
    exit 2
}

set checkpoint [file normalize [lindex $argv 0]]
set output_dir [file normalize [lindex $argv 1]]
if {![file isfile $checkpoint]} {
    puts stderr "Failed-route checkpoint not found: $checkpoint"
    exit 2
}
file mkdir $output_dir
open_checkpoint $checkpoint

report_design_analysis -congestion -file [file join $output_dir congestion.rpt]
report_design_analysis -complexity -hierarchical_depth 2 -file [file join $output_dir complexity.rpt]
report_route_status -file [file join $output_dir route-status.rpt]
report_high_fanout_nets -file [file join $output_dir high-fanout-nets.rpt]
report_timing_summary -file [file join $output_dir timing-summary.rpt]
report_utilization -file [file join $output_dir utilization.rpt]

close_design
puts "Route diagnostics saved under $output_dir"
exit 0
