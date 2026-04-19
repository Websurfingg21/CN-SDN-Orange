SDN Mininet based Packet Drop Simulator.

Problem Statement : Simulate packet loss using SDN flow rules.

This project demonstrates how Software Defined Networking (SDN) can be used to control network behavior centrally by simulating packet loss using Mininet and an OpenFlow controller (POX). A custom topology with multiple hosts connected to a switch is created in Mininet, while the controller defines match-action flow rules that selectively drop packets based on source and destination IP addresses. Instead of relying on traditional distributed routing, the controller dynamically installs rules in the switch’s flow table, enabling fine-grained control over specific traffic flows (e.g., dropping packets from one host to another while allowing all other communication). The system behavior is verified using tools like ping and Wireshark to observe packet transmission and loss, and further validated through automated regression testing to ensure that drop rules persist and function correctly under different scenarios.

Setup and Execution explained in Project Report "PES2UG24CS577_VedantSharma.pdf"


PES2UG24CS577
Vedant Sharma
PES2UG24CS577@stu.pes.edu