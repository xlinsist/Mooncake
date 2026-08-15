#pragma once

#include <atomic>
#include <cstdint>
#include <string_view>
#include <unordered_set>
#include <vector>

#include <ylt/util/tl/expected.hpp>

#include "types.h"
#include "replica.h"

namespace mooncake {

enum class StorageTarget : uint8_t {
    LOCAL_NVME = 0,
    REMOTE_NOF = 1,
};

enum class PlacementPolicyKind : uint8_t {
    LEGACY = 0,
    LOCAL_ONLY = 1,
    REMOTE_ONLY = 2,
    ROUND_ROBIN = 3,
};

struct PlacementContext {
    std::string_view key;
    uint64_t object_size{0};
    std::string_view requester_host_id;
    bool local_available{false};
    bool remote_available{false};
};

tl::expected<PlacementPolicyKind, std::string> ParsePlacementPolicy(
    std::string_view value);

class PlacementPolicy {
   public:
    explicit PlacementPolicy(PlacementPolicyKind kind) : kind_(kind) {}

    PlacementPolicyKind kind() const { return kind_; }

    tl::expected<StorageTarget, ErrorCode> SelectWriteTarget(
        const PlacementContext& context);

    static const Replica::Descriptor* SelectReadSource(
        const std::vector<Replica::Descriptor>& replicas,
        const std::unordered_set<std::string>& local_endpoints);

    static bool SupportsScatterRangeRead(const Replica::Descriptor& replica);

   private:
    PlacementPolicyKind kind_;
    std::atomic<uint64_t> round_robin_sequence_{0};
};

}  // namespace mooncake
