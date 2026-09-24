package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

final class DiagnosticConfigBehaviorTest {
  @Test
  void generatedProfileConfigExposesFixedBoundsAndChannelOwnedAllowlists() {
    assertEquals(4, DiagnosticConfig.MAX_DEPTH);
    assertEquals(16, DiagnosticConfig.MAX_ACCESSORS);
    assertEquals(4, DiagnosticConfig.MAX_COLLECTION_ITEMS);
    assertEquals(160, DiagnosticConfig.MAX_SAMPLE_CHARS);
    assertEquals(4096, DiagnosticConfig.MAX_CHANNEL_CHARS);
    assertEquals("127.0.0.1", DiagnosticConfig.RECEIVER_HOST);
    assertEquals(7712, DiagnosticConfig.RECEIVER_PORT);
    assertEquals(
        "com.nhn.android.nmap.fileprovider",
        DiagnosticConfig.CAPTURE_AUTHORITY);
    assertEquals(
        "NaverNavi", DiagnosticConfig.CAPTURE_EXTERNAL_FILES_ROOT_PATH);
    assertEquals(
        "navi_trace", DiagnosticConfig.CAPTURE_FILE_PROVIDER_ROOT_NAME);
    assertEquals("com.android.shell", DiagnosticConfig.CAPTURE_GRANTEE_PACKAGE);
    assertEquals(5_000, DiagnosticConfig.CAPTURE_QUIESCE_TIMEOUT_MS);
    assertEquals(
        "capture-v3-export.sqlite3",
        DiagnosticConfig.OFFLINE_CAPTURE_EXPORT_DATABASE_NAME);
    assertEquals(32, DiagnosticConfig.OFFLINE_CAPTURE_BATCH_SIZE);
    assertEquals("capture-v3.sqlite3", DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_NAME);
    assertEquals(
        "capture-v3-recovery.sqlite3", DiagnosticConfig.OFFLINE_CAPTURE_DATABASE_RECOVERY_NAME);
    assertEquals("naver-diagnostic", DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME);
    assertEquals(250, DiagnosticConfig.OFFLINE_CAPTURE_FLUSH_INTERVAL_MS);
    assertEquals(4_194_304L, DiagnosticConfig.OFFLINE_CAPTURE_JOURNAL_SIZE_LIMIT_BYTES);
    assertEquals(256, DiagnosticConfig.OFFLINE_CAPTURE_LOCAL_QUEUE_CAPACITY);
    assertEquals(67_108_864L, DiagnosticConfig.OFFLINE_CAPTURE_MAX_DATABASE_BYTES);
    assertEquals(64, DiagnosticConfig.OFFLINE_CAPTURE_NETWORK_QUEUE_CAPACITY);
    assertEquals(
        "naver-6.8.0.5-diagnostic-offline-v3", DiagnosticConfig.OFFLINE_CAPTURE_PAYLOAD_BUILD_ID);
    assertEquals(64, DiagnosticConfig.OFFLINE_CAPTURE_PINNED_PER_CHANNEL);
    assertEquals(1_000, DiagnosticConfig.OFFLINE_CAPTURE_RETRY_INITIAL_MS);
    assertEquals(30_000, DiagnosticConfig.OFFLINE_CAPTURE_RETRY_MAX_MS);
    assertEquals(1, DiagnosticConfig.OFFLINE_CAPTURE_SCHEMA_VERSION);
    assertEquals(256, DiagnosticConfig.OFFLINE_CAPTURE_WAL_AUTOCHECKPOINT_PAGES);
    assertEquals(256, DiagnosticConfig.STATUS_ROW_QUOTA);
    assertEquals(256, DiagnosticConfig.ROUTE_ROW_QUOTA);
    assertEquals(1_536, DiagnosticConfig.TBT_CURRENT_ROW_QUOTA);
    assertEquals(1_536, DiagnosticConfig.TBT_NEXT_ROW_QUOTA);
    assertEquals(1_536, DiagnosticConfig.LANE_ROW_QUOTA);
    assertEquals(6_144, DiagnosticConfig.SAFETY_ROW_QUOTA);
    assertArrayEquals(new String[0], DiagnosticConfig.accessorAllowlist("status"));
    for (String channel : new String[] {"tbt_current", "tbt_next", "safety", "route", "lane"}) {
      assertEquals(false, DiagnosticConfig.accessorAllowlist(channel).length == 0);
    }
    assertArrayEquals(new String[0], DiagnosticConfig.accessorAllowlist("unknown"));
  }
}
