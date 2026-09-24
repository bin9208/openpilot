package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

final class OfflineCaptureStoreTest {
  @Test
  void retentionPolicyAcceptsOnlyTheSixConfiguredChannels() {
    CaptureRetentionPolicy policy = new CaptureRetentionPolicy();

    assertTrue(policy.isChannel("status"));
    assertTrue(policy.isChannel("route"));
    assertTrue(policy.isChannel("tbt_current"));
    assertTrue(policy.isChannel("tbt_next"));
    assertTrue(policy.isChannel("lane"));
    assertTrue(policy.isChannel("safety"));
    assertFalse(policy.isChannel("unknown"));
    assertEquals(11264, policy.quota("status") + policy.quota("route")
        + policy.quota("tbt_current") + policy.quota("tbt_next")
        + policy.quota("lane") + policy.quota("safety"));
    assertEquals(64, policy.pinnedPerChannel());
    assertThrows(IllegalArgumentException.class, () -> policy.quota("unknown"));
  }

  @Test
  void safetyOverflowCannotEvictTbtLaneRouteOrStatus() throws Exception {
    FakeCaptureDatabase database = new FakeCaptureDatabase();
    DiagnosticCounters counters = new DiagnosticCounters();
    OfflineCaptureStore store = new OfflineCaptureStore(
        database, new CaptureRetentionPolicy(), "run-1",
        "naver-6.8.0.5-diagnostic-offline-v1", counters);

    store.writeBatch(frames("status", 256), counters.snapshot());
    store.writeBatch(frames("route", 256), counters.snapshot());
    store.writeBatch(frames("tbt_current", 1536), counters.snapshot());
    store.writeBatch(frames("tbt_next", 1536), counters.snapshot());
    store.writeBatch(frames("lane", 1536), counters.snapshot());
    counters.addLocalQueueDropped(4L);
    store.writeBatch(frames("safety", 6200), counters.snapshot());

    assertEquals(256, database.count("status"));
    assertEquals(256, database.count("route"));
    assertEquals(1536, database.count("tbt_current"));
    assertEquals(1536, database.count("tbt_next"));
    assertEquals(1536, database.count("lane"));
    assertEquals(6144, database.count("safety"));
    assertEquals(64, database.pinnedCount("safety"));
    assertEquals("4", database.meta("local_queue_dropped"));
  }

  @Test
  void firstSixtyFourFramesPerChannelArePinnedBeforePruning() throws Exception {
    FakeCaptureDatabase database = new FakeCaptureDatabase();
    DiagnosticCounters counters = new DiagnosticCounters();
    OfflineCaptureStore store = store(database, counters);

    store.writeBatch(frames("status", 300), counters.snapshot());

    assertEquals(256, database.count("status"));
    assertEquals(64, database.pinnedCount("status"));
    assertEquals(1L, database.firstPinnedSequence("status"));
    assertEquals(64L, database.lastPinnedSequence("status"));
    assertEquals(44L, counters.snapshot().retentionDeletes);
    assertEquals("44", database.meta("retention_deletes"));
  }

  @Test
  void insertFailureRollsBackTheBatchAndStillEndsTheTransaction() {
    FakeCaptureDatabase database = new FakeCaptureDatabase();
    database.failOnInsert = 2;
    OfflineCaptureStore store = store(database);

    assertThrows(IOException.class,
        () -> store.writeBatch(frames("status", 2), new DiagnosticCounters().snapshot()));

    assertEquals(0, database.count("status"));
    assertEquals(1, database.beginCount);
    assertEquals(1, database.endCount);
    assertEquals(0, database.successfulTransactionCount);
  }

  @Test
  void pruneFailureRollsBackAndDoesNotCountUncommittedDeletes() throws Exception {
    FakeCaptureDatabase database = new FakeCaptureDatabase();
    DiagnosticCounters counters = new DiagnosticCounters();
    OfflineCaptureStore store = store(database, counters);
    database.failDelete = true;

    assertThrows(
        IOException.class,
        () -> store.writeBatch(frames("status", 300), counters.snapshot()));

    assertEquals(0, database.count("status"));
    assertEquals(0L, counters.snapshot().retentionDeletes);
    assertEquals(null, database.meta("retention_deletes"));
  }

  @Test
  void laterBatchCannotResetCommittedRetentionDeleteMetadata() throws Exception {
    FakeCaptureDatabase database = new FakeCaptureDatabase();
    DiagnosticCounters counters = new DiagnosticCounters();
    OfflineCaptureStore store = store(database, counters);

    store.writeBatch(frames("status", 300), counters.snapshot());
    store.writeBatch(frames("route", 1), counters.snapshot());

    assertEquals(44L, counters.snapshot().retentionDeletes);
    assertEquals("44", database.meta("retention_deletes"));
  }

  @Test
  void batchPersistsEveryCounterAndCloseMarksOnlyItsRunClean() throws Exception {
    FakeCaptureDatabase database = new FakeCaptureDatabase();
    DiagnosticCounters counters = new DiagnosticCounters();
    OfflineCaptureStore store = store(database, counters);
    counters.addObjectQueueDropped(1L);
    counters.addLocalQueueDropped(2L);
    counters.addNetworkQueueDropped(3L);
    counters.addStorageErrors(4L);
    counters.addNetworkErrors(5L);
    counters.addRetentionDeletes(6L);
    counters.addRejectedFrames(7L);

    store.writeBatch(frames("route", 1), counters.snapshot());

    assertEquals("1", database.meta("object_queue_dropped"));
    assertEquals("2", database.meta("local_queue_dropped"));
    assertEquals("3", database.meta("network_queue_dropped"));
    assertEquals("4", database.meta("storage_errors"));
    assertEquals("5", database.meta("network_errors"));
    assertEquals("6", database.meta("retention_deletes"));
    assertEquals("7", database.meta("rejected_frames"));
    assertFalse(database.runClean("run-1"));

    store.close();

    assertTrue(database.runClean("run-1"));
    assertTrue(database.closed);
  }

  private static OfflineCaptureStore store(FakeCaptureDatabase database) {
    return store(database, new DiagnosticCounters());
  }

  private static OfflineCaptureStore store(
      FakeCaptureDatabase database, DiagnosticCounters counters) {
    return new OfflineCaptureStore(
        database, new CaptureRetentionPolicy(), "run-1",
        "naver-6.8.0.5-diagnostic-offline-v1", counters);
  }

  private static List<EncodedDiagnosticFrame> frames(String channel, int count) {
    List<EncodedDiagnosticFrame> frames = new ArrayList<>();
    for (int sequence = 1; sequence <= count; sequence++) {
      frames.add(new EncodedDiagnosticFrame(channel, sequence, sequence, 0L, "{}"));
    }
    return frames;
  }

  private static final class FakeCaptureDatabase implements CaptureDatabase {
    private List<Row> rows = new ArrayList<>();
    private Map<String, String> metadata = new HashMap<>();
    private Map<String, Boolean> cleanRuns = new HashMap<>();
    private List<Row> savedRows;
    private Map<String, String> savedMetadata;
    private Map<String, Boolean> savedCleanRuns;
    int failOnInsert;
    int insertCount;
    int beginCount;
    int endCount;
    int successfulTransactionCount;
    boolean failDelete;
    boolean transactionSuccessful;
    boolean closed;

    @Override
    public void beginTransaction() {
      beginCount++;
      transactionSuccessful = false;
      savedRows = copyRows(rows);
      savedMetadata = new HashMap<>(metadata);
      savedCleanRuns = new HashMap<>(cleanRuns);
    }

    @Override
    public void ensureRun(String runId, long startedMonotonicNs, String buildId) {
      cleanRuns.putIfAbsent(runId, false);
    }

    @Override
    public void markRunClean(String runId) {
      cleanRuns.put(runId, true);
    }

    @Override
    public long countRows(String channel) {
      return count(channel);
    }

    @Override
    public long countPinnedRows(String channel) {
      return pinnedCount(channel);
    }

    @Override
    public void insert(String runId, EncodedDiagnosticFrame frame, boolean pinned) throws IOException {
      insertCount++;
      if (failOnInsert == insertCount) {
        throw new IOException("injected insert failure");
      }
      rows.add(new Row(frame.channel(), frame.sequence(), pinned));
    }

    @Override
    public void deleteOldestUnpinned(String channel, long count) throws IOException {
      if (failDelete) {
        throw new IOException("injected delete failure");
      }
      for (int index = 0; index < rows.size() && count > 0L;) {
        Row row = rows.get(index);
        if (channel.equals(row.channel) && !row.pinned) {
          rows.remove(index);
          count--;
        } else {
          index++;
        }
      }
    }

    @Override
    public void putMeta(String key, String value) {
      metadata.put(key, value);
    }

    @Override
    public void setTransactionSuccessful() {
      transactionSuccessful = true;
      successfulTransactionCount++;
    }

    @Override
    public void endTransaction() {
      endCount++;
      if (!transactionSuccessful) {
        rows = savedRows;
        metadata = savedMetadata;
        cleanRuns = savedCleanRuns;
      }
    }

    @Override
    public void close() {
      closed = true;
    }

    int count(String channel) {
      int count = 0;
      for (Row row : rows) {
        if (channel.equals(row.channel)) count++;
      }
      return count;
    }

    int pinnedCount(String channel) {
      int count = 0;
      for (Row row : rows) {
        if (channel.equals(row.channel) && row.pinned) count++;
      }
      return count;
    }

    String meta(String key) {
      return metadata.get(key);
    }

    boolean runClean(String runId) {
      return Boolean.TRUE.equals(cleanRuns.get(runId));
    }

    long firstPinnedSequence(String channel) {
      for (Row row : rows) {
        if (channel.equals(row.channel) && row.pinned) return row.sequence;
      }
      throw new AssertionError("no pinned row for " + channel);
    }

    long lastPinnedSequence(String channel) {
      long sequence = -1L;
      for (Row row : rows) {
        if (channel.equals(row.channel) && row.pinned) sequence = row.sequence;
      }
      return sequence;
    }

    private static List<Row> copyRows(List<Row> source) {
      List<Row> copy = new ArrayList<>();
      for (Row row : source) {
        copy.add(new Row(row.channel, row.sequence, row.pinned));
      }
      return copy;
    }
  }

  private static final class Row {
    final String channel;
    final long sequence;
    final boolean pinned;

    Row(String channel, long sequence, boolean pinned) {
      this.channel = channel;
      this.sequence = sequence;
      this.pinned = pinned;
    }
  }
}
