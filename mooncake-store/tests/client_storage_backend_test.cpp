// Regression tests for Client::PrepareStorageBackend (issue #3134): an
// invalid storage configuration must surface as an error instead of
// dereferencing a null backend or leaving a half-initialized one behind.

#include <glog/logging.h>
#include <gtest/gtest.h>

#include <filesystem>

#include "client_service.h"

namespace mooncake {
namespace {

class TestableClient : public Client {
   public:
    TestableClient()
        : Client(/*local_hostname=*/"localhost:9003",
                 /*metadata_connstring=*/"",
                 /*protocol=*/"tcp",
                 /*labels=*/{}) {}

    using Client::PrepareStorageBackend;
};

Replica::Descriptor MemoryReplica(
    ReplicaID id, const std::string& endpoint,
    ReplicaStatus status = ReplicaStatus::COMPLETE) {
    MemoryDescriptor descriptor;
    descriptor.buffer_descriptor =
        AllocatedBuffer::Descriptor{1024, 0x1000, "tcp", endpoint};
    return Replica::Descriptor{id, std::move(descriptor), status};
}

Replica::Descriptor NofReplica(ReplicaID id, const std::string& endpoint,
                               ReplicaStatus status = ReplicaStatus::COMPLETE) {
    NoFDescriptor descriptor;
    descriptor.buffer_descriptor =
        AllocatedBuffer::Descriptor{1024, 0x2000, "nvmeof", endpoint};
    return Replica::Descriptor{id, std::move(descriptor), status};
}

Replica::Descriptor DiskReplica(
    ReplicaID id, ReplicaStatus status = ReplicaStatus::COMPLETE) {
    return Replica::Descriptor{id, DiskDescriptor{"object", 1024}, status};
}

Replica::Descriptor LocalReplica(
    ReplicaID id, std::string backend_id, std::string locator,
    uint64_t generation, ReplicaStatus status = ReplicaStatus::COMPLETE) {
    LocalDiskDescriptor descriptor;
    descriptor.client_id = UUID{1, 2};
    descriptor.object_size = 1024;
    descriptor.transport_endpoint = "local";
    descriptor.backend_id = std::move(backend_id);
    descriptor.locator = std::move(locator);
    descriptor.generation = generation;
    return Replica::Descriptor{id, std::move(descriptor), status};
}

TEST(ClientPrepareStorageBackendTest, InvalidRootDirReturnsErrorWithoutCrash) {
    TestableClient client;
    // Before the fix this dereferenced a null StorageBackend and crashed.
    ErrorCode err = client.PrepareStorageBackend(
        "/nonexistent_mooncake_store_test_path/12345", "fsdir", true, 0);
    EXPECT_NE(err, ErrorCode::OK);
}

TEST(ClientPrepareStorageBackendTest, EmptyFsdirReturnsErrorWithoutCrash) {
    TestableClient client;
    ErrorCode err = client.PrepareStorageBackend(
        std::filesystem::current_path().string(), "", true, 0);
    EXPECT_NE(err, ErrorCode::OK);
}

TEST(ClientPrepareStorageBackendTest, ValidRootDirSucceeds) {
    std::string root = std::filesystem::current_path().string() +
                       "/data/client_prepare_storage_backend_test";
    std::filesystem::create_directories(root);

    TestableClient client;
    EXPECT_EQ(client.PrepareStorageBackend(root, "fsdir", true, 0),
              ErrorCode::OK);

    std::filesystem::remove_all(root);
}

TEST(ClientReadSourceSelectionTest, IgnoresIncompleteReplicas) {
    TestableClient client;
    const std::vector<Replica::Descriptor> replicas = {
        MemoryReplica(1, "memory", ReplicaStatus::PROCESSING), DiskReplica(2)};

    auto selected = client.GetPreferredReplica(replicas);

    ASSERT_TRUE(selected.has_value());
    EXPECT_EQ(selected->id, 2);
}

TEST(ClientReadSourceSelectionTest, PolicyOverridesMasterReplicaOrder) {
    TestableClient client;
    const std::vector<Replica::Descriptor> replicas = {
        DiskReplica(1), NofReplica(2, "nof"), MemoryReplica(3, "memory")};

    auto selected = client.GetPreferredReplica(replicas);

    ASSERT_TRUE(selected.has_value());
    EXPECT_EQ(selected->id, 3);
}

TEST(ClientReadSourceSelectionTest, SameNodeBatchRequiresCompleteMemory) {
    TestableClient client;
    const std::vector<Replica::Descriptor> replicas = {
        NofReplica(1, "nof"),
        MemoryReplica(2, "memory", ReplicaStatus::PROCESSING),
        MemoryReplica(3, "memory")};

    auto selected = client.GetPreferredReplica(
        replicas, Client::ReadSourceRequirement::MEMORY_ONLY);

    ASSERT_TRUE(selected.has_value());
    EXPECT_EQ(selected->id, 3);
    EXPECT_EQ(
        client
            .GetPreferredReplica({NofReplica(4, "nof")},
                                 Client::ReadSourceRequirement::MEMORY_ONLY)
            .error(),
        ErrorCode::INVALID_REPLICA);
}

TEST(ClientReadSourceSelectionTest, RangeRequiresTransferBackedReplica) {
    TestableClient client;
    const std::vector<Replica::Descriptor> replicas = {DiskReplica(1),
                                                       NofReplica(2, "nof")};

    auto selected = client.GetPreferredReplica(
        replicas, Client::ReadSourceRequirement::SCATTER_RANGE);

    ASSERT_TRUE(selected.has_value());
    EXPECT_EQ(selected->id, 2);
    EXPECT_EQ(
        client
            .GetPreferredReplica({DiskReplica(3)},
                                 Client::ReadSourceRequirement::SCATTER_RANGE)
            .error(),
        ErrorCode::INVALID_REPLICA);
}

TEST(ClientFinalizeReconciliationTest, RequiresExactCompleteLocalLocator) {
    const LocalObjectLocator committed{"backend-a", "objects/generation-7",
                                       1024, 7};
    EXPECT_TRUE(detail::HasCommittedLocalReplica(
        {LocalReplica(1, "backend-a", "objects/generation-7", 7)}, committed));
    EXPECT_FALSE(detail::HasCommittedLocalReplica(
        {LocalReplica(2, "backend-a", "objects/generation-6", 6)}, committed));
    EXPECT_FALSE(detail::HasCommittedLocalReplica(
        {LocalReplica(3, "backend-a", "objects/generation-7", 7,
                      ReplicaStatus::PROCESSING)},
        committed));
}

}  // namespace
}  // namespace mooncake
