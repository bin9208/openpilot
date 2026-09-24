package ai.comma.naver.payload;

import java.util.concurrent.atomic.AtomicLong;

final class DiagnosticCounters {
  static final class Snapshot {
    final long objectQueueDropped;
    final long localQueueDropped;
    final long networkQueueDropped;
    final long storageErrors;
    final long networkErrors;
    final long retentionDeletes;
    final long rejectedFrames;

    Snapshot(
        long objectQueueDropped,
        long localQueueDropped,
        long networkQueueDropped,
        long storageErrors,
        long networkErrors,
        long retentionDeletes,
        long rejectedFrames) {
      this.objectQueueDropped = objectQueueDropped;
      this.localQueueDropped = localQueueDropped;
      this.networkQueueDropped = networkQueueDropped;
      this.storageErrors = storageErrors;
      this.networkErrors = networkErrors;
      this.retentionDeletes = retentionDeletes;
      this.rejectedFrames = rejectedFrames;
    }
  }

  private final AtomicLong objectQueueDropped = new AtomicLong();
  private final AtomicLong localQueueDropped = new AtomicLong();
  private final AtomicLong networkQueueDropped = new AtomicLong();
  private final AtomicLong storageErrors = new AtomicLong();
  private final AtomicLong networkErrors = new AtomicLong();
  private final AtomicLong retentionDeletes = new AtomicLong();
  private final AtomicLong rejectedFrames = new AtomicLong();

  void addObjectQueueDropped(long count) { add(objectQueueDropped, count); }
  void addLocalQueueDropped(long count) { add(localQueueDropped, count); }
  void addNetworkQueueDropped(long count) { add(networkQueueDropped, count); }
  void addStorageErrors(long count) { add(storageErrors, count); }
  void addNetworkErrors(long count) { add(networkErrors, count); }
  void addRetentionDeletes(long count) { add(retentionDeletes, count); }
  void addRejectedFrames(long count) { add(rejectedFrames, count); }

  Snapshot snapshot() {
    return new Snapshot(
        objectQueueDropped.get(),
        localQueueDropped.get(),
        networkQueueDropped.get(),
        storageErrors.get(),
        networkErrors.get(),
        retentionDeletes.get(),
        rejectedFrames.get());
  }

  private static void add(AtomicLong counter, long count) {
    if (count < 0L) {
      throw new IllegalArgumentException("negative diagnostic counter increment");
    }
    counter.addAndGet(count);
  }
}
