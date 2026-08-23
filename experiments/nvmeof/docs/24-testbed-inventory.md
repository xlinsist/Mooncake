# Two-node testbed inventory and provenance boundary

## Paper-facing configuration

| Component | Client (`intel-bigmem-2`) | Target (`intel-bigmem`) |
| --- | --- | --- |
| Hostname | `smicro-amd-two-numa` | `intel-bigmem-nvm-stratix` |
| CPU | 2 × AMD EPYC 9754, 128 cores/socket, SMT2 | 2 × Intel Xeon Platinum 8358P, 32 cores/socket, SMT2 |
| Logical CPUs / NUMA nodes | 512 / 2 | 128 / 4 |
| Memory | 1,584,754,048 KiB (`MemTotal`) | 2,113,403,048 KiB (`MemTotal`) |
| Kernel | Linux `6.8.0-136-generic` | Linux `6.8.0-90-generic` |
| RDMA NIC | ConnectX-6 `mlx5_0` | ConnectX-6 `mlx5_0` |
| Link | HDR 200 Gb/s InfiniBand | HDR 200 Gb/s InfiniBand |
| NVMe role | Micron 7300 3.5 TB, ext4 at `/mnt/datassd` | Intel 400 GB, SPDK bdev `Nvme0n1` |

The persistent fabric path is NVMe-oF/RDMA at `10.0.0.5:4420`, NQN
`nqn.2026-08.local.mooncake:nof-phase1`, NSID 1. The client sees this namespace
as `/dev/nvme2n1`. The target uses SPDK commit
`186986cf1044eaaf73e1c67ebcf3a7ce2f1376bb`.

Store size/reuse/load/public-trace experiments use Mooncake workload checkout
`255736477145236f39702f44e7a523a36914828c`. The durable arrival-debt harness
records benchmark source commit
`d885387ed127f8c4b23587fc9455983df2646cad`; later repository commits only add
aggregates and documentation and must not be substituted for the executed
source version.

## Provenance boundary

The structured snapshot is committed at
`experiments/nvmeof/results/20260823T235050Z-testbed-inventory/inventory.json`.
It was collected read-only after the workload matrices on the same named hosts.
It did not restart a service, reconnect a namespace, write a device, or modify
the existing `nof-phase1` subsystem.

Same-run artifacts independently preserve the fields that affect claim
validity:

- the size-sweep raw archive records client hostname, Store commit, mounts,
  namespace, policy, target service, and final restoration;
- the durable pacing artifact records client kernel, benchmark source commit,
  local mount, trace digests, thread/fsync settings, and exact target
  subsystem/bdev/service restoration;
- the target bdev record identifies the exported Intel 400 GB device and SPDK
  namespace geometry.

CPU, memory, NIC PCI model, and package versions were not captured inside every
case. They must be described as a same-day post-run inventory snapshot, not as
per-case telemetry. No GPU participated in the reported Store or durable
storage-path experiments.

## Path distinction

The Store paired experiments compare direct and transparent execution on the
same selected backend and trace; the testbed table therefore supports their
wrapper-overhead claim.

The durable public-trace comparison does **not** use matched substrates. Its
local path is the client Micron NVMe with ext4. Its remote path is a temporary
target-side file-backed AIO bdev, NVMe-oF/RDMA, and client XFS. The persistent
Intel `Nvme0n1` identity in the table describes the Store/NoF testbed and target
service; it must not be presented as the backing device of the temporary
durable path.
