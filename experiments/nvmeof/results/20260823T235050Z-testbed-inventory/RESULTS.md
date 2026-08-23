# Two-node testbed inventory snapshot

This artifact records a read-only post-run inventory of the two hosts used by
the 2026-08-23 KV-cache Store and durable public-trace experiments. It adds the
CPU, memory, NIC, SSD, kernel, tool, and source-version fields needed by the
paper testbed table.

The snapshot does not retroactively prove that every hardware and package
field was unchanged during every earlier case. The workload artifacts provide
independent same-run evidence for the client hostname, source commit, mounts,
namespace, policy, trace digests, and target restoration. The paper must label
the additional CPU, memory, NIC PCI model, and tool versions as a same-day
post-run snapshot.

No workload or service was restarted. The capture used read-only local and SSH
commands plus the SPDK `bdev_get_bdevs -b Nvme0n1` RPC. The existing
`nof-phase1` subsystem was not modified.

Key topology:

- client: dual-socket AMD EPYC 9754, 1.5 TiB-class memory, local Micron 7300
  3.5 TB NVMe, ConnectX-6 HDR 200 Gb/s InfiniBand;
- target: dual-socket Intel Xeon Platinum 8358P, 2.0 TiB-class memory, exported
  Intel 400 GB NVMe, ConnectX-6 HDR 200 Gb/s InfiniBand;
- fabric: NVMe-oF/RDMA, `10.0.0.5:4420`, persistent NQN
  `nqn.2026-08.local.mooncake:nof-phase1`, NSID 1.

The structured source of truth is `inventory.json`; `capture-commands.md`
lists the read-only collection commands.
