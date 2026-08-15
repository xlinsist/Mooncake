// Snapshot round-trip tests for the L2->L1 promotion-on-hit feature.
//
// The base fixture's TearDown automatically calls TestSnapshotAndRestore
// against the service_ member, which:
//   1. Persists the master state to local snapshot storage.
//   2. Boots a fresh MasterService in restore mode against that snapshot.
//   3. Asserts CaptureServiceState() matches before vs. after.
//
// LOCAL_DISK replica content is captured via CaptureServiceState's
// CompareReplicaDescriptor (which compares LocalDiskDescriptor fields), and
// LocalDiskSegmentState (offloading_objects map) is also captured. Anything
// these tests put into those fields will round-trip; anything not captured
// (e.g., per-shard PromotionTask map, per-segment promotion_objects map)
// is, by design, transient — the master is allowed to drop it on restart
// and let clients re-trigger via heartbeat.

#include "master_service_test_for_snapshot_base.h"

namespace mooncake::test {

class MasterServicePromotionSnapshotTest
    : public MasterServiceSnapshotTestBase {
   protected:
    static bool glog_initialized_;

    void SetUp() override {
        MasterServiceSnapshotTestBase::SetUp();
        if (!glog_initialized_) {
            google::InitGoogleLogging("MasterServicePromotionSnapshotTest");
            FLAGS_logtostderr = true;
            glog_initialized_ = true;
        }
    }

    // Build a master with promotion-on-hit enabled. enable_offload is
    // required (promotion gates on it). admission_threshold=1 keeps the
    // gate first-touch so tests don't need many reads.
    void CreateMasterServiceWithPromotion() {
        auto config = MasterServiceConfig::builder().build();
        // Builder doesn't expose promotion knobs. Use direct field
        // assignment.
        config.enable_offload = true;
        config.promotion_on_hit = true;
        config.promotion_admission_threshold = 1;
        config.put_start_discard_timeout_sec = 0;
        config.put_start_release_timeout_sec = 1;
        service_ = std::make_unique<MasterService>(config);
    }

    Segment MakeMemSegment(const std::string& name, size_t base) const {
        Segment s;
        s.id = generate_uuid();
        s.name = name;
        s.base = base;
        s.size = kDefaultSegmentSize;
        s.te_endpoint = name;
        return s;
    }

    UUID MountMemAndDisk(const std::string& seg_name, size_t base) {
        Segment seg = MakeMemSegment(seg_name, base);
        UUID client_id = generate_uuid();
        auto mount = service_->MountSegment(seg, client_id);
        EXPECT_TRUE(mount.has_value());
        auto mount_ld = service_->MountLocalDiskSegment(client_id, true);
        EXPECT_TRUE(mount_ld.has_value());
        return client_id;
    }

    bool InjectLocalDiskReplica(const UUID& client_id, const std::string& key,
                                int64_t size,
                                const std::string& transport_endpoint) {
        std::vector<OffloadTaskItem> tasks{
            OffloadTaskItem{.tenant_id = TenantId::Default().value(),
                            .key = key,
                            .size = size}};
        StorageObjectMetadata sm;
        sm.bucket_id = 0;
        sm.offset = 0;
        sm.key_size = static_cast<int64_t>(key.size());
        sm.data_size = size;
        sm.transport_endpoint = transport_endpoint;
        std::vector<StorageObjectMetadata> metas{sm};
        return service_->NotifyOffloadSuccess(client_id, tasks, metas)
            .has_value();
    }

    void ValidateRestoredService(MasterService& restored) override {
        if (!validate_removal_tombstone_) {
            return;
        }
        auto removals = restored.PollLocalDiskRemovals(removal_owner_);
        ASSERT_TRUE(removals);
        ASSERT_EQ(removals->size(), 1);
        const auto& removal = removals->front();
        EXPECT_EQ(removal.tenant_id, TenantId::Default().value());
        EXPECT_EQ(removal.key, removal_key_);
        EXPECT_EQ(removal.descriptor.client_id, removal_owner_);
        EXPECT_EQ(removal.descriptor.object_size, 4096);
        EXPECT_EQ(removal.descriptor.transport_endpoint, "127.0.0.1:19001");
        EXPECT_EQ(removal.descriptor.backend_id.value_or(""), "stable-backend");
        EXPECT_EQ(removal.descriptor.locator.value_or(""), "objects/version-3");
        EXPECT_EQ(removal.descriptor.generation.value_or(0), 3);
        ASSERT_TRUE(restored.AckLocalDiskRemoval(removal_owner_, removal_key_,
                                                 TenantId::Default(), true));
        auto after_ack = restored.PollLocalDiskRemovals(removal_owner_);
        ASSERT_TRUE(after_ack);
        EXPECT_TRUE(after_ack->empty());
        EXPECT_EQ(restored.GetKeyCount(), 0);
    }

    bool validate_removal_tombstone_{false};
    UUID removal_owner_{};
    std::string removal_key_;
};

bool MasterServicePromotionSnapshotTest::glog_initialized_ = false;

// A LOCAL_DISK replica created via the offload path round-trips intact:
// after snapshot+restore, the descriptor type, client_id, object_size, and
// transport_endpoint must all match.
TEST_F(MasterServicePromotionSnapshotTest, LocalDiskReplicaRoundTrip) {
    CreateMasterServiceWithPromotion();
    UUID client_id = MountMemAndDisk("seg_a", kDefaultSegmentBase);

    ASSERT_TRUE(
        InjectLocalDiskReplica(client_id, "k_cold", 1024, "seg_a_endpoint"));

    auto before = service_->GetReplicaList("k_cold", TenantId::Default());
    ASSERT_TRUE(before.has_value());
    ASSERT_EQ(before->replicas.size(), 1u);
    EXPECT_TRUE(before->replicas[0].is_local_disk_replica());
}

TEST_F(MasterServicePromotionSnapshotTest,
       DirectLocalRemovalTombstoneRoundTrip) {
    setenv("MC_HETERO_STORAGE_POLICY", "local_only", 1);
    CreateMasterServiceWithPromotion();
    const UUID owner = generate_uuid();
    ASSERT_TRUE(service_->MountLocalDiskSegment(owner, true));

    auto start =
        service_->PutStart(owner, "direct-removed", TenantId::Default(), 4096,
                           ManagedReplicateConfig());
    ASSERT_TRUE(start);
    ObjectMeta metadata;
    metadata.key = "direct-removed";
    metadata.local_disk_transport_endpoint = "127.0.0.1:19001";
    metadata.local_disk_backend_id = "stable-backend";
    metadata.local_disk_locator = "objects/version-3";
    metadata.local_disk_generation = 3;
    ASSERT_TRUE(service_->PutEnd(owner, metadata, TenantId::Default(),
                                 ReplicaType::LOCAL_DISK));
    ASSERT_TRUE(service_->Remove("direct-removed", TenantId::Default(), true));

    auto removals = service_->PollLocalDiskRemovals(owner);
    ASSERT_TRUE(removals);
    ASSERT_EQ(removals->size(), 1);
    EXPECT_EQ(removals->front().descriptor.backend_id.value_or(""),
              "stable-backend");
    EXPECT_EQ(removals->front().descriptor.locator.value_or(""),
              "objects/version-3");
    EXPECT_EQ(removals->front().descriptor.generation.value_or(0), 3);
    validate_removal_tombstone_ = true;
    removal_owner_ = owner;
    removal_key_ = "direct-removed";
    unsetenv("MC_HETERO_STORAGE_POLICY");
}

// A key with both LOCAL_DISK and a separate MEMORY replica round-trips.
// Tests that mixed-replica metadata serialization handles both descriptor
// variants in the same object.
TEST_F(MasterServicePromotionSnapshotTest, MixedMemoryAndLocalDiskRoundTrip) {
    CreateMasterServiceWithPromotion();
    UUID client_id = MountMemAndDisk("seg_a", kDefaultSegmentBase);

    // Put a MEMORY replica.
    ReplicateConfig rc;
    rc.replica_num = 1;
    ASSERT_TRUE(
        service_->PutStart(client_id, "k_mixed", TenantId::Default(), 1024, rc)
            .has_value());
    ASSERT_TRUE(service_
                    ->PutEnd(client_id, "k_mixed", TenantId::Default(),
                             ReplicaType::MEMORY)
                    .has_value());

    // Add LOCAL_DISK alongside.
    ASSERT_TRUE(
        InjectLocalDiskReplica(client_id, "k_mixed", 1024, "seg_a_endpoint"));

    auto descs = service_->GetReplicaList("k_mixed", TenantId::Default());
    ASSERT_TRUE(descs.has_value());
    EXPECT_EQ(descs->replicas.size(), 2u);
}

// LocalDiskSegment.enable_offloading flag is preserved across snapshot.
// (CaptureServiceState compares this explicitly.)
TEST_F(MasterServicePromotionSnapshotTest,
       LocalDiskSegmentEnableOffloadingPreserved) {
    CreateMasterServiceWithPromotion();
    UUID client_id = MountMemAndDisk("seg_a", kDefaultSegmentBase);
    (void)client_id;
    // No further action — the LocalDiskSegment exists with enable_offloading
    // = true. TearDown's TestSnapshotAndRestore round-trips and compares.
}

// Multiple LOCAL_DISK holders, one DRAM segment. Each holder's segment
// metadata must round-trip independently.
TEST_F(MasterServicePromotionSnapshotTest, MultipleLocalDiskHoldersRoundTrip) {
    CreateMasterServiceWithPromotion();

    // Mount two DRAM+LOCAL_DISK pairs.
    UUID client_a = MountMemAndDisk("seg_a", kDefaultSegmentBase);
    UUID client_b =
        MountMemAndDisk("seg_b", kDefaultSegmentBase + kDefaultSegmentSize);

    ASSERT_TRUE(
        InjectLocalDiskReplica(client_a, "k_a", 1024, "seg_a_endpoint"));
    ASSERT_TRUE(
        InjectLocalDiskReplica(client_b, "k_b", 2048, "seg_b_endpoint"));

    auto a = service_->GetReplicaList("k_a", TenantId::Default());
    auto b = service_->GetReplicaList("k_b", TenantId::Default());
    ASSERT_TRUE(a.has_value());
    ASSERT_TRUE(b.has_value());
    EXPECT_EQ(a->replicas[0].get_local_disk_descriptor().client_id, client_a);
    EXPECT_EQ(b->replicas[0].get_local_disk_descriptor().client_id, client_b);
    EXPECT_EQ(a->replicas[0].get_local_disk_descriptor().object_size, 1024u);
    EXPECT_EQ(b->replicas[0].get_local_disk_descriptor().object_size, 2048u);
}

// Persisting state with an in-flight PromotionTask must not crash and must
// not mutate the visible replica state. The PromotionTask itself is
// allowed to be dropped on restore (transient), but the LOCAL_DISK source
// it references must come back unchanged.
TEST_F(MasterServicePromotionSnapshotTest, InFlightPromotionTaskSnapshotSafe) {
    CreateMasterServiceWithPromotion();
    UUID client_id = MountMemAndDisk("seg_a", kDefaultSegmentBase);

    ASSERT_TRUE(
        InjectLocalDiskReplica(client_id, "k_cold", 1024, "seg_a_endpoint"));

    // Trigger promotion gate to enqueue a task. This pins the source
    // replica's refcnt and adds a per-shard PromotionTask plus a per-
    // segment promotion_objects entry.
    auto get = service_->GetReplicaList("k_cold", TenantId::Default());
    ASSERT_TRUE(get.has_value());

    // The visible replica list should still expose exactly one LOCAL_DISK
    // replica with correct content. Snapshot+restore happens in TearDown
    // and must preserve this view.
    EXPECT_EQ(get->replicas.size(), 1u);
    EXPECT_TRUE(get->replicas[0].is_local_disk_replica());
}

// Promotion-on-hit can be configured on, off, or implicitly off (when
// enable_offload is false). The persisted master config must not affect
// replica state round-trip in any of these modes — only the runtime gate
// behavior changes. This test asserts the off-by-feature-flag case:
// LOCAL_DISK keys still round-trip even when promotion is disabled.
}  // namespace mooncake::test

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
