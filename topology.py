#!/usr/bin/env python3
"""
topology.py  (POX version)
===========================
Mininet topology for the Packet Drop Simulator.

Network layout
--------------
    h1 (10.0.0.1) ──┐
    h2 (10.0.0.2) ──┤
                    s1 ── RemoteController (POX, port 6633)
    h3 (10.0.0.3) ──┤
    h4 (10.0.0.4) ──┘

Usage
-----
    # Just open CLI (manual testing)
    sudo python3 topology.py

    # Run all automated test scenarios
    sudo python3 topology.py --run-tests

    # Install one drop rule then open CLI
    sudo python3 topology.py --drop-src 10.0.0.1 --drop-dst 10.0.0.2

NOTE: Start POX controller FIRST in another terminal:
    cd ~/pox
    sudo python3 pox.py log.level --DEBUG openflow.of_01 drop_controller

Author : SDN Mininet Project – UE24CS252B
"""

import argparse
import subprocess
import time
import sys

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.topo import Topo
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from mininet.link import TCLink


# ── Topology ───────────────────────────────────────────────────────────────────

class DropSimTopo(Topo):
    """Single OVS switch with 4 hosts and bandwidth-limited links."""

    def build(self):
        s1 = self.addSwitch("s1")
        for i in range(1, 5):
            h = self.addHost(f"h{i}", ip=f"10.0.0.{i}/24",
                             mac=f"00:00:00:00:00:0{i}")
            # 10 Mbps, 5 ms delay – gives realistic ping RTTs for measurement
            self.addLink(h, s1, bw=10, delay="5ms")


# ── Utility helpers ────────────────────────────────────────────────────────────

def banner(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def ovs_add_drop(sw_name, src_ip, dst_ip, priority=200):
    """Install DROP rule using ovs-ofctl (works independently of controller)."""
    cmd = (
        f"ovs-ofctl add-flow {sw_name} "
        f"\"priority={priority},ip,nw_src={src_ip},nw_dst={dst_ip},actions=drop\""
    )
    subprocess.call(cmd, shell=True)
    info(f"  [RULE] DROP installed: {src_ip} → {dst_ip}\n")


def ovs_del_drop(sw_name, src_ip, dst_ip):
    """Remove a DROP rule using ovs-ofctl."""
    cmd = (
        f"ovs-ofctl del-flows {sw_name} "
        f"\"ip,nw_src={src_ip},nw_dst={dst_ip}\""
    )
    subprocess.call(cmd, shell=True)
    info(f"  [RULE] DROP removed: {src_ip} → {dst_ip}\n")


def dump_flows(sw_name):
    """Print the current flow table."""
    out = subprocess.check_output(
        f"ovs-ofctl dump-flows {sw_name}", shell=True, text=True
    )
    info(f"\n--- Flow table ({sw_name}) ---\n{out}\n")


def ping_loss(net, src_name, dst_name, count=5):
    """Ping from src to dst; return packet-loss percentage."""
    src = net.get(src_name)
    dst = net.get(dst_name)
    result = src.cmd(f"ping -c {count} -W 1 {dst.IP()}")
    loss = 100.0
    for line in result.splitlines():
        if "packet loss" in line:
            for token in line.split():
                if token.endswith("%"):
                    try:
                        loss = float(token[:-1])
                    except ValueError:
                        pass
    info(f"  [{src_name} → {dst_name}] Packet loss: {loss:.0f}%\n")
    return loss


def run_iperf(net, src_name, dst_name, duration=5):
    """Measure TCP throughput with iperf."""
    src = net.get(src_name)
    dst = net.get(dst_name)
    info(f"  iperf [{src_name} → {dst_name}] {duration}s …\n")
    dst.cmd("iperf -s -D")
    time.sleep(0.5)
    out = src.cmd(f"iperf -c {dst.IP()} -t {duration}")
    dst.cmd("kill %iperf")
    for line in out.splitlines():
        if "bits/sec" in line:
            info(f"    {line.strip()}\n")


# ── Test scenarios ─────────────────────────────────────────────────────────────

def scenario_1_baseline(net):
    banner("SCENARIO 1 – Baseline: Normal Forwarding (no drop rules)")
    net.pingAll()
    info("\n[Expected] 0% packet loss for all pairs.\n")


def scenario_2_drop(net):
    banner("SCENARIO 2 – Drop Rule: h1 (10.0.0.1) → h2 (10.0.0.2)")
    ovs_add_drop("s1", "10.0.0.1", "10.0.0.2")
    dump_flows("s1")

    info("\n[Blocked flow h1 → h2 – expect 100% loss]:\n")
    loss_blocked = ping_loss(net, "h1", "h2")

    info("\n[Allowed flow h1 → h3 – expect 0% loss]:\n")
    loss_allowed = ping_loss(net, "h1", "h3")

    assert loss_blocked == 100.0, \
        f"FAIL: expected 100% loss h1→h2, got {loss_blocked}%"
    assert loss_allowed == 0.0, \
        f"FAIL: expected 0% loss h1→h3, got {loss_allowed}%"
    info("\n[PASS] Scenario 2 assertions passed.\n")
    return loss_blocked, loss_allowed


def scenario_3_regression(net):
    banner("SCENARIO 3 – Regression: Rule Persistence")

    # 3a – rule from scenario 2 should still be active
    info("  [3a] h1→h2 drop rule still active?\n")
    loss = ping_loss(net, "h1", "h2")
    assert loss == 100.0, f"FAIL: rule should persist, got {loss}%"
    info("  [PASS] Rule persisted.\n")

    # 3b – add second drop rule
    info("  [3b] Adding second drop rule: h3 → h4\n")
    ovs_add_drop("s1", "10.0.0.3", "10.0.0.4")
    loss2 = ping_loss(net, "h3", "h4")
    assert loss2 == 100.0, f"FAIL: h3→h4 should be dropped, got {loss2}%"
    info("  [PASS] Second drop rule works.\n")

    # 3c – remove first rule, h1→h2 should recover
    info("  [3c] Removing h1→h2 rule – traffic should restore\n")
    ovs_del_drop("s1", "10.0.0.1", "10.0.0.2")
    time.sleep(1)
    loss3 = ping_loss(net, "h1", "h2")
    assert loss3 == 0.0, f"FAIL: h1→h2 should recover, got {loss3}%"
    info("  [PASS] Traffic restored after rule removal.\n")

    dump_flows("s1")
    info("\n[PASS] All regression checks passed.\n")


def scenario_4_iperf(net):
    banner("SCENARIO 4 – Throughput Measurement with iperf")
    info("  [Allowed flow h1 → h3]:\n")
    run_iperf(net, "h1", "h3", duration=5)

    info("  [Dropped flow h3 → h4 – expect 0 throughput / connection fail]:\n")
    h3 = net.get("h3")
    h4 = net.get("h4")
    h4.cmd("iperf -s -D")
    time.sleep(0.5)
    out = h3.cmd(f"iperf -c {h4.IP()} -t 5 2>&1")
    h4.cmd("kill %iperf")
    # Print first 300 chars to avoid noise
    info(f"    {out[:300]}\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Packet Drop Simulator – Mininet (POX)"
    )
    parser.add_argument("--controller-ip",   default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6633)
    parser.add_argument("--run-tests",  action="store_true",
                        help="Run all automated scenarios")
    parser.add_argument("--drop-src",   default=None,
                        help="Source IP for a manual drop rule")
    parser.add_argument("--drop-dst",   default=None,
                        help="Destination IP for a manual drop rule")
    args = parser.parse_args()

    setLogLevel("info")

    topo       = DropSimTopo()
    controller = RemoteController("c0",
                                   ip=args.controller_ip,
                                   port=args.controller_port)
    net = Mininet(topo=topo,
                  controller=controller,
                  switch=OVSSwitch,
                  link=TCLink,
                  autoSetMacs=False)

    info("\n*** Starting Packet Drop Simulator (POX) ***\n")
    net.start()

    info("Waiting for controller handshake …\n")
    time.sleep(3)

    try:
        if args.run_tests:
            scenario_1_baseline(net)
            scenario_2_drop(net)
            scenario_3_regression(net)
            scenario_4_iperf(net)
            banner("ALL SCENARIOS COMPLETE")
        elif args.drop_src and args.drop_dst:
            ovs_add_drop("s1", args.drop_src, args.drop_dst)
            dump_flows("s1")

        CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
