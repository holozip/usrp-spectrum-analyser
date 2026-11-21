#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
working_src="$repo_root/src/firmware"
project="$working_src/B210_Project_Firmwire.xpr"
output_dir="$repo_root/build/firmware"
build_project_dir="$output_dir/project"
artifact="$output_dir/usrp_b210_fpga.bin"
impl_dir="$output_dir/runs/impl_1"
vivado_bin="${VIVADO:-vivado}"
jobs="${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '4')}"
action="${1:-build}"

usage() {
    cat <<'EOF'
Usage: scripts/build-firmware.sh [build|diagnose|install]

Commands:
  build    Build src/firmware with Vivado 2026 and write build/firmware/usrp_b210_fpga.bin.
  diagnose Analyze a saved failed route checkpoint and write reports under build/firmware/route-diagnostics.
  install  Install the last built artifact into /usr/local/share/uhd/images.

Environment:
  VIVADO   Vivado executable or wrapper (default: vivado)
  JOBS     Maximum Vivado implementation jobs (default: detected CPU count)
EOF
}

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

lock_workspace() {
    mkdir -p "$output_dir"
    exec 9>"$output_dir/.vivado.lock"
    flock -n 9 || fail "a firmware build or diagnostic is already running"
}

resolve_vivado() {
    local executable
    executable="$(command -v -- "$vivado_bin")" || fail "Vivado not found: $vivado_bin (set VIVADO=/path/to/vivado)"
    vivado_bin="$(realpath -s -- "$executable")"
}

run_vivado() {
    local log_file="$1"
    shift
    (
        cd -- "$output_dir"
        "$vivado_bin" -mode batch -nolog -nojournal "$@"
    ) 2>&1 | tee "$log_file"
}

require_artifact() {
    [[ -s "$artifact" ]] || fail "built artifact is missing: $artifact (run '$0 build' first)"
}

case "$action" in
    -h|--help|help) usage; exit 0 ;;
    build)
        [[ -f "$project" ]] || fail "firmware project not found: $project"
        resolve_vivado
        [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || fail "JOBS must be a positive integer, got '$jobs'"
        lock_workspace
        version_output="$(cd -- "$output_dir" && "$vivado_bin" -version 2>&1)" || fail "could not query Vivado version: $version_output"
        if ! grep -Eq 'vivado v2026\.' <<<"$version_output"; then
            fail "Vivado 2026.x is required; detected: $(head -n 1 <<<"$version_output")"
        fi
        python3 "$repo_root/scripts/stage-vivado-project.py" "$project" "$build_project_dir"
        candidate="$(mktemp "$output_dir/.usrp_b210_fpga.bin.XXXXXXXX")"
        trap 'rm -f -- "$candidate"' EXIT
        printf 'Building with %s (%s jobs)\n' "$vivado_bin" "$jobs"
        run_vivado "$output_dir/build.log" \
            -source "$repo_root/scripts/vivado-build.tcl" \
            -tclargs "$build_project_dir/${project##*/}" "$candidate" "$jobs"
        [[ -s "$candidate" ]] || fail "Vivado completed without producing a bitstream"
        chmod 0644 "$candidate"
        mv -fT -- "$candidate" "$artifact"
        printf 'Built %s (%s bytes)\n' "$artifact" "$(wc -c < "$artifact")"
        sha256sum "$artifact"
        ;;
    diagnose)
        lock_workspace
        # Diagnose a pre-existing run until the first run in the new location.
        if [[ ! -d "$impl_dir" ]]; then
            impl_dir="$working_src/B210_Project_Firmwire.runs/impl_1"
        fi
        failed_checkpoint="$impl_dir/b200_routed_error.dcp"
        [[ -s "$failed_checkpoint" ]] || fail "failed route checkpoint not found: $failed_checkpoint (run a build first)"
        resolve_vivado
        diagnostics_dir="$output_dir/route-diagnostics"
        mkdir -p "$diagnostics_dir"
        rm -f -- "$diagnostics_dir"/*.rpt
        run_vivado "$output_dir/diagnose.log" \
            -source "$repo_root/scripts/diagnose-firmware-route.tcl" \
            -tclargs "$failed_checkpoint" "$diagnostics_dir"
        printf 'Route diagnostics written to %s\n' "$diagnostics_dir"
        ;;
    install)
        require_artifact
        dest="/usr/local/share/uhd/images/usrp_b210_fpga.bin"
        install -D -m 0644 -- "$artifact" "$dest"
        printf 'Installed %s\n' "$dest"
        ;;
    *) usage >&2; fail "unknown command: $action" ;;
esac
