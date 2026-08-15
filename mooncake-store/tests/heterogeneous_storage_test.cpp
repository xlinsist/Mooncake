#include "placement_policy.h"
#include "master_service.h"
#include "crc32c.h"
#include "storage_backend.h"

#include <algorithm>
#include <atomic>
#include <filesystem>
#include <thread>
#include <vector>

#include <gtest/gtest.h>

namespace mooncake {
namespace test {

class HeterogeneousStorageTestAccess {
   public:
    static void SetHost(MasterService& service, const UUID& client_id,
                        const std::string& host_id) {
        service.UpdateClientHostId(client_id, host_id);
    }

    static void ExpireOwner(MasterService& service, const UUID& owner) {
        service.ClearInvalidHandles({});
        auto segments = service.segment_manager_.getSegmentAccess();
        segments.UnmountLocalDiskSegment(owner);
    }

    static int64_t LocalDiskUsedBytes(MasterService& service,
                                      const UUID& owner) {
        auto access = service.segment_manager_.getLocalDiskSegmentAccess();
        return access.getClientLocalDiskSegment()
            .at(owner)
            ->ssd_used_bytes.load(std::memory_order_relaxed);
    }
};

}  // namespace test
namespace {

PlacementContext Available(bool local, bool remote) {
    return PlacementContext{.key = "key",
                            .object_size = 4096,
                            .requester_host_id = "host",
                            .local_available = local,
                            .remote_available = remote};
}

MasterServiceConfig LocalMasterConfig() {
    MasterServiceConfig config;
    config.enable_offload = true;
    config.default_kv_lease_ttl = 0;
    return config;
}

void PutManagedLocal(MasterService& service, const UUID& client_id,
                     const std::string& key, const std::string& backend_id) {
    auto start = service.PutStart(client_id, key, TenantId::Default(), 1024,
                                  ManagedReplicateConfig());
    ASSERT_TRUE(start);
    ObjectMeta metadata;
    metadata.key = key;
    metadata.local_disk_transport_endpoint = "127.0.0.1:19001";
    metadata.local_disk_backend_id = backend_id;
    metadata.local_disk_locator = "objects/" + key;
    metadata.local_disk_generation = 1;
    ASSERT_TRUE(service.PutEnd(client_id, metadata, TenantId::Default(),
                               ReplicaType::LOCAL_DISK));
}

#ifdef USE_NOF
void PutManagedNof(MasterService& service, const UUID& client_id,
                   const std::string& key, const TenantId& tenant_id) {
    auto start = service.PutStart(client_id, key, tenant_id, 4096,
                                  ManagedReplicateConfig());
    ASSERT_TRUE(start.has_value());
    ASSERT_TRUE(
        service.PutEnd(client_id, key, tenant_id, ReplicaType::NOF_SSD));
}

NoFSegment TestNofSegment(const UUID& id, const std::string& name,
                          uintptr_t base, const std::string& endpoint,
                          size_t size = 16 * 1024 * 1024) {
    NoFSegment segment;
    segment.id = id;
    segment.name = name;
    segment.base = base;
    segment.size = size;
    segment.te_endpoint = endpoint;
    return segment;
}

uint64_t RemoteNofRemoveMetricValue() {
    const auto metrics = MasterMetricManager::instance().serialize_metrics();
    const std::string labels =
        "storage_remove_total{target=\"remote_nof\",result=\"success\"}";
    const auto metric_pos = metrics.find(labels);
    if (metric_pos == std::string::npos) {
        return 0;
    }
    const auto value_pos = metrics.find(' ', metric_pos + labels.size());
    if (value_pos == std::string::npos) {
        return 0;
    }
    return std::stoull(metrics.substr(value_pos + 1));
}
#endif

TEST(HeterogeneousStoragePolicyTest, ParsesSupportedPolicies) {
    EXPECT_EQ(ParsePlacementPolicy("legacy").value(),
              PlacementPolicyKind::LEGACY);
    EXPECT_EQ(ParsePlacementPolicy("LOCAL_ONLY").value(),
              PlacementPolicyKind::LOCAL_ONLY);
    EXPECT_EQ(ParsePlacementPolicy("remote_only").value(),
              PlacementPolicyKind::REMOTE_ONLY);
    EXPECT_EQ(ParsePlacementPolicy("round_robin").value(),
              PlacementPolicyKind::ROUND_ROBIN);
    EXPECT_FALSE(ParsePlacementPolicy("fallback").has_value());
}

TEST(HeterogeneousStoragePolicyTest, SafeObjectKeyIdIsStableAndOpaque) {
    EXPECT_EQ(SafeObjectKeyId("tenant-secret-key"),
              SafeObjectKeyId("tenant-secret-key"));
    EXPECT_NE(SafeObjectKeyId("tenant-secret-key"),
              SafeObjectKeyId("another-key"));
    EXPECT_EQ(SafeObjectKeyId("tenant-secret-key").size(), 8);
    EXPECT_EQ(SafeObjectKeyId("tenant-secret-key").find("secret"),
              std::string::npos);
}

TEST(HeterogeneousStoragePolicyTest, DistinguishesManagedAndManualConfigs) {
    ReplicateConfig explicit_config;
    EXPECT_EQ(
        explicit_config.placement_control.value_or(PlacementControl::MANUAL),
        PlacementControl::MANUAL);

    auto omitted_config = ManagedReplicateConfig();
    EXPECT_EQ(
        omitted_config.placement_control.value_or(PlacementControl::MANUAL),
        PlacementControl::MANAGED);
}

TEST(HeterogeneousStoragePolicyTest, StrictPoliciesNeverFallback) {
    PlacementPolicy local(PlacementPolicyKind::LOCAL_ONLY);
    EXPECT_EQ(local.SelectWriteTarget(Available(true, true)).value(),
              StorageTarget::LOCAL_NVME);
    EXPECT_EQ(local.SelectWriteTarget(Available(false, true)).error(),
              ErrorCode::NO_AVAILABLE_HANDLE);

    PlacementPolicy remote(PlacementPolicyKind::REMOTE_ONLY);
    EXPECT_EQ(remote.SelectWriteTarget(Available(true, true)).value(),
              StorageTarget::REMOTE_NOF);
    EXPECT_EQ(remote.SelectWriteTarget(Available(true, false)).error(),
              ErrorCode::NO_AVAILABLE_HANDLE);
}

TEST(HeterogeneousStoragePolicyTest, RoundRobinIsStableAndSkipsUnavailable) {
    PlacementPolicy policy(PlacementPolicyKind::ROUND_ROBIN);
    EXPECT_EQ(policy.SelectWriteTarget(Available(true, true)).value(),
              StorageTarget::LOCAL_NVME);
    EXPECT_EQ(policy.SelectWriteTarget(Available(true, true)).value(),
              StorageTarget::REMOTE_NOF);
    EXPECT_EQ(policy.SelectWriteTarget(Available(true, true)).value(),
              StorageTarget::LOCAL_NVME);
    EXPECT_EQ(policy.SelectWriteTarget(Available(false, true)).value(),
              StorageTarget::REMOTE_NOF);
    EXPECT_EQ(policy.SelectWriteTarget(Available(true, false)).value(),
              StorageTarget::LOCAL_NVME);
    EXPECT_EQ(policy.SelectWriteTarget(Available(false, false)).error(),
              ErrorCode::NO_AVAILABLE_HANDLE);
}

TEST(HeterogeneousStoragePolicyTest, RoundRobinSelectionIsThreadSafe) {
    PlacementPolicy policy(PlacementPolicyKind::ROUND_ROBIN);
    std::atomic<uint64_t> local{0};
    std::atomic<uint64_t> remote{0};
    std::vector<std::thread> threads;
    for (int thread_index = 0; thread_index < 8; ++thread_index) {
        threads.emplace_back([&] {
            for (int iteration = 0; iteration < 1000; ++iteration) {
                auto target = policy.SelectWriteTarget(Available(true, true));
                ASSERT_TRUE(target.has_value());
                if (*target == StorageTarget::LOCAL_NVME) {
                    local.fetch_add(1, std::memory_order_relaxed);
                } else {
                    remote.fetch_add(1, std::memory_order_relaxed);
                }
            }
        });
    }
    for (auto& thread : threads) {
        thread.join();
    }
    EXPECT_EQ(local.load(), 4000);
    EXPECT_EQ(remote.load(), 4000);
}

TEST(HeterogeneousStoragePolicyTest,
     SessionScatterRangeReadSupportsMemoryAndNof) {
    const auto complete = ReplicaStatus::COMPLETE;
    EXPECT_TRUE(PlacementPolicy::SupportsScatterRangeRead(
        Replica::Descriptor{1, MemoryDescriptor{}, complete}));
    EXPECT_TRUE(PlacementPolicy::SupportsScatterRangeRead(
        Replica::Descriptor{2, NoFDescriptor{}, complete}));
    EXPECT_FALSE(PlacementPolicy::SupportsScatterRangeRead(
        Replica::Descriptor{3, DiskDescriptor{}, complete}));
    EXPECT_FALSE(PlacementPolicy::SupportsScatterRangeRead(
        Replica::Descriptor{4, LocalDiskDescriptor{}, complete}));
}

TEST(HeterogeneousStorageMasterTest, DirectLocalLifecyclePersistsLocator) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID client_id{11, 22};
    ASSERT_TRUE(service.MountLocalDiskSegment(client_id, true).has_value());

    ReplicateConfig config;
    config.placement_control = PlacementControl::MANAGED;
    auto start = service.PutStart(client_id, "direct-local",
                                  TenantId::Default(), 4096, config);
    ASSERT_TRUE(start.has_value());
    ASSERT_EQ(start->size(), 1);
    EXPECT_TRUE(start->front().is_local_disk_replica());
    EXPECT_EQ(start->front().status, ReplicaStatus::PROCESSING);

    ObjectMeta metadata;
    metadata.key = "direct-local";
    metadata.local_disk_transport_endpoint = "127.0.0.1:19001";
    metadata.local_disk_backend_id = "backend-a";
    metadata.local_disk_locator = "objects/direct-local";
    metadata.local_disk_generation = 7;
    ASSERT_TRUE(service
                    .PutEnd(client_id, metadata, TenantId::Default(),
                            ReplicaType::LOCAL_DISK)
                    .has_value());

    auto replicas = service.GetReplicaList("direct-local", TenantId::Default());
    ASSERT_TRUE(replicas.has_value());
    ASSERT_EQ(replicas->replicas.size(), 1);
    const auto& descriptor =
        replicas->replicas.front().get_local_disk_descriptor();
    EXPECT_EQ(descriptor.backend_id.value_or(""), "backend-a");
    EXPECT_EQ(descriptor.locator.value_or(""), "objects/direct-local");
    EXPECT_EQ(descriptor.generation.value_or(0), 7);

    ASSERT_TRUE(
        service.Remove("direct-local", TenantId::Default(), true).has_value());
    auto removals = service.PollLocalDiskRemovals(client_id);
    ASSERT_TRUE(removals.has_value());
    ASSERT_EQ(removals->size(), 1);
    EXPECT_EQ(removals->front().descriptor.locator.value_or(""),
              "objects/direct-local");
    ASSERT_TRUE(service
                    .AckLocalDiskRemoval(client_id, "direct-local",
                                         TenantId::Default(), true)
                    .has_value());
    EXPECT_EQ(service.ExistKey("direct-local", TenantId::Default()).value(),
              false);
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     DirectLocalRemoveDeletesPhysicalObjectBeforeMetadataAck) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    const auto root =
        std::filesystem::temp_directory_path() /
        ("mooncake-direct-remove-" + UuidToString(generate_uuid()));
    ASSERT_TRUE(std::filesystem::create_directories(root));
    {
        FileStorageConfig storage_config;
        storage_config.storage_filepath = root.string();
        FilePerKeyConfig backend_config;
        backend_config.fsdir = "objects";
        backend_config.enable_eviction = false;
        StorageBackendAdaptor backend(storage_config, backend_config);
        ASSERT_TRUE(backend.Init());

        MasterService service(LocalMasterConfig());
        const UUID owner{101, 102};
        ASSERT_TRUE(service.MountLocalDiskSegment(owner, true));
        auto start =
            service.PutStart(owner, "physical-remove", TenantId::Default(),
                             4096, ManagedReplicateConfig());
        ASSERT_TRUE(start);
        ASSERT_EQ(start->size(), 1);
        ASSERT_TRUE(start->front().is_local_disk_replica());

        std::string value(4096, 'x');
        auto staged = backend.StageObject("physical-remove",
                                          {{value.data(), value.size()}});
        ASSERT_TRUE(staged);
        auto locator = backend.CommitObject(*staged);
        ASSERT_TRUE(locator);
        ObjectMeta metadata;
        metadata.key = "physical-remove";
        metadata.local_disk_transport_endpoint = "127.0.0.1:19012";
        metadata.local_disk_backend_id = locator->backend_id;
        metadata.local_disk_locator = locator->locator;
        metadata.local_disk_generation = locator->generation;
        ASSERT_TRUE(service.PutEnd(owner, metadata, TenantId::Default(),
                                   ReplicaType::LOCAL_DISK));

        std::string loaded(value.size(), '\0');
        ASSERT_TRUE(
            backend.LoadObject(*locator, {loaded.data(), loaded.size()}));
        EXPECT_EQ(loaded, value);
        ASSERT_TRUE(
            service.Remove("physical-remove", TenantId::Default(), true));
        auto removed_query =
            service.GetReplicaList("physical-remove", TenantId::Default());
        ASSERT_FALSE(removed_query);
        EXPECT_EQ(removed_query.error(), ErrorCode::OBJECT_NOT_FOUND);
        auto removals = service.PollLocalDiskRemovals(owner);
        ASSERT_TRUE(removals);
        ASSERT_EQ(removals->size(), 1);
        EXPECT_EQ(removals->front().descriptor.locator.value_or(""),
                  locator->locator);

        ASSERT_TRUE(backend.RemoveObject(*locator));
        ASSERT_TRUE(backend.RemoveObject(*locator));
        auto stale_read =
            backend.LoadObject(*locator, {loaded.data(), loaded.size()});
        EXPECT_FALSE(stale_read);
        ASSERT_TRUE(service.AckLocalDiskRemoval(owner, "physical-remove",
                                                TenantId::Default(), true));
        EXPECT_FALSE(
            service.ExistKey("physical-remove", TenantId::Default()).value());
    }
    std::filesystem::remove_all(root);
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     LocalUpsertPersistsOldLocatorUntilOwnerAcknowledgesRemoval) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID owner{13, 14};
    ASSERT_TRUE(service.MountLocalDiskSegment(owner, true));
    PutManagedLocal(service, owner, "upsert-local", "backend-a");

    auto start = service.UpsertStart(owner, "upsert-local", TenantId::Default(),
                                     1024, ManagedReplicateConfig());
    ASSERT_TRUE(start.has_value());
    ObjectMeta metadata;
    metadata.key = "upsert-local";
    metadata.local_disk_transport_endpoint = "127.0.0.1:19001";
    metadata.local_disk_backend_id = "backend-a";
    metadata.local_disk_locator = "objects/upsert-local-v2";
    metadata.local_disk_generation = 2;
    ASSERT_TRUE(service.UpsertEnd(owner, metadata, TenantId::Default(),
                                  ReplicaType::LOCAL_DISK));

    auto replicas = service.GetReplicaList("upsert-local", TenantId::Default());
    ASSERT_TRUE(replicas.has_value());
    ASSERT_EQ(1u, replicas->replicas.size());
    EXPECT_EQ(
        "objects/upsert-local-v2",
        replicas->replicas.front().get_local_disk_descriptor().locator.value_or(
            ""));

    auto removals = service.PollLocalDiskRemovals(owner);
    ASSERT_TRUE(removals.has_value());
    ASSERT_EQ(1u, removals->size());
    EXPECT_EQ("objects/upsert-local",
              removals->front().descriptor.locator.value_or(""));

    auto next_start =
        service.UpsertStart(owner, "upsert-local", TenantId::Default(), 1024,
                            ManagedReplicateConfig());
    ASSERT_TRUE(next_start.has_value());
    metadata.local_disk_locator = "objects/upsert-local-v3";
    metadata.local_disk_generation = 3;
    ASSERT_TRUE(service.UpsertEnd(owner, metadata, TenantId::Default(),
                                  ReplicaType::LOCAL_DISK));
    removals = service.PollLocalDiskRemovals(owner);
    ASSERT_TRUE(removals.has_value());
    ASSERT_EQ(2u, removals->size());
    ASSERT_TRUE(service.AckLocalDiskRemoval(owner, "upsert-local",
                                            TenantId::Default(), true));

    auto current = service.GetReplicaList("upsert-local", TenantId::Default());
    ASSERT_TRUE(current.has_value());
    ASSERT_EQ(1u, current->replicas.size());
    EXPECT_EQ(
        "objects/upsert-local-v3",
        current->replicas.front().get_local_disk_descriptor().locator.value_or(
            ""));
    EXPECT_TRUE(service.PollLocalDiskRemovals(owner)->empty());

    ASSERT_TRUE(service.Remove("upsert-local", TenantId::Default(), true));
    auto final_removals = service.PollLocalDiskRemovals(owner);
    ASSERT_TRUE(final_removals.has_value());
    ASSERT_EQ(1u, final_removals->size());
    EXPECT_EQ("objects/upsert-local-v3",
              final_removals->front().descriptor.locator.value_or(""));
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest, RecordsManagedPlacementDecisions) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID client_id{23, 24};

    auto unavailable =
        service.PutStart(client_id, "metric-unavailable", TenantId::Default(),
                         4096, ManagedReplicateConfig());
    EXPECT_FALSE(unavailable.has_value());
    ASSERT_TRUE(service.MountLocalDiskSegment(client_id, true));
    auto selected =
        service.PutStart(client_id, "metric-success", TenantId::Default(), 4096,
                         ManagedReplicateConfig());
    ASSERT_TRUE(selected.has_value());

    const auto metrics = MasterMetricManager::instance().serialize_metrics();
    EXPECT_NE(metrics.find("placement_decision_total{policy=\"local_only\","
                           "target=\"local_nvme\",result=\"unavailable\"}"),
              std::string::npos);
    EXPECT_NE(metrics.find("placement_decision_total{policy=\"local_only\","
                           "target=\"local_nvme\",result=\"success\"}"),
              std::string::npos);
    EXPECT_NE(metrics.find("placement_decision_latency_us"), std::string::npos);
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

#ifdef USE_NOF
TEST(HeterogeneousStorageMasterTest, RecordsRemoteNofRemoveFromMetadata) {
    setenv("MC_HETERO_STORAGE_POLICY", "remote_only", 1);
    MasterService service(LocalMasterConfig());
    NoFSegment segment;
    segment.id = {71, 72};
    segment.name = "metric-nof";
    segment.base = 0x400000000;
    segment.size = 16 * 1024 * 1024;
    segment.te_endpoint = "127.0.0.1:19010";
    const UUID client_id{73, 74};
    ASSERT_TRUE(service.MountNoFSegment(segment, client_id));

    PutManagedNof(service, client_id, "metric-remote-remove",
                  TenantId::Default());
    const auto before = RemoteNofRemoveMetricValue();
    ASSERT_TRUE(
        service.Remove("metric-remote-remove", TenantId::Default(), true));
    EXPECT_EQ(RemoteNofRemoveMetricValue(), before + 1);
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest, RecordsBulkRemoteNofRemovalsPerObject) {
    setenv("MC_HETERO_STORAGE_POLICY", "remote_only", 1);
    MasterService service(LocalMasterConfig());
    NoFSegment segment;
    segment.id = {81, 82};
    segment.name = "bulk-metric-nof";
    segment.base = 0x500000000;
    segment.size = 16 * 1024 * 1024;
    segment.te_endpoint = "127.0.0.1:19011";
    const UUID client_id{83, 84};
    ASSERT_TRUE(service.MountNoFSegment(segment, client_id));

    const auto& tenant = TenantId::Default();
    PutManagedNof(service, client_id, "batch-a", tenant);
    PutManagedNof(service, client_id, "batch-b", tenant);
    PutManagedNof(service, client_id, "regex-a", tenant);
    PutManagedNof(service, client_id, "tenant-all", tenant);
    const auto before = RemoteNofRemoveMetricValue();

    auto batch = service.BatchRemove({"batch-a", "batch-b"}, tenant, true);
    ASSERT_EQ(batch.size(), 2);
    EXPECT_TRUE(batch[0].has_value());
    EXPECT_TRUE(batch[1].has_value());
    EXPECT_EQ(service.RemoveByRegex("^regex-", tenant, true).value(), 1);
    EXPECT_EQ(service.RemoveAll(tenant, true), 1);
    PutManagedNof(service, client_id, "global-all", tenant);
    EXPECT_EQ(service.RemoveAll(true), 1);
    EXPECT_EQ(RemoteNofRemoveMetricValue(), before + 5);
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     RemoteOnlyPublishesAndRemovesOnlyNofReplica) {
    setenv("MC_HETERO_STORAGE_POLICY", "remote_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID client_id{75, 76};
    ASSERT_TRUE(service.MountLocalDiskSegment(client_id, true));
    ASSERT_TRUE(
        service.MountNoFSegment(TestNofSegment({77, 78}, "remote-only-nof",
                                               0x410000000, "127.0.0.1:19013"),
                                client_id));

    PutManagedNof(service, client_id, "remote-only-lifecycle",
                  TenantId::Default());
    auto replicas =
        service.GetReplicaList("remote-only-lifecycle", TenantId::Default());
    ASSERT_TRUE(replicas);
    ASSERT_EQ(replicas->replicas.size(), 1);
    EXPECT_TRUE(replicas->replicas.front().is_nof_replica());
    EXPECT_EQ(replicas->replicas.front().status, ReplicaStatus::COMPLETE);

    ASSERT_TRUE(
        service.Remove("remote-only-lifecycle", TenantId::Default(), true));
    auto removed =
        service.GetReplicaList("remote-only-lifecycle", TenantId::Default());
    ASSERT_FALSE(removed);
    EXPECT_EQ(removed.error(), ErrorCode::OBJECT_NOT_FOUND);
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     ExplicitManualConfigIsNotRewrittenByGlobalPolicy) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID client_id{91, 92};
    ASSERT_TRUE(service.MountLocalDiskSegment(client_id, true));
    ASSERT_TRUE(service.MountNoFSegment(
        TestNofSegment({93, 94}, "manual-nof", 0x610000000, "127.0.0.1:19016"),
        client_id));

    ReplicateConfig manual_config;
    manual_config.replica_num = 0;
    manual_config.nof_replica_num = 1;
    auto start = service.PutStart(client_id, "manual-nof-under-local-policy",
                                  TenantId::Default(), 4096, manual_config);
    ASSERT_TRUE(start);
    ASSERT_EQ(start->size(), 1);
    EXPECT_TRUE(start->front().is_nof_replica());
    EXPECT_FALSE(start->front().is_local_disk_replica());

    ASSERT_TRUE(service.PutRevoke(client_id, "manual-nof-under-local-policy",
                                  TenantId::Default(), ReplicaType::NOF_SSD));
    EXPECT_FALSE(service
                     .GetReplicaList("manual-nof-under-local-policy",
                                     TenantId::Default())
                     .has_value());
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     RoundRobinPublishesAlternatingBackendDescriptors) {
    setenv("MC_HETERO_STORAGE_POLICY", "round_robin", 1);
    MasterService service(LocalMasterConfig());
    const UUID client_id{85, 86};
    ASSERT_TRUE(service.MountLocalDiskSegment(client_id, true));
    ASSERT_TRUE(
        service.MountNoFSegment(TestNofSegment({87, 88}, "round-robin-nof",
                                               0x510000000, "127.0.0.1:19014"),
                                client_id));

    for (size_t index = 0; index < 4; ++index) {
        const std::string key =
            "round-robin-lifecycle-" + std::to_string(index);
        auto start = service.PutStart(client_id, key, TenantId::Default(), 4096,
                                      ManagedReplicateConfig());
        ASSERT_TRUE(start);
        ASSERT_EQ(start->size(), 1);
        const bool expect_local = index % 2 == 0;
        if (expect_local) {
            ASSERT_TRUE(start->front().is_local_disk_replica());
            ObjectMeta metadata;
            metadata.key = key;
            metadata.local_disk_transport_endpoint = "127.0.0.1:19015";
            metadata.local_disk_backend_id = "round-robin-backend";
            metadata.local_disk_locator = "objects/" + key;
            metadata.local_disk_generation = index + 1;
            ASSERT_TRUE(service.PutEnd(client_id, metadata, TenantId::Default(),
                                       ReplicaType::LOCAL_DISK));
        } else {
            ASSERT_TRUE(start->front().is_nof_replica());
            ASSERT_TRUE(service.PutEnd(client_id, key, TenantId::Default(),
                                       ReplicaType::NOF_SSD));
        }

        auto replicas = service.GetReplicaList(key, TenantId::Default());
        ASSERT_TRUE(replicas);
        ASSERT_EQ(replicas->replicas.size(), 1);
        EXPECT_EQ(replicas->replicas.front().is_local_disk_replica(),
                  expect_local);
        EXPECT_EQ(replicas->replicas.front().is_nof_replica(), !expect_local);
        EXPECT_EQ(replicas->replicas.front().status, ReplicaStatus::COMPLETE);
        ASSERT_TRUE(service.Remove(key, TenantId::Default(), true));
        if (expect_local) {
            ASSERT_TRUE(service.AckLocalDiskRemoval(client_id, key,
                                                    TenantId::Default(), true));
        }
        EXPECT_FALSE(service.ExistKey(key, TenantId::Default()).value());
    }
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     StrictManagedPoliciesRejectTargetsWithoutCapacity) {
    const UUID client_id{95, 96};
    {
        setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
        MasterService service(LocalMasterConfig());
        ASSERT_TRUE(service.MountLocalDiskSegment(client_id, true));
        ASSERT_TRUE(service.ReportSsdCapacity(client_id, 4095));

        auto start = service.PutStart(client_id, "full-local",
                                      TenantId::Default(), 4096,
                                      ManagedReplicateConfig());
        ASSERT_FALSE(start);
        EXPECT_EQ(start.error(), ErrorCode::NO_AVAILABLE_HANDLE);
    }
    {
        setenv("MC_HETERO_STORAGE_POLICY", "remote_only", 1);
        MasterService service(LocalMasterConfig());
        ASSERT_TRUE(service.MountNoFSegment(
            TestNofSegment({97, 98}, "full-nof", 0x710000000,
                           "127.0.0.1:19017", 4095),
            client_id));

        auto start = service.PutStart(client_id, "full-remote",
                                      TenantId::Default(), 4096,
                                      ManagedReplicateConfig());
        ASSERT_FALSE(start);
        EXPECT_EQ(start.error(), ErrorCode::NO_AVAILABLE_HANDLE);
    }
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     RevokedLocalReservationDoesNotConsumeReportedCapacity) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID client_id{103, 104};
    ASSERT_TRUE(service.MountLocalDiskSegment(client_id, true));
    ASSERT_TRUE(service.ReportSsdCapacity(client_id, 4096));

    auto first = service.PutStart(client_id, "revoked-capacity",
                                  TenantId::Default(), 4096,
                                  ManagedReplicateConfig());
    ASSERT_TRUE(first);
    ASSERT_TRUE(service.PutRevoke(client_id, "revoked-capacity",
                                  TenantId::Default(),
                                  ReplicaType::LOCAL_DISK));
    EXPECT_EQ(test::HeterogeneousStorageTestAccess::LocalDiskUsedBytes(
                  service, client_id),
              0);

    auto retry = service.PutStart(client_id, "capacity-after-revoke",
                                  TenantId::Default(), 4096,
                                  ManagedReplicateConfig());
    ASSERT_TRUE(retry);
    EXPECT_TRUE(retry->front().is_local_disk_replica());
    ASSERT_TRUE(service.PutRevoke(client_id, "capacity-after-revoke",
                                  TenantId::Default(),
                                  ReplicaType::LOCAL_DISK));
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     RoundRobinSkipsFullTargetWithoutAdvancingRotation) {
    setenv("MC_HETERO_STORAGE_POLICY", "round_robin", 1);
    MasterService service(LocalMasterConfig());
    const UUID client_id{99, 100};
    ASSERT_TRUE(service.MountLocalDiskSegment(client_id, true));
    ASSERT_TRUE(service.ReportSsdCapacity(client_id, 4096));
    ASSERT_TRUE(service.MountNoFSegment(
        TestNofSegment({101, 102}, "capacity-round-robin-nof", 0x720000000,
                       "127.0.0.1:19018"),
        client_id));

    auto first = service.PutStart(client_id, "capacity-local",
                                  TenantId::Default(), 4096,
                                  ManagedReplicateConfig());
    ASSERT_TRUE(first);
    ASSERT_TRUE(first->front().is_local_disk_replica());
    ObjectMeta local_metadata;
    local_metadata.key = "capacity-local";
    local_metadata.local_disk_transport_endpoint = "127.0.0.1:19019";
    local_metadata.local_disk_backend_id = "capacity-backend";
    local_metadata.local_disk_locator = "objects/capacity-local";
    local_metadata.local_disk_generation = 1;
    ASSERT_TRUE(service.PutEnd(client_id, local_metadata, TenantId::Default(),
                               ReplicaType::LOCAL_DISK));
    EXPECT_EQ(test::HeterogeneousStorageTestAccess::LocalDiskUsedBytes(
                  service, client_id),
              4096);

    for (const std::string key : {"capacity-remote-a", "capacity-remote-b"}) {
        auto start = service.PutStart(client_id, key, TenantId::Default(), 4096,
                                      ManagedReplicateConfig());
        ASSERT_TRUE(start);
        ASSERT_TRUE(start->front().is_nof_replica());
        ASSERT_TRUE(service.PutRevoke(client_id, key, TenantId::Default(),
                                      ReplicaType::NOF_SSD));
    }

    ASSERT_TRUE(service.Remove("capacity-local", TenantId::Default(), true));
    ASSERT_TRUE(service.AckLocalDiskRemoval(
        client_id, "capacity-local", TenantId::Default(), true));
    EXPECT_EQ(test::HeterogeneousStorageTestAccess::LocalDiskUsedBytes(
                  service, client_id),
              0);

    auto after_release = service.PutStart(
        client_id, "capacity-after-release", TenantId::Default(), 4096,
        ManagedReplicateConfig());
    ASSERT_TRUE(after_release);
    EXPECT_TRUE(after_release->front().is_nof_replica());
    ASSERT_TRUE(service.PutRevoke(client_id, "capacity-after-release",
                                  TenantId::Default(), ReplicaType::NOF_SSD));
    unsetenv("MC_HETERO_STORAGE_POLICY");
}
#endif

TEST(HeterogeneousStorageMasterTest, ManagedRevokeLeavesNoPublishedReplica) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID client_id{89, 90};
    ASSERT_TRUE(service.MountLocalDiskSegment(client_id, true));
    auto start =
        service.PutStart(client_id, "revoke-no-phantom", TenantId::Default(),
                         4096, ManagedReplicateConfig());
    ASSERT_TRUE(start);
    ASSERT_EQ(start->size(), 1);
    ASSERT_EQ(start->front().status, ReplicaStatus::PROCESSING);

    ASSERT_TRUE(service.PutRevoke(client_id, "revoke-no-phantom",
                                  TenantId::Default(),
                                  ReplicaType::LOCAL_DISK));
    auto replicas =
        service.GetReplicaList("revoke-no-phantom", TenantId::Default());
    ASSERT_FALSE(replicas);
    EXPECT_EQ(replicas.error(), ErrorCode::OBJECT_NOT_FOUND);
    EXPECT_FALSE(
        service.ExistKey("revoke-no-phantom", TenantId::Default()).value());
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest, BackendRestartRebindsDurableOwnership) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID original_owner{31, 32};
    const UUID restarted_owner{41, 42};
    test::HeterogeneousStorageTestAccess::SetHost(service, original_owner,
                                                  "stable-host");
    test::HeterogeneousStorageTestAccess::SetHost(service, restarted_owner,
                                                  "stable-host");
    ASSERT_TRUE(service.MountLocalDiskSegment(original_owner, true));

    auto config = ManagedReplicateConfig();
    auto start = service.PutStart(original_owner, "restart-local",
                                  TenantId::Default(), 1024, config);
    ASSERT_TRUE(start);
    ObjectMeta metadata;
    metadata.key = "restart-local";
    metadata.local_disk_transport_endpoint = "127.0.0.1:19001";
    metadata.local_disk_backend_id = "stable-backend";
    metadata.local_disk_locator = "objects/version-a";
    metadata.local_disk_generation = 1;
    ASSERT_TRUE(service.PutEnd(original_owner, metadata, TenantId::Default(),
                               ReplicaType::LOCAL_DISK));

    test::HeterogeneousStorageTestAccess::ExpireOwner(service, original_owner);
    auto unavailable_before_rebind =
        service.GetReplicaList("restart-local", TenantId::Default());
    ASSERT_FALSE(unavailable_before_rebind);
    EXPECT_EQ(unavailable_before_rebind.error(),
              ErrorCode::REPLICA_IS_NOT_READY);

    ASSERT_TRUE(service.MountLocalDiskSegment(restarted_owner, true));
    auto rebound = service.RebindLocalDiskBackend(
        restarted_owner, "stable-backend", "127.0.0.1:19002");
    ASSERT_TRUE(rebound);
    EXPECT_EQ(*rebound, 1);
    auto replicas =
        service.GetReplicaList("restart-local", TenantId::Default());
    ASSERT_TRUE(replicas);
    const auto& descriptor =
        replicas->replicas.front().get_local_disk_descriptor();
    EXPECT_EQ(descriptor.client_id, restarted_owner);
    EXPECT_EQ(descriptor.transport_endpoint, "127.0.0.1:19002");

    ASSERT_TRUE(service.Remove("restart-local", TenantId::Default(), true));
    ASSERT_TRUE(service.PollLocalDiskRemovals(original_owner)->empty());
    auto removals = service.PollLocalDiskRemovals(restarted_owner);
    ASSERT_TRUE(removals);
    ASSERT_EQ(removals->size(), 1);
    EXPECT_EQ(removals->front().descriptor.backend_id.value_or(""),
              "stable-backend");
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     LocalReplicaIsUnreadableUntilOwnerSegmentIsMounted) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID owner{35, 36};
    ASSERT_TRUE(service.MountLocalDiskSegment(owner, true));
    PutManagedLocal(service, owner, "owner-offline", "stable-offline-backend");

    test::HeterogeneousStorageTestAccess::ExpireOwner(service, owner);
    auto unavailable =
        service.GetReplicaList("owner-offline", TenantId::Default());
    ASSERT_FALSE(unavailable);
    EXPECT_EQ(unavailable.error(), ErrorCode::REPLICA_IS_NOT_READY);

    ASSERT_TRUE(service.MountLocalDiskSegment(owner, true));
    auto available =
        service.GetReplicaList("owner-offline", TenantId::Default());
    ASSERT_TRUE(available);
    ASSERT_EQ(available->replicas.size(), 1);
    EXPECT_TRUE(available->replicas.front().is_local_disk_replica());
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest, BackendRebindRequiresMountedOwner) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID original_owner{43, 44};
    const UUID unmounted_owner{45, 46};
    ASSERT_TRUE(service.MountLocalDiskSegment(original_owner, true));
    PutManagedLocal(service, original_owner, "rebind-auth", "stable-backend");

    auto rebound = service.RebindLocalDiskBackend(
        unmounted_owner, "stable-backend", "127.0.0.1:19003");
    ASSERT_FALSE(rebound);
    EXPECT_EQ(rebound.error(), ErrorCode::SEGMENT_NOT_FOUND);

    auto replicas = service.GetReplicaList("rebind-auth", TenantId::Default());
    ASSERT_TRUE(replicas);
    const auto& descriptor =
        replicas->replicas.front().get_local_disk_descriptor();
    EXPECT_EQ(descriptor.client_id, original_owner);
    EXPECT_EQ(descriptor.transport_endpoint, "127.0.0.1:19001");
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     BackendRebindRejectsMountedOwnerFromDifferentHost) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID original_owner{47, 48};
    const UUID wrong_host_owner{49, 50};
    test::HeterogeneousStorageTestAccess::SetHost(service, original_owner,
                                                  "host-a");
    test::HeterogeneousStorageTestAccess::SetHost(service, wrong_host_owner,
                                                  "host-b");
    ASSERT_TRUE(service.MountLocalDiskSegment(original_owner, true));
    PutManagedLocal(service, original_owner, "rebind-host-auth",
                    "stable-host-backend");
    test::HeterogeneousStorageTestAccess::ExpireOwner(service, original_owner);
    ASSERT_TRUE(service.MountLocalDiskSegment(wrong_host_owner, true));

    auto rebound = service.RebindLocalDiskBackend(
        wrong_host_owner, "stable-host-backend", "127.0.0.1:19004");
    ASSERT_FALSE(rebound);
    EXPECT_EQ(rebound.error(), ErrorCode::ILLEGAL_CLIENT);

    auto replicas =
        service.GetReplicaList("rebind-host-auth", TenantId::Default());
    ASSERT_FALSE(replicas);
    EXPECT_EQ(replicas.error(), ErrorCode::REPLICA_IS_NOT_READY);
    auto admin_replicas = service.GetReplicaListForAdmin(
        "rebind-host-auth", TenantId::Default());
    ASSERT_TRUE(admin_replicas);
    const auto& descriptor =
        admin_replicas->replicas.front().get_local_disk_descriptor();
    EXPECT_EQ(descriptor.client_id, original_owner);
    EXPECT_EQ(descriptor.host_id.value_or(""), "host-a");
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest,
     BulkRemoveRetainsPhysicalDeleteTombstones) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    MasterService service(LocalMasterConfig());
    const UUID owner{51, 52};
    ASSERT_TRUE(service.MountLocalDiskSegment(owner, true));
    PutManagedLocal(service, owner, "bulk-a", "bulk-backend");
    PutManagedLocal(service, owner, "bulk-b", "bulk-backend");

    auto batch_result = service.BatchRemove(std::vector<std::string>{"bulk-a"},
                                            TenantId::Default(), true);
    ASSERT_EQ(batch_result.size(), 1);
    ASSERT_TRUE(batch_result.front());
    auto regex_result =
        service.RemoveByRegex("bulk-b", TenantId::Default(), true);
    ASSERT_TRUE(regex_result);
    EXPECT_EQ(*regex_result, 1);

    auto removals = service.PollLocalDiskRemovals(owner);
    ASSERT_TRUE(removals);
    ASSERT_EQ(removals->size(), 2);
    EXPECT_FALSE(service.ExistKey("bulk-a", TenantId::Default()).value());
    EXPECT_FALSE(service.ExistKey("bulk-b", TenantId::Default()).value());
    ASSERT_TRUE(service.AckLocalDiskRemoval(owner, "bulk-a",
                                            TenantId::Default(), false));
    auto retried = service.PollLocalDiskRemovals(owner);
    ASSERT_TRUE(retried);
    auto bulk_a = std::find_if(retried->begin(), retried->end(),
                               [](const LocalDiskRemoval& removal) {
                                   return removal.key == "bulk-a";
                               });
    ASSERT_NE(bulk_a, retried->end());
    EXPECT_EQ(bulk_a->descriptor.removal_retry_count.value_or(0), 1);
    for (const auto& removal : *removals) {
        ASSERT_TRUE(service.AckLocalDiskRemoval(owner, removal.key,
                                                TenantId::Default(), true));
    }
    EXPECT_EQ(service.GetKeyCount(), 0);
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

TEST(HeterogeneousStorageMasterTest, RemovalWaitsForEveryLocalOwnerAck) {
    MasterService service(LocalMasterConfig());
    const UUID owner_a{61, 62};
    const UUID owner_b{63, 64};
    const std::string key = "multi-owner";

    Replica replica_a(owner_a, 1024, "127.0.0.1:19001", "backend-a",
                      "objects/a", 1, ReplicaStatus::COMPLETE);
    Replica replica_b(owner_b, 1024, "127.0.0.1:19002", "backend-b",
                      "objects/b", 2, ReplicaStatus::COMPLETE);
    ASSERT_TRUE(
        service.AddReplica(owner_a, key, TenantId::Default(), replica_a));
    ASSERT_TRUE(
        service.AddReplica(owner_b, key, TenantId::Default(), replica_b));

    ASSERT_TRUE(service.Remove(key, TenantId::Default(), true));
    ASSERT_EQ(service.PollLocalDiskRemovals(owner_a)->size(), 1);
    ASSERT_EQ(service.PollLocalDiskRemovals(owner_b)->size(), 1);

    ASSERT_TRUE(
        service.AckLocalDiskRemoval(owner_a, key, TenantId::Default(), true));
    EXPECT_TRUE(service.PollLocalDiskRemovals(owner_a)->empty());
    EXPECT_EQ(service.PollLocalDiskRemovals(owner_b)->size(), 1);
    EXPECT_EQ(service.GetKeyCount(), 1);

    ASSERT_TRUE(
        service.AckLocalDiskRemoval(owner_b, key, TenantId::Default(), true));
    EXPECT_EQ(service.GetKeyCount(), 0);
}

}  // namespace
}  // namespace mooncake
