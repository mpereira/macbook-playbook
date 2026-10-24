#!/usr/bin/env bash
# Records OpenZFS versions and memory use on macOS, to compare before and
# after an upgrade or an ARC limit change.
#
# Usage: zfs-memory-snapshot.sh <label> [seconds]
#
# Prints a summary and saves it, with the raw kstats, to
# ${ZFS_SNAPSHOT_DIR:-~/zfs-memory-snapshots}/<timestamp>-<label>.txt.
# Rates (swap, paging, disk) are measured over [seconds], 60 by default, so
# keep the Mac doing its usual work while it runs. It changes nothing and
# doesn't need sudo.

set -uo pipefail

label="${1:?usage: $0 <label> [seconds]}"
interval="${2:-60}"
directory="${ZFS_SNAPSHOT_DIR:-$HOME/zfs-memory-snapshots}"
output="$directory/$(date +%Y%m%dT%H%M%S)-$label.txt"
zfs_bin=/usr/local/zfs/bin

mkdir -p "$directory"

section() {
  printf '\n## %s\n' "$1"
}

# Runs a command, labelled, and carries on if it fails.
run() {
  printf '$ %s\n' "$*"
  "$@" || printf '(exit %s)\n' "$?"
}

kstat() {
  sysctl -n "$1" 2>/dev/null || echo "unavailable"
}

gib() {
  case "$1" in
    ''|*[!0-9]*) echo "$1" ;;
    *) awk -v b="$1" 'BEGIN { printf "%.2f GiB (%d)", b / 2^30, b }' ;;
  esac
}

vm_stat_value() {
  awk -v key="$1" -F: '$1 == key { gsub(/[ .]/, "", $2); print $2 }' <<<"$2"
}

snapshot() {
  echo "# OpenZFS memory snapshot: $label"
  echo "Taken: $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "macOS: $(sw_vers -productVersion) ($(sw_vers -buildVersion)), $(uname -m)"
  echo "Booted: $(sysctl -n kern.boottime)"
  echo "Uptime: $(uptime)"

  section "Versions"
  # zfs version prints the userland version and the loaded driver (zfs-kmod).
  # They differ when a new package is installed but the Mac hasn't rebooted.
  run "$zfs_bin/zfs" version
  echo "Homebrew cask: $(HOMEBREW_NO_AUTO_UPDATE=1 brew list --cask --versions openzfs 2>/dev/null || echo 'not installed')"
  pkgutil --pkg-info org.openzfsonosx.zfs 2>/dev/null | grep -E '^(version|install-time)'
  for kext in /Library/Extensions/*zfs*.kext; do
    [ -e "$kext" ] || continue
    echo "On disk: $kext $(defaults read "$kext/Contents/Info" CFBundleVersion 2>/dev/null)"
  done
  echo "Loaded: $(kmutil showloaded --list-only 2>/dev/null | grep -Ei 'zfs|lundman' || echo 'none listed by kmutil')"
  if [ -e /dev/zfs ]; then
    echo "/dev/zfs: present since $(/usr/bin/stat -f %Sm /dev/zfs)"
  else
    echo "/dev/zfs: missing, so the driver isn't loaded"
  fi

  section "Pools"
  run "$zfs_bin/zpool" list -o name,size,allocated,free,fragmentation,capacity,health
  run "$zfs_bin/zpool" status -x

  section "Tunables"
  echo "zfs_arc.max: $(gib "$(kstat kstat.zfs.darwin.tunable.zfs_arc.max)")"
  echo "zfs_arc.min: $(gib "$(kstat kstat.zfs.darwin.tunable.zfs_arc.min)")"
  echo "/etc/zfs/zsysctl.conf:"
  grep -v '^\s*#' /etc/zfs/zsysctl.conf 2>/dev/null | grep . || echo "  (missing or empty)"

  section "Memory now"
  arcstats="$(sysctl kstat.zfs.misc.arcstats 2>/dev/null)"
  spl="$(sysctl kstat.spl.misc.spl_misc 2>/dev/null)"
  vm_before="$(vm_stat)"
  page_size="$(sed -n '1s/.*page size of \([0-9]*\) bytes.*/\1/p' <<<"$vm_before")"
  echo "RAM: $(gib "$(sysctl -n hw.memsize)")"
  echo "ARC size: $(gib "$(kstat kstat.zfs.misc.arcstats.size)")"
  echo "ARC target (c): $(gib "$(kstat kstat.zfs.misc.arcstats.c)")"
  echo "ARC max (c_max): $(gib "$(kstat kstat.zfs.misc.arcstats.c_max)")"
  echo "ARC min (c_min): $(gib "$(kstat kstat.zfs.misc.arcstats.c_min)")"
  # Everything ZFS has taken from the kernel, ARC included. The part above the
  # ARC size isn't limited by zfs_arc.max.
  echo "ZFS allocated from the OS: $(gib "$(kstat kstat.spl.misc.spl_misc.os_mem_alloc)")"
  echo "SPL dynamic memory cap: $(gib "$(kstat kstat.spl.misc.spl_misc.spl_osif_dynamic_memory_cap)")"
  echo "SPL VM pressure level: $(kstat kstat.spl.misc.spl_misc.spl_vm_pressure_level)"
  echo "ARC memory throttles: $(kstat kstat.zfs.misc.arcstats.memory_throttle_count)"
  echo "Wired: $(gib "$(($(vm_stat_value 'Pages wired down' "$vm_before") * page_size))")"
  echo "Compressed: $(gib "$(($(vm_stat_value 'Pages occupied by compressor' "$vm_before") * page_size))")"
  echo "Swap: $(sysctl -n vm.swapusage)"
  echo "Memory pressure level: $(sysctl -n kern.memorystatus_vm_pressure_level) (1 normal, 2 warning, 4 critical)"

  section "Rates over ${interval}s"
  "$zfs_bin/zpool" iostat -y "$interval" 1 >"$output.zpool-iostat" 2>&1 &
  iostat -d -K -c 2 -w "$interval" >"$output.iostat" 2>&1
  wait
  vm_after="$(vm_stat)"
  for key in Swapins Swapouts Pageins Pageouts; do
    before="$(vm_stat_value "$key" "$vm_before")"
    after="$(vm_stat_value "$key" "$vm_after")"
    awk -v k="$key" -v d="$((after - before))" -v s="$interval" \
      'BEGIN { printf "%s: %d pages (%.1f/s)\n", k, d, d / s }'
  done
  echo "Disks (iostat, second sample is the ${interval}s average):"
  cat "$output.iostat"
  echo "Pools (zpool iostat):"
  cat "$output.zpool-iostat"
  rm -f "$output.iostat" "$output.zpool-iostat"

  section "Largest processes by memory"
  top -l 1 -o mem -n 10 -stats pid,command,mem | sed -n '/^PID/,$p'

  section "Raw kstats"
  echo "$arcstats"
  echo "$spl"
}

snapshot 2>&1 | tee "$output"
echo
echo "Saved to $output"
