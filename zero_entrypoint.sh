ip l sh | grep veth | awk '{print $2}' | awk -F@ '{print $1}' | xargs -I {} sudo ip link set dev {} xdp obj zeroentrypoint_bpf.o sec xdp/upf_zero_entrypoint
