#!/bin/sh

echo performance | sudo tee /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
echo performance | sudo tee /sys/devices/system/cpu/cpu1/cpufreq/scaling_governor
echo performance | sudo tee /sys/devices/system/cpu/cpu2/cpufreq/scaling_governor
echo performance | sudo tee /sys/devices/system/cpu/cpu3/cpufreq/scaling_governor
echo performance | sudo tee /sys/devices/system/cpu/cpu4/cpufreq/scaling_governor
echo performance | sudo tee /sys/devices/system/cpu/cpu5/cpufreq/scaling_governor
echo performance | sudo tee /sys/devices/system/cpu/cpu6/cpufreq/scaling_governor
echo performance | sudo tee /sys/devices/system/cpu/cpu7/cpufreq/scaling_governor
echo performance | sudo tee /sys/devices/system/cpu/cpu8/cpufreq/scaling_governor
echo performance | sudo tee /sys/devices/system/cpu/cpu9/cpufreq/scaling_governor

sudo systemctl stop irqbalance.service

#sudo ethtool -C enp87s0 rx-usecs 0

#sudo ethtool -G enp87s0 rx 4096 tx 4096
sudo ethtool -G enp2s0f0np0 rx 8160 tx 8160
sudo ethtool -G enp2s0f1np1 rx 8160 tx 8160

sudo /home/test1/open5gs-compose/affinity.sh enp2s0f0np0 0-9
sudo /home/test1/open5gs-compose/affinity.sh enp2s0f1np1 0-9
#sudo /home/test1/open5gs-compose/affinity.sh enp87s0 0-9

#sudo ethtool -A enp87s0 rx off tx off
sudo ethtool -A enp2s0f0np0 rx off tx off
sudo ethtool -A enp2s0f1np1 rx off tx off

#sudo ethtool -K enp87s0 tso off gro off lro off gso off
sudo ethtool -K enp2s0f0np0 tso off gro off lro off gso off 
sudo ethtool -K enp2s0f1np1 tso off gro off lro off gso off 

ip l sh | grep veth | awk '{print $2}' | awk -F@ '{print $1}' | xargs -I {} sudo ip link set dev {} xdp obj zeroentrypoint_bpf.o sec xdp/upf_zero_entrypoint
