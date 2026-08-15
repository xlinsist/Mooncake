#include <glog/logging.h>
#include <gtest/gtest.h>

#include "serializer.h"
#include "rpc_types.h"
#include "serialize/serializer.h"
#include "segment.h"

namespace mooncake::test {

// Example class implementing serialization following the usage documentation
class ExampleClass {
   public:
    ExampleClass() : id_(0), value_(0.0), name_("") {}

    ExampleClass(int id, double value, const std::string& name)
        : id_(id), value_(value), name_(name) {}

    // Serialization method (works with both counter and writer)
    template <typename T>
    void serialize_to(T& serializer) const {
        serializer.write(&id_, sizeof(id_));
        serializer.write(&value_, sizeof(value_));

        // Serialize string length first, then string data
        size_t name_length = name_.length();
        serializer.write(&name_length, sizeof(name_length));
        if (!name_.empty()) {
            serializer.write(name_.data(), name_.length());
        }
    }

    // Deserialization method
    template <typename T>
    static std::shared_ptr<ExampleClass> deserialize_from(T& serializer) {
        try {
            auto obj = std::make_shared<ExampleClass>();

            // Deserialize basic members
            serializer.read(&obj->id_, sizeof(obj->id_));
            serializer.read(&obj->value_, sizeof(obj->value_));

            // Deserialize string
            size_t name_length;
            serializer.read(&name_length, sizeof(name_length));
            if (name_length > 0) {
                obj->name_.resize(name_length);
                serializer.read(&obj->name_[0], name_length);
            }

            return obj;
        } catch (const std::exception& e) {
            return nullptr;
        }
    }

    // Getters for testing
    int getId() const { return id_; }
    double getValue() const { return value_; }
    const std::string& getName() const { return name_; }

    // Equality operator for testing
    bool operator==(const ExampleClass& other) const {
        return id_ == other.id_ && value_ == other.value_ &&
               name_ == other.name_;
    }

   protected:
    int id_;
    double value_;
    std::string name_;
};

class ExampleClassWithException {
   public:
    ExampleClassWithException() : value_(0) {}
    ExampleClassWithException(int value) : value_(value) {}

    template <typename T>
    void serialize_to(T& serializer) const {
        serializer.write(&value_, sizeof(value_));
    }

    template <typename T>
    static std::shared_ptr<ExampleClassWithException> deserialize_from(
        T& serializer) {
        throw std::runtime_error("throw_exception");
    }

   private:
    int value_;
};

class SerializerTest : public ::testing::Test {
   protected:
    void SetUp() override {
        google::InitGoogleLogging("SerializerTest");
        FLAGS_logtostderr = true;
    }

    void TearDown() override { google::ShutdownGoogleLogging(); }
};

TEST_F(SerializerTest, ExampleClassSerialization) {
    // Create an example object
    ExampleClass original(42, 3.14159, "Test Object");

    // Test serialization
    std::vector<SerializedByte> buffer;
    ASSERT_EQ(serialize_to(original, buffer), ErrorCode::OK);
    ASSERT_FALSE(buffer.empty());

    // Test deserialization
    auto restored = deserialize_from<ExampleClass>(buffer);
    ASSERT_NE(restored, nullptr);

    // Verify the deserialized object matches the original
    EXPECT_EQ(restored->getId(), original.getId());
    EXPECT_DOUBLE_EQ(restored->getValue(), original.getValue());
    EXPECT_EQ(restored->getName(), original.getName());
    EXPECT_TRUE(*restored == original);
}

TEST_F(SerializerTest, ExampleClassSerializationWithSharedPtr) {
    // Test with shared_ptr
    auto original =
        std::make_shared<ExampleClass>(777, 2.718, "Shared Pointer Test");

    std::vector<SerializedByte> buffer;
    ASSERT_EQ(serialize_to(original, buffer), ErrorCode::OK);

    auto restored = deserialize_from<ExampleClass>(buffer);
    ASSERT_NE(restored, nullptr);
    EXPECT_TRUE(*restored == *original);
}

TEST_F(SerializerTest, ExampleClassSerializationNullPointer) {
    // Test with null shared_ptr
    std::shared_ptr<ExampleClass> null_ptr = nullptr;

    std::vector<SerializedByte> buffer;
    ASSERT_EQ(serialize_to(null_ptr, buffer), ErrorCode::INVALID_PARAMS);
}

TEST_F(SerializerTest, ExampleClassDeserializationCorruptedBuffer) {
    // Create a valid object and serialize it
    ExampleClass original(1, 1.0, "Test");
    std::vector<SerializedByte> buffer;
    ASSERT_EQ(serialize_to(original, buffer), ErrorCode::OK);

    // Corrupt the buffer by removing the last byte
    buffer.pop_back();

    // Try to deserialize corrupted buffer
    auto restored = deserialize_from<ExampleClass>(buffer);
    EXPECT_EQ(restored, nullptr);
}

TEST_F(SerializerTest, ExampleClassDeserializationWithException) {
    // Create a valid object and serialize it
    ExampleClassWithException original(1);
    std::vector<SerializedByte> buffer;
    ASSERT_EQ(serialize_to(original, buffer), ErrorCode::OK);

    // Try to deserialize the buffer, the deserialization method will throw an
    // exception.
    auto restored = deserialize_from<ExampleClassWithException>(buffer);
    EXPECT_EQ(restored, nullptr);
}

TEST_F(SerializerTest, MountedSegmentSerializationPreservesHostId) {
    MountedSegment original;
    original.segment.id = generate_uuid();
    original.segment.name = "segment_host1";
    original.segment.base = 0x300000000;
    original.segment.size = 1024 * 1024;
    original.segment.te_endpoint = "segment_host1";
    original.segment.host_id = "host1";
    original.status = SegmentStatus::OK;

    msgpack::sbuffer buffer;
    MsgpackPacker packer(&buffer);
    ASSERT_TRUE(
        Serializer<MountedSegment>::serialize(original, packer).has_value());

    auto object_handle = msgpack::unpack(buffer.data(), buffer.size());
    auto restored =
        Serializer<MountedSegment>::deserialize(object_handle.get());
    ASSERT_TRUE(restored.has_value());
    EXPECT_EQ(restored->segment.id, original.segment.id);
    EXPECT_EQ(restored->segment.name, original.segment.name);
    EXPECT_EQ(restored->segment.host_id, original.segment.host_id);
    EXPECT_EQ(restored->status, original.status);
}

TEST_F(SerializerTest, MountedSegmentDeserializesLegacyFormatWithoutHostId) {
    const UUID segment_id = generate_uuid();

    msgpack::sbuffer buffer;
    MsgpackPacker packer(&buffer);
    packer.pack_array(8);
    packer.pack(UuidToString(segment_id));
    packer.pack(std::string("legacy_segment"));
    packer.pack(static_cast<uint64_t>(0x300000000));
    packer.pack(static_cast<uint64_t>(1024 * 1024));
    packer.pack(std::string("legacy_segment"));
    packer.pack(static_cast<int16_t>(SegmentStatus::OK));
    packer.pack(false);
    packer.pack_nil();

    auto object_handle = msgpack::unpack(buffer.data(), buffer.size());
    auto restored =
        Serializer<MountedSegment>::deserialize(object_handle.get());
    ASSERT_TRUE(restored.has_value());
    EXPECT_EQ(restored->segment.id, segment_id);
    EXPECT_EQ(restored->segment.name, "legacy_segment");
    EXPECT_TRUE(restored->segment.host_id.empty());
    EXPECT_EQ(restored->status, SegmentStatus::OK);
}

TEST_F(SerializerTest, LocalDiskReplicaRoundTripPreservesLocator) {
    const UUID owner{101, 202};
    Replica original(owner, 8192, "127.0.0.1:19001", "backend-a",
                     "objects/version-7", 7, "host-a", 3,
                     ReplicaStatus::REMOVED);
    SegmentView segment_view(nullptr);
    msgpack::sbuffer buffer;
    MsgpackPacker packer(&buffer);
    ASSERT_TRUE(Serializer<Replica>::serialize(original, segment_view, packer));

    auto object_handle = msgpack::unpack(buffer.data(), buffer.size());
    auto restored =
        Serializer<Replica>::deserialize(object_handle.get(), segment_view);
    ASSERT_TRUE(restored);
    EXPECT_EQ((*restored)->status(), ReplicaStatus::REMOVED);
    const auto descriptor = (*restored)->get_descriptor();
    ASSERT_TRUE(descriptor.is_local_disk_replica());
    const auto& local = descriptor.get_local_disk_descriptor();
    EXPECT_EQ(local.client_id, owner);
    EXPECT_EQ(local.object_size, 8192);
    EXPECT_EQ(local.transport_endpoint, "127.0.0.1:19001");
    EXPECT_EQ(local.backend_id.value_or(""), "backend-a");
    EXPECT_EQ(local.locator.value_or(""), "objects/version-7");
    EXPECT_EQ(local.generation.value_or(0), 7);
    EXPECT_EQ(local.host_id.value_or(""), "host-a");
    EXPECT_EQ(local.removal_retry_count.value_or(0), 3);
}

TEST_F(SerializerTest, LocalDiskReplicaDeserializesLegacyPayload) {
    const UUID owner{303, 404};
    msgpack::sbuffer buffer;
    MsgpackPacker packer(&buffer);
    packer.pack_array(4);
    packer.pack(static_cast<uint64_t>(77));
    packer.pack(static_cast<int16_t>(ReplicaStatus::COMPLETE));
    packer.pack(static_cast<int8_t>(ReplicaType::LOCAL_DISK));
    packer.pack_array(3);
    packer.pack(UuidToString(owner));
    packer.pack(static_cast<uint64_t>(4096));
    packer.pack(std::string("127.0.0.1:19002"));

    SegmentView segment_view(nullptr);
    auto object_handle = msgpack::unpack(buffer.data(), buffer.size());
    auto restored =
        Serializer<Replica>::deserialize(object_handle.get(), segment_view);
    ASSERT_TRUE(restored);
    const auto descriptor = (*restored)->get_descriptor();
    ASSERT_TRUE(descriptor.is_local_disk_replica());
    const auto& local = descriptor.get_local_disk_descriptor();
    EXPECT_EQ(local.client_id, owner);
    EXPECT_EQ(local.object_size, 4096);
    EXPECT_EQ(local.transport_endpoint, "127.0.0.1:19002");
    EXPECT_TRUE(local.backend_id.value_or("").empty());
    EXPECT_TRUE(local.locator.value_or("").empty());
    EXPECT_EQ(local.generation.value_or(0), 0);
    EXPECT_TRUE(local.host_id.value_or("").empty());
    EXPECT_EQ(local.removal_retry_count.value_or(0), 0);
}

TEST_F(SerializerTest, ManagedPlacementRequestPreservesCompatibleFields) {
    ManagedPlacementStartRequest original;
    original.client_id = UUID{11, 22};
    original.key = "managed-key";
    original.value_length = 8192;
    original.config.placement_control = PlacementControl::MANAGED;
    original.config.local_replica_num = 2;
    original.tenant_id = "tenant-a";

    auto encoded = struct_pack::serialize(original);
    auto decoded =
        struct_pack::deserialize<ManagedPlacementStartRequest>(encoded);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->client_id, original.client_id);
    EXPECT_EQ(decoded->key, original.key);
    EXPECT_EQ(decoded->value_length, original.value_length);
    EXPECT_EQ(
        decoded->config.placement_control.value_or(PlacementControl::MANUAL),
        PlacementControl::MANAGED);
    EXPECT_EQ(decoded->config.local_replica_num.value_or(0), 2);
    EXPECT_EQ(decoded->tenant_id, original.tenant_id);
}

TEST_F(SerializerTest, PlacementEndRequestPreservesLocator) {
    PlacementEndRequest original;
    original.client_id = UUID{33, 44};
    original.object_meta.key = "placed-key";
    original.object_meta.local_disk_transport_endpoint = "127.0.0.1:19001";
    original.object_meta.local_disk_backend_id = "backend-a";
    original.object_meta.local_disk_locator = "objects/generation-9";
    original.object_meta.local_disk_generation = 9;
    original.replica_type = ReplicaType::LOCAL_DISK;
    original.tenant_id = "tenant-b";

    auto encoded = struct_pack::serialize(original);
    auto decoded = struct_pack::deserialize<PlacementEndRequest>(encoded);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->object_meta.local_disk_transport_endpoint.value_or(""),
              "127.0.0.1:19001");
    EXPECT_EQ(decoded->object_meta.local_disk_backend_id.value_or(""),
              "backend-a");
    EXPECT_EQ(decoded->object_meta.local_disk_locator.value_or(""),
              "objects/generation-9");
    EXPECT_EQ(decoded->object_meta.local_disk_generation.value_or(0), 9);
}

TEST_F(SerializerTest, LocalDiskReadBatchRequestPreservesLocators) {
    LocalDiskReadRequest read;
    read.storage_key = "tenant/key";
    read.size = 4096;
    read.backend_id = "backend-b";
    read.locator = "objects/generation-12";
    read.object_size = 16384;
    read.generation = 12;
    LocalDiskReadBatchRequest original{{read}};

    auto encoded = struct_pack::serialize(original);
    auto decoded = struct_pack::deserialize<LocalDiskReadBatchRequest>(encoded);
    ASSERT_TRUE(decoded.has_value());
    ASSERT_EQ(decoded->requests.size(), 1);
    const auto& restored = decoded->requests.front();
    EXPECT_EQ(restored.backend_id.value_or(""), "backend-b");
    EXPECT_EQ(restored.locator.value_or(""), "objects/generation-12");
    EXPECT_EQ(restored.object_size.value_or(0), 16384);
    EXPECT_EQ(restored.generation.value_or(0), 12);
}

}  // namespace mooncake::test

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
