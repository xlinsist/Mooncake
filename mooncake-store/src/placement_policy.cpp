#include "placement_policy.h"

#include <algorithm>
#include <cctype>
#include <string>

#include "replica_selection.h"

namespace mooncake {

tl::expected<PlacementPolicyKind, std::string> ParsePlacementPolicy(
    std::string_view value) {
    std::string normalized(value);
    std::transform(
        normalized.begin(), normalized.end(), normalized.begin(),
        [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    if (normalized.empty() || normalized == "legacy") {
        return PlacementPolicyKind::LEGACY;
    }
    if (normalized == "local_only") {
        return PlacementPolicyKind::LOCAL_ONLY;
    }
    if (normalized == "remote_only") {
        return PlacementPolicyKind::REMOTE_ONLY;
    }
    if (normalized == "round_robin") {
        return PlacementPolicyKind::ROUND_ROBIN;
    }
    return tl::make_unexpected("unsupported heterogeneous storage policy: " +
                               std::string(value));
}

tl::expected<StorageTarget, ErrorCode> PlacementPolicy::SelectWriteTarget(
    const PlacementContext& context) {
    switch (kind_) {
        case PlacementPolicyKind::LOCAL_ONLY:
            if (context.local_available) {
                return StorageTarget::LOCAL_NVME;
            }
            return tl::make_unexpected(ErrorCode::NO_AVAILABLE_HANDLE);
        case PlacementPolicyKind::REMOTE_ONLY:
            if (context.remote_available) {
                return StorageTarget::REMOTE_NOF;
            }
            return tl::make_unexpected(ErrorCode::NO_AVAILABLE_HANDLE);
        case PlacementPolicyKind::ROUND_ROBIN:
            if (context.local_available && context.remote_available) {
                const auto sequence = round_robin_sequence_.fetch_add(
                    1, std::memory_order_relaxed);
                return sequence % 2 == 0 ? StorageTarget::LOCAL_NVME
                                         : StorageTarget::REMOTE_NOF;
            }
            if (context.local_available) {
                return StorageTarget::LOCAL_NVME;
            }
            if (context.remote_available) {
                return StorageTarget::REMOTE_NOF;
            }
            return tl::make_unexpected(ErrorCode::NO_AVAILABLE_HANDLE);
        case PlacementPolicyKind::LEGACY:
            return tl::make_unexpected(ErrorCode::INVALID_PARAMS);
    }
    return tl::make_unexpected(ErrorCode::INVALID_PARAMS);
}

const Replica::Descriptor* PlacementPolicy::SelectReadSource(
    const std::vector<Replica::Descriptor>& replicas,
    const std::unordered_set<std::string>& local_endpoints) {
    return SelectBestReplica(replicas, local_endpoints);
}

bool PlacementPolicy::SupportsScatterRangeRead(
    const Replica::Descriptor& replica) {
    return replica.is_memory_replica() || replica.is_nof_replica();
}

}  // namespace mooncake
