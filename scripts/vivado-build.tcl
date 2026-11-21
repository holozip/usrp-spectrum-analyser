# Vivado batch entry point. Write run output alongside the published image.
if {$argc != 3} {
    puts stderr "Usage: vivado-build.tcl <project.xpr> <output.bin> <jobs>"
    exit 2
}

set project_file [file normalize [lindex $argv 0]]
set output_file [file normalize [lindex $argv 1]]
set jobs [lindex $argv 2]

if {![file isfile $project_file]} {
    puts stderr "Project file not found: $project_file"
    exit 2
}
if {![string is integer -strict $jobs] || $jobs < 1} {
    puts stderr "jobs must be a positive integer: $jobs"
    exit 2
}
if {![regexp {^2026\.} [version -short]]} {
    puts stderr "Vivado 2026.x is required; detected [version -short]"
    exit 2
}

set runs_dir [file join [file dirname $output_file] runs]
file mkdir $runs_dir
open_project $project_file

set ips [get_ips -quiet]
if {[llength $ips] > 0} {
    puts "Upgrading [llength $ips] IP cores for [version -short]"
    upgrade_ip $ips
    generate_target all $ips
}

set run [get_runs -quiet impl_1]
if {[llength $run] != 1} {
    puts stderr "Expected exactly one impl_1 run in $project_file"
    close_project
    exit 2
}

# Ask the implementation run to emit the raw configuration .bin alongside the .bit.
set_property STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE 1 $run
reset_run impl_1
set synth_run [get_runs synth_1]
if {[get_property NEEDS_REFRESH $synth_run]} {
    reset_run synth_1
}
# Clear any persisted incremental settings before placing and routing from scratch.
set_property AUTO_INCREMENTAL_CHECKPOINT 0 $run
set_property INCREMENTAL_CHECKPOINT "" $run
set_property STEPS.PLACE_DESIGN.ARGS.DIRECTIVE AltSpreadLogic_low $run
set_property STEPS.PHYS_OPT_DESIGN.ARGS.DIRECTIVE ExploreWithHoldFix $run
set_property STEPS.ROUTE_DESIGN.ARGS.DIRECTIVE RuntimeOptimized $run
launch_runs impl_1 -dir $runs_dir -to_step write_bitstream -jobs $jobs
wait_on_run impl_1

set status [get_property STATUS $run]
if {![string match "write_bitstream Complete!*" $status]} {
    puts stderr "Implementation did not complete successfully (status: $status)"
    close_project
    exit 1
}

set run_dir [get_property DIRECTORY $run]
set timing_file [file join $run_dir b200_timing_summary_routed.rpt]
if {![file isfile $timing_file]} {
    error "Vivado did not write the routed timing summary: $timing_file"
}
set report [open $timing_file r]
set timing_summary [read $report]
close $report
if {![string match "*All user specified timing constraints are met.*" $timing_summary]} {
    error "Implementation did not meet timing. See $timing_file"
}

set generated_bin [file join $run_dir b200.bin]
if {![file isfile $generated_bin] || [file size $generated_bin] < 1000000} {
    puts stderr "Expected a non-empty raw bitstream at $generated_bin"
    close_project
    exit 1
}

file copy -force $generated_bin $output_file
puts "Packaged raw bitstream: $output_file ([file size $output_file] bytes)"
close_project
exit 0
