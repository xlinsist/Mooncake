# Capture commands

The following read-only commands produced the inventory snapshot. Serial
numbers returned by NVMe tooling are intentionally omitted from
`inventory.json` because the paper needs device models, not unique device
identifiers.

```bash
ssh intel-bigmem-2 'hostname; uname -srmo; lscpu; free -h; \
  awk "/MemTotal/ {print}" /proc/meminfo; \
  lsblk -d -o NAME,SIZE,MODEL,ROTA,TRAN; rdma link; \
  cat /sys/class/infiniband/mlx5_0/ports/1/{rate,link_layer}; \
  nvme version; python3 --version; gcc --version; cmake --version; fio --version; \
  git -C /sharenvme/userhome/zhouxulin/mooncake-kv-e82f0bb7 rev-parse HEAD'

ssh intel-bigmem 'hostname; uname -srmo; lscpu; free -h; \
  awk "/MemTotal/ {print}" /proc/meminfo; \
  lsblk -d -o NAME,SIZE,MODEL,ROTA,TRAN; rdma link; \
  cat /sys/class/infiniband/mlx5_0/ports/1/{rate,link_layer}; \
  git -C /sharenvme/userhome/zhouxl/mooncake-nof-phase1/spdk rev-parse HEAD; \
  systemctl show mooncake-nof-spdk.service -p ActiveState -p SubState -p MainPID'

ssh intel-bigmem 'sudo -n \
  /sharenvme/userhome/zhouxl/mooncake-nof-phase1/spdk/scripts/rpc.py \
  bdev_get_bdevs -b Nvme0n1'
```

The NIC PCI model was read from the `mlx5_0` sysfs device BDF with `lspci -nn`.
The source commits for the Store and durable pacing families are also preserved
inside their respective raw artifacts.
