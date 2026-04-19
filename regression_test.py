#!/usr/bin/env python3
"""
regression_test.py  (POX version)
===================================
Standalone regression test suite — does NOT need a running POX controller.
Uses OVS in standalone/learning mode + ovs-ofctl to inject drop rules directly.

Tests
-----
  T1 – Baseline: all 4 hosts can reach each other
  T2 – Drop rule blocks h1 → h2
  T3 – Non-targeted flows are unaffected (h1→h3, h2→h4)
  T4 – Removing the rule restores h1 → h2
  T5 – Two simultaneous drop rules both work
  T6 – Drop rule persists after unrelated traffic is generated

Run:
    sudo python3 regression_test.py

Author : SDN Mininet Project – UE24CS252B
"""

import subprocess
import sys
import time

from mininet.net import Mininet
from mininet.node import OVSSwitch, Controller
from mininet.topo import Topo
from mininet.log import setLogLevel

# ── Colour helpers ─────────────────────────────────────────────────────────────
PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"

results = []   # list of (test_name, passed: bool)


def banner(msg):
    print(f"\n{'─'*60}\n  {msg}\n{'─'*60}")


def record(name, ok, detail=""):
    status = PASS if ok else FAIL
    print(f"  {status}  {name}" + (f"  ({detail})" if detail else ""))
    results.append((name, ok))


# ── Topology ───────────────────────────────────────────────────────────────────

class FourHostTopo(Topo):
    def build(self):
        s1 = self.addSwitch("s1")
        for i in range(1, 5):
            h = self.addHost(f"h{i}", ip=f"10.0.0.{i}/24")
            self.addLink(h, s1)


def build_net():
    net = Mininet(topo=FourHostTopo(),
                  controller=Controller,   # built-in learning switch
                  switch=OVSSwitch,
                  autoSetMacs=True)
    return net


# ── Rule helpers ───────────────────────────────────────────────────────────────

def add_drop(sw, src_ip, dst_ip, priority=200):
    subprocess.call(
        f"ovs-ofctl add-flow {sw} "
        f"\"priority={priority},ip,nw_src={src_ip},nw_dst={dst_ip},actions=drop\"",
        shell=True
    )


def del_drop(sw, src_ip, dst_ip):
    subprocess.call(
        f"ovs-ofctl del-flows {sw} \"ip,nw_src={src_ip},nw_dst={dst_ip}\"",
        shell=True
    )


def dump_flows(sw):
    out = subprocess.check_output(
        f"ovs-ofctl dump-flows {sw}", shell=True, text=True
    )
    print(f"\n{INFO} Flow table ({sw}):\n{out}")


def ping_loss(net, src_name, dst_name, count=4):
    src = net.get(src_name)
    dst = net.get(dst_name)
    out = src.cmd(f"ping -c {count} -W 1 {dst.IP()}")
    for line in out.splitlines():
        if "packet loss" in line:
            for token in line.split():
                if token.endswith("%"):
                    try:
                        return float(token[:-1])
                    except ValueError:
                        pass
    return 100.0


# ── Individual tests ───────────────────────────────────────────────────────────

def t1_baseline(net):
    banner("T1 – Baseline: all hosts reachable")
    l12 = ping_loss(net, "h1", "h2")
    l34 = ping_loss(net, "h3", "h4")
    record("T1a: h1→h2 baseline 0% loss", l12 == 0.0, f"{l12:.0f}%")
    record("T1b: h3→h4 baseline 0% loss", l34 == 0.0, f"{l34:.0f}%")


def t2_drop_blocks(net):
    banner("T2 – Drop rule blocks h1 → h2")
    add_drop("s1", "10.0.0.1", "10.0.0.2")
    time.sleep(0.3)
    loss = ping_loss(net, "h1", "h2")
    record("T2: h1→h2 blocked (100% loss)", loss == 100.0, f"{loss:.0f}%")


def t3_non_targeted_ok(net):
    banner("T3 – Non-targeted flows unaffected")
    l13 = ping_loss(net, "h1", "h3")
    l24 = ping_loss(net, "h2", "h4")
    record("T3a: h1→h3 still 0% loss", l13 == 0.0, f"{l13:.0f}%")
    record("T3b: h2→h4 still 0% loss", l24 == 0.0, f"{l24:.0f}%")


def t4_remove_restores(net):
    banner("T4 – Removing drop rule restores h1 → h2")
    del_drop("s1", "10.0.0.1", "10.0.0.2")
    time.sleep(1)
    loss = ping_loss(net, "h1", "h2")
    record("T4: h1→h2 restored (0% loss)", loss == 0.0, f"{loss:.0f}%")


def t5_multiple_rules(net):
    banner("T5 – Multiple simultaneous drop rules")
    add_drop("s1", "10.0.0.1", "10.0.0.2")
    add_drop("s1", "10.0.0.3", "10.0.0.4")
    time.sleep(0.3)

    l12 = ping_loss(net, "h1", "h2")
    l34 = ping_loss(net, "h3", "h4")
    l13 = ping_loss(net, "h1", "h3")   # should be unaffected

    record("T5a: h1→h2 dropped",       l12 == 100.0, f"{l12:.0f}%")
    record("T5b: h3→h4 dropped",       l34 == 100.0, f"{l34:.0f}%")
    record("T5c: h1→h3 unaffected",    l13 == 0.0,   f"{l13:.0f}%")

    del_drop("s1", "10.0.0.1", "10.0.0.2")
    del_drop("s1", "10.0.0.3", "10.0.0.4")


def t6_persistence(net):
    banner("T6 – Drop rule persists after unrelated traffic")
    add_drop("s1", "10.0.0.2", "10.0.0.3")
    time.sleep(0.3)

    # Generate unrelated traffic
    net.get("h1").cmd("ping -c 4 10.0.0.4 &")
    net.get("h4").cmd("ping -c 4 10.0.0.1 &")
    time.sleep(2)

    loss = ping_loss(net, "h2", "h3")
    record("T6: h2→h3 rule persists after other traffic",
           loss == 100.0, f"{loss:.0f}%")
    del_drop("s1", "10.0.0.2", "10.0.0.3")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    setLogLevel("warning")
    print("\n" + "=" * 60)
    print("  Packet Drop Simulator – Regression Test Suite (POX)")
    print("=" * 60)

    net = build_net()
    net.start()
    time.sleep(2)

    try:
        t1_baseline(net)
        t2_drop_blocks(net)
        t3_non_targeted_ok(net)
        t4_remove_restores(net)
        t5_multiple_rules(net)
        t6_persistence(net)
        dump_flows("s1")
    finally:
        net.stop()

    banner("TEST SUMMARY")
    passed = sum(1 for _, ok in results if ok)
    total  = len(results)
    for name, ok in results:
        print(f"  {'✓' if ok else '✗'}  {name}")
    print(f"\n  Result: {passed}/{total} tests passed")
    print("=" * 60 + "\n")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
