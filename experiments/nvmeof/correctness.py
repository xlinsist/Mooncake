#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import uuid


def payload(seed: int, size: int) -> bytes:
    return random.Random(seed).randbytes(size)


def connect():
    from mooncake.store import MooncakeDistributedStore

    store = MooncakeDistributedStore()
    rc = store.setup(
        os.environ["LOCAL_HOSTNAME"],
        os.environ["METADATA_URL"],
        int(os.environ.get("GLOBAL_SEGMENT_SIZE", 1 << 30)),
        int(os.environ.get("LOCAL_BUFFER_SIZE", 1 << 30)),
        os.environ.get("PROTOCOL", "rdma"),
        os.environ["CLIENT_RDMA_DEVICE"],
        os.environ["MASTER_ADDR"],
    )
    if rc != 0:
        raise RuntimeError(f"store setup failed: {rc}")
    return store


def replica_config(memory_replicas: int):
    from mooncake.store import ReplicateConfig

    config = ReplicateConfig()
    config.replica_num = memory_replicas
    config.nof_replica_num = 1
    return config


def verify(store, key: str, expected: bytes):
    actual = store.get(key)
    if hashlib.sha256(actual).digest() != hashlib.sha256(expected).digest():
        raise AssertionError(f"SHA-256 mismatch for {key}")


def child_read(args):
    store = connect()
    expected = payload(args.seed, args.size)
    verify(store, args.key, expected)
    store.close()


def exercise_mode(memory_replicas: int, count: int, sizes: list[int], prefix: str):
    store = connect()
    config = replica_config(memory_replicas)
    keys = []
    for size_index, size in enumerate(sizes):
        for object_index in range(count):
            seed = size_index * 1_000_000 + object_index
            key = f"{prefix}-m{memory_replicas}-s{size}-i{object_index}"
            expected = payload(seed, size)
            rc = store.put(key, expected, config)
            if rc != 0:
                raise AssertionError(f"put failed for {key}: {rc}")
            verify(store, key, expected)
            verify(store, key, expected)
            keys.append((key, seed, size))

    key, seed, size = keys[len(keys) // 2]
    subprocess.run(
        [
            sys.executable,
            __file__,
            "child-read",
            "--key",
            key,
            "--seed",
            str(seed),
            "--size",
            str(size),
        ],
        check=True,
        env=os.environ,
    )

    duplicate_key, duplicate_seed, duplicate_size = keys[0]
    rc = store.put(duplicate_key, payload(duplicate_seed, duplicate_size), config)
    if rc != 0:
        raise AssertionError(f"idempotent duplicate put failed: {rc}")
    verify(store, duplicate_key, payload(duplicate_seed, duplicate_size))

    for key, _, _ in keys:
        rc = store.remove(key, True)
        if rc != 0:
            raise AssertionError(f"remove failed for {key}: {rc}")
        if store.get(key) not in (b"", None):
            raise AssertionError(f"removed key remains readable: {key}")
    store.close()


def unaligned_probe(prefix: str):
    store = connect()
    config = replica_config(0)
    key = f"{prefix}-unaligned"
    value = payload(42, 4097)
    rc = store.put(key, value, config)
    outcome = {"return_code": rc, "behavior": "rejected"}
    if rc == 0:
        verify(store, key, value)
        outcome["behavior"] = "accepted_and_verified"
        store.remove(key, True)
    store.close()
    return outcome


def stability(seconds: int, prefix: str):
    store = connect()
    configs = [replica_config(0), replica_config(1)]
    deadline = time.monotonic() + seconds
    operations = 0
    while time.monotonic() < deadline:
        size = (4096, 131072, 1048576)[operations % 3]
        value = payload(operations, size)
        key = f"{prefix}-stability-{operations}"
        config = configs[operations % 2]
        rc = store.put(key, value, config)
        if rc != 0:
            raise AssertionError(f"stability put failed: {rc}")
        verify(store, key, value)
        if store.remove(key, True) != 0:
            raise AssertionError("stability remove failed")
        operations += 1
    store.close()
    return operations


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--count", type=int, default=100)
    run.add_argument("--output", required=True)
    steady = sub.add_parser("stability")
    steady.add_argument("--seconds", type=int, default=60)
    steady.add_argument("--output", required=True)
    child = sub.add_parser("child-read")
    child.add_argument("--key", required=True)
    child.add_argument("--seed", type=int, required=True)
    child.add_argument("--size", type=int, required=True)
    args = parser.parse_args()

    if args.command == "child-read":
        child_read(args)
        return

    prefix = f"nvmeof-{uuid.uuid4().hex}"
    started = time.time()
    result = {"status": "pass", "started_epoch": started}
    try:
        if args.command == "run":
            sizes = [4096, 131072, 1048576, 8388608]
            exercise_mode(0, args.count, sizes, prefix)
            exercise_mode(1, args.count, sizes, prefix)
            result["unaligned"] = unaligned_probe(prefix)
            result.update({"objects_per_size": args.count, "sizes": sizes})
        else:
            result["operations"] = stability(args.seconds, prefix)
            result["duration_seconds"] = args.seconds
    except Exception as error:
        result.update({"status": "fail", "error": repr(error)})
        raise
    finally:
        result["elapsed_seconds"] = time.time() - started
        with open(args.output, "w", encoding="utf-8") as output:
            json.dump(result, output, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
