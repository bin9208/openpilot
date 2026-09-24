package ai.comma.naver.payload;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

final class CaptureRetentionPolicy {
  private final Map<String, Integer> quotas;

  CaptureRetentionPolicy() {
    Map<String, Integer> quota = new LinkedHashMap<>();
    quota.put("status", DiagnosticConfig.STATUS_ROW_QUOTA);
    quota.put("route", DiagnosticConfig.ROUTE_ROW_QUOTA);
    quota.put("tbt_current", DiagnosticConfig.TBT_CURRENT_ROW_QUOTA);
    quota.put("tbt_next", DiagnosticConfig.TBT_NEXT_ROW_QUOTA);
    quota.put("lane", DiagnosticConfig.LANE_ROW_QUOTA);
    quota.put("safety", DiagnosticConfig.SAFETY_ROW_QUOTA);
    quotas = Collections.unmodifiableMap(quota);
  }

  boolean isChannel(String channel) {
    return quotas.containsKey(channel);
  }

  int quota(String channel) {
    Integer quota = quotas.get(channel);
    if (quota == null) {
      throw new IllegalArgumentException("unsupported capture channel: " + channel);
    }
    return quota;
  }

  int pinnedPerChannel() {
    return DiagnosticConfig.OFFLINE_CAPTURE_PINNED_PER_CHANNEL;
  }
}
