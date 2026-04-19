"""
drop_controller.py  (POX version)
==================================
POX OpenFlow 1.0 controller for the Packet Drop Simulator.

Place this file inside the POX components directory:
    pox/ext/drop_controller.py

Launch with:
    sudo python3 pox.py log.level --DEBUG drop_controller

Behaviour
---------
* Acts as a learning L2 switch for all traffic by default.
* Maintains a list of (src_ip, dst_ip) DROP rules.
* On every packet_in, if the IP pair matches a drop rule → silently discard.
* Otherwise → learn MAC, install forwarding flow, forward packet.
* Drop rules are installed as high-priority OpenFlow flow entries
  with NO actions (= drop).

Author : SDN Mininet Project – UE24CS252B
"""

from pox.core import core
from pox.lib.util import dpid_to_str
import pox.openflow.libopenflow_01 as of
from pox.lib.packet import ethernet, ipv4
from pox.lib.addresses import IPAddr, EthAddr

log = core.getLogger()

# ── Priority levels ────────────────────────────────────────────────────────────
PRIORITY_DROP    = 200   # wins over everything
PRIORITY_FORWARD = 100   # learnt forwarding
PRIORITY_FLOOD   =   1   # table-miss fallback


class DropController:
    """
    Per-switch instance that handles OpenFlow events.

    drop_rules : list of (IPAddr, IPAddr) tuples  (src_ip, dst_ip)
    mac_to_port: dict  mac → port
    """

    def __init__(self, connection, drop_rules):
        self.connection  = connection
        self.mac_to_port = {}
    
        # DROP rule: h2 → h1
        self.drop_rules = drop_rules

        # Listen to OF events on this connection
        connection.addListeners(self)
        log.info("Switch %s connected.", dpid_to_str(connection.dpid))

        # Re-install all current drop rules onto this (possibly reconnected) switch
        for src_ip, dst_ip in self.drop_rules:
            self._install_drop_flow(src_ip, dst_ip)

    # ── OpenFlow event handlers ────────────────────────────────────────────────

    def _handle_PacketIn(self, event):
        """
        Called for every packet that hits the table-miss rule.
        1. Parse the packet.
        2. Check against drop rules.
        3. Learn MAC → port.
        4. Forward (and optionally install a flow rule).
        """
        packet_data = event.parsed
        if not packet_data.parsed:
            log.warning("Ignoring incomplete packet.")
            return

        in_port = event.port
        dpid    = event.connection.dpid

        eth = packet_data
        src_mac = eth.src
        dst_mac = eth.dst

        # ── DROP check ────────────────────────────────────────────────────────
        ip_pkt = packet_data.find("ipv4")
        if ip_pkt:
            for (src_ip, dst_ip) in self.drop_rules:
                if ip_pkt.srcip == src_ip and ip_pkt.dstip == dst_ip:
                    log.info("[DROP] %s → %s  (packet_in discarded on sw %s)",
                             ip_pkt.srcip, ip_pkt.dstip, dpid_to_str(dpid))
                    return   # discard – do not forward

        # ── MAC learning ──────────────────────────────────────────────────────
        self.mac_to_port[src_mac] = in_port

        # ── Forwarding ────────────────────────────────────────────────────────
        if dst_mac in self.mac_to_port:
            out_port = self.mac_to_port[dst_mac]

            # Install a proactive forwarding flow
            msg             = of.ofp_flow_mod()
            msg.priority    = PRIORITY_FORWARD
            msg.idle_timeout = 30
            msg.hard_timeout = 0
            msg.match        = of.ofp_match.from_packet(packet_data, in_port)
            msg.actions.append(of.ofp_action_output(port=out_port))
            msg.data         = event.ofp          # send buffered packet too
            self.connection.send(msg)
        else:
            # Destination unknown → flood
            msg = of.ofp_packet_out()
            msg.data = event.ofp
            msg.in_port = in_port
            msg.actions.append(of.ofp_action_output(port=of.OFPP_FLOOD))
            self.connection.send(msg)

    # ── Drop rule management ───────────────────────────────────────────────────

    def _install_drop_flow(self, src_ip, dst_ip):
        """Push a high-priority DROP flow entry (no actions) to the switch."""
        msg              = of.ofp_flow_mod()
        msg.priority     = PRIORITY_DROP
        msg.hard_timeout = 0
        msg.idle_timeout = 0
        msg.match.dl_type = 0x0800           # IPv4
        msg.match.nw_src  = IPAddr(str(src_ip))
        msg.match.nw_dst  = IPAddr(str(dst_ip))
        # No actions → DROP
        self.connection.send(msg)
        log.info("[RULE INSTALLED] DROP %s → %s on switch %s",
                 src_ip, dst_ip, dpid_to_str(self.connection.dpid))

    def _remove_drop_flow(self, src_ip, dst_ip):
        """Delete a DROP flow entry from the switch."""
        msg              = of.ofp_flow_mod()
        msg.command      = of.OFPFC_DELETE
        msg.priority     = PRIORITY_DROP
        msg.match.dl_type = 0x0800
        msg.match.nw_src  = IPAddr(str(src_ip))
        msg.match.nw_dst  = IPAddr(str(dst_ip))
        msg.out_port      = of.OFPP_NONE
        self.connection.send(msg)
        log.info("[RULE REMOVED] DROP %s → %s on switch %s",
                 src_ip, dst_ip, dpid_to_str(self.connection.dpid))


class DropControllerComponent:
    """
    POX component — manages all switch connections and the shared drop-rule list.

    To add/remove rules from Python (e.g. from the POX interactive shell):
        core.drop_controller.add_drop_rule("10.0.0.1", "10.0.0.2")
        core.drop_controller.remove_drop_rule("10.0.0.1", "10.0.0.2")
    """

    def __init__(self):
        # Shared drop rules list – (IPAddr, IPAddr) tuples
        self.drop_rules = [
            (IPAddr("10.0.0.2"), IPAddr("10.0.0.1"))
        ]
        # dpid → DropController instance
        self._switches   = {}

        core.openflow.addListeners(self)
        log.info("DropControllerComponent ready.")

    # ── POX event: new switch ──────────────────────────────────────────────────

    def _handle_ConnectionUp(self, event):
        sw = DropController(event.connection, self.drop_rules)
        self._switches[event.dpid] = sw

        # FORCE install drop rules after connection
        for src_ip, dst_ip in self.drop_rules:
            sw._install_drop_flow(src_ip, dst_ip)

    def _handle_ConnectionDown(self, event):
        self._switches.pop(event.dpid, None)
        log.info("Switch %s disconnected.", dpid_to_str(event.dpid))

    # ── Public API ─────────────────────────────────────────────────────────────

    def add_drop_rule(self, src_ip: str, dst_ip: str):
        """
        Add a DROP rule for (src_ip, dst_ip) on ALL connected switches.
        Call from topology.py via:
            from pox.core import core
            core.call_when_ready(lambda: core.drop_controller.add_drop_rule(...),
                                 "drop_controller")
        """
        rule = (IPAddr(src_ip), IPAddr(dst_ip))
        if rule not in self.drop_rules:
            self.drop_rules.append(rule)
        for sw in self._switches.values():
            sw._install_drop_flow(*rule)
        log.info("[API] add_drop_rule %s → %s", src_ip, dst_ip)

    def remove_drop_rule(self, src_ip: str, dst_ip: str):
        """Remove a DROP rule from the shared list and all connected switches."""
        rule = (IPAddr(src_ip), IPAddr(dst_ip))
        if rule in self.drop_rules:
            self.drop_rules.remove(rule)
        for sw in self._switches.values():
            sw._remove_drop_flow(*rule)
        log.info("[API] remove_drop_rule %s → %s", src_ip, dst_ip)

    def list_rules(self):
        """Print current drop rules to the log."""
        if not self.drop_rules:
            log.info("[API] No active drop rules.")
        for src, dst in self.drop_rules:
            log.info("[API]  DROP %s → %s", src, dst)


# ── POX entry point ────────────────────────────────────────────────────────────

def launch():
    """Called by POX when the component is loaded."""
    component = DropControllerComponent()
    core.register("drop_controller", component)
    log.info("Packet Drop Simulator controller launched.")
