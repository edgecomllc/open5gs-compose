#!/bin/bash

# ============================================
# Network Interfaces and CPU Performance Tuning Script
# ============================================

# ============================================
# CONFIGURATION - Edit these variables
# ============================================

# CPUs to configure (examples: "0-9", "0,2,4,6", "0-3,8-11")
CPUS="0-9"

# Network interfaces to configure
NET_INTERFACES="enp87s0 enp2s0f0np0 enp2s0f1np1"

# Path to affinity script
AFFINITY_SCRIPT="/home/test1/open5gs-compose/affinity.sh"

# ============================================
# FUNCTIONS
# ============================================

# Execute command with sudo and print it
exec_cmd() {
    local cmd="sudo $*"
    echo "  $cmd"
    sudo "$@" 2>/dev/null || echo "  [FAILED] $*"
}

# Convert CPU range to list (e.g., "0-9" -> "0 1 2 3 4 5 6 7 8 9")
expand_cpu_range() {
    local range="$1"
    local cpus=""
    
    IFS=',' read -ra parts <<< "$range"
    for part in "${parts[@]}"; do
        if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
            for ((i=${BASH_REMATCH[1]}; i<=${BASH_REMATCH[2]}; i++)); do
                cpus="$cpus $i"
            done
        else
            cpus="$cpus $part"
        fi
    done
    echo "$cpus"
}

# Check if interface exists
iface_exists() {
    ip link show "$1" &>/dev/null
}

# Get maximum ring size for an interface
get_max_ring_size() {
    local iface="$1"
    local direction="$2"  # rx or tx
    
    # Convert to uppercase for matching
    local dir_upper=$(echo "$direction" | tr 'a-z' 'A-Z')
    
    # Get the value from "Pre-set maximums" section
    sudo ethtool -g "$iface" 2>/dev/null | awk -v dir="$dir_upper" '
        /Pre-set maximums:/ { in_max=1; next }
        /Current hardware settings:/ { in_max=0 }
        in_max && $1 == dir":" { print $2; exit }
    '
}

# Set rings to maximum values
set_max_rings() {
    local iface="$1"
    
    local max_rx=$(get_max_ring_size "$iface" "rx")
    local max_tx=$(get_max_ring_size "$iface" "tx")
    
    if [ -z "$max_rx" ] || [ -z "$max_tx" ]; then
        echo "  Could not determine max ring sizes for $iface"
        return 1
    fi
    
    exec_cmd ethtool -G "$iface" rx "$max_rx" tx "$max_tx"
    return 0
}

# ============================================
# MAIN SCRIPT
# ============================================

echo "=========================================="
echo "Network Interfaces and CPU Performance Tuning"
echo "=========================================="

# 1. Set CPU governor to performance
echo -e "\n[1] Setting CPU governor to performance..."
CPU_LIST=$(expand_cpu_range "$CPUS")
echo "  CPUs: $CPU_LIST"

for cpu in $CPU_LIST; do
    gov_file="/sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_governor"
    if [ -f "$gov_file" ]; then
        echo "performance" | sudo tee "$gov_file" >/dev/null
        echo "  echo performance | sudo tee $gov_file"
        echo "  CPU$cpu: performance set"
    else
        echo "  CPU$cpu: governor file not found (skipping)"
    fi
done

# 2. Stop irqbalance
echo -e "\n[2] Stopping irqbalance..."
exec_cmd systemctl stop irqbalance.service

# 3. Configure network interfaces
echo -e "\n[3] Configuring network interfaces..."

for iface in $NET_INTERFACES; do
    if ! iface_exists "$iface"; then
        echo "  Interface $iface not found - skipping"
        continue
    fi
    
    echo -e "\n  Configuring $iface..."
    
    # Set RX/TX rings to maximum
    set_max_rings "$iface"
    
    # Disable flow control
    exec_cmd ethtool -A "$iface" rx off tx off
    
    # Disable offload features
    exec_cmd ethtool -K "$iface" tso off gro off lro off gso off
done

# 4. Set CPU affinity for interfaces
echo -e "\n[4] Setting CPU affinity..."

if [ -f "$AFFINITY_SCRIPT" ]; then
    first_cpu=$(echo "$CPU_LIST" | awk '{print $1}')
    last_cpu=$(echo "$CPU_LIST" | awk '{print $NF}')
    cpu_range="${first_cpu}-${last_cpu}"
    echo "  CPU range: $cpu_range"
    
    for iface in $NET_INTERFACES; do
        if iface_exists "$iface"; then
            exec_cmd "$AFFINITY_SCRIPT" "$iface" "$cpu_range"
        fi
    done
else
    echo "  WARNING: Affinity script not found at: $AFFINITY_SCRIPT"
fi

echo -e "\n=========================================="
echo "Done!"
echo "=========================================="
