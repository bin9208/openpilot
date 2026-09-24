package ai.comma.naver.payload;

import java.io.IOException;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

final class OfflineCaptureStore implements FrameBatchConsumer {
  private final CaptureDatabase database;
  private final CaptureRetentionPolicy retentionPolicy;
  private final String runId;
  private final String buildId;
  private final DiagnosticCounters diagnosticCounters;
  private final long startedMonotonicNs;
  private boolean runPersisted;
  private boolean closed;

  OfflineCaptureStore(
      CaptureDatabase database,
      CaptureRetentionPolicy retentionPolicy,
      String runId,
      String buildId,
      DiagnosticCounters diagnosticCounters) {
    if (database == null || retentionPolicy == null || runId == null || buildId == null
        || diagnosticCounters == null) {
      throw new IllegalArgumentException("offline capture store requires non-null inputs");
    }
    this.database = database;
    this.retentionPolicy = retentionPolicy;
    this.runId = runId;
    this.buildId = buildId;
    this.diagnosticCounters = diagnosticCounters;
    startedMonotonicNs = System.nanoTime();
  }

  @Override
  public void writeBatch(List<EncodedDiagnosticFrame> frames, DiagnosticCounters.Snapshot counters)
      throws IOException {
    if (closed) {
      throw new IllegalStateException("offline capture store is closed");
    }
    if (frames == null || counters == null) {
      throw new IllegalArgumentException("offline capture batch requires non-null inputs");
    }
    for (EncodedDiagnosticFrame frame : frames) {
      if (frame == null || !retentionPolicy.isChannel(frame.channel())) {
        throw new IllegalArgumentException("unsupported capture frame");
      }
    }

    boolean began = false;
    boolean committed = false;
    boolean transactionSuccessful = false;
    long pendingRetentionDeletes = 0L;
    try {
      database.beginTransaction();
      began = true;
      database.ensureRun(runId, startedMonotonicNs, buildId);
      Map<String, Long> pinnedCounts = new HashMap<>();
      Set<String> affectedChannels = new LinkedHashSet<>();
      for (EncodedDiagnosticFrame frame : frames) {
        String channel = frame.channel();
        Long pinnedCount = pinnedCounts.get(channel);
        if (pinnedCount == null) {
          pinnedCount = database.countPinnedRows(channel);
        }
        boolean pinned = pinnedCount < retentionPolicy.pinnedPerChannel();
        if (pinned) {
          pinnedCount++;
        }
        pinnedCounts.put(channel, pinnedCount);
        database.insert(runId, frame, pinned);
        affectedChannels.add(channel);
      }
      for (String channel : affectedChannels) {
        long excess = database.countRows(channel) - retentionPolicy.quota(channel);
        if (excess > 0L) {
          database.deleteOldestUnpinned(channel, excess);
          pendingRetentionDeletes += excess;
        }
      }
      long retainedBefore = Math.max(
          counters.retentionDeletes,
          diagnosticCounters.snapshot().retentionDeletes);
      putCounters(counters, retainedBefore + pendingRetentionDeletes);
      database.setTransactionSuccessful();
      transactionSuccessful = true;
    } finally {
      if (began) {
        database.endTransaction();
        committed = transactionSuccessful;
      }
    }
    if (committed && pendingRetentionDeletes > 0L) {
      diagnosticCounters.addRetentionDeletes(pendingRetentionDeletes);
    }
    runPersisted = true;
  }

  @Override
  public void close() {
    if (closed) {
      return;
    }
    closed = true;
    try {
      if (runPersisted) {
        database.markRunClean(runId);
      }
    } catch (IOException exception) {
      throw new IllegalStateException("failed to mark capture run clean", exception);
    } finally {
      database.close();
    }
  }

  private void putCounters(
      DiagnosticCounters.Snapshot counters, long retentionDeletes) throws IOException {
    database.putMeta("object_queue_dropped", Long.toString(counters.objectQueueDropped));
    database.putMeta("local_queue_dropped", Long.toString(counters.localQueueDropped));
    database.putMeta("network_queue_dropped", Long.toString(counters.networkQueueDropped));
    database.putMeta("storage_errors", Long.toString(counters.storageErrors));
    database.putMeta("network_errors", Long.toString(counters.networkErrors));
    database.putMeta("retention_deletes", Long.toString(retentionDeletes));
    database.putMeta("rejected_frames", Long.toString(counters.rejectedFrames));
  }
}
